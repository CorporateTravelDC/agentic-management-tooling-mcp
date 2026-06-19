"""
aviation_weather.py — MCP tools for AviationWeather.gov public API.

All endpoints are open/public — no API key required.

Endpoints used:
  GET /metar?ids=KIAD,KDCA&format=json
  GET /taf?ids=KIAD&format=json
  GET /pirep?format=json&distance=100&lat=38.9&lon=-77.0
"""

from __future__ import annotations

import httpx

from config import AVIATIONWEATHER_URL

_TIMEOUT = 20


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=AVIATIONWEATHER_URL,
        timeout=_TIMEOUT,
        headers={"Accept": "application/json"},
    )


def get_metar(stations: list[str]) -> dict:
    """
    Fetch current METAR observations for one or more ICAO station IDs.

    Args:
        stations: List of ICAO station identifiers (e.g. ['KIAD', 'KDCA', 'KBWI']).

    Returns:
        dict with keys:
          - stations_requested (list[str])
          - observations (list[dict]): each entry contains stationId, observationTime,
            temp, dewpoint, windDir, windSpeed, windGust, visibility, altimeter,
            skyCondition (list), flightCategory, rawOb
          - count (int)
    """
    if not stations:
        return {"error": "No stations provided", "observations": [], "count": 0}

    ids = ",".join(s.strip().upper() for s in stations)
    with _client() as client:
        resp = client.get("/metar", params={"ids": ids, "format": "json"})
        resp.raise_for_status()
        raw = resp.json()

    # AviationWeather returns a list of observation objects
    obs_list = raw if isinstance(raw, list) else []

    observations = []
    for obs in obs_list:
        observations.append({
            "stationId":       obs.get("icaoId") or obs.get("stationId", ""),
            "observationTime": obs.get("reportTime") or obs.get("obsTime", ""),
            "temp_c":          obs.get("temp"),
            "dewpoint_c":      obs.get("dewp"),
            "windDir":         obs.get("wdir"),
            "windSpeed_kt":    obs.get("wspd"),
            "windGust_kt":     obs.get("wgst"),
            "visibility_sm":   obs.get("visib"),
            "altimeter_inhg":  obs.get("altim"),
            "skyCondition":    obs.get("clouds", []),
            "flightCategory":  obs.get("fltcat") or obs.get("flightCategory", ""),
            "rawOb":           obs.get("rawOb") or obs.get("rawMETAR", ""),
        })

    return {
        "stations_requested": stations,
        "observations": observations,
        "count": len(observations),
    }


def get_taf(stations: list[str]) -> dict:
    """
    Fetch Terminal Aerodrome Forecasts (TAF) for one or more ICAO station IDs.

    Args:
        stations: List of ICAO station identifiers (e.g. ['KIAD', 'KDCA']).

    Returns:
        dict with keys:
          - stations_requested (list[str])
          - forecasts (list[dict]): each entry contains stationId, issueTime,
            validTimeFrom, validTimeTo, rawTAF, forecast (list of period dicts)
          - count (int)
    """
    if not stations:
        return {"error": "No stations provided", "forecasts": [], "count": 0}

    ids = ",".join(s.strip().upper() for s in stations)
    with _client() as client:
        resp = client.get("/taf", params={"ids": ids, "format": "json"})
        resp.raise_for_status()
        raw = resp.json()

    taf_list = raw if isinstance(raw, list) else []

    forecasts = []
    for taf in taf_list:
        forecasts.append({
            "stationId":     taf.get("icaoId") or taf.get("stationId", ""),
            "issueTime":     taf.get("issueTime", ""),
            "validTimeFrom": taf.get("validTimeFrom", ""),
            "validTimeTo":   taf.get("validTimeTo", ""),
            "rawTAF":        taf.get("rawTAF", ""),
            "forecast":      taf.get("fcsts", taf.get("forecast", [])),
        })

    return {
        "stations_requested": stations,
        "forecasts": forecasts,
        "count": len(forecasts),
    }


def get_pirep(lat: float, lon: float, radius_nm: int = 100) -> dict:
    """
    Fetch PIREPs (Pilot Reports) within a given radius of a lat/lon point.

    Args:
        lat:       Latitude in decimal degrees (e.g. 38.9 for Washington DC area).
        lon:       Longitude in decimal degrees (e.g. -77.0 for Washington DC area).
        radius_nm: Search radius in nautical miles (default 100).

    Returns:
        dict with keys:
          - lat, lon, radius_nm
          - pireps (list[dict]): each entry contains receipt_time, aircraft_type,
            altitude_ft, flight_conditions, sky_conditions, turbulence, icing,
            temp_c, wind_dir, wind_speed_kt, raw_pirep
          - count (int)
    """
    params = {
        "format": "json",
        "distance": radius_nm,
        "lat": lat,
        "lon": lon,
    }
    with _client() as client:
        resp = client.get("/pirep", params=params)
        resp.raise_for_status()
        raw = resp.json()

    pirep_list = raw if isinstance(raw, list) else []

    pireps = []
    for p in pirep_list:
        pireps.append({
            "receipt_time":     p.get("receiptTime", ""),
            "aircraft_type":    p.get("acType", ""),
            "altitude_ft":      p.get("altitude"),
            "flight_conditions": p.get("fltCat", ""),
            "sky_conditions":   p.get("sky", ""),
            "turbulence":       p.get("turb", ""),
            "icing":            p.get("icing", ""),
            "temp_c":           p.get("temp"),
            "wind_dir":         p.get("wdir"),
            "wind_speed_kt":    p.get("wspd"),
            "raw_pirep":        p.get("rawOb", ""),
        })

    return {
        "lat": lat,
        "lon": lon,
        "radius_nm": radius_nm,
        "pireps": pireps,
        "count": len(pireps),
    }
