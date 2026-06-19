# general-agentic-management-mcp

Standalone MCP server exposing operational tools backed entirely by open/public APIs. No private infrastructure, no API keys, no proprietary endpoints required.

## Tools (11 total)

### Aviation Weather — `aviationweather.gov`
- `get_metar(stations)` — current METAR observations for ICAO station IDs
- `get_taf(stations)` — Terminal Aerodrome Forecasts
- `get_pirep(lat, lon, radius_nm)` — Pilot Reports within a radius

### FAA TFRs — `tfr.faa.gov`
- `get_active_tfrs()` — all active Temporary Flight Restrictions (XML parsed gracefully)

### NWS Alerts — `api.weather.gov`
- `get_nws_alerts(state, zone, lat, lon)` — active weather alerts by state/zone/point
- `get_nws_forecast(lat, lon)` — 7-day forecast for a lat/lon point

### Flight Tracking — `api.airplanes.live`
- `track_flight_by_callsign(callsign)` — ADS-B telemetry by ICAO callsign
- `track_flight_by_registration(registration)` — telemetry by tail number
- `track_flight_by_hex(hex)` — telemetry by ICAO 24-bit hex (most reliable)

### Amtrak — `api-v3.amtraker.com`
- `get_nec_train_status(train_number)` — NEC train status at Washington Union Station
- `get_train_delay(train_number)` — delay in minutes for a specific train at WAS

## Installation

```bash
pip install mcp httpx xmltodict
python3 -c "import server; print('OK')"
```

## Usage

```bash
# stdio transport (Claude Desktop / MCP clients)
python server.py

# SSE transport (remote clients)
python server.py --transport sse
```

## Claude Desktop config

```json
{
  "mcpServers": {
    "ops-tools": {
      "command": "python",
      "args": ["/opt/general-agentic-management-mcp/server.py"]
    }
  }
}
```

## Public APIs used

| Tool group      | Endpoint                                         |
|-----------------|--------------------------------------------------|
| Aviation Weather| `https://aviationweather.gov/api/data`           |
| FAA TFR         | `https://tfr.faa.gov/tfr2/xml_files/fr.xml`      |
| NWS Alerts      | `https://api.weather.gov`                         |
| Flight Tracking | `https://api.airplanes.live/v2`                  |
| Amtrak          | `https://api-v3.amtraker.com/v3`                 |
