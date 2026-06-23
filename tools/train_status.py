"""
tools/train_status.py -- Amtrak train status via amtraker.com public API.

Station code is a required parameter; no station is hardcoded.
Works for any Amtrak station on any corridor.
"""

import httpx
from config import AMTRAK_URL


def _fetch_train(train_number: str) -> list[dict]:
    url = f"{AMTRAK_URL}/trains/{train_number}"
    with httpx.Client(timeout=15.0) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()


def get_train_status(
    station_code: str,
    train_number: str | None = None,
) -> dict:
    """
    Get current Amtrak train status at any station.

    Useful for ground transport coordination: know when a train arrives
    before dispatching a car, or monitor delays for client pickup timing.

    Args:
        station_code: Three-letter Amtrak station code (e.g. 'WAS', 'NYP',
                      'BOS', 'PHL', 'BAL', 'RVR'). Case-insensitive.
        train_number: Optional specific train number (e.g. '2150', '95').
                      Omit to return all trains currently active at the station.

    Returns:
        Dict with 'trains' list. Each entry includes: train_number, route_name,
        origin, destination, train_state, and a 'station_stop' block with
        scheduled and estimated arrival/departure times and delay_minutes.
    """
    station_code = station_code.upper().strip()
    trains = []

    if train_number:
        try:
            raw = _fetch_train(train_number)
        except Exception as exc:
            return {"error": str(exc), "station_code": station_code}

        for entry in raw:
            stop = _extract_stop(entry, station_code)
            if stop:
                trains.append(_format_train(entry, stop))
    else:
        # Fetch all active trains and filter by station
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(f"{AMTRAK_URL}/trains")
                resp.raise_for_status()
                all_trains = resp.json()
        except Exception as exc:
            return {"error": str(exc), "station_code": station_code}

        for entry in all_trains:
            if not isinstance(entry, dict):
                continue
            stop = _extract_stop(entry, station_code)
            if stop:
                trains.append(_format_train(entry, stop))

    return {
        "station_code": station_code,
        "train_count": len(trains),
        "trains": trains,
    }


def get_train_delay(
    train_number: str,
    station_code: str,
) -> dict:
    """
    Get delay in minutes for a specific Amtrak train at a given station.

    Args:
        train_number: Amtrak train number (e.g. '2150', '95').
        station_code: Three-letter station code (e.g. 'WAS', 'NYP').

    Returns:
        Dict with: train_number, station_code, delay_minutes (int),
        on_time (bool), route_name, train_state, station_stop.
    """
    station_code = station_code.upper().strip()
    try:
        raw = _fetch_train(train_number)
    except Exception as exc:
        return {"error": str(exc), "train_number": train_number}

    for entry in raw:
        stop = _extract_stop(entry, station_code)
        if stop:
            delay = stop.get("arrival_delay_minutes") or stop.get("departure_delay_minutes") or 0
            return {
                "train_number": train_number,
                "station_code": station_code,
                "delay_minutes": delay,
                "on_time": delay <= 0,
                "route_name": entry.get("routeName", ""),
                "train_state": entry.get("trainState", ""),
                "station_stop": stop,
            }

    return {
        "error": f"Train {train_number} not found at station {station_code}.",
        "train_number": train_number,
        "station_code": station_code,
    }


def _extract_stop(entry: dict, station_code: str) -> dict | None:
    stations = entry.get("stations", [])
    for s in stations:
        if s.get("code", "").upper() == station_code:
            return {
                "code": station_code,
                "scheduled_arrival":  s.get("schArr"),
                "estimated_arrival":  s.get("estArr"),
                "scheduled_departure": s.get("schDep"),
                "estimated_departure": s.get("estDep"),
                "arrival_delay_minutes":   s.get("arrDlyMin", 0),
                "departure_delay_minutes": s.get("depDlyMin", 0),
                "status": s.get("status"),
            }
    return None


def _format_train(entry: dict, stop: dict) -> dict:
    return {
        "train_number": entry.get("trainNum", ""),
        "route_name":   entry.get("routeName", ""),
        "origin":       entry.get("origCode", ""),
        "destination":  entry.get("destCode", ""),
        "train_state":  entry.get("trainState", ""),
        "station_stop": stop,
    }
