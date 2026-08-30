"""
gig_mobility/endpoint_anomaly.py -- generalized background-traffic anomaly
detection for gig-platform mobile apps (Uber, Lyft, DoorDash, etc.),
factored out of a real Uber-specific deployment (a driver phone monitored
via a home DNS ad-blocker's query log, 2026-08-12 through 2026-08-29).

Detects two distinct anomaly classes from a DNS query log (Pi-hole or any
source producing (timestamp, domain) tuples for one client):

  1. CNAME-family drift -- a platform's legitimate traffic almost always
     routes through a small, stable set of first-party CNAME targets. A
     new subdomain that skips that family entirely (no CNAME, or a CNAME
     to unrelated generic CDN infra) is worth flagging, especially when it
     appears alongside a "reconnect burst" (see below).
  2. Novelty -- any domain never seen before from the monitored client,
     weighted higher if it falls inside a reconnect-burst window (a
     cluster of activity right after the app comes back online/reconnects
     -- real "app just woke up and started talking" traffic, not
     background noise, so anything riding along with it is more suspect).

Plus two operational tracking capabilities that turn a one-shot detector
into a running program instead of a manual mining pass every time:

  3. Blocked-hit frequency -- once a domain is denylisted, how often does
     the app keep trying to reach it? A sudden frequency change (much
     harder retrying, or going silent) is itself a signal worth watching.
  4. Discovery-pattern tracking -- a running history of when NEW anomalies
     were found, so a human (or a later automated pass) can see whether
     they cluster (e.g. right after an app update) or arrive at a roughly
     steady background rate. Degrades gracefully with as few as one data
     point -- this is descriptive bookkeeping, not a claim of statistical
     significance from a handful of events.

This module is deliberately platform-agnostic: every function takes its
platform-specific inputs (marker domains, known-good CNAME targets,
already-investigated exceptions) via PlatformEndpointConfig rather than
hardcoding Uber's. example_uber_config() below is the real, validated
config from the deployment this was extracted from -- included as a
genuine usable default (these are public first-party DNS infrastructure
names, not secrets), not just a placeholder.

I/O is intentionally thin and swappable: this module never opens a
database connection or resolves DNS itself -- callers inject a
`cname_lookup_fn(domain) -> (cname_target_or_None, ip_or_None)` and supply
query rows directly, so it stays testable without a live resolver or a
real query-log database, and has no dependency on any specific DNS
ad-blocker's schema.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class PlatformEndpointConfig:
    """Everything platform-specific this module needs, supplied by the
    caller. See example_uber_config() for a real, filled-in instance."""

    platform: str
    root_domain: str
    # Reconnect markers: real-time dispatch/connectivity domains queried
    # specifically when the app is actively reaching its backend (going
    # online, location pings) -- a fresh hit after a gap is a real
    # "app just reconnected" signal, not routine background chatter.
    marker_domains: set[str]
    # The platform's own legitimate CNAME family -- a *.root_domain name
    # CNAMEing here is normal, established infrastructure.
    known_good_cname_targets: set[str]
    # Already investigated and denylisted -- don't re-flag as new.
    known_blocked: set[str] = field(default_factory=set)
    # Already investigated, confirmed benign despite not matching the
    # CNAME-family pattern (e.g. a legitimate third-party analytics cloak).
    known_safe_direct: set[str] = field(default_factory=set)
    reconnect_gap_min: float = 20.0
    burst_window_s: float = 120.0


def example_uber_config() -> PlatformEndpointConfig:
    """The real, validated config from the deployment this module was
    extracted from (confirmed 2026-08-12, still current 2026-08-29).
    Every domain here is Uber's own public first-party infrastructure --
    safe to publish, not a secret."""
    return PlatformEndpointConfig(
        platform="uber",
        root_domain="uber.com",
        marker_domains={
            "tc2.uber.com", "cn-geo1.uber.com",
            "driver.android.mobile-config.uber.com",
        },
        known_good_cname_targets={
            "frontends-cloud.uber.com", "cn-neg.cfe.uber.com",
            "cn-neg-geo.cfe.uber.com", "cloudflare-weighted.uber.com",
            "frontends-gcp-cloudflare-verification.uber.com",
            "frontends-cloudflare.uber.com",
        },
        known_blocked={"fpfcpn2u.uber.com", "ak04a6qc.uber.com"},
        known_safe_direct={"tracking.ibt.uber.com"},
    )


def classify_domain(domain: str, config: PlatformEndpointConfig, cname_lookup_fn) -> str:
    """Returns one of: 'known-good', 'known-blocked', 'known-safe-direct',
    or 'ANOMALOUS (cname=..., ip=...)'.

    cname_lookup_fn(domain) -> (cname_target_or_None, ip_or_None) is
    injected so this stays unit-testable without a live resolver."""
    if domain in config.known_blocked:
        return "known-blocked"
    if domain in config.known_safe_direct:
        return "known-safe-direct"
    cname_target, ip = cname_lookup_fn(domain)
    if cname_target in config.known_good_cname_targets:
        return "known-good"
    if ip == "0.0.0.0":
        return "known-blocked"  # denylisted by something else already
    return f"ANOMALOUS (cname={cname_target!r}, ip={ip!r})"


def detect_reconnect_bursts(
    hits: list[tuple[int, str]],
    config: PlatformEndpointConfig,
    last_marker_ts: dict[str, float],
) -> list[tuple[int, str]]:
    """hits: (timestamp, domain) rows in chronological order for one
    client, already scoped to the window since the last check. Mutates
    last_marker_ts in place (caller persists it) and returns the list of
    (timestamp, marker_domain) reconnect events detected this pass."""
    reconnect_windows = []
    for ts, domain in hits:
        if domain not in config.marker_domains:
            continue
        prev = last_marker_ts.get(domain, 0)
        if ts - prev >= config.reconnect_gap_min * 60:
            reconnect_windows.append((ts, domain))
        last_marker_ts[domain] = ts
    return reconnect_windows


def find_novel_domains(
    hits: list[tuple[int, str]],
    seen_domains: set[str],
    reconnect_windows: list[tuple[int, str]],
    config: PlatformEndpointConfig,
) -> tuple[list[str], list[str]]:
    """Returns (novel_in_burst, novel_elsewhere) -- domains never seen
    before from this client, split by whether they fell inside a
    reconnect-burst window. Mutates seen_domains in place (caller
    persists it) by adding every domain in hits, regardless of novelty."""
    novel_in_burst, novel_elsewhere = [], []
    for ts, domain in hits:
        is_new = domain not in seen_domains
        in_burst = any(abs(ts - wts) <= config.burst_window_s for wts, _ in reconnect_windows)
        if is_new:
            (novel_in_burst if in_burst else novel_elsewhere).append(domain)
        seen_domains.add(domain)
    return novel_in_burst, novel_elsewhere


def find_cname_anomalies(
    hits: list[tuple[int, str]],
    reconnect_windows: list[tuple[int, str]],
    config: PlatformEndpointConfig,
    cname_lookup_fn,
) -> list[tuple[str, str]]:
    """CNAME-family check, scoped to *.root_domain hits inside a
    reconnect burst (background *.root_domain traffic outside a burst is
    not classified -- too noisy, and not the signal this check targets).
    Returns [(domain, verdict), ...] for anything ANOMALOUS."""
    anomalies = []
    suffix = f".{config.root_domain}"
    for ts, domain in hits:
        if not domain.endswith(suffix):
            continue
        in_burst = any(abs(ts - wts) <= config.burst_window_s for wts, _ in reconnect_windows)
        if not in_burst:
            continue
        verdict = classify_domain(domain, config, cname_lookup_fn)
        if verdict.startswith("ANOMALOUS"):
            anomalies.append((domain, verdict))
    return anomalies


def track_blocked_hit_frequency(
    blocked_hits: list[tuple[int, str]],
    state: dict,
    now_ts: float,
) -> dict:
    """blocked_hits: (timestamp, domain) rows for domains already in
    config.known_blocked that were actually blocked (not merely queried)
    since the last check. Mutates state's 'blocked_hit_counts'/
    'blocked_hit_last_ts' in place (caller persists state) and returns a
    per-domain summary: {domain: {"hits_this_pass": n, "total_hits": n,
    "last_hit_ts": ts, "days_since_first_seen_blocked": x or None}}.

    A domain the app keeps hammering after being blocked is itself worth
    knowing about -- either it hasn't gotten the memo (transient retries
    are normal and expected, not alarming on their own) or a sudden jump
    in rate suggests something changed client-side (app update, new
    retry logic) worth a closer look."""
    counts = state.setdefault("blocked_hit_counts", {})
    last_ts = state.setdefault("blocked_hit_last_ts", {})
    first_seen = state.setdefault("blocked_hit_first_seen_ts", {})

    this_pass: dict[str, int] = {}
    for ts, domain in blocked_hits:
        this_pass[domain] = this_pass.get(domain, 0) + 1
        counts[domain] = counts.get(domain, 0) + 1
        last_ts[domain] = max(last_ts.get(domain, 0), ts)
        first_seen.setdefault(domain, ts)

    summary = {}
    for domain, hits_this_pass in this_pass.items():
        days_tracked = (now_ts - first_seen[domain]) / 86400 if domain in first_seen else None
        summary[domain] = {
            "hits_this_pass": hits_this_pass,
            "total_hits": counts[domain],
            "last_hit_ts": last_ts[domain],
            "days_since_first_seen_blocked": round(days_tracked, 1) if days_tracked else None,
        }
    return summary


def track_discovery_pattern(
    discovery_log: list[dict],
    new_anomaly: dict | None,
    now_ts: float,
) -> dict:
    """discovery_log: persisted list of {"ts": float, "domain": str,
    "kind": str} entries, one per genuinely-new anomaly ever confirmed
    (caller appends via new_anomaly, e.g. {"domain": "x.uber.com",
    "kind": "cname-drift"} or {"domain": "y.com", "kind": "novel-in-burst"}
    -- this function stamps "ts" itself). Mutates discovery_log in place
    (caller persists it) and returns pattern stats:

      {"total_discovered": n, "mean_gap_days": x or None,
       "median_gap_days": x or None, "days_since_last": x or None,
       "history": [...]}

    Deliberately makes no claim beyond simple descriptive gap statistics
    -- with only a handful of real events, a mean/median gap is a rough
    orientation for a human reading the alert ("3rd new one in 17 days,
    prior gap was 12 days"), not a forecast or a statistically validated
    pattern. Returns None for mean/median when fewer than 2 gaps exist
    yet, rather than fabricating a number from 0-1 data points."""
    if new_anomaly is not None:
        discovery_log.append({**new_anomaly, "ts": now_ts})

    if not discovery_log:
        return {"total_discovered": 0, "mean_gap_days": None,
                "median_gap_days": None, "days_since_last": None, "history": []}

    ordered = sorted(discovery_log, key=lambda e: e["ts"])
    gaps_days = [
        (ordered[i + 1]["ts"] - ordered[i]["ts"]) / 86400
        for i in range(len(ordered) - 1)
    ]
    return {
        "total_discovered": len(ordered),
        "mean_gap_days": round(statistics.mean(gaps_days), 1) if gaps_days else None,
        "median_gap_days": round(statistics.median(gaps_days), 1) if gaps_days else None,
        "days_since_last": round((now_ts - ordered[-1]["ts"]) / 86400, 1),
        "history": ordered,
    }
