# agentic-management-tooling-mcp

> **Planned rename:** This repository will be renamed to `agentic-management-tooling-mcp` to better reflect its scope.

A living collection of MCP (Model Context Protocol) server tools, originally developed alongside a private operational dispatch platform and progressively generalized for use by any operator, developer, or integrator building agentic workflows.

These tools are made available because the underlying patterns — API cost guardrails, session state management, mutation safety rails, mobility and network intelligence, gig-work analysis — are useful well beyond the platform they came from. If you are building an executive assistant, a travel concierge, a limo or black-car dispatch system, a virtual assistant, or anything in the emergency management or telecommunications space, there is likely something here you can drop in directly.

No private infrastructure is required. All tools either call open/public APIs or operate entirely on locally-supplied data files.

---

## Status

This is a living document. Tools are added as new capabilities are extracted from the dispatch platform or contributed from adjacent workflows. The tool count and module list below reflect the current state; check commit history for what has changed recently.

---

## Structure

```
agentic/          Safety rails, API budget controls, session state, async ops,
                  entity watchlists, go/no-go scoring, health monitoring.
                  Platform-agnostic — works with any LLM provider.

tools/            Operational data tools backed by public APIs.
                  Aviation weather, FAA TFRs, NWS alerts, flight tracking,
                  Amtrak train status. No API keys required.

intelligence/     Analysis tools for structured exports and derived intelligence.
                  LinkedIn network analysis, mobility intelligence,
                  cell coverage analysis.

gig_mobility/     Gig-platform export normalization and analysis.
                  Uber, Lyft, DoorDash, Grubhub and generic exports.
                  Geographic primitives via Maidenhead grid.
```

---

## Tools (51 total)

### agentic/ (17 tools)

| Tool | Purpose |
|---|---|
| `mutation_gate` | SR1 — blocks any state-changing API call unless `confirmed=True` is explicit. Logs every intercept. |
| `model_routing_check` | SR2 — recommends model tier (any provider) based on task type and remaining budget. |
| `data_resolution_gate` | Hard guardrail: Maidenhead resolution > 6 chars requires attested own/sanitized data or fails. |
| `api_cost_estimate` | Estimates call cost from a user-maintained vendor-agnostic pricing registry. |
| `api_budget_check` | Tracks cumulative session spend against a configurable ceiling. |
| `api_budget_reset` | Resets a session cost accumulator. |
| `pricing_registry_update` | Adds or updates a provider/model entry in the pricing registry. |
| `session_snapshot_save` | Polls caller-supplied endpoints, writes timestamped JSON snapshot. |
| `session_snapshot_restore` | Returns saved snapshot with age. |
| `session_snapshot_age` | Returns snapshot age in seconds. |
| `http_health_check` | GETs any URL, returns health/latency/status. |
| `feed_freshness_audit` | Reports stale vs fresh for a list of data feeds with configurable thresholds. |
| `trigger_poll` | Polls async job status until success, failure, or timeout. |
| `idempotency_check` | Prevents double-fire on non-idempotent operations via TTL-keyed state. |
| `watchlist_add` / `watchlist_remove` / `watchlist_get` / `watchlist_check` | Named entity lists (flights, clients, reservation codes, etc.). |
| `readiness_score` | Multi-factor go/no-go scoring. Generalizes CPS-style checks to any domain. |

### tools/ (11 tools)

| Tool | Purpose |
|---|---|
| `get_metar` | Current METAR observations for any ICAO station. |
| `get_taf` | Terminal Aerodrome Forecasts. |
| `get_pirep` | Pilot reports within a radius. |
| `get_active_tfrs` | Active FAA Temporary Flight Restrictions. |
| `get_nws_alerts` | Active NWS alerts by state, zone, or point. |
| `get_nws_forecast` | 7-day NWS forecast for any lat/lon. |
| `track_flight_by_callsign` | ADS-B telemetry by ICAO callsign. |
| `track_flight_by_registration` | ADS-B telemetry by tail number. |
| `track_flight_by_hex` | ADS-B telemetry by ICAO hex (most reliable for repeated polling). |
| `get_train_status` | Amtrak train status at any station. |
| `get_train_delay` | Delay in minutes for a specific Amtrak train at a given station. |

### intelligence/ (11 tools)

| Tool | Purpose |
|---|---|
| `linkedin_network_breakdown` | Industry + tenure breakdown from a LinkedIn data export. |
| `linkedin_content_analysis` | Post/comment topic analysis, co-occurrence. |
| `linkedin_engagement_patterns` | Reaction patterns, monthly activity, most-engaged contacts. |
| `mobility_security_brief` | Surfaces high-activity and scarcity zones for advance/security planning. |
| `mobility_marketing_brief` | Identifies underserved high-value corridors for limo/concierge positioning. |
| `mobility_emergency_correlate` | Correlates mobility gaps with timestamped incident data. |
| `mobility_outage_supplement` | Correlates gig data with Downdetector-format outage export. |
| `coverage_load_opencellid` | Loads OpenCelliD CSV tower data for a bounding box. |
| `coverage_grid_overlay` | Bins towers into Maidenhead grid squares by provider. |
| `coverage_gap_analysis` | Identifies coverage gaps in high-mobility areas. |
| `coverage_provider_comparison` | Compares carrier coverage quality by neighborhood. |

### gig_mobility/ (12 tools)

| Tool | Purpose |
|---|---|
| `gig_normalize_export` | Normalizes any gig platform CSV/JSON into a common schema. |
| `gig_revenue_analysis` | High/low value breakdown, percentile distribution, platform comparison. |
| `gig_cluster_analysis` | Groups by pickup area, dropoff area, time of day, or day of week. |
| `gig_availability_heatmap` | Driver availability proxy from trip density. |
| `gig_network_anomaly_detect` | Correlates cancellation gaps with outage event timestamps. |
| `gig_cuisine_normalize` | Maps restaurant names to generic cuisine type, discards restaurant identity. |
| `gig_neighborhood_demand_pattern` | Cuisine demand by neighborhood x time-of-day x day-of-week. |
| `gig_restaurant_time_analysis` | Busiest times by cuisine type, decoupled from trip records. |
| `latlon_to_maidenhead` | Converts coordinates to Maidenhead grid square (4/6/8 chars). |
| `maidenhead_to_bbox` | Returns bounding box for a Maidenhead square. |
| `latlon_to_utm` | Converts coordinates to UTM. |
| `reverse_geocode` | Returns neighborhood, postcode, city for a lat/lon via Nominatim. |

---

## Installation

```bash
pip install mcp httpx xmltodict maidenhead
```

Copy the pricing registry template and fill in your provider rates:

```bash
cp pricing_registry.template.json $AGENTIC_MCP_STATE_DIR/pricing_registry.json
# Edit pricing_registry.json with your provider/model rates
```

## Configuration

```bash
# Required — no default. Server refuses to start without this.
export AGENTIC_MCP_STATE_DIR=/path/to/your/state/directory
```

On first run the server will confirm the state directory path and ask you to acknowledge before writing any files.

## Usage

```bash
python server.py                    # stdio (Claude Desktop / MCP clients)
python server.py --transport sse    # SSE for remote clients
```

## Claude Desktop config

```json
{
  "mcpServers": {
    "agentic-tools": {
      "command": "python",
      "args": ["/opt/agentic-management-tooling-mcp/server.py"],
      "env": {
        "AGENTIC_MCP_STATE_DIR": "/home/youruser/.local/share/agentic-mcp"
      }
    }
  }
}
```

## Privacy and resolution guardrails

Geographic tools default to **6-character Maidenhead resolution** (~4.6km x 2.3km, neighborhood level).

Resolution of 8, 10, or 12 characters is blocked for third-party or unverified data. To use finer resolution you must explicitly attest:
- `own_data=True` — this is data you collected yourself, and
- `pre_sanitized=True` — PII has been removed prior to import.

Both flags must be set or the tool returns a structured refusal. This is logged to the audit trail.

## Public APIs used

| Module | Endpoint |
|---|---|
| Aviation Weather | `https://aviationweather.gov/api/data` |
| FAA TFR | `https://tfr.faa.gov/tfr2/xml_files/fr.xml` |
| NWS Alerts | `https://api.weather.gov` |
| Flight Tracking | `https://api.airplanes.live/v2` |
| Amtrak | `https://api-v3.amtraker.com/v3` |
| Reverse Geocode | `https://nominatim.openstreetmap.org/reverse` (rate-limited, cached) |
| Cell Towers | OpenCelliD CSV (operator supplies file — `https://opencellid.org`) |
