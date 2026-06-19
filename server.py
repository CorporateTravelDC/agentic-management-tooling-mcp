"""
server.py — FastMCP entry point for ops-tools-mcp.

Standalone MCP server exposing public-API tools for aviation weather,
FAA TFRs, NWS alerts, real-time flight tracking, and Amtrak train status.

No private infrastructure required. All tools call open/public APIs directly.

Usage:
    python server.py                       # stdio transport (default)
    python server.py --transport sse       # SSE transport for remote clients

Tools registered (11 total):
  Aviation Weather (3): get_metar, get_taf, get_pirep
  FAA TFR (1):          get_active_tfrs
  NWS Alerts (2):       get_nws_alerts, get_nws_forecast
  Flight Tracking (3):  track_flight_by_callsign, track_flight_by_registration,
                        track_flight_by_hex
  Amtrak (2):           get_nec_train_status, get_train_delay
"""

from mcp.server.fastmcp import FastMCP

# ── Tool modules ──────────────────────────────────────────────────────────────
import tools.aviation_weather as _aw
import tools.faa_tfr as _tfr
import tools.nws_alerts as _nws
import tools.flight_track as _ft
import tools.train_status as _ts

# ── FastMCP server ────────────────────────────────────────────────────────────
mcp = FastMCP(
    "ops-tools-mcp",
    instructions=(
        "Standalone MCP server for operational tools using public APIs only. "
        "Provides: aviation weather (METAR/TAF/PIREP) from AviationWeather.gov, "
        "FAA Temporary Flight Restrictions from tfr.faa.gov, NWS weather alerts "
        "and 7-day forecasts, real-time flight tracking via airplanes.live (ADS-B), "
        "and Amtrak NEC train status via amtraker.com. "
        "No API keys or private configuration required."
    ),
)


# ── Aviation Weather ──────────────────────────────────────────────────────────

@mcp.tool()
def get_metar(stations: list[str]) -> dict:
    """
    Fetch current METAR weather observations for one or more ICAO stations.

    Args:
        stations: List of ICAO station IDs (e.g. ['KIAD', 'KDCA', 'KBWI']).

    Returns:
        Dict with 'observations' list. Each observation includes stationId,
        observationTime, temp_c, dewpoint_c, windDir, windSpeed_kt, windGust_kt,
        visibility_sm, altimeter_inhg, skyCondition, flightCategory, rawOb.
    """
    return _aw.get_metar(stations=stations)


@mcp.tool()
def get_taf(stations: list[str]) -> dict:
    """
    Fetch Terminal Aerodrome Forecasts (TAF) for one or more ICAO stations.

    Args:
        stations: List of ICAO station IDs (e.g. ['KIAD', 'KDCA']).

    Returns:
        Dict with 'forecasts' list. Each entry includes stationId, issueTime,
        validTimeFrom, validTimeTo, rawTAF, forecast (list of period dicts).
    """
    return _aw.get_taf(stations=stations)


@mcp.tool()
def get_pirep(lat: float, lon: float, radius_nm: int = 100) -> dict:
    """
    Fetch Pilot Reports (PIREPs) within a radius of a lat/lon point.

    Args:
        lat:       Latitude in decimal degrees (e.g. 38.9 for Washington DC).
        lon:       Longitude in decimal degrees (e.g. -77.0 for Washington DC).
        radius_nm: Search radius in nautical miles (default 100).

    Returns:
        Dict with 'pireps' list. Each entry includes receipt_time, aircraft_type,
        altitude_ft, flight_conditions, turbulence, icing, temp_c, raw_pirep.
    """
    return _aw.get_pirep(lat=lat, lon=lon, radius_nm=radius_nm)


# ── FAA TFR ───────────────────────────────────────────────────────────────────

@mcp.tool()
def get_active_tfrs() -> dict:
    """
    Fetch and parse the FAA TFR XML feed, returning all active Temporary Flight Restrictions.

    Queries https://tfr.faa.gov/tfr2/xml_files/fr.xml — no auth required.
    Handles FAA's frequently malformed XML gracefully; partial results returned
    with parse_warnings when the XML cannot be fully parsed.

    Returns:
        Dict with 'tfrs' list. Each TFR includes: notam_id, type, location,
        floor_ft, ceiling_ft, effective, expires, description.
        Also includes 'parse_warnings' (list) and 'source_url'.
    """
    return _tfr.get_active_tfrs()


# ── NWS Alerts ────────────────────────────────────────────────────────────────

@mcp.tool()
def get_nws_alerts(
    state: str = None,
    zone: str = None,
    lat: float = None,
    lon: float = None,
) -> dict:
    """
    Get active National Weather Service alerts filtered by location.

    Provide at most one filter. If none provided, returns all active nationwide alerts.

    Args:
        state: Two-letter US state code (e.g. 'VA', 'MD', 'DC').
        zone:  NWS zone ID (e.g. 'VAZ013').
        lat:   Latitude for point-based filter (requires lon).
        lon:   Longitude for point-based filter (requires lat).

    Returns:
        Dict with 'alerts' list. Each alert includes: id, event, headline,
        description, severity, certainty, urgency, effective, expires, areas.
    """
    return _nws.get_nws_alerts(state=state, zone=zone, lat=lat, lon=lon)


@mcp.tool()
def get_nws_forecast(lat: float, lon: float) -> dict:
    """
    Get the 7-day NWS forecast for a lat/lon point (two-step API call).

    Args:
        lat: Latitude in decimal degrees (e.g. 38.9072 for Washington DC).
        lon: Longitude in decimal degrees (e.g. -77.0369 for Washington DC).

    Returns:
        Dict with 'periods' list (up to 14 day/night periods). Each period includes:
        name, startTime, endTime, isDaytime, temperature, temperatureUnit,
        windSpeed, windDirection, shortForecast, detailedForecast.
    """
    return _nws.get_nws_forecast(lat=lat, lon=lon)


# ── Flight Tracking ───────────────────────────────────────────────────────────

@mcp.tool()
def track_flight_by_callsign(callsign: str) -> dict:
    """
    Look up a flight by ICAO callsign and return its current ADS-B telemetry.

    Use ICAO 3-letter prefix (e.g. KLM651, UAL925, AAL100). Callsign-to-aircraft
    mappings can lag by up to 24 hours — verify with track_flight_by_registration
    when airframe identity matters.

    Args:
        callsign: ICAO callsign string (e.g. 'KLM651', 'UAL925').

    Returns:
        Dict with: hex, registration, type, lat, lon, alt_baro, gs, track,
        baro_rate, phase (CLIMB/CRUISE/DESCENT/GROUND), squawk, nic, rc, rssi, seen.
    """
    return _ft.track_flight_by_callsign(callsign=callsign)


@mcp.tool()
def track_flight_by_registration(registration: str) -> dict:
    """
    Look up an aircraft by tail/registration number.

    Registration is airframe-bound and does not change between flights.
    Use this to confirm ICAO hex before any watchlist entry.

    Args:
        registration: Tail number (e.g. 'N12345', 'PH-BKB', 'G-EUYA').

    Returns:
        Dict with aircraft telemetry including confirmed hex.
    """
    return _ft.track_flight_by_registration(registration=registration)


@mcp.tool()
def track_flight_by_hex(hex: str) -> dict:
    """
    Look up an aircraft by ICAO 24-bit hex address (fastest, most reliable).

    Hex is airframe-bound. Use for all repeated position polls once hex is confirmed.

    Args:
        hex: 6-character ICAO hex string (e.g. '484150', 'a1b2c3').

    Returns:
        Dict with full ADS-B telemetry.
    """
    return _ft.track_flight_by_hex(hex=hex)


# ── Amtrak NEC Train Status ───────────────────────────────────────────────────

@mcp.tool()
def get_nec_train_status(train_number: str = None) -> dict:
    """
    Get current Amtrak NEC train status at Washington Union Station (WAS).

    Args:
        train_number: Amtrak train number (e.g. '2125', '95'). Omit for all WAS trains.

    Returns:
        Dict with 'trains' list. Each train includes: train_number, route_name,
        origin, destination, train_state, was_stop (with scheduled/estimated
        arrival/departure times and delay_minutes).
    """
    return _ts.get_nec_train_status(train_number=train_number)


@mcp.tool()
def get_train_delay(train_number: str) -> dict:
    """
    Get delay in minutes for a specific Amtrak train at Washington Union Station.

    Args:
        train_number: Amtrak train number (e.g. '2125', '95').

    Returns:
        Dict with: train_number, delay_minutes (int), on_time (bool),
        route_name, train_state, was_stop.
    """
    return _ts.get_train_delay(train_number=train_number)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    import sys
    transport = "stdio"
    if "--transport" in sys.argv:
        idx = sys.argv.index("--transport")
        if idx + 1 < len(sys.argv):
            transport = sys.argv[idx + 1]
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
