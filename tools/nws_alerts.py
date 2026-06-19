"""
nws_alerts.py — MCP tools for National Weather Service (NWS) public API.

All endpoints are open/public — no API key required.
NWS API docs: https://www.weather.gov/documentation/services-web-api

Two tools:
  get_nws_alerts  — active weather alerts filtered by state, zone, or lat/lon
  get_nws_forecast — 7-day forecast for a lat/lon point
"""

from __future__ import annotations

from typing import Optional

import httpx

from config import NWS_URL

_TIMEOUT = 20
_USER_AGENT = "ops-tools-mcp/0.1 (public; contact: ops-tools@example.com)"


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=NWS_URL,
        timeout=_TIMEOUT,
        headers={
            "Accept": "application/geo+json",
            "User-Agent": _USER_AGENT,
        },
    )


def _format_alert(feature: dict) -> dict:
    """Normalize a GeoJSON alert feature to a flat dict."""
    props = feature.get("properties", {})
    return {
        "id":          props.get("id", ""),
        "event":       props.get("event", ""),
        "headline":    props.get("headline", ""),
        "description": (props.get("description") or "")[:500],
        "severity":    props.get("severity", ""),
        "certainty":   props.get("certainty", ""),
        "urgency":     props.get("urgency", ""),
        "effective":   props.get("effective", ""),
        "expires":     props.get("expires", ""),
        "areas":       props.get("areaDesc", "").split("; ") if props.get("areaDesc") else [],
        "status":      props.get("status", ""),
        "messageType": props.get("messageType", ""),
        "category":    props.get("category", ""),
        "sender":      props.get("senderName", ""),
    }


def get_nws_alerts(
    state: Optional[str] = None,
    zone: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> dict:
    """
    Get active National Weather Service alerts, optionally filtered by location.

    At most one filter should be provided. If none are provided, returns all
    currently active alerts nationwide (may be large).

    Args:
        state: Two-letter US state code (e.g. 'VA', 'MD', 'DC').
               Uses ?area=STATE parameter.
        zone:  NWS zone ID (e.g. 'VAZ013'). Uses ?zone=ZONE parameter.
        lat:   Latitude for point-based filtering (requires lon).
        lon:   Longitude for point-based filtering (requires lat).

    Returns:
        dict with keys:
          - alerts (list[dict]): each alert has id, event, headline, description,
            severity, certainty, urgency, effective, expires, areas
          - count (int)
          - filter_used (str): description of the filter applied
    """
    params: dict = {"status": "actual"}
    filter_desc = "none (all active alerts)"

    if state:
        params["area"] = state.strip().upper()
        filter_desc = f"state={params['area']}"
    elif zone:
        params["zone"] = zone.strip().upper()
        filter_desc = f"zone={params['zone']}"
    elif lat is not None and lon is not None:
        # NWS point-based alert query uses the /alerts/active/area/{lat},{lon} pattern
        # but the standard endpoint accepts ?point=lat,lon
        params["point"] = f"{lat},{lon}"
        filter_desc = f"point={lat},{lon}"

    with _client() as client:
        resp = client.get("/alerts/active", params=params)
        resp.raise_for_status()
        data = resp.json()

    features = data.get("features", [])
    alerts = [_format_alert(f) for f in features]

    return {
        "alerts": alerts,
        "count": len(alerts),
        "filter_used": filter_desc,
    }


def get_nws_forecast(lat: float, lon: float) -> dict:
    """
    Get the 7-day NWS forecast for a latitude/longitude point.

    Makes two API calls:
      1. GET /points/{lat},{lon}  → retrieves forecast grid office and URL
      2. GET {forecastUrl}        → retrieves the 7-day period forecast

    Args:
        lat: Latitude in decimal degrees (e.g. 38.9072 for Washington DC).
        lon: Longitude in decimal degrees (e.g. -77.0369 for Washington DC).

    Returns:
        dict with keys:
          - lat, lon
          - office (str): NWS forecast office identifier
          - grid_id (str): grid point identifier
          - forecast_url (str): URL used for the forecast
          - periods (list[dict]): up to 14 forecast periods (day/night pairs).
            Each period has: name, startTime, endTime, isDaytime, temperature,
            temperatureUnit, windSpeed, windDirection, shortForecast, detailedForecast
          - count (int): number of periods returned
          - updated (str): forecast generation time
    """
    with _client() as client:
        # Step 1: resolve lat/lon to grid point
        point_resp = client.get(f"/points/{lat},{lon}")
        point_resp.raise_for_status()
        point_data = point_resp.json()

    props = point_data.get("properties", {})
    forecast_url = props.get("forecast", "")
    office = props.get("cwa", props.get("gridId", ""))
    grid_id = f"{props.get('gridX', '')},{props.get('gridY', '')}"

    if not forecast_url:
        return {
            "lat": lat,
            "lon": lon,
            "error": "Could not resolve forecast URL from NWS points API",
            "periods": [],
            "count": 0,
        }

    # Step 2: fetch the actual forecast
    with httpx.Client(
        timeout=_TIMEOUT,
        headers={"Accept": "application/geo+json", "User-Agent": _USER_AGENT},
    ) as client:
        fc_resp = client.get(forecast_url)
        fc_resp.raise_for_status()
        fc_data = fc_resp.json()

    fc_props = fc_data.get("properties", {})
    raw_periods = fc_props.get("periods", [])

    periods = []
    for p in raw_periods:
        periods.append({
            "name":             p.get("name", ""),
            "startTime":        p.get("startTime", ""),
            "endTime":          p.get("endTime", ""),
            "isDaytime":        p.get("isDaytime", True),
            "temperature":      p.get("temperature"),
            "temperatureUnit":  p.get("temperatureUnit", "F"),
            "windSpeed":        p.get("windSpeed", ""),
            "windDirection":    p.get("windDirection", ""),
            "shortForecast":    p.get("shortForecast", ""),
            "detailedForecast": p.get("detailedForecast", ""),
        })

    return {
        "lat": lat,
        "lon": lon,
        "office": office,
        "grid_id": grid_id,
        "forecast_url": forecast_url,
        "periods": periods,
        "count": len(periods),
        "updated": fc_props.get("updated", ""),
    }
