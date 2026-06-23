"""
gig_mobility/gig_analysis.py -- Gig platform export analysis.

Normalizes exports from Uber, Lyft, DoorDash, Grubhub, and generic
CSV/JSON into a common schema. Analyzes revenue patterns, geographic
clusters, driver availability proxies, network anomalies, cuisine
demand by neighborhood, and restaurant time patterns.

Privacy: Street addresses and restaurant names are consumed then
discarded. Only neighborhood/postcode and cuisine type are retained.
"""

import csv
import json
import datetime
import collections
from pathlib import Path
from typing import Any

from gig_mobility.geo_utils import reverse_geocode, latlon_to_maidenhead
from config import MAIDENHEAD_DEFAULT_PRECISION


# ---------------------------------------------------------------------------
# Platform column maps
# ---------------------------------------------------------------------------

_PLATFORM_MAPS = {
    "uber": {
        "trip_id":       ["Trip ID", "trip_id", "Uuid"],
        "start_time":    ["Begin Trip Time", "start_time", "Request Time"],
        "end_time":      ["Dropoff Time", "end_time"],
        "fare":          ["Fare Amount", "fare", "Price"],
        "pickup_lat":    ["Begin Trip Lat", "start_lat"],
        "pickup_lon":    ["Begin Trip Lng", "start_lng"],
        "dropoff_lat":   ["Dropoff Lat", "end_lat"],
        "dropoff_lon":   ["Dropoff Lng", "end_lng"],
        "restaurant":    ["Restaurant Name", "merchant_name"],
        "cuisine":       ["Restaurant Category", "category"],
        "trip_type":     ["Product Type", "service"],
    },
    "lyft": {
        "trip_id":       ["Ride ID", "id"],
        "start_time":    ["Requested", "start_time"],
        "end_time":      ["Dropoff", "end_time"],
        "fare":          ["Ride Total", "total_amount"],
        "pickup_lat":    ["Origin Lat", "pickup_lat"],
        "pickup_lon":    ["Origin Lng", "pickup_lng"],
        "dropoff_lat":   ["Destination Lat", "dropoff_lat"],
        "dropoff_lon":   ["Destination Lng", "dropoff_lng"],
        "trip_type":     ["Ride Type"],
    },
    "doordash": {
        "trip_id":       ["Order ID", "order_id"],
        "start_time":    ["Order Placed", "created_at"],
        "end_time":      ["Delivered At", "delivered_at"],
        "fare":          ["Order Total", "subtotal"],
        "dropoff_lat":   ["Delivery Lat", "lat"],
        "dropoff_lon":   ["Delivery Lng", "lng"],
        "restaurant":    ["Store Name", "restaurant_name", "merchant"],
        "cuisine":       ["Store Category", "cuisine_type"],
    },
    "grubhub": {
        "trip_id":       ["Order Number", "order_id"],
        "start_time":    ["Order Date", "placed_at"],
        "end_time":      ["Delivered", "delivered_at"],
        "fare":          ["Order Total", "total"],
        "dropoff_lat":   ["Latitude", "lat"],
        "dropoff_lon":   ["Longitude", "lng"],
        "restaurant":    ["Restaurant", "name"],
        "cuisine":       ["Cuisine", "category"],
    },
    "generic": {
        "trip_id":       ["id", "trip_id", "order_id"],
        "start_time":    ["start_time", "created_at", "timestamp"],
        "end_time":      ["end_time", "completed_at", "delivered_at"],
        "fare":          ["fare", "total", "amount", "price"],
        "pickup_lat":    ["pickup_lat", "origin_lat", "start_lat"],
        "pickup_lon":    ["pickup_lon", "origin_lon", "start_lng"],
        "dropoff_lat":   ["dropoff_lat", "dest_lat", "lat", "latitude"],
        "dropoff_lon":   ["dropoff_lon", "dest_lon", "lng", "longitude"],
        "restaurant":    ["restaurant", "merchant", "store", "vendor"],
        "cuisine":       ["cuisine", "category", "type"],
    },
}

# ---------------------------------------------------------------------------
# Cuisine keyword matching
# ---------------------------------------------------------------------------

_CUISINE_KEYWORDS = {
    "pizza":       ["pizza", "pizzeria", "pie"],
    "chinese":     ["chinese", "china", "wok", "dim sum", "peking", "szechuan", "cantonese"],
    "mexican":     ["mexican", "taco", "burrito", "cantina", "tex-mex", "quesadilla"],
    "japanese":    ["japanese", "sushi", "ramen", "izakaya", "hibachi", "teriyaki", "bento"],
    "indian":      ["indian", "curry", "tandoor", "biryani", "naan", "masala"],
    "italian":     ["italian", "pasta", "trattoria", "osteria", "risotto"],
    "american":    ["burger", "bbq", "barbecue", "diner", "grill", "wings", "american"],
    "thai":        ["thai", "pad thai", "satay"],
    "mediterranean": ["mediterranean", "falafel", "kebab", "shawarma", "hummus", "gyro"],
    "vietnamese":  ["vietnamese", "pho", "banh mi", "bun"],
    "korean":      ["korean", "kimchi", "bibimbap", "bulgogi"],
    "greek":       ["greek", "souvlaki"],
    "seafood":     ["seafood", "fish", "crab", "lobster", "oyster", "shrimp", "sushi"],
    "chicken":     ["chicken", "poultry", "wings"],
    "sandwich":    ["sandwich", "sub", "deli", "hoagie", "wrap"],
    "breakfast":   ["breakfast", "brunch", "pancake", "waffle", "bagel", "cafe"],
    "dessert":     ["ice cream", "gelato", "dessert", "bakery", "pastry", "donut", "cake"],
    "coffee":      ["coffee", "cafe", "espresso", "tea", "boba"],
}


def _classify_cuisine(name: str, declared_type: str | None) -> str:
    """Map a restaurant name or declared type to a generic cuisine label."""
    if declared_type:
        check = declared_type.lower()
        for cuisine, keywords in _CUISINE_KEYWORDS.items():
            if any(k in check for k in keywords):
                return cuisine

    if name:
        check = name.lower()
        for cuisine, keywords in _CUISINE_KEYWORDS.items():
            if any(k in check for k in keywords):
                return cuisine

    return "other"


def _resolve_field(row: dict, candidates: list[str]) -> Any:
    """Return the first matching value from a list of candidate column names."""
    for key in candidates:
        if key in row:
            return row[key]
    return None


def _parse_fare(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_time(value: Any) -> datetime.datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %H:%M",
                "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# gig_normalize_export
# ---------------------------------------------------------------------------

def gig_normalize_export(
    file_path: str,
    platform: str = "generic",
    own_data: bool = False,
    pre_sanitized: bool = False,
    geo_precision: int = MAIDENHEAD_DEFAULT_PRECISION,
) -> dict:
    """
    Normalize a gig platform CSV/JSON export into a common schema.

    Street addresses and restaurant names are consumed and discarded.
    Only neighborhood, postcode, cuisine type, and time/value data are retained.

    Supported platforms: uber, lyft, doordash, grubhub, generic.

    Args:
        file_path:      Path to the CSV or JSON export file.
        platform:       Platform key (default: 'generic').
        own_data:       Required for geo_precision >= 8.
        pre_sanitized:  Required for geo_precision >= 8.
        geo_precision:  Maidenhead precision (default 6, max 8 without attestation).

    Returns:
        {"platform": str, "record_count": int, "skipped": int,
         "records": list of normalized records, "warnings": list}
    """
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    platform = platform.lower()
    col_map = _PLATFORM_MAPS.get(platform, _PLATFORM_MAPS["generic"])

    # Clamp precision
    if geo_precision >= 8 and not (own_data and pre_sanitized):
        geo_precision = MAIDENHEAD_DEFAULT_PRECISION
        warnings = ["geo_precision capped at 6: own_data and pre_sanitized both required for >= 8."]
    else:
        warnings = []

    rows = []
    if path.suffix.lower() == ".json":
        with open(path) as f:
            raw = json.load(f)
        rows = raw if isinstance(raw, list) else raw.get("data", raw.get("trips", raw.get("orders", [])))
    else:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

    normalized = []
    skipped = 0

    for row in rows:
        fare = _parse_fare(_resolve_field(row, col_map.get("fare", [])))
        start_time = _parse_time(_resolve_field(row, col_map.get("start_time", [])))

        if fare is None or start_time is None:
            skipped += 1
            continue

        dropoff_lat = _resolve_field(row, col_map.get("dropoff_lat", []))
        dropoff_lon = _resolve_field(row, col_map.get("dropoff_lon", []))
        restaurant_name = _resolve_field(row, col_map.get("restaurant", []))
        declared_cuisine = _resolve_field(row, col_map.get("cuisine", []))

        # Classify cuisine then discard restaurant name
        cuisine_type = _classify_cuisine(
            str(restaurant_name) if restaurant_name else "",
            str(declared_cuisine) if declared_cuisine else None,
        )

        # Reverse geocode destination to neighborhood/postcode, discard street
        geo = {}
        maidenhead_grid = None
        if dropoff_lat and dropoff_lon:
            try:
                lat, lon = float(dropoff_lat), float(dropoff_lon)
                geo_result = reverse_geocode(lat, lon)
                geo = {
                    "neighborhood": geo_result.get("neighborhood"),
                    "suburb": geo_result.get("suburb"),
                    "postcode": geo_result.get("postcode"),
                    "city": geo_result.get("city"),
                }
                grid_result = latlon_to_maidenhead(lat, lon, geo_precision, own_data, pre_sanitized)
                maidenhead_grid = grid_result["grid"]
            except Exception as exc:
                warnings.append(f"Geo error on record: {exc}")

        normalized.append({
            "trip_id": _resolve_field(row, col_map.get("trip_id", [])),
            "platform": platform,
            "start_time": start_time.isoformat(),
            "day_of_week": start_time.strftime("%A"),
            "hour_of_day": start_time.hour,
            "fare_usd": round(fare, 2),
            "fare_bucket": "high" if fare >= 30 else ("mid" if fare >= 12 else "low"),
            "cuisine_type": cuisine_type,
            "maidenhead": maidenhead_grid,
            **geo,
        })

    return {
        "platform": platform,
        "record_count": len(normalized),
        "skipped": skipped,
        "records": normalized,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Revenue analysis
# ---------------------------------------------------------------------------

def gig_revenue_analysis(records: list[dict]) -> dict:
    """
    Analyze revenue distribution across normalized gig records.

    Args:
        records: Normalized records from gig_normalize_export.

    Returns:
        Revenue statistics, percentile breakdown, and per-platform summary.
    """
    if not records:
        return {"error": "No records provided."}

    fares = sorted(r["fare_usd"] for r in records if "fare_usd" in r)
    n = len(fares)
    total = sum(fares)

    def percentile(data, p):
        idx = int(len(data) * p / 100)
        return round(data[min(idx, len(data) - 1)], 2)

    buckets = collections.Counter(r.get("fare_bucket", "unknown") for r in records)
    by_platform = collections.defaultdict(list)
    for r in records:
        by_platform[r.get("platform", "unknown")].append(r.get("fare_usd", 0))

    return {
        "record_count": n,
        "total_usd": round(total, 2),
        "mean_usd": round(total / n, 2),
        "median_usd": percentile(fares, 50),
        "p10_usd": percentile(fares, 10),
        "p25_usd": percentile(fares, 25),
        "p75_usd": percentile(fares, 75),
        "p90_usd": percentile(fares, 90),
        "min_usd": round(fares[0], 2),
        "max_usd": round(fares[-1], 2),
        "fare_buckets": dict(buckets),
        "by_platform": {
            p: {"count": len(v), "total": round(sum(v), 2), "mean": round(sum(v) / len(v), 2)}
            for p, v in by_platform.items()
        },
    }


# ---------------------------------------------------------------------------
# Cluster analysis
# ---------------------------------------------------------------------------

def gig_cluster_analysis(
    records: list[dict],
    cluster_by: str = "maidenhead",
) -> dict:
    """
    Group records by a geographic or temporal dimension.

    Args:
        records:    Normalized records from gig_normalize_export.
        cluster_by: One of: 'maidenhead', 'postcode', 'neighborhood',
                    'suburb', 'time_of_day', 'day_of_week'.

    Returns:
        {"cluster_by": str, "clusters": list sorted by trip count desc}
    """
    valid = ["maidenhead", "postcode", "neighborhood", "suburb", "time_of_day", "day_of_week"]
    if cluster_by not in valid:
        return {"error": f"cluster_by must be one of: {valid}"}

    groups = collections.defaultdict(list)
    for r in records:
        if cluster_by == "time_of_day":
            h = r.get("hour_of_day")
            if h is not None:
                label = f"{h:02d}:00"
                groups[label].append(r)
        else:
            key = r.get(cluster_by)
            if key:
                groups[str(key)].append(r)

    clusters = []
    for key, items in groups.items():
        fares = [i.get("fare_usd", 0) for i in items]
        clusters.append({
            "key": key,
            "trip_count": len(items),
            "total_usd": round(sum(fares), 2),
            "mean_usd": round(sum(fares) / len(fares), 2) if fares else 0,
        })

    clusters.sort(key=lambda x: x["trip_count"], reverse=True)
    return {"cluster_by": cluster_by, "clusters": clusters}


# ---------------------------------------------------------------------------
# Availability heatmap
# ---------------------------------------------------------------------------

def gig_availability_heatmap(records: list[dict]) -> dict:
    """
    Derive a driver/delivery availability proxy from trip density.

    Groups trips by Maidenhead grid and hour of day. High trip density in
    a grid/time cell implies high driver availability during that window.

    Args:
        records: Normalized records from gig_normalize_export.

    Returns:
        {"cells": list of {grid, hour, trip_count, mean_fare_usd} sorted by count}
    """
    cells = collections.defaultdict(list)
    for r in records:
        grid = r.get("maidenhead")
        hour = r.get("hour_of_day")
        if grid and hour is not None:
            cells[(grid, hour)].append(r.get("fare_usd", 0))

    result = [
        {
            "grid": k[0],
            "hour": k[1],
            "trip_count": len(v),
            "mean_fare_usd": round(sum(v) / len(v), 2) if v else 0,
        }
        for k, v in cells.items()
    ]
    result.sort(key=lambda x: x["trip_count"], reverse=True)
    return {"cells": result}


# ---------------------------------------------------------------------------
# Network anomaly detection
# ---------------------------------------------------------------------------

def gig_network_anomaly_detect(
    records: list[dict],
    outage_events: list[dict],
    window_minutes: int = 30,
) -> dict:
    """
    Correlate gaps in gig trip activity with outage event timestamps.

    Args:
        records:        Normalized records from gig_normalize_export.
        outage_events:  List of {"timestamp": ISO str, "provider": str, "description": str}.
        window_minutes: Time window to consider a gap as correlated with an outage.

    Returns:
        {"correlations": list, "uncorrelated_gaps": int, "total_outages": int}
    """
    times = sorted(
        datetime.datetime.fromisoformat(r["start_time"])
        for r in records if r.get("start_time")
    )

    # Find gaps > 2x the median inter-trip interval
    if len(times) < 2:
        return {"correlations": [], "uncorrelated_gaps": 0, "total_outages": len(outage_events)}

    intervals = [(times[i + 1] - times[i]).total_seconds() / 60 for i in range(len(times) - 1)]
    median_interval = sorted(intervals)[len(intervals) // 2]
    gap_threshold = max(median_interval * 2, 15)

    gaps = [
        {"start": times[i], "end": times[i + 1], "gap_minutes": intervals[i]}
        for i, interval in enumerate(intervals)
        if interval > gap_threshold
    ]

    correlations = []
    window = datetime.timedelta(minutes=window_minutes)

    for outage in outage_events:
        try:
            ot = datetime.datetime.fromisoformat(outage["timestamp"].rstrip("Z"))
        except Exception:
            continue
        for gap in gaps:
            if gap["start"] - window <= ot <= gap["end"] + window:
                correlations.append({
                    "outage_timestamp": outage["timestamp"],
                    "outage_provider": outage.get("provider", "unknown"),
                    "outage_description": outage.get("description", ""),
                    "gap_start": gap["start"].isoformat(),
                    "gap_end": gap["end"].isoformat(),
                    "gap_minutes": round(gap["gap_minutes"], 1),
                })
                break

    return {
        "correlations": correlations,
        "uncorrelated_gaps": len(gaps) - len(correlations),
        "total_outages": len(outage_events),
        "gap_threshold_minutes": round(gap_threshold, 1),
    }


# ---------------------------------------------------------------------------
# Cuisine normalization
# ---------------------------------------------------------------------------

def gig_cuisine_normalize(
    records: list[dict],
    restaurant_field: str = "restaurant",
    cuisine_field: str | None = None,
) -> dict:
    """
    Classify raw restaurant names in a dataset to generic cuisine types.

    Operates on raw (pre-normalization) records if normalize_export has not
    already been called. Restaurant names are discarded after classification.

    Args:
        records:          List of dicts containing at least a restaurant field.
        restaurant_field: Column name for restaurant names.
        cuisine_field:    Optional column name for declared cuisine type.

    Returns:
        {"records": list with "cuisine_type" added, "cuisine_summary": dict}
    """
    result = []
    for r in records:
        name = str(r.get(restaurant_field, ""))
        declared = str(r.get(cuisine_field, "")) if cuisine_field else None
        cuisine = _classify_cuisine(name, declared)
        new_r = {k: v for k, v in r.items() if k not in (restaurant_field, cuisine_field)}
        new_r["cuisine_type"] = cuisine
        result.append(new_r)

    summary = dict(collections.Counter(r["cuisine_type"] for r in result))
    return {"records": result, "cuisine_summary": summary}


# ---------------------------------------------------------------------------
# Neighborhood demand pattern
# ---------------------------------------------------------------------------

def gig_neighborhood_demand_pattern(records: list[dict]) -> dict:
    """
    Summarize cuisine demand by neighborhood, day of week, and hour of day.

    Produces the pizza-on-Thursday / Chinese-on-Sunday style insight.
    All geographic data must already be at neighborhood/postcode level
    (i.e., records from gig_normalize_export).

    Args:
        records: Normalized records from gig_normalize_export.

    Returns:
        {"patterns": list of {neighborhood, cuisine_type, day_of_week, hour_of_day, count, total_usd}}
    """
    cells = collections.defaultdict(lambda: {"count": 0, "total_usd": 0.0})
    for r in records:
        hood = r.get("neighborhood") or r.get("suburb") or r.get("postcode") or "unknown"
        cuisine = r.get("cuisine_type", "other")
        dow = r.get("day_of_week", "unknown")
        hour = r.get("hour_of_day")
        key = (hood, cuisine, dow, hour)
        cells[key]["count"] += 1
        cells[key]["total_usd"] += r.get("fare_usd", 0)

    patterns = [
        {
            "neighborhood": k[0],
            "cuisine_type": k[1],
            "day_of_week": k[2],
            "hour_of_day": k[3],
            "count": v["count"],
            "total_usd": round(v["total_usd"], 2),
        }
        for k, v in cells.items()
    ]
    patterns.sort(key=lambda x: x["count"], reverse=True)
    return {"patterns": patterns}


# ---------------------------------------------------------------------------
# Restaurant time analysis
# ---------------------------------------------------------------------------

def gig_restaurant_time_analysis(records: list[dict]) -> dict:
    """
    Analyze busiest times by cuisine type, decoupled from trip/delivery details.

    Args:
        records: Normalized records from gig_normalize_export (cuisine_type required).

    Returns:
        {"by_cuisine": dict of cuisine_type -> {hour_of_day -> count, day_of_week -> count}}
    """
    by_cuisine: dict = collections.defaultdict(lambda: {
        "by_hour": collections.Counter(),
        "by_day": collections.Counter(),
        "total_trips": 0,
        "total_usd": 0.0,
    })

    for r in records:
        cuisine = r.get("cuisine_type", "other")
        hour = r.get("hour_of_day")
        dow = r.get("day_of_week")
        by_cuisine[cuisine]["total_trips"] += 1
        by_cuisine[cuisine]["total_usd"] += r.get("fare_usd", 0)
        if hour is not None:
            by_cuisine[cuisine]["by_hour"][str(hour)] += 1
        if dow:
            by_cuisine[cuisine]["by_day"][dow] += 1

    result = {}
    for cuisine, data in by_cuisine.items():
        result[cuisine] = {
            "total_trips": data["total_trips"],
            "total_usd": round(data["total_usd"], 2),
            "peak_hour": max(data["by_hour"], key=data["by_hour"].get) if data["by_hour"] else None,
            "peak_day": max(data["by_day"], key=data["by_day"].get) if data["by_day"] else None,
            "by_hour": dict(sorted(data["by_hour"].items(), key=lambda x: int(x[0]))),
            "by_day": dict(data["by_day"]),
        }

    return {"by_cuisine": result}
