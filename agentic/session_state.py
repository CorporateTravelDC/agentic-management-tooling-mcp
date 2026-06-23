"""
agentic/session_state.py -- Session snapshot save / restore / age.

Saves the state of caller-supplied HTTP endpoints to a named JSON file.
Used to preserve situational awareness across context resets in long-running
agentic sessions. No platform-specific URLs are hardcoded.
"""

import json
import datetime
import httpx
from pathlib import Path
from config import get_state_dir


def _snapshot_path(snapshot_key: str) -> Path:
    safe_key = "".join(c if c.isalnum() or c in "-_" else "_" for c in snapshot_key)
    return get_state_dir(confirm=False) / f"snapshot_{safe_key}.json"


def session_snapshot_save(
    snapshot_key: str,
    endpoints: list[dict],
    timeout_seconds: float = 10.0,
) -> dict:
    """
    Poll a set of HTTP endpoints and save the results as a named snapshot.

    Args:
        snapshot_key: Identifier for this snapshot (e.g. 'dispatch', 'crm-state').
        endpoints:    List of {"name": str, "url": str, "headers": dict (optional)}.
        timeout_seconds: Per-request timeout.

    Returns:
        {"snapshot_key": str, "saved_at": str, "endpoint_count": int, "errors": list}
    """
    saved_at = datetime.datetime.utcnow().isoformat() + "Z"
    results = {}
    errors = []

    with httpx.Client(timeout=timeout_seconds) as client:
        for ep in endpoints:
            name = ep.get("name", ep.get("url", "unknown"))
            url = ep.get("url", "")
            headers = ep.get("headers", {})
            try:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                try:
                    results[name] = resp.json()
                except Exception:
                    results[name] = resp.text
            except Exception as exc:
                errors.append({"name": name, "error": str(exc)})
                results[name] = None

    snapshot = {
        "snapshot_key": snapshot_key,
        "saved_at": saved_at,
        "endpoints": [ep.get("name", ep.get("url")) for ep in endpoints],
        "data": results,
    }

    with open(_snapshot_path(snapshot_key), "w") as f:
        json.dump(snapshot, f, indent=2)

    return {
        "snapshot_key": snapshot_key,
        "saved_at": saved_at,
        "endpoint_count": len(endpoints),
        "errors": errors,
    }


def session_snapshot_restore(snapshot_key: str) -> dict:
    """
    Read a named snapshot and return its contents with age.

    Args:
        snapshot_key: Identifier used when saving.

    Returns:
        Full snapshot dict plus "age_seconds" field, or error if not found.
    """
    path = _snapshot_path(snapshot_key)
    if not path.exists():
        return {"error": f"No snapshot found for key '{snapshot_key}'."}

    with open(path) as f:
        snapshot = json.load(f)

    saved_at = datetime.datetime.fromisoformat(snapshot["saved_at"].rstrip("Z"))
    age = (datetime.datetime.utcnow() - saved_at).total_seconds()
    snapshot["age_seconds"] = round(age, 1)
    return snapshot


def session_snapshot_age(snapshot_key: str) -> dict:
    """
    Return how old a snapshot is without loading its full data.

    Args:
        snapshot_key: Identifier used when saving.

    Returns:
        {"snapshot_key": str, "age_seconds": float, "saved_at": str} or error.
    """
    path = _snapshot_path(snapshot_key)
    if not path.exists():
        return {"error": f"No snapshot found for key '{snapshot_key}'."}

    with open(path) as f:
        snapshot = json.load(f)

    saved_at = datetime.datetime.fromisoformat(snapshot["saved_at"].rstrip("Z"))
    age = (datetime.datetime.utcnow() - saved_at).total_seconds()
    return {
        "snapshot_key": snapshot_key,
        "age_seconds": round(age, 1),
        "saved_at": snapshot["saved_at"],
    }
