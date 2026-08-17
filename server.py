"""
server.py -- FastMCP entry point for agentic-management-tooling-mcp.

51 tools across four capability namespaces, +3 more when intelligence/
sub-modules are implemented (see below):
  agentic/       Safety rails, API budget, session state, async ops,
                 entity watchlists, go/no-go scoring, health monitoring.
  tools/         Public-API operational data tools.
  intelligence/  LinkedIn (linkedin_analysis.py, live as of 2026-07-27),
                 mobility (mobility_intelligence.py, not yet implemented),
                 and coverage (coverage_intelligence.py, not yet implemented)
                 analysis. Each sub-module is gated independently -- one
                 being unimplemented doesn't block the others.
  gig_mobility/  Gig platform normalization and demand intelligence.

Transport:
  python server.py                 # stdio (Claude Desktop / MCP clients)
  python server.py --transport sse # SSE for remote clients

Requires:
  AGENTIC_MCP_STATE_DIR env var set before startup.
"""

import sys
from mcp.server.fastmcp import FastMCP

# Validate state dir at startup -- exits with clear message if unset
from config import get_state_dir
_state_dir = get_state_dir(confirm=True)

# ---------------------------------------------------------------------------
# Agentic management tools
# ---------------------------------------------------------------------------
from agentic.guardrails      import mutation_gate, model_routing_check, data_resolution_gate
from agentic.api_budget      import api_cost_estimate, api_budget_check, api_budget_reset, pricing_registry_update
from agentic.session_state   import session_snapshot_save, session_snapshot_restore, session_snapshot_age
from agentic.health_monitor  import http_health_check, feed_freshness_audit
from agentic.async_ops       import trigger_poll, idempotency_check
from agentic.entity_watchlist import watchlist_add, watchlist_remove, watchlist_get, watchlist_check
from agentic.go_nogo         import readiness_score

# ---------------------------------------------------------------------------
# Operational data tools (public APIs)
# ---------------------------------------------------------------------------
from tools.aviation_weather import get_metar, get_taf, get_pirep
from tools.faa_tfr          import get_active_tfrs
from tools.nws_alerts       import get_nws_alerts, get_nws_forecast
from tools.flight_track     import track_flight_by_callsign, track_flight_by_registration, track_flight_by_hex
from tools.train_status     import get_train_status, get_train_delay
from tools.airport_arrivals import get_airport_arrivals

# ---------------------------------------------------------------------------
# Intelligence tools (optional -- each sub-domain loaded independently, so
# one unimplemented module doesn't block the others. Split 2026-07-27 --
# previously all three lived behind one try/except, so linkedin_analysis
# alone existing wouldn't have been enough to light up its 3 tools; mobility
# and coverage remain unimplemented (no source module yet, deliberately
# deferred -- see README's intelligence/ section) and stay unavailable
# without affecting linkedin.
# ---------------------------------------------------------------------------
import logging as _log

_linkedin_available = False
try:
    from intelligence.linkedin_analysis import linkedin_network_breakdown, linkedin_content_analysis, linkedin_engagement_patterns
    _linkedin_available = True
except ImportError:
    _log.getLogger("agentic_mcp").warning("intelligence/linkedin_analysis.py not implemented -- 3 LinkedIn tools unavailable")

_mobility_intel_available = False
try:
    from intelligence.mobility_intelligence import mobility_security_brief, mobility_marketing_brief, mobility_emergency_correlate, mobility_outage_supplement
    _mobility_intel_available = True
except ImportError:
    _log.getLogger("agentic_mcp").warning("intelligence/mobility_intelligence.py not implemented -- 4 mobility-intelligence tools unavailable")

_coverage_intel_available = False
try:
    from intelligence.coverage_intelligence import coverage_load_opencellid, coverage_grid_overlay, coverage_gap_analysis, coverage_provider_comparison
    _coverage_intel_available = True
except ImportError:
    _log.getLogger("agentic_mcp").warning("intelligence/coverage_intelligence.py not implemented -- 4 coverage tools unavailable")

# ---------------------------------------------------------------------------
# Gig mobility tools
# ---------------------------------------------------------------------------
from gig_mobility.geo_utils    import latlon_to_maidenhead, maidenhead_to_bbox, latlon_to_utm, reverse_geocode
from gig_mobility.gig_analysis import (
    gig_normalize_export, gig_revenue_analysis, gig_cluster_analysis,
    gig_availability_heatmap, gig_network_anomaly_detect,
    gig_cuisine_normalize, gig_neighborhood_demand_pattern, gig_restaurant_time_analysis,
)

# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "agentic-management-tooling-mcp",
    instructions=(
        "Vendor-agnostic agentic management tools. "
        "Covers: safety rails (SR1/SR2), API budget tracking, session state "
        "persistence, async operation lifecycle, entity watchlists, go/no-go "
        "scoring, HTTP health monitoring, flight and train tracking, weather, "
        "FAA TFRs, LinkedIn export analysis, gig platform export normalization, "
        "mobility and coverage intelligence. "
        "Geographic tools default to 6-char Maidenhead resolution. "
        "Finer resolution requires attestation via data_resolution_gate."
    ),
)

# ── Agentic: Safety rails ───────────────────────────────────────────────────

@mcp.tool()
def mutation_gate_tool(method: str, url: str, payload: dict = None, confirmed: bool = False, idempotent: bool = False) -> dict:
    """SR1: Block any state-changing API call until confirmed=True is explicit. Logs every intercept."""
    return mutation_gate(method, url, payload, confirmed, idempotent)

@mcp.tool()
def model_routing_check_tool(estimated_input_tokens: int, estimated_output_tokens: int, task_type: str, budget_remaining: float, force_tier: str = None) -> dict:
    """SR2: Recommend model tier (any provider) based on task type and remaining budget."""
    return model_routing_check(estimated_input_tokens, estimated_output_tokens, task_type, budget_remaining, force_tier)

@mcp.tool()
def data_resolution_gate_tool(requested_precision: int, data_source: str, own_data: bool = False, pre_sanitized: bool = False) -> dict:
    """Hard guardrail: Maidenhead precision >= 8 requires own_data + pre_sanitized attestation or fails."""
    return data_resolution_gate(requested_precision, data_source, own_data, pre_sanitized)

# ── Agentic: API budget ──────────────────────────────────────────────────────

@mcp.tool()
def api_cost_estimate_tool(provider: str, model_id: str, input_tokens: int, output_tokens: int) -> dict:
    """Estimate LLM call cost from the user-maintained vendor-agnostic pricing registry."""
    return api_cost_estimate(provider, model_id, input_tokens, output_tokens)

@mcp.tool()
def api_budget_check_tool(session_id: str, cost_to_add: float, ceiling: float) -> dict:
    """Track cumulative session spend against a configurable ceiling."""
    return api_budget_check(session_id, cost_to_add, ceiling)

@mcp.tool()
def api_budget_reset_tool(session_id: str) -> dict:
    """Reset a session cost accumulator."""
    return api_budget_reset(session_id)

@mcp.tool()
def pricing_registry_update_tool(provider: str, model_id: str, input_per_mtok: float, output_per_mtok: float, notes: str = "") -> dict:
    """Add or update a provider/model pricing entry at runtime."""
    return pricing_registry_update(provider, model_id, input_per_mtok, output_per_mtok, notes)

# ── Agentic: Session state ──────────────────────────────────────────────────

@mcp.tool()
def session_snapshot_save_tool(snapshot_key: str, endpoints: list[dict], timeout_seconds: float = 10.0) -> dict:
    """Poll caller-supplied HTTP endpoints and save a named JSON snapshot for context restore."""
    return session_snapshot_save(snapshot_key, endpoints, timeout_seconds)

@mcp.tool()
def session_snapshot_restore_tool(snapshot_key: str) -> dict:
    """Restore a named snapshot with age metadata. Use after a context reset."""
    return session_snapshot_restore(snapshot_key)

@mcp.tool()
def session_snapshot_age_tool(snapshot_key: str) -> dict:
    """Return how old a snapshot is in seconds without loading the full data."""
    return session_snapshot_age(snapshot_key)

# ── Agentic: Health monitoring ──────────────────────────────────────────────

@mcp.tool()
def http_health_check_tool(url: str, expected_status: int = 200, timeout_seconds: float = 10.0, bearer_token: str = None) -> dict:
    """HTTP GET health check against any URL. Returns health, status code, and latency."""
    return http_health_check(url, expected_status, timeout_seconds, bearer_token)

@mcp.tool()
def feed_freshness_audit_tool(feeds: list[dict]) -> dict:
    """Audit a list of data feeds for staleness. Each feed: {name, url, timestamp_field_path, threshold_seconds}."""
    return feed_freshness_audit(feeds)

# ── Agentic: Async ops ───────────────────────────────────────────────────────

@mcp.tool()
def trigger_poll_tool(status_url: str, trigger_id: str, outcome_field: str, success_values: list[str], failure_values: list[str], timeout_seconds: float = 60.0, poll_interval_seconds: float = 5.0, bearer_token: str = None) -> dict:
    """Poll a status URL until a field reaches a terminal value. Generalizes 202-style async APIs."""
    return trigger_poll(status_url, trigger_id, outcome_field, success_values, failure_values, timeout_seconds, poll_interval_seconds, bearer_token)

@mcp.tool()
def idempotency_check_tool(operation_key: str, ttl_seconds: int = 300) -> dict:
    """Prevent double-fire on non-idempotent operations. Returns allowed=False within TTL."""
    return idempotency_check(operation_key, ttl_seconds)

# ── Agentic: Entity watchlist ────────────────────────────────────────────────

@mcp.tool()
def watchlist_add_tool(list_name: str, entry: str) -> dict:
    """Add an entry to a named watchlist (flights, clients, tail numbers, etc.)."""
    return watchlist_add(list_name, entry)

@mcp.tool()
def watchlist_remove_tool(list_name: str, entry: str) -> dict:
    """Remove an entry from a named watchlist."""
    return watchlist_remove(list_name, entry)

@mcp.tool()
def watchlist_get_tool(list_name: str) -> dict:
    """Return all entries on a named watchlist."""
    return watchlist_get(list_name)

@mcp.tool()
def watchlist_check_tool(list_name: str, entry: str) -> dict:
    """Check whether a specific entry is on a named watchlist."""
    return watchlist_check(list_name, entry)

# ── Agentic: Go/no-go ────────────────────────────────────────────────────────

@mcp.tool()
def readiness_score_tool(factors: list[dict]) -> dict:
    """Multi-factor go/no-go scoring. Each factor: {name, value, threshold, weight, invert, marginal_band}."""
    return readiness_score(factors)

# ── Operational tools: Aviation weather ─────────────────────────────────────

@mcp.tool()
def get_metar(stations: list[str]) -> dict:
    """Current METAR observations for any ICAO stations. Use for departure/arrival airport weather briefing."""
    from tools.aviation_weather import get_metar as _f
    return _f(stations=stations)

@mcp.tool()
def get_taf(stations: list[str]) -> dict:
    """Terminal Aerodrome Forecasts for any ICAO stations. Use for travel-day weather planning."""
    from tools.aviation_weather import get_taf as _f
    return _f(stations=stations)

@mcp.tool()
def get_pirep(lat: float, lon: float, radius_nm: int = 100) -> dict:
    """Pilot reports within a radius. Use to check en-route conditions for scheduled or charter flights."""
    from tools.aviation_weather import get_pirep as _f
    return _f(lat=lat, lon=lon, radius_nm=radius_nm)

# ── Operational tools: FAA TFR ───────────────────────────────────────────────

@mcp.tool()
def get_active_tfrs() -> dict:
    """Active FAA Temporary Flight Restrictions. Use to check if airspace restrictions may delay an inbound flight."""
    from tools.faa_tfr import get_active_tfrs as _f
    return _f()

# ── Operational tools: NWS ───────────────────────────────────────────────────

@mcp.tool()
def get_nws_alerts(state: str = None, zone: str = None, lat: float = None, lon: float = None) -> dict:
    """Active NWS weather alerts by state, zone, or lat/lon point. Use for destination weather risk assessment."""
    from tools.nws_alerts import get_nws_alerts as _f
    return _f(state=state, zone=zone, lat=lat, lon=lon)

@mcp.tool()
def get_nws_forecast(lat: float, lon: float) -> dict:
    """7-day NWS forecast for any lat/lon. Use for travel day planning at any origin or destination."""
    from tools.nws_alerts import get_nws_forecast as _f
    return _f(lat=lat, lon=lon)

# ── Operational tools: Flight tracking ───────────────────────────────────────

@mcp.tool()
def track_flight_by_callsign(callsign: str) -> dict:
    """ADS-B telemetry by ICAO callsign. Use to track a commercial flight for pickup timing."""
    from tools.flight_track import track_flight_by_callsign as _f
    return _f(callsign=callsign)

@mcp.tool()
def track_flight_by_registration(registration: str) -> dict:
    """ADS-B telemetry by tail number. Use to track a charter or private aircraft arrival."""
    from tools.flight_track import track_flight_by_registration as _f
    return _f(registration=registration)

@mcp.tool()
def track_flight_by_hex(hex: str) -> dict:
    """ADS-B telemetry by ICAO 24-bit hex. Most reliable for repeated position polling."""
    from tools.flight_track import track_flight_by_hex as _f
    return _f(hex=hex)

# ── Operational tools: Amtrak ────────────────────────────────────────────────

@mcp.tool()
def get_train_status_tool(station_code: str, train_number: str = None) -> dict:
    """Amtrak train status at any station. Use for ground transport coordination at any rail hub."""
    return get_train_status(station_code=station_code, train_number=train_number)

@mcp.tool()
def get_train_delay_tool(train_number: str, station_code: str) -> dict:
    """Delay in minutes for a specific Amtrak train at a given station."""
    return get_train_delay(train_number=train_number, station_code=station_code)

# ── Operational tools: Airport arrivals (generalized, any airport) ──────────

@mcp.tool()
def get_airport_arrivals_tool(airport: str, carriers: list[str] = None, within_minutes: int = 90) -> dict:
    """Forward-looking arrivals for any airport (ICAO or IATA code), optionally filtered by carrier.
    Layered lookup: free MWAA website source for DCA/IAD, FlightAware AeroAPI fallback for any
    airport (requires FLIGHTAWARE_API_KEY env var). Use for ground transport / pickup timing at
    any airport, not just DC-area hubs."""
    return get_airport_arrivals(airport=airport, carriers=carriers, within_minutes=within_minutes)

if _linkedin_available:
    # ── Intelligence: LinkedIn ────────────────────────────────────────────────────
    # Ported 2026-07-27 from the linkedin-export-analyzer Claude skill, which did
    # this same analysis as model-authored inline Python re-run every session.
    # This makes it a real, deterministic MCP tool instead.

    @mcp.tool()
    def linkedin_network_breakdown_tool(export_path: str, reference_date: str = None) -> dict:
        """Industry and tenure breakdown from a LinkedIn "Get a copy of your data" export
        (ZIP or already-extracted directory). reference_date (YYYY-MM-DD) controls tenure
        bucketing -- use the export date when known, defaults to today. Never surfaces
        names, emails, or per-connection PII -- aggregate counts and classifications only."""
        return linkedin_network_breakdown(export_path=export_path, reference_date=reference_date)

    @mcp.tool()
    def linkedin_content_analysis_tool(export_path: str) -> dict:
        """Post and comment topic analysis and topic co-occurrence from a LinkedIn export.
        Comment/share text itself is never returned -- only topic labels and counts."""
        return linkedin_content_analysis(export_path=export_path)

    @mcp.tool()
    def linkedin_engagement_patterns_tool(export_path: str) -> dict:
        """Reaction-type breakdown, monthly activity trend, and most-endorsed contacts from
        a LinkedIn export. LinkedIn's export has no per-reaction author field, so "most
        engaged with" is derived from Endorsement_Given_Info.csv -- the one file that
        actually names people you engaged with, by your own prior action."""
        return linkedin_engagement_patterns(export_path=export_path)

if _mobility_intel_available:
    # ── Intelligence: Mobility ────────────────────────────────────────────────────

    @mcp.tool()
    def mobility_security_brief_tool(heatmap_data: dict, time_window: str = None) -> dict:
        """Surface high-activity and driver-scarcity zones for advance/security planning."""
        return mobility_security_brief(heatmap_data=heatmap_data, time_window=time_window)

    @mcp.tool()
    def mobility_marketing_brief_tool(cluster_data: dict, revenue_data: dict) -> dict:
        """Identify underserved high-value corridors for limo/concierge/black-car positioning."""
        return mobility_marketing_brief(cluster_data=cluster_data, revenue_data=revenue_data)

    @mcp.tool()
    def mobility_emergency_correlate_tool(heatmap_data: dict, incident_timestamps: list[dict]) -> dict:
        """Correlate mobility gaps with timestamped incident data for emergency management use."""
        return mobility_emergency_correlate(heatmap_data=heatmap_data, incident_timestamps=incident_timestamps)

    @mcp.tool()
    def mobility_outage_supplement_tool(records: list[dict], downdetector_csv_path: str) -> dict:
        """Correlate gig data with a Downdetector-format outage export to identify connectivity failures."""
        return mobility_outage_supplement(records=records, downdetector_csv_path=downdetector_csv_path)

if _coverage_intel_available:
    # ── Intelligence: Coverage ────────────────────────────────────────────────────

    @mcp.tool()
    def coverage_load_opencellid_tool(csv_path: str, bbox: dict) -> dict:
        """Load OpenCelliD cell tower CSV for a bounding box. bbox: {sw_lat, sw_lon, ne_lat, ne_lon}."""
        return coverage_load_opencellid(csv_path=csv_path, bbox=bbox)

    @mcp.tool()
    def coverage_grid_overlay_tool(tower_data: list[dict], grid_chars: int = 6) -> dict:
        """Bin cell towers into Maidenhead grid squares by provider at 6 or 8 char resolution."""
        return coverage_grid_overlay(tower_data=tower_data, grid_chars=grid_chars)

    @mcp.tool()
    def coverage_gap_analysis_tool(grid_coverage: dict, mobility_data: dict) -> dict:
        """Identify areas where mobility demand is high but cell coverage is thin."""
        return coverage_gap_analysis(grid_coverage=grid_coverage, mobility_data=mobility_data)

    @mcp.tool()
    def coverage_provider_comparison_tool(grid_coverage: dict, area: str = None) -> dict:
        """Compare carrier coverage quality by neighborhood or Maidenhead grid."""
        return coverage_provider_comparison(grid_coverage=grid_coverage, area=area)

# ── Gig mobility: Geo utils ───────────────────────────────────────────────────

@mcp.tool()
def latlon_to_maidenhead_tool(lat: float, lon: float, precision: int = 6, own_data: bool = False, pre_sanitized: bool = False) -> dict:
    """Convert lat/lon to Maidenhead grid locator. Precision >= 8 requires attestation."""
    return latlon_to_maidenhead(lat, lon, precision, own_data, pre_sanitized)

@mcp.tool()
def maidenhead_to_bbox_tool(grid_square: str) -> dict:
    """Return SW/NE bounding box and center coordinates for a Maidenhead grid square."""
    return maidenhead_to_bbox(grid_square)

@mcp.tool()
def latlon_to_utm_tool(lat: float, lon: float) -> dict:
    """Convert lat/lon to UTM zone, easting, and northing."""
    return latlon_to_utm(lat, lon)

@mcp.tool()
def reverse_geocode_tool(lat: float, lon: float) -> dict:
    """Neighborhood-level reverse geocode via Nominatim. Returns neighborhood, postcode, city. No street data."""
    return reverse_geocode(lat, lon)

# ── Gig mobility: Analysis ────────────────────────────────────────────────────

@mcp.tool()
def gig_normalize_export_tool(file_path: str, platform: str = "generic", own_data: bool = False, pre_sanitized: bool = False, geo_precision: int = 6) -> dict:
    """Normalize a gig platform CSV/JSON export. Platforms: uber, lyft, doordash, grubhub, generic."""
    return gig_normalize_export(file_path, platform, own_data, pre_sanitized, geo_precision)

@mcp.tool()
def gig_revenue_analysis_tool(records: list[dict]) -> dict:
    """Revenue distribution, percentile breakdown, and per-platform comparison."""
    return gig_revenue_analysis(records)

@mcp.tool()
def gig_cluster_analysis_tool(records: list[dict], cluster_by: str = "maidenhead") -> dict:
    """Group records by maidenhead, postcode, neighborhood, suburb, time_of_day, or day_of_week."""
    return gig_cluster_analysis(records, cluster_by)

@mcp.tool()
def gig_availability_heatmap_tool(records: list[dict]) -> dict:
    """Driver/delivery availability proxy from trip density by grid square and hour."""
    return gig_availability_heatmap(records)

@mcp.tool()
def gig_network_anomaly_detect_tool(records: list[dict], outage_events: list[dict], window_minutes: int = 30) -> dict:
    """Correlate trip activity gaps with outage event timestamps."""
    return gig_network_anomaly_detect(records, outage_events, window_minutes)

@mcp.tool()
def gig_cuisine_normalize_tool(records: list[dict], restaurant_field: str = "restaurant", cuisine_field: str = None) -> dict:
    """Map restaurant names to generic cuisine types, discarding restaurant identity."""
    return gig_cuisine_normalize(records, restaurant_field, cuisine_field)

@mcp.tool()
def gig_neighborhood_demand_pattern_tool(records: list[dict]) -> dict:
    """Cuisine demand by neighborhood x day of week x hour of day. Reveals pizza-on-Thursday patterns."""
    return gig_neighborhood_demand_pattern(records)

@mcp.tool()
def gig_restaurant_time_analysis_tool(records: list[dict]) -> dict:
    """Busiest hours and days by cuisine type, decoupled from trip/delivery details."""
    return gig_restaurant_time_analysis(records)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    transport = "stdio"
    if "--transport" in sys.argv:
        idx = sys.argv.index("--transport")
        if idx + 1 < len(sys.argv):
            transport = sys.argv[idx + 1]
    mcp.run(transport=transport)




@mcp.tool()
def stack_watchdog_status_tool(dispatch_token: str = None) -> dict:
    """Check the full corporatetraveldc stack health in one call.

    Runs HTTP health checks against all four stack endpoints (dispatch-web,
    dispatch-runner, ntfy, ollama) and, if a token is supplied, fetches the
    last ctdi-watchdog run record from the dispatch admin API.

    Use this as the first tool when asked "is the stack healthy?", "what's
    the watchdog status?", or "did anything go down recently?".

    Args:
        dispatch_token: Optional bearer token for /admin/watchdog/status.
                        Without it the watchdog record is skipped.

    Returns:
        dict with:
            all_healthy (bool): True if every HTTP endpoint returned 200,
            endpoints (dict): per-endpoint {healthy, status_code, latency_ms},
            watchdog (dict | None): last run record from dispatch admin API,
                fields: timestamp, age_seconds, healthy, healed, failed.
    """
    import os
    import time
    import httpx
    import json as _json

    # Host serving the dispatch stack; set AGENTIC_MCP_DISPATCH_HOST in the
    # deployment environment (e.g. the box's Tailscale IP).
    _host = os.environ.get("AGENTIC_MCP_DISPATCH_HOST", "127.0.0.1")

    ENDPOINTS = {
        "dispatch-web":    f"http://{_host}:8000/healthz",
        "dispatch-runner": f"http://{_host}:8001/healthz",
        "ntfy":            f"http://{_host}:2586/v1/health",
        "ollama":          f"http://{_host}:11434/api/tags",
    }

    results: dict[str, dict] = {}
    for name, url in ENDPOINTS.items():
        results[name] = http_health_check(url, expected_status=200, timeout_seconds=8.0)

    watchdog_record = None
    if dispatch_token:
        try:
            with httpx.Client(timeout=8.0) as client:
                r = client.get(
                    f"http://{_host}:8000/admin/watchdog/status",
                    headers={"Authorization": f"Bearer {dispatch_token}"},
                )
                if r.status_code == 200:
                    watchdog_record = r.json()
                    watchdog_record["age_seconds"] = int(time.time()) - watchdog_record.get("unix", 0)
        except Exception:
            pass

    return {
        "all_healthy": all(v.get("healthy") for v in results.values()),
        "endpoints": results,
        "watchdog": watchdog_record,
    }


if __name__ == "__main__":
    main()
