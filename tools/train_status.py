"""
train_status.py — MCP tools for Amtrak NEC train status via amtraker.com v3 API.

No dispatch dependency. Uses the public amtraker.com v3 API which aggregates
Amtrak's data feed. Focus: Washington Union Station (WAS / WASH).

Amtraker v3 API:
  GET /trains/{train_number}   → list of train objects for that number
  GET /trains                  → all trains (large; use sparingly)

Station code for Washington Union Station: "WAS" (Amtrak) / "WASH" (amtraker)
"""

from __future__ import annotations

from typing import Optional

import httpx

from config import AMTRAK_URL

_TIMEOUT = 20
_WAS_CODES = {"WAS", "WASH", "WASHINGTON"}  # Amtraker may use any of these


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=AMTRAK_URL,
        timeout=_TIMEOUT,
        headers={"Accept": "application/json"},
    )


def _find_was_stop(stations: list[dict]) -> Optional[dict]:
    """Return the Washington Union Station stop dict from a train's stations list."""
    for stop in stations:
        code = (stop.get("code", "") or "").upper()
        name = (stop.get("name", "") or "").upper()
        if code in _WAS_CODES or "WASHINGTON" in name or "UNION STATION" in name:
            return stop
    return None


def _delay_minutes(stop: dict) -> int:
    """
    Compute delay in integer minutes from a stop dict.
    Amtraker v3 provides scheduled and estimated times (ISO strings).
    """
    sched_arr = stop.get("schArr") or stop.get("scheduledArrival")
    est_arr   = stop.get("arr")    or stop.get("estArrival")
    sched_dep = stop.get("schDep") or stop.get("scheduledDeparture")
    est_dep   = stop.get("dep")    or stop.get("estDeparture")

    # Prefer arrival comparison; fall back to departure
    pairs = [(sched_arr, est_arr), (sched_dep, est_dep)]
    from datetime import datetime, timezone

    for sched, est in pairs:
        if sched and est:
            try:
                # Amtraker v3 returns epoch milliseconds as integers or ISO strings
                if isinstance(sched, (int, float)):
                    sched_dt = datetime.fromtimestamp(sched / 1000, tz=timezone.utc)
                    est_dt   = datetime.fromtimestamp(est   / 1000, tz=timezone.utc)
                else:
                    sched_dt = datetime.fromisoformat(sched.replace("Z", "+00:00"))
                    est_dt   = datetime.fromisoformat(est.replace("Z", "+00:00"))
                delta = est_dt - sched_dt
                return int(delta.total_seconds() / 60)
            except Exception:
                continue
    return 0


def _format_train(train: dict, was_stop: Optional[dict]) -> dict:
    """Build a normalized train status dict."""
    out: dict = {
        "train_number":   str(train.get("trainNum", train.get("train_number", ""))),
        "route_name":     train.get("routeName", train.get("route_name", "")),
        "origin":         train.get("origCode", train.get("origin", "")),
        "destination":    train.get("destCode", train.get("destination", "")),
        "train_state":    train.get("trainState", train.get("status", "")),
        "service":        train.get("service", ""),
        "last_updated":   train.get("lastValTS", train.get("last_updated", "")),
    }

    if was_stop:
        delay = _delay_minutes(was_stop)
        out["was_stop"] = {
            "code":            was_stop.get("code", "WAS"),
            "name":            was_stop.get("name", "Washington Union Station"),
            "scheduled_arr":   was_stop.get("schArr") or was_stop.get("scheduledArrival"),
            "estimated_arr":   was_stop.get("arr")    or was_stop.get("estArrival"),
            "scheduled_dep":   was_stop.get("schDep") or was_stop.get("scheduledDeparture"),
            "estimated_dep":   was_stop.get("dep")    or was_stop.get("estDeparture"),
            "status":          was_stop.get("status", ""),
            "delay_minutes":   delay,
        }
        out["delay_minutes"] = delay
        out["on_time"] = delay <= 5
    else:
        out["was_stop"] = None
        out["delay_minutes"] = None
        out["on_time"] = None

    return out


# ── MCP tool functions ───────────────────────────────────────────────────────

def get_nec_train_status(train_number: Optional[str] = None) -> dict:
    """
    Get current Amtrak NEC train status at Washington Union Station.

    When train_number is provided, returns status for that specific train only.
    When omitted, returns all active trains that stop at WAS.

    The amtraker v3 API is queried directly (no Amtrak login required).

    Args:
        train_number: Amtrak train number string (e.g. '2125', '2150', '95').
                      Omit for all active trains at WAS.

    Returns:
        dict with keys: trains (list), count, station ("WAS").
        Each train includes: train_number, route_name, origin, destination,
        train_state, was_stop (with scheduled/estimated times and delay_minutes).
    """
    with _client() as client:
        if train_number:
            resp = client.get(f"/trains/{train_number}")
            if resp.status_code == 404:
                return {
                    "trains": [],
                    "count": 0,
                    "station": "WAS",
                    "error": f"No data for train {train_number}",
                }
            resp.raise_for_status()
            raw = resp.json()
            # amtraker v3 returns a list of train instances for the number
            train_list = raw if isinstance(raw, list) else [raw]
        else:
            # Fetch all trains — large but necessary without train number
            resp = client.get("/trains")
            resp.raise_for_status()
            raw = resp.json()
            # v3: {"1": [...], "2": [...], ...} keyed by train number
            train_list = []
            if isinstance(raw, dict):
                for v in raw.values():
                    if isinstance(v, list):
                        train_list.extend(v)
                    elif isinstance(v, dict):
                        train_list.append(v)
            elif isinstance(raw, list):
                train_list = raw

    results: list[dict] = []
    for train in train_list:
        stations = train.get("stations", [])
        was_stop = _find_was_stop(stations)
        # When fetching all trains, skip those with no WAS stop
        if train_number is None and was_stop is None:
            continue
        results.append(_format_train(train, was_stop))

    return {
        "trains": results,
        "count": len(results),
        "station": "WAS",
        "train_number_filter": train_number,
    }


def get_train_delay(train_number: str) -> dict:
    """
    Get delay in minutes for a specific Amtrak train at Washington Union Station.

    Args:
        train_number: Amtrak train number (e.g. '2125', '95').

    Returns:
        dict with keys: train_number, delay_minutes (int), on_time (bool),
        route_name, train_state, was_stop.
        Returns {"error": ...} if the train is not found or has no WAS stop.
    """
    result = get_nec_train_status(train_number=train_number)

    if "error" in result:
        return {"error": result["error"], "train_number": train_number}

    trains = result.get("trains", [])
    if not trains:
        return {
            "error": f"Train {train_number} not found or not serving WAS",
            "train_number": train_number,
        }

    # Return the first (most relevant) instance
    t = trains[0]
    return {
        "train_number":  t["train_number"],
        "route_name":    t["route_name"],
        "train_state":   t["train_state"],
        "delay_minutes": t.get("delay_minutes"),
        "on_time":       t.get("on_time"),
        "was_stop":      t.get("was_stop"),
    }
