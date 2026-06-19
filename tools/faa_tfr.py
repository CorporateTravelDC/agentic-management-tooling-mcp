"""
faa_tfr.py — MCP tool for fetching and parsing FAA TFR XML feed.

Source: https://tfr.faa.gov/tfr2/xml_files/fr.xml (public, no auth required)

FAA's TFR XML is frequently malformed or incomplete. This module wraps
all parsing in try/except blocks and returns whatever data can be extracted.

xmltodict is used to convert XML → dict → list of TFR objects.
"""

from __future__ import annotations

import httpx
import xmltodict

from config import FAA_TFR_URL

_TIMEOUT = 30  # TFR feed can be slow


def _safe_int(val, default=None):
    """Convert a value to int, returning default on failure."""
    if val is None:
        return default
    try:
        return int(float(str(val).replace(",", "")))
    except (ValueError, TypeError):
        return default


def _extract_tfr(item: dict) -> dict:
    """
    Extract a normalized TFR record from a raw parsed XML item dict.

    FAA TFR XML schema is inconsistent — fields may appear at different
    nesting levels depending on TFR type. We probe several known paths.
    """
    # Top-level NOTAM group
    notam = item.get("notamNumber", item.get("NOTAM_NUMBER", ""))

    # TFR type: 0=VIP, 1=Special Security, 3=Stadium, 6=Emergency, etc.
    tfr_type_code = item.get("codeType", item.get("TYPE", ""))

    # Location / facility identifier
    location = (
        item.get("facilityDesignation")
        or item.get("locationDesignation")
        or item.get("facilityIdent")
        or item.get("FACILITY_DESIGNATION")
        or ""
    )

    # Altitude floor / ceiling
    floor_raw = (
        item.get("codeDistVerLower")
        or item.get("valDistVerLower")
        or item.get("FLOOR")
        or ""
    )
    ceil_raw = (
        item.get("codeDistVerUpper")
        or item.get("valDistVerUpper")
        or item.get("CEILING")
        or ""
    )

    # Effective times — may be nested under TFRAreaGroup or dateEffective
    eff_start = (
        item.get("dateEffective")
        or item.get("DATE_EFFECTIVE")
        or item.get("effectiveDate")
        or ""
    )
    eff_end = (
        item.get("dateExpire")
        or item.get("DATE_EXPIRE")
        or item.get("expireDate")
        or ""
    )

    # Free-text description — typically in txtDescrUSNS
    description = (
        item.get("txtDescrUSNS")
        or item.get("txtDescr")
        or item.get("DESCRIPTION")
        or item.get("itemText")
        or ""
    )

    # Attempt to parse altitude integers; FAA often encodes as "SFC" or "FL180"
    floor_ft = _safe_int(floor_raw)
    ceiling_ft = _safe_int(ceil_raw)

    # If floor is "SFC" or equivalent, set to 0
    if floor_ft is None and str(floor_raw).upper() in ("SFC", "MSL", "AGL", "0"):
        floor_ft = 0

    return {
        "notam_id":    str(notam),
        "type":        str(tfr_type_code),
        "location":    str(location),
        "floor_ft":    floor_ft,
        "ceiling_ft":  ceiling_ft,
        "effective":   str(eff_start),
        "expires":     str(eff_end),
        "description": str(description)[:500] if description else "",
    }


def get_active_tfrs() -> dict:
    """
    Fetch and parse the FAA TFR XML feed, returning a list of active TFRs.

    Queries https://tfr.faa.gov/tfr2/xml_files/fr.xml directly. No auth required.
    FAA's XML is frequently malformed; parsing errors are caught gracefully and
    partial results are returned with a parse_warnings field.

    Returns:
        dict with keys:
          - tfrs (list[dict]): each entry contains notam_id, type, location,
            floor_ft, ceiling_ft, effective, expires, description
          - count (int)
          - parse_warnings (list[str]): any XML parsing issues encountered
          - source_url (str)
    """
    warnings = []

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(FAA_TFR_URL, follow_redirects=True)
            resp.raise_for_status()
            xml_text = resp.text
    except httpx.HTTPStatusError as e:
        return {
            "tfrs": [],
            "count": 0,
            "parse_warnings": [f"HTTP error fetching TFR feed: {e}"],
            "source_url": FAA_TFR_URL,
        }
    except Exception as e:
        return {
            "tfrs": [],
            "count": 0,
            "parse_warnings": [f"Network error: {e}"],
            "source_url": FAA_TFR_URL,
        }

    # Parse XML — FAA XML often has encoding issues; try with and without
    parsed = None
    try:
        parsed = xmltodict.parse(xml_text)
    except Exception as e:
        # Try stripping the XML declaration and re-parsing
        warnings.append(f"Initial XML parse failed ({e}), retrying with stripped header")
        try:
            stripped = "\n".join(
                line for line in xml_text.splitlines()
                if not line.strip().startswith("<?xml")
            )
            parsed = xmltodict.parse(stripped)
        except Exception as e2:
            return {
                "tfrs": [],
                "count": 0,
                "parse_warnings": [f"XML parse failed after retry: {e2}"],
                "source_url": FAA_TFR_URL,
            }

    # Navigate the parsed tree to find TFR items
    # FAA XML structure: XNOTAMLIST -> Group -> NOTAM (list or single item)
    tfr_items: list[dict] = []
    try:
        root = parsed or {}

        # Try common root keys in order
        for root_key in ("XNOTAMLIST", "xnotamList", "NotamList", "NOTAMLIST"):
            if root_key in root:
                root = root[root_key]
                break

        # Look for Group or item list
        for group_key in ("Group", "group", "NOTAM", "notam", "item"):
            if group_key in root:
                group = root[group_key]
                if isinstance(group, list):
                    tfr_items = group
                elif isinstance(group, dict):
                    # Might be a single item or have nested NOTAM list
                    inner_notam = group.get("NOTAM") or group.get("notam")
                    if inner_notam:
                        if isinstance(inner_notam, list):
                            tfr_items = inner_notam
                        else:
                            tfr_items = [inner_notam]
                    else:
                        tfr_items = [group]
                break

        # If still empty, try to walk the tree one more level
        if not tfr_items and isinstance(root, dict):
            for v in root.values():
                if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                    tfr_items = v
                    break
                elif isinstance(v, dict):
                    # One more level
                    for vv in v.values():
                        if isinstance(vv, list) and len(vv) > 0 and isinstance(vv[0], dict):
                            tfr_items = vv
                            break
                    if tfr_items:
                        break

    except Exception as e:
        warnings.append(f"Tree navigation error: {e}")

    # Extract normalized TFR records
    tfrs = []
    for item in tfr_items:
        if not isinstance(item, dict):
            continue
        try:
            tfr = _extract_tfr(item)
            tfrs.append(tfr)
        except Exception as e:
            warnings.append(f"Skipped malformed TFR item: {e}")

    return {
        "tfrs": tfrs,
        "count": len(tfrs),
        "parse_warnings": warnings,
        "source_url": FAA_TFR_URL,
    }
