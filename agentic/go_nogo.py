"""
agentic/go_nogo.py -- Multi-factor go/no-go readiness scoring.

Generalizes the CPS (Critical Predictability State) pattern to any domain:
HEMS dispatch, event readiness, vehicle availability, staffing checks,
service launch gates, or any composite go/no-go decision.
"""


def readiness_score(factors: list[dict]) -> dict:
    """
    Compute a composite go/no-go score from a list of weighted factors.

    Each factor specifies a current value and a threshold. The tool determines
    whether the factor passes or fails and combines weighted results into a
    composite score and label.

    Args:
        factors: List of factor descriptors:
          {
            "name": str,             # Display name
            "value": float,          # Current observed value
            "threshold": float,      # Pass/fail boundary
            "weight": float,         # Relative importance (e.g. 1.0)
            "invert": bool,          # If True, value must be <= threshold to pass
                                     # If False (default), value must be >= threshold
            "marginal_band": float,  # Optional: width of marginal zone below threshold
                                     # e.g. marginal_band=0.1 means value within 10%
                                     # below threshold is MARGINAL not FAIL
          }

    Returns:
        {
          "label": "GO" | "MARGINAL" | "NO-GO",
          "score": float,               # 0.0 (worst) to 1.0 (best)
          "total_weight": float,
          "passing_weight": float,
          "factors": list of per-factor results,
        }

    Examples:
        # HEMS weather check
        factors = [
            {"name": "ceiling_ft", "value": 1500, "threshold": 1000, "weight": 2.0},
            {"name": "visibility_sm", "value": 3.0, "threshold": 2.0, "weight": 2.0},
            {"name": "wind_kt", "value": 18, "threshold": 25, "weight": 1.0, "invert": True},
        ]

        # Venue readiness check
        factors = [
            {"name": "staff_confirmed", "value": 8, "threshold": 6, "weight": 1.5},
            {"name": "setup_pct", "value": 0.85, "threshold": 0.90, "weight": 1.0,
             "marginal_band": 0.10},
        ]
    """
    if not factors:
        return {
            "label": "NO-GO",
            "score": 0.0,
            "total_weight": 0.0,
            "passing_weight": 0.0,
            "factors": [],
        }

    results = []
    total_weight = 0.0
    passing_weight = 0.0
    has_fail = False
    has_marginal = False

    for f in factors:
        name = f.get("name", "unnamed")
        value = float(f.get("value", 0))
        threshold = float(f.get("threshold", 0))
        weight = float(f.get("weight", 1.0))
        invert = bool(f.get("invert", False))
        marginal_band = float(f.get("marginal_band", 0))

        total_weight += weight

        if invert:
            passes = value <= threshold
            in_marginal = (not passes) and (value <= threshold + threshold * marginal_band) if marginal_band else False
        else:
            passes = value >= threshold
            in_marginal = (not passes) and (value >= threshold - threshold * marginal_band) if marginal_band else False

        if passes:
            status = "OK"
            passing_weight += weight
        elif in_marginal:
            status = "MARGINAL"
            has_marginal = True
            passing_weight += weight * 0.5
        else:
            status = "FAIL"
            has_fail = True

        results.append({
            "name": name,
            "value": value,
            "threshold": threshold,
            "invert": invert,
            "weight": weight,
            "status": status,
        })

    score = round(passing_weight / total_weight, 3) if total_weight > 0 else 0.0

    if has_fail:
        label = "NO-GO"
    elif has_marginal or score < 0.85:
        label = "MARGINAL"
    else:
        label = "GO"

    return {
        "label": label,
        "score": score,
        "total_weight": round(total_weight, 3),
        "passing_weight": round(passing_weight, 3),
        "factors": results,
    }
