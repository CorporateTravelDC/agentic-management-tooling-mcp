"""
agentic/async_ops.py -- Async operation lifecycle management.

trigger_poll:       Poll any status URL until success, failure, or timeout.
idempotency_check:  Prevent double-fire on non-idempotent operations.
"""

import json
import time
import datetime
import httpx
from pathlib import Path
from config import get_state_dir

_IDEM_FILENAME = "idempotency_state.json"


def _idem_path() -> Path:
    return get_state_dir(confirm=False) / _IDEM_FILENAME


def _load_idem() -> dict:
    path = _idem_path()
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def _save_idem(state: dict) -> None:
    with open(_idem_path(), "w") as f:
        json.dump(state, f, indent=2)


def trigger_poll(
    status_url: str,
    trigger_id: str,
    outcome_field: str,
    success_values: list[str],
    failure_values: list[str],
    timeout_seconds: float = 60.0,
    poll_interval_seconds: float = 5.0,
    bearer_token: str | None = None,
) -> dict:
    """
    Poll a status URL until an outcome field reaches a terminal value.

    Generalizes the async job lifecycle for any API that returns 202 Accepted
    and exposes a status endpoint. Does not retry the original POST.

    Args:
        status_url:           URL to poll for job status.
        trigger_id:           Identifier to filter results (logged only; filtering
                              logic depends on the API's response shape).
        outcome_field:        Dot-notation path to the status field in the response.
        success_values:       List of field values that indicate success.
        failure_values:       List of field values that indicate failure.
        timeout_seconds:      Stop polling after this many seconds.
        poll_interval_seconds: Time between polls.
        bearer_token:         Optional Authorization: Bearer token.

    Returns:
        {"outcome": "success"|"failure"|"timeout", "final_value": str|None,
         "polls": int, "elapsed_seconds": float, "last_response": dict|None}
    """
    headers = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    from functools import reduce
    start = time.monotonic()
    polls = 0
    last_response = None

    while (time.monotonic() - start) < timeout_seconds:
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(status_url, headers=headers)
            resp.raise_for_status()
            last_response = resp.json()
            polls += 1

            value = reduce(
                lambda d, k: d.get(k) if isinstance(d, dict) else None,
                outcome_field.split("."), last_response
            )
            str_value = str(value) if value is not None else None

            if str_value in success_values:
                return {
                    "outcome": "success", "final_value": str_value,
                    "polls": polls,
                    "elapsed_seconds": round(time.monotonic() - start, 1),
                    "last_response": last_response,
                }
            if str_value in failure_values:
                return {
                    "outcome": "failure", "final_value": str_value,
                    "polls": polls,
                    "elapsed_seconds": round(time.monotonic() - start, 1),
                    "last_response": last_response,
                }
        except Exception:
            polls += 1

        time.sleep(poll_interval_seconds)

    return {
        "outcome": "timeout", "final_value": None,
        "polls": polls,
        "elapsed_seconds": round(time.monotonic() - start, 1),
        "last_response": last_response,
    }


def idempotency_check(
    operation_key: str,
    ttl_seconds: int = 300,
) -> dict:
    """
    Check whether a named operation was recently fired.

    Returns allowed=False if the operation was fired within ttl_seconds.
    Call this before any non-idempotent operation (e.g. sending a notification,
    creating a booking, firing a webhook).

    Args:
        operation_key: Unique string identifying the operation.
        ttl_seconds:   How long to block re-firing after first call.

    Returns:
        {"allowed": bool, "operation_key": str,
         "last_fired_at": str|None, "age_seconds": float|None}
    """
    state = _load_idem()
    now = datetime.datetime.utcnow()
    entry = state.get(operation_key)

    if entry:
        last = datetime.datetime.fromisoformat(entry["fired_at"])
        age = (now - last).total_seconds()
        if age < ttl_seconds:
            return {
                "allowed": False,
                "operation_key": operation_key,
                "last_fired_at": entry["fired_at"],
                "age_seconds": round(age, 1),
            }

    state[operation_key] = {"fired_at": now.isoformat() + "Z"}
    _save_idem(state)
    return {
        "allowed": True,
        "operation_key": operation_key,
        "last_fired_at": None,
        "age_seconds": None,
    }
