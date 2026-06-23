"""
agentic/entity_watchlist.py -- Named entity watchlists.

Persistent, case-insensitive lists for any entity type: flight numbers,
tail numbers, client IDs, reservation codes, phone numbers, etc.
Each named list is stored as a separate JSON file in the state directory.
"""

import json
import datetime
from pathlib import Path
from config import get_state_dir


def _list_path(list_name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in list_name)
    return get_state_dir(confirm=False) / f"watchlist_{safe}.json"


def _load(list_name: str) -> dict:
    path = _list_path(list_name)
    if not path.exists():
        return {"list_name": list_name, "entries": [], "updated_at": None}
    with open(path) as f:
        return json.load(f)


def _save(list_name: str, data: dict) -> None:
    data["updated_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    with open(_list_path(list_name), "w") as f:
        json.dump(data, f, indent=2)


def watchlist_add(list_name: str, entry: str) -> dict:
    """
    Add an entry to a named watchlist. Case-insensitive deduplication.

    Args:
        list_name: Watchlist name (e.g. 'vip-flights', 'active-clients').
        entry:     Value to add.

    Returns:
        {"list_name": str, "entry": str, "added": bool, "count": int}
    """
    data = _load(list_name)
    normalized = entry.strip().upper()
    existing = [e.upper() for e in data["entries"]]
    if normalized in existing:
        return {"list_name": list_name, "entry": entry, "added": False, "count": len(data["entries"])}
    data["entries"].append(entry.strip())
    _save(list_name, data)
    return {"list_name": list_name, "entry": entry, "added": True, "count": len(data["entries"])}


def watchlist_remove(list_name: str, entry: str) -> dict:
    """
    Remove an entry from a named watchlist. Case-insensitive match.

    Returns:
        {"list_name": str, "entry": str, "removed": bool, "count": int}
    """
    data = _load(list_name)
    normalized = entry.strip().upper()
    before = len(data["entries"])
    data["entries"] = [e for e in data["entries"] if e.strip().upper() != normalized]
    removed = len(data["entries"]) < before
    if removed:
        _save(list_name, data)
    return {"list_name": list_name, "entry": entry, "removed": removed, "count": len(data["entries"])}


def watchlist_get(list_name: str) -> dict:
    """
    Return the full contents of a named watchlist.

    Returns:
        {"list_name": str, "entries": list[str], "count": int, "updated_at": str|None}
    """
    data = _load(list_name)
    return {
        "list_name": list_name,
        "entries": data["entries"],
        "count": len(data["entries"]),
        "updated_at": data.get("updated_at"),
    }


def watchlist_check(list_name: str, entry: str) -> dict:
    """
    Check whether a specific entry is on a named watchlist.

    Args:
        list_name: Watchlist name.
        entry:     Value to check.

    Returns:
        {"list_name": str, "entry": str, "found": bool}
    """
    data = _load(list_name)
    normalized = entry.strip().upper()
    found = normalized in [e.strip().upper() for e in data["entries"]]
    return {"list_name": list_name, "entry": entry, "found": found}
