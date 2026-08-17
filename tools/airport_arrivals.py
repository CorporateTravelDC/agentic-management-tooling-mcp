"""
tools/airport_arrivals.py -- generalized forward-looking arrivals lookup for
any airport, layered across free and paid public sources.

No airport is hardcoded. A deployment can configure its own free-tier
local-airport-website source(s) via the LOCAL_AIRPORT_WEBSITE_FIDS env var
(JSON object, same shape as the built-in default below) -- every other
airport already works through the universal AeroAPI tier with zero config.
Callers pass any ICAO or IATA airport code.

Tiers tried in order, first non-empty result wins:
  1. Configured local-airport-website FIDS scrape -- only for airports
     present in LOCAL_AIRPORT_WEBSITE_FIDS (free, no key needed). Empty by
     default; see below for how to configure one for your own deployment.
  2. FlightAware AeroAPI scheduled_arrivals -- any airport (requires
     FLIGHTAWARE_API_KEY; returns a clear error dict if unset rather than
     silently returning nothing)

This mirrors the layered resolver built into the corporatetraveldc dispatch
platform (which additionally has a SWIM/flight-events tier ahead of these
two, backed by that platform's own FAA SWIM ingest -- not available here
since this package has no SWIM subscription of its own). Use
dispatch_get_fids_arrivals (a separate MCP, corporatetravel-dispatch) when
you specifically want the SWIM-primary version for DCA/IAD/BWI; use this
tool for a general-purpose lookup at any airport, or when the dispatch
platform isn't reachable.

2026-08-16: previously had DCA/IAD (MWAA-operated DC hubs) hardcoded as the
only possible free tier -- genuinely portable already via the AeroAPI tier,
but the free-tier optimization itself only ever benefited a DC deployment.
Moved to env-var config so ANY deployment gets the same free-tier benefit
for their own local airport(s), not just this one. Example, for a
deployment near two airports with public arrivals JSON endpoints:

  LOCAL_AIRPORT_WEBSITE_FIDS='{"ORD":{"url":"https://example.com/fids.json","referer":"https://example.com/"}}'

Response shape assumed for any configured source: {"arrivals": [{"IATA":
str, "flightnumber": str, "dep_airport_code": str, "status": str,
"publishedTime": "YYYY-MM-DD HH:MM:SS", "mod_gate"/"gate": str,
"arr_terminal": str}, ...]} -- the exact MWAA JSON shape. A different
website's JSON shape would need a small adapter, not supported generically
here (that's the honest limit of "free tier," not a bug).
"""

import json
import os
from datetime import datetime, timedelta, timezone

import httpx

FLIGHTAWARE_API_BASE = "https://aeroapi.flightaware.com/aeroapi"


def _load_local_airport_fids() -> dict:
    """LOCAL_AIRPORT_WEBSITE_FIDS env var -- JSON object of
    {IATA: {"url": ..., "referer": ...}}. Empty dict (no free tier for any
    airport) if unset or invalid -- AeroAPI still covers everything, this
    is purely an optional cost-saving optimization per deployment."""
    raw = os.environ.get("LOCAL_AIRPORT_WEBSITE_FIDS", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {}
        return parsed
    except (json.JSONDecodeError, TypeError):
        return {}


_MWAA_AIRPORTS = _load_local_airport_fids()
_MWAA_UA = "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36"
_MWAA_COOKIE = "flight-info=1"
_MWAA_FORWARD_STATUSES = {"Scheduled", "InAir", "Delayed"}

# ICAO <-> IATA aliasing for the airport-code param -- accept either.
_ICAO_TO_IATA_HINT = {"KDCA": "DCA", "KIAD": "IAD", "KBWI": "BWI"}

_AEROAPI_FORWARD_STATUS_HINTS = ("scheduled", "en route", "en-route", "airborne")


def _normalize_airport(airport: str) -> tuple[str, str]:
    """Return (icao_code, iata_hint) -- best-effort normalization without a
    full airport database. If a 3-letter code is given, assume a 'K' prefix
    (US convention) for the ICAO form; if 4-letter, use as-is."""
    a = airport.strip().upper()
    if len(a) == 4:
        icao = a
        iata = _ICAO_TO_IATA_HINT.get(a, a[1:] if a.startswith("K") else a)
    elif len(a) == 3:
        iata = a
        icao = f"K{a}"
    else:
        icao = a
        iata = a
    return icao, iata


def _mwaa_lookup(iata: str, carriers: set[str] | None, within_minutes: int) -> list[dict]:
    cfg = _MWAA_AIRPORTS.get(iata)
    if not cfg:
        return []
    headers = {
        "User-Agent": _MWAA_UA,
        "Accept": "application/json",
        "Referer": cfg["referer"],
        "Cookie": _MWAA_COOKIE,
    }
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(cfg["url"], headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []

    now = datetime.now()
    cutoff = now + timedelta(minutes=within_minutes)
    results = []
    for f in data.get("arrivals", []):
        carrier = f.get("IATA")
        if carriers and carrier not in carriers:
            continue
        status = f.get("status")
        if status not in _MWAA_FORWARD_STATUSES:
            continue
        pub = f.get("publishedTime")
        if not pub:
            continue
        try:
            pub_dt = datetime.strptime(pub, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if not (now <= pub_dt <= cutoff):
            continue
        results.append({
            "source":     "local_website",
            "airport":    iata,
            "carrier":    carrier,
            "flight_num": f.get("flightnumber"),
            "origin":     f.get("dep_airport_code"),
            "status":     status,
            "scheduled":  pub,
            "gate":       f.get("mod_gate") or f.get("gate"),
            "terminal":   f.get("arr_terminal"),
        })
    return results


def _aeroapi_lookup(
    icao: str, carriers: set[str] | None, within_minutes: int, api_key: str
) -> list[dict]:
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{FLIGHTAWARE_API_BASE}/airports/{icao}/flights/scheduled_arrivals",
                headers={"x-apikey": api_key, "Accept": "application/json"},
                params={"max_pages": 1},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(minutes=within_minutes)
    results = []
    for f in data.get("scheduled_arrivals", []):
        if f.get("cancelled") or f.get("diverted"):
            continue
        iata_carrier = (f.get("operator_iata") or "").upper() or None
        codeshare_iatas = {(c or "")[:2].upper() for c in (f.get("codeshares_iata") or [])}
        if carriers:
            match_set = ({iata_carrier} if iata_carrier else set()) | codeshare_iatas
            if not (match_set & carriers):
                continue
        sched_raw = f.get("estimated_in") or f.get("scheduled_in")
        if not sched_raw:
            continue
        try:
            sched_dt = datetime.strptime(sched_raw, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if not (now <= sched_dt <= cutoff):
            continue
        status = f.get("status") or ""
        if not any(h in status.lower() for h in _AEROAPI_FORWARD_STATUS_HINTS):
            continue
        origin_info = f.get("origin") or {}
        results.append({
            "source":     "aeroapi",
            "airport":    icao,
            "carrier":    iata_carrier,
            "flight_num": f.get("flight_number"),
            "origin":     origin_info.get("code_iata") or origin_info.get("code"),
            "status":     status,
            "scheduled":  sched_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "gate":       None,
            "terminal":   None,
        })
    return results


def get_airport_arrivals(
    airport: str,
    carriers: list[str] | None = None,
    within_minutes: int = 90,
) -> dict:
    """
    Layered forward-looking arrivals lookup for any airport.

    Tries a configured free local-airport-website source first (if any), then falls back
    to FlightAware AeroAPI (any airport, requires FLIGHTAWARE_API_KEY env
    var -- returns a clear error if unset rather than silently finding
    nothing).

    Args:
        airport: ICAO (4-letter, e.g. 'KDCA') or IATA (3-letter, e.g. 'DCA')
                 airport code. Not limited to DC-area airports -- any
                 airport AeroAPI covers works, DCA/IAD just get an extra
                 free tier ahead of it.
        carriers: Optional list of IATA carrier codes to filter by, e.g.
                  ['AA', 'UA']. Omit for all carriers.
        within_minutes: Forward-looking window in minutes (default 90).

    Returns:
        Dict with: airport, source_used ('local_website' | 'aeroapi' |
        'none'), results (list of flight dicts: source, airport, carrier,
        flight_num, origin, status, scheduled, gate, terminal), and note
        (explains which tier served the data or why nothing did).
        On AeroAPI-required-but-unconfigured, includes an 'error' key.
    """
    import os

    icao, iata = _normalize_airport(airport)
    carrier_set = {c.strip().upper() for c in carriers} if carriers else None

    mwaa_results = _mwaa_lookup(iata, carrier_set, within_minutes)
    if mwaa_results:
        return {
            "airport": iata,
            "source_used": "local_website",
            "results": mwaa_results,
            "note": None,
        }

    api_key = os.environ.get("FLIGHTAWARE_API_KEY") or None
    if not api_key:
        note = (
            "No local-airport-website data (none configured for this airport "
            "via LOCAL_AIRPORT_WEBSITE_FIDS) and FLIGHTAWARE_API_KEY is not "
            "set, so AeroAPI can't be tried either. Set FLIGHTAWARE_API_KEY "
            "in the environment to enable the fallback for this and every "
            "other airport."
        )
        return {
            "airport": iata,
            "source_used": "none",
            "results": [],
            "note": note,
            "error": "FLIGHTAWARE_API_KEY not configured",
        }

    aeroapi_results = _aeroapi_lookup(icao, carrier_set, within_minutes, api_key)
    if aeroapi_results:
        return {
            "airport": iata,
            "source_used": "aeroapi",
            "results": aeroapi_results,
            "note": "Served from FlightAware AeroAPI (paid/metered).",
        }

    return {
        "airport": iata,
        "source_used": "none",
        "results": [],
        "note": "No matching flights from any source in this window.",
    }
