# agentic-management-tooling-mcp

Vendor-agnostic MCP server toolkit for building agentic workflows with real operational safety.

Built under live operational pressure — not a demo. The safety rail primitives in `agentic/`
came from a production executive transport dispatch system where a runaway agent loop or a
mistimed mutation has real consequences. The rest followed.

No private infrastructure required. No proprietary API keys for most tools. Drop in and run.

---

## The problem this solves

LLM agents operating over real systems have three failure modes that are underserved by
most MCP toolkits:

**Mutation without confirmation.** An agent writes, deletes, or triggers something it
should not because no gate was in the path. `mutation_gate` blocks every state-changing
call until `confirmed=True` is explicit. Every intercept is logged.

**Cost runaway.** Long-running agentic sessions accumulate API spend invisibly.
`api_budget_check` and `api_cost_estimate` track cumulative session spend against a
configurable ceiling using a vendor-agnostic pricing registry you maintain locally.

**State loss across tool calls.** Agents lose context between calls. `session_snapshot_save`
and `session_snapshot_restore` give any agent a durable checkpoint it can reload after
a handoff, restart, or context compaction.

Everything else in this repo is operational data and analysis tooling that runs cleanly
on top of those primitives.

---

## Namespaces

```
agentic/          Safety rails, mutation guards, API budget controls, session state,
                  async polling, entity watchlists, go/no-go scoring, health monitoring.
                  Vendor-agnostic -- works with any LLM provider and any MCP client.

tools/            Operational data tools backed by public APIs with no keys required.
                  Aviation weather, FAA TFRs, NWS alerts, ADS-B flight tracking,
                  Amtrak train status.

intelligence/     Structured analysis from export files and derived data.
                  LinkedIn network analysis, mobility pattern intelligence,
                  cellular coverage gap analysis.

gig_mobility/     Gig-platform export normalization and analysis.
                  Uber, Lyft, DoorDash, Grubhub and generic CSV/JSON exports.
                  Geographic primitives via Maidenhead grid.
```

---

## Tools (51 total)

### agentic/ -- 17 tools

The core of this repo. These are the primitives that make the rest safe to run
in an agentic context.

| Tool | Purpose |
|---|---|
| `mutation_gate` | SR1 -- blocks any state-changing call unless `confirmed=True` is explicit. Logs every intercept. |
| `model_routing_check` | SR2 -- recommends model tier (any provider) based on task type and remaining budget. |
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

### tools/ -- 11 tools

Public API tools. No keys required. Useful standalone; designed to feed into
`readiness_score` and the `agentic/` safety layer.

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

### intelligence/ -- 11 tools

Analysis tools that operate on structured export files. No live API calls;
all inference is local.

| Tool | Purpose |
|---|---|
| `linkedin_network_breakdown` | Industry and tenure breakdown from a LinkedIn data export. |
| `linkedin_content_analysis` | Post and comment topic analysis, co-occurrence. |
| `linkedin_engagement_patterns` | Reaction patterns, monthly activity, most-engaged contacts. |
| `mobility_security_brief` | Surfaces high-activity and scarcity zones for advance and security planning. |
| `mobility_marketing_brief` | Identifies underserved high-value corridors for concierge/transport positioning. |
| `mobility_emergency_correlate` | Correlates mobility gaps with timestamped incident data. |
| `mobility_outage_supplement` | Correlates gig data with Downdetector-format outage exports. |
| `coverage_load_opencellid` | Loads OpenCelliD CSV tower data for a bounding box. |
| `coverage_grid_overlay` | Bins towers into Maidenhead grid squares by provider. |
| `coverage_gap_analysis` | Identifies coverage gaps in high-mobility areas. |
| `coverage_provider_comparison` | Compares carrier coverage quality by neighborhood. |

### gig_mobility/ -- 12 tools

Export normalization and demand analysis for gig platforms. Privacy-preserving
by design -- restaurant identity is discarded, location is Maidenhead-binned,
no PII surfaces in output.

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
# Required -- no default. Server refuses to start without this.
export AGENTIC_MCP_STATE_DIR=/path/to/your/state/directory
```

On first run the server confirms the state directory path and asks you to acknowledge
before writing any files.

## Usage

```bash
python server.py                    # stdio (Claude Desktop / any MCP client)
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

---

## Privacy and resolution guardrails

Geographic tools default to **6-character Maidenhead resolution** (~4.6km x 2.3km,
neighborhood level).

Resolution of 8, 10, or 12 characters is blocked for third-party or unverified data.
To use finer resolution you must explicitly attest:

- `own_data=True` -- this is data you collected yourself, and
- `pre_sanitized=True` -- PII has been removed prior to import.

Both flags must be set or the tool returns a structured refusal. Every invocation
is logged to the audit trail regardless of outcome.

---

## Public APIs used

| Namespace | Endpoint |
|---|---|
| Aviation Weather | `https://aviationweather.gov/api/data` |
| FAA TFR | `https://tfr.faa.gov/tfr2/xml_files/fr.xml` |
| NWS Alerts | `https://api.weather.gov` |
| Flight Tracking | `https://api.airplanes.live/v2` |
| Amtrak | `https://api-v3.amtraker.com/v3` |
| Reverse Geocode | `https://nominatim.openstreetmap.org/reverse` (rate-limited, cached) |
| Cell Towers | OpenCelliD CSV (operator supplies file -- `https://opencellid.org`) |
