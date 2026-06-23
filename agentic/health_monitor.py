"""
agentic/health_monitor.py -- HTTP health checks and feed freshness auditing.

Works against any URL. No platform-specific endpoints are hardcoded.
"""

import time
import datetime
import httpx
from functools import reduce


def http_health_check(
    url: str,
    expected_status: int = 200,
    timeout_seconds: float = 10.0,
    bearer_token: str | None = None,
) -> dict:
    """
    Perform an HTTP GET health check against any URL.

    Args:
        url:             Target URL.
        expected_status: Expected HTTP status code (default 200).
        timeout_seconds: Request timeout.
        bearer_token:    Optional Authorization: Bearer token.

    Returns:
        {
          "url": str,
          "healthy": bool,
          "status_code": int | None,
          "latency_ms": float | None,
          "expected_status": int,
          "error": str | None,
        }
    """
    headers = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    start = time.monotonic()
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.get(url, headers=headers)
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        healthy = resp.status_code == expected_status
        return {
            "url": url,
            "healthy": healthy,
            "status_code": resp.status_code,
            "latency_ms": latency_ms,
            "expected_status": expected_status,
            "error": None if healthy else f"Expected {expected_status}, got {resp.status_code}",
        }
    except Exception as exc:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {
            "url": url,
            "healthy": False,
            "status_code": None,
            "latency_ms": latency_ms,
            "expected_status": expected_status,
            "error": str(exc),
        }


def feed_freshness_audit(feeds: list[dict]) -> dict:
    """
    Audit a list of data feeds for staleness.

    Each feed entry specifies a URL to poll and a dot-notation path to a
    timestamp field in the JSON response. The tool compares that timestamp
    to now and flags feeds exceeding their threshold.

    Args:
        feeds: List of feed descriptors:
          {
            "name": str,
            "url": str,
            "timestamp_field_path": str,  # dot-notation, e.g. "data.updated_at"
            "threshold_seconds": int,
            "bearer_token": str (optional),
          }

    Returns:
        {
          "audited_at": str,
          "total": int,
          "fresh": int,
          "stale": int,
          "feeds": list of per-feed results,
        }
    """
    audited_at = datetime.datetime.utcnow().isoformat() + "Z"
    results = []

    for feed in feeds:
        name = feed.get("name", feed.get("url", "unknown"))
        url = feed.get("url", "")
        field_path = feed.get("timestamp_field_path", "")
        threshold = feed.get("threshold_seconds", 300)
        token = feed.get("bearer_token")

        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

            # Traverse dot-notation path
            value = reduce(lambda d, k: d.get(k, {}) if isinstance(d, dict) else None,
                           field_path.split("."), data)

            if value is None:
                results.append({
                    "name": name, "fresh": False, "age_seconds": None,
                    "threshold_seconds": threshold,
                    "error": f"Field '{field_path}' not found in response.",
                })
                continue

            ts = datetime.datetime.fromisoformat(str(value).rstrip("Z"))
            age = (datetime.datetime.utcnow() - ts).total_seconds()
            results.append({
                "name": name,
                "fresh": age <= threshold,
                "age_seconds": round(age, 1),
                "threshold_seconds": threshold,
                "last_value": str(value),
                "error": None,
            })

        except Exception as exc:
            results.append({
                "name": name, "fresh": False, "age_seconds": None,
                "threshold_seconds": threshold, "error": str(exc),
            })

    fresh_count = sum(1 for r in results if r.get("fresh"))
    return {
        "audited_at": audited_at,
        "total": len(results),
        "fresh": fresh_count,
        "stale": len(results) - fresh_count,
        "feeds": results,
    }
