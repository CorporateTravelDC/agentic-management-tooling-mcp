"""
gig_mobility/geo_utils.py -- Geographic primitives.

Maidenhead grid locator (same system used in amateur radio APRS),
UTM coordinates, and reverse geocoding via Nominatim (OpenStreetMap).

Resolution policy (enforced by data_resolution_gate in agentic/guardrails.py):
  Default max: 6-char Maidenhead (~4.6km x 2.3km, neighborhood level)
  Hard guardrail: 8+ chars requires own_data + pre_sanitized attestation
"""

import math
import time
import httpx
import maidenhead as mh
from config import NOMINATIM_URL, MAIDENHEAD_DEFAULT_PRECISION


# ---------------------------------------------------------------------------
# Nominatim rate-limit: 1 request per second (OSM policy)
_last_nominatim_call: float = 0.0


def _nominatim_get(lat: float, lon: float) -> dict:
    global _last_nominatim_call
    elapsed = time.monotonic() - _last_nominatim_call
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    _last_nominatim_call = time.monotonic()

    with httpx.Client(timeout=10.0) as client:
        resp = client.get(
            NOMINATIM_URL,
            params={"lat": lat, "lon": lon, "format": "json", "zoom": 14},
            headers={"User-Agent": "agentic-management-tooling-mcp/0.2"},
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------

def latlon_to_maidenhead(
    lat: float,
    lon: float,
    precision: int = MAIDENHEAD_DEFAULT_PRECISION,
    own_data: bool = False,
    pre_sanitized: bool = False,
) -> dict:
    """
    Convert a lat/lon coordinate to a Maidenhead grid locator.

    Default precision: 6 chars (~4.6km x 2.3km, neighborhood level).
    Precision 8, 10, or 12 requires own_data=True AND pre_sanitized=True.
    If attestation is missing, falls back to 6-char precision.

    Args:
        lat:           Latitude in decimal degrees.
        lon:           Longitude in decimal degrees.
        precision:     4, 6, 8, 10, or 12.
        own_data:      Attest data is operator-owned (required for precision >= 8).
        pre_sanitized: Attest PII has been removed (required for precision >= 8).

    Returns:
        {"grid": str, "precision": int, "lat": float, "lon": float,
         "capped": bool, "cap_reason": str | None}
    """
    if precision not in (4, 6, 8, 10, 12):
        precision = MAIDENHEAD_DEFAULT_PRECISION

    capped = False
    cap_reason = None

    if precision >= 8 and not (own_data and pre_sanitized):
        precision = MAIDENHEAD_DEFAULT_PRECISION
        capped = True
        cap_reason = (
            "Precision >= 8 requires own_data=True and pre_sanitized=True. "
            "Capped at 6-char resolution."
        )

    grid = mh.to_maiden(lat, lon, precision // 2)
    return {
        "grid": grid,
        "precision": len(grid),
        "lat": lat,
        "lon": lon,
        "capped": capped,
        "cap_reason": cap_reason,
    }


def maidenhead_to_bbox(grid_square: str) -> dict:
    """
    Return the bounding box (SW and NE corners) for a Maidenhead grid square.

    Args:
        grid_square: Maidenhead locator string (4, 6, or 8 chars).

    Returns:
        {"grid": str, "sw_lat": float, "sw_lon": float,
         "ne_lat": float, "ne_lon": float, "center_lat": float, "center_lon": float}
    """
    lat, lon = mh.to_location(grid_square)
    precision = len(grid_square)

    # Width/height of each precision tier
    lon_widths = {2: 20.0, 4: 2.0, 6: 0.0833, 8: 0.00833}
    lat_widths = {2: 10.0, 4: 1.0, 6: 0.0417, 8: 0.00417}

    dlon = lon_widths.get(precision, 2.0)
    dlat = lat_widths.get(precision, 1.0)

    return {
        "grid": grid_square,
        "sw_lat": round(lat - dlat / 2, 6),
        "sw_lon": round(lon - dlon / 2, 6),
        "ne_lat": round(lat + dlat / 2, 6),
        "ne_lon": round(lon + dlon / 2, 6),
        "center_lat": round(lat, 6),
        "center_lon": round(lon, 6),
    }


def latlon_to_utm(lat: float, lon: float) -> dict:
    """
    Convert lat/lon to UTM coordinates (zone, easting, northing).

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.

    Returns:
        {"zone_number": int, "zone_letter": str, "easting": float,
         "northing": float, "lat": float, "lon": float}
    """
    zone_number = int((lon + 180) / 6) + 1

    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    lon0_rad = math.radians((zone_number - 1) * 6 - 180 + 3)

    a = 6378137.0
    f = 1 / 298.257223563
    b = a * (1 - f)
    e2 = 1 - (b / a) ** 2
    n = (a - b) / (a + b)

    A = a / (1 + n) * (1 + n**2 / 4 + n**4 / 64)
    alpha = [
        n / 2 - 2 * n**2 / 3 + 5 * n**3 / 16,
        13 * n**2 / 48 - 3 * n**3 / 5,
        61 * n**3 / 240,
    ]

    t = math.sinh(math.atanh(math.sin(lat_rad))
                  - 2 * math.sqrt(e2) / (1 + math.sqrt(e2))
                  * math.atanh(2 * math.sqrt(e2) * math.sin(lat_rad) / (1 + e2 * math.sin(lat_rad)**2 + 1)))
    xi_p = math.atan(t / math.cos(lon_rad - lon0_rad))
    eta_p = math.atanh(math.sin(lon_rad - lon0_rad) / math.sqrt(1 + t**2))

    xi = xi_p + sum(alpha[j - 1] * math.sin(2 * j * xi_p) * math.cosh(2 * j * eta_p) for j in range(1, 4))
    eta = eta_p + sum(alpha[j - 1] * math.cos(2 * j * xi_p) * math.sinh(2 * j * eta_p) for j in range(1, 4))

    k0 = 0.9996
    E = k0 * A * eta + 500000
    N = k0 * A * xi + (0 if lat >= 0 else 10000000)

    letters = "CDEFGHJKLMNPQRSTUVWX"
    lat_idx = int((lat + 80) / 8)
    zone_letter = letters[min(lat_idx, len(letters) - 1)]

    return {
        "zone_number": zone_number,
        "zone_letter": zone_letter,
        "easting": round(E, 2),
        "northing": round(N, 2),
        "lat": lat,
        "lon": lon,
    }


def reverse_geocode(lat: float, lon: float) -> dict:
    """
    Return neighborhood-level location data for a lat/lon point.

    Uses Nominatim (OpenStreetMap). Rate-limited to 1 request/second.
    Results contain neighborhood, suburb, postcode, city, and state.
    Street-level data is deliberately excluded.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.

    Returns:
        {"lat": float, "lon": float, "neighborhood": str|None,
         "suburb": str|None, "postcode": str|None, "city": str|None,
         "state": str|None, "country": str|None, "error": str|None}
    """
    try:
        data = _nominatim_get(lat, lon)
        addr = data.get("address", {})
        return {
            "lat": lat,
            "lon": lon,
            "neighborhood": addr.get("neighbourhood") or addr.get("neighborhood"),
            "suburb": addr.get("suburb") or addr.get("quarter"),
            "postcode": addr.get("postcode"),
            "city": addr.get("city") or addr.get("town") or addr.get("village"),
            "state": addr.get("state"),
            "country": addr.get("country_code", "").upper() or None,
            "error": None,
        }
    except Exception as exc:
        return {
            "lat": lat, "lon": lon,
            "neighborhood": None, "suburb": None, "postcode": None,
            "city": None, "state": None, "country": None,
            "error": str(exc),
        }
