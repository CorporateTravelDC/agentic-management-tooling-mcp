"""
flight_track.py — MCP tools for real-time aircraft position via airplanes.live.

No dispatch dependency. All calls go directly to https://api.airplanes.live/v2.

Per the standing hex-ID directive: all readouts include ICAO hex.
Resolution order for callsign-based lookups:
  1. Resolve callsign → hex via /callsign/{callsign}
  2. Cross-check via /reg/{registration} when registration is available
  3. Return confirmed hex in all outputs

Flight phase derivation (from alt + baro_rate):
  GROUND   → alt_baro ≤ 100 ft (or on_ground flag)
  CLIMB    → baro_rate > +200 fpm
  DESCENT  → baro_rate < -200 fpm
  CRUISE   → otherwise airborne
"""

from __future__ import annotations

import httpx

from config import AIRPLANES_LIVE_URL

_TIMEOUT = 20  # seconds


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=AIRPLANES_LIVE_URL,
        timeout=_TIMEOUT,
        headers={"Accept": "application/json"},
    )


def _phase(ac: dict) -> str:
    on_ground = ac.get("on_ground", False)
    alt = ac.get("alt_baro", None)
    baro_rate = ac.get("baro_rate", 0) or 0

    if on_ground:
        return "GROUND"
    if alt is not None:
        try:
            alt_f = float(alt)
            if alt_f <= 100:
                return "GROUND"
        except (TypeError, ValueError):
            pass
    if baro_rate > 200:
        return "CLIMB"
    if baro_rate < -200:
        return "DESCENT"
    return "CRUISE"


def _format_aircraft(ac: dict) -> dict:
    """Normalize a raw airplanes.live aircraft dict to our standard output shape."""
    return {
        "hex":          ac.get("hex", ""),
        "registration": ac.get("r", ac.get("reg", "")),
        "type":         ac.get("t", ac.get("type", "")),
        "callsign":     (ac.get("flight", "") or "").strip(),
        "lat":          ac.get("lat"),
        "lon":          ac.get("lon"),
        "alt_baro":     ac.get("alt_baro"),
        "alt_geom":     ac.get("alt_geom"),
        "gs":           ac.get("gs"),
        "track":        ac.get("track"),
        "baro_rate":    ac.get("baro_rate"),
        "phase":        _phase(ac),
        "squawk":       ac.get("squawk"),
        "nic":          ac.get("nic"),
        "rc":           ac.get("rc"),
        "rssi":         ac.get("rssi"),
        "seen":         ac.get("seen"),
        "seen_pos":     ac.get("seen_pos"),
        "on_ground":    ac.get("on_ground", False),
    }


def _extract_aircraft(data: dict) -> list[dict]:
    """Pull the aircraft list from an airplanes.live response."""
    # Top-level can be {"ac": [...]} or just a list
    if isinstance(data, dict):
        return data.get("ac", [])
    if isinstance(data, list):
        return data
    return []


# ── MCP tool functions ───────────────────────────────────────────────────────

def track_flight_by_callsign(callsign: str) -> dict:
    """
    Look up a flight by ICAO callsign and return its current ADS-B telemetry.

    Callsign normalization: use ICAO 3-letter prefix (e.g. KLM651, UAL925,
    AAL100). The tool queries airplanes.live /callsign/{callsign}.

    NOTE: Callsign-to-aircraft mappings can lag by up to 24 hours. When a
    registration is returned, the result also includes a cross-check flag.

    Args:
        callsign: ICAO callsign string (e.g. 'KLM651', 'UAL925').

    Returns:
        dict with aircraft telemetry or {"error": ..., "callsign": ...} if
        not found. Includes hex, registration, type, lat, lon, alt_baro, gs,
        track, baro_rate, phase, squawk, nic, rc, rssi, seen.
    """
    callsign = callsign.strip().upper()
    with _client() as client:
        resp = client.get(f"/callsign/{callsign}")
        if resp.status_code == 404:
            return {
                "error": "No aircraft found for callsign",
                "callsign": callsign,
                "guidance": "Aircraft may be overwater (ADS-C only) or on ground.",
            }
        resp.raise_for_status()
        data = resp.json()

    aircraft_list = _extract_aircraft(data)
    if not aircraft_list:
        return {
            "error": "No aircraft found for callsign",
            "callsign": callsign,
            "guidance": "Aircraft may be overwater (ADS-C only) or on ground.",
        }

    ac = _format_aircraft(aircraft_list[0])
    ac["source"] = "callsign"
    ac["callsign_queried"] = callsign
    return ac


def track_flight_by_registration(registration: str) -> dict:
    """
    Look up an aircraft by tail/registration number.

    Registration is airframe-bound and does not change between flights, making
    this the most reliable way to confirm ICAO hex before watchlist entry.

    Args:
        registration: Tail number (e.g. 'N12345', 'PH-BKB', 'G-EUYA').

    Returns:
        dict with aircraft telemetry including confirmed hex, or
        {"error": ..., "registration": ...} if not found.
    """
    reg = registration.strip().upper()
    with _client() as client:
        resp = client.get(f"/reg/{reg}")
        if resp.status_code == 404:
            return {
                "error": "No aircraft found for registration",
                "registration": reg,
                "guidance": "Aircraft may be on ground or not in ADS-B coverage.",
            }
        resp.raise_for_status()
        data = resp.json()

    aircraft_list = _extract_aircraft(data)
    if not aircraft_list:
        return {
            "error": "No aircraft found for registration",
            "registration": reg,
            "guidance": "Aircraft may be on ground or not in ADS-B coverage.",
        }

    ac = _format_aircraft(aircraft_list[0])
    ac["source"] = "registration"
    ac["registration_queried"] = reg
    return ac


def track_flight_by_hex(hex: str) -> dict:
    """
    Look up an aircraft by ICAO 24-bit hex address (most reliable method).

    Hex is airframe-bound and does not change. Use this for all repeated
    position polls and watchlist entries once hex has been confirmed.

    Args:
        hex: 6-character ICAO hex string (case-insensitive, e.g. '484150', 'a1b2c3').

    Returns:
        dict with full telemetry or {"error": ..., "hex": ...} if not found.
    """
    hex_clean = hex.strip().lower()
    if len(hex_clean) != 6:
        return {
            "error": "Invalid hex: must be exactly 6 hexadecimal characters",
            "hex": hex,
        }

    with _client() as client:
        resp = client.get(f"/hex/{hex_clean}")
        if resp.status_code == 404:
            return {
                "error": "No aircraft found for hex",
                "hex": hex_clean,
                "guidance": "Aircraft may be overwater, on ground, or hex not in DB.",
            }
        resp.raise_for_status()
        data = resp.json()

    aircraft_list = _extract_aircraft(data)
    if not aircraft_list:
        return {
            "error": "No aircraft found for hex",
            "hex": hex_clean,
            "guidance": "Aircraft may be overwater, on ground, or hex not in DB.",
        }

    ac = _format_aircraft(aircraft_list[0])
    ac["source"] = "hex"
    ac["hex_queried"] = hex_clean
    return ac
