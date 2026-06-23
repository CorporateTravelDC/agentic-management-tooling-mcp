"""
agentic/guardrails.py -- Safety rails for agentic workflows.

SR1: mutation_gate       -- Block any state-changing call without explicit confirmation.
SR2: model_routing_check -- Recommend model tier based on task complexity and budget.
     data_resolution_gate -- Hard block on fine geographic resolution for third-party data.
"""

import json
import datetime
from pathlib import Path
from config import get_state_dir, MAIDENHEAD_DEFAULT_PRECISION, MAIDENHEAD_GUARDRAIL_PRECISION


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def _audit(event: str, detail: dict) -> None:
    state_dir = get_state_dir(confirm=False)
    log_path = state_dir / "audit.jsonl"
    entry = {
        "ts": datetime.datetime.utcnow().isoformat() + "Z",
        "event": event,
        **detail,
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# SR1 -- Mutation gate
# ---------------------------------------------------------------------------

def mutation_gate(
    method: str,
    url: str,
    payload: dict | None = None,
    confirmed: bool = False,
    idempotent: bool = False,
) -> dict:
    """
    SR1: Safety rail for any state-changing API call.

    Must be called before firing any POST, PUT, PATCH, or DELETE against an
    external service. If confirmed=False, returns a structured challenge that
    the calling agent must resolve before proceeding.

    Args:
        method:     HTTP method (POST, PUT, PATCH, DELETE). GET is always allowed.
        url:        Target URL.
        payload:    Request body (optional, for logging only -- not transmitted).
        confirmed:  Must be True for the call to be allowed. Caller sets this
                    explicitly to signal deliberate intent.
        idempotent: True if re-sending is safe (e.g. PUT). Non-idempotent calls
                    (POST to a notification endpoint) are flagged in the log.

    Returns:
        {
          "allowed": bool,
          "method": str,
          "url": str,
          "idempotent": bool,
          "challenge": str | None,   # Present when allowed=False
          "intercepted_at": str,
        }
    """
    method = method.upper()
    safe_methods = {"GET", "HEAD", "OPTIONS"}

    if method in safe_methods:
        return {
            "allowed": True,
            "method": method,
            "url": url,
            "idempotent": True,
            "challenge": None,
            "intercepted_at": datetime.datetime.utcnow().isoformat() + "Z",
        }

    ts = datetime.datetime.utcnow().isoformat() + "Z"

    if not confirmed:
        _audit("SR1_INTERCEPT", {
            "method": method,
            "url": url,
            "idempotent": idempotent,
            "confirmed": False,
        })
        return {
            "allowed": False,
            "method": method,
            "url": url,
            "idempotent": idempotent,
            "challenge": (
                f"SR1: {method} {url} requires explicit confirmation. "
                f"Re-call with confirmed=True to proceed. "
                f"{'WARNING: this operation is not idempotent -- do not retry automatically.' if not idempotent else ''}"
            ).strip(),
            "intercepted_at": ts,
        }

    _audit("SR1_ALLOWED", {
        "method": method,
        "url": url,
        "idempotent": idempotent,
        "confirmed": True,
    })
    return {
        "allowed": True,
        "method": method,
        "url": url,
        "idempotent": idempotent,
        "challenge": None,
        "intercepted_at": ts,
    }


# ---------------------------------------------------------------------------
# SR2 -- Model routing check
# ---------------------------------------------------------------------------

_TASK_TIERS = {
    "classification": "tier_1",
    "extraction":     "tier_1",
    "summarization":  "tier_2",
    "rewriting":      "tier_2",
    "reasoning":      "tier_3",
    "generation":     "tier_3",
    "analysis":       "tier_3",
}

_TIER_LABELS = {
    "tier_1": "fast/cheap (e.g. haiku-class)",
    "tier_2": "mid-range (e.g. sonnet-class)",
    "tier_3": "capable (e.g. sonnet-class or above)",
    "tier_4": "frontier (e.g. opus-class)",
}


def model_routing_check(
    estimated_input_tokens: int,
    estimated_output_tokens: int,
    task_type: str,
    budget_remaining: float,
    force_tier: str | None = None,
) -> dict:
    """
    SR2: Recommend a model tier before an LLM call.

    Vendor-agnostic -- returns a tier label, not a specific model name.
    Map tier labels to your provider's models in your own config.

    Task types: classification, extraction, summarization, rewriting,
                reasoning, generation, analysis.

    Args:
        estimated_input_tokens:  Approximate input token count.
        estimated_output_tokens: Approximate output token count.
        task_type:               One of the supported task types above.
        budget_remaining:        Remaining budget in USD for this session.
        force_tier:              Override routing with a specific tier (optional).

    Returns:
        {
          "recommended_tier": str,
          "tier_description": str,
          "estimated_tokens": int,
          "block": bool,              # True if budget_remaining <= 0
          "reasoning": str,
        }
    """
    task_type = task_type.lower().strip()
    estimated_tokens = estimated_input_tokens + estimated_output_tokens

    if budget_remaining <= 0:
        _audit("SR2_BLOCK", {"task_type": task_type, "budget_remaining": budget_remaining})
        return {
            "recommended_tier": "block",
            "tier_description": "blocked -- budget exhausted",
            "estimated_tokens": estimated_tokens,
            "block": True,
            "reasoning": "budget_remaining is zero or negative. Reset or increase budget before proceeding.",
        }

    if force_tier and force_tier in _TIER_LABELS:
        return {
            "recommended_tier": force_tier,
            "tier_description": _TIER_LABELS[force_tier],
            "estimated_tokens": estimated_tokens,
            "block": False,
            "reasoning": f"Operator forced tier: {force_tier}.",
        }

    base_tier = _TASK_TIERS.get(task_type, "tier_3")

    # Upgrade tier if token count is large
    if estimated_tokens > 50_000 and base_tier == "tier_3":
        recommended = "tier_4"
        reason = f"Task type '{task_type}' at {estimated_tokens:,} tokens warrants frontier model."
    elif estimated_tokens > 100_000:
        recommended = "tier_4"
        reason = f"Token count {estimated_tokens:,} exceeds mid-range context comfort zone."
    else:
        recommended = base_tier
        reason = f"Task type '{task_type}' maps to {base_tier}."

    # Downgrade if budget is very tight
    if budget_remaining < 0.05 and recommended in ("tier_3", "tier_4"):
        recommended = "tier_2"
        reason += f" Downgraded to tier_2: budget_remaining ${budget_remaining:.4f} is low."

    _audit("SR2_ROUTE", {
        "task_type": task_type,
        "recommended": recommended,
        "estimated_tokens": estimated_tokens,
        "budget_remaining": budget_remaining,
    })

    return {
        "recommended_tier": recommended,
        "tier_description": _TIER_LABELS.get(recommended, recommended),
        "estimated_tokens": estimated_tokens,
        "block": False,
        "reasoning": reason,
    }


# ---------------------------------------------------------------------------
# Data resolution gate -- Maidenhead precision guardrail
# ---------------------------------------------------------------------------

def data_resolution_gate(
    requested_precision: int,
    data_source: str,
    own_data: bool = False,
    pre_sanitized: bool = False,
) -> dict:
    """
    Hard guardrail for geographic data resolution.

    Default maximum: 6-character Maidenhead (~4.6km x 2.3km, neighborhood level).

    Precision >= 8 (block level or finer) is blocked for third-party or
    unverified data. Both own_data=True AND pre_sanitized=True must be
    explicitly set by the operator to proceed at higher resolution.
    This is logged to the audit trail regardless of outcome.

    Args:
        requested_precision: Maidenhead chars (4, 6, 8, 10, or 12).
        data_source:         Brief description of the data source (for audit log).
        own_data:            Operator attests this is their own collected data.
        pre_sanitized:       Operator attests PII has been removed before import.

    Returns:
        {
          "allowed": bool,
          "requested_precision": int,
          "effective_precision": int,   # Precision that will be applied
          "block_reason": str | None,
          "attestation_required": list[str],
        }
    """
    if requested_precision not in (4, 6, 8, 10, 12):
        return {
            "allowed": False,
            "requested_precision": requested_precision,
            "effective_precision": MAIDENHEAD_DEFAULT_PRECISION,
            "block_reason": f"Invalid precision {requested_precision}. Valid values: 4, 6, 8, 10, 12.",
            "attestation_required": [],
        }

    if requested_precision < MAIDENHEAD_GUARDRAIL_PRECISION:
        _audit("GEO_GATE_PASS", {
            "requested_precision": requested_precision,
            "data_source": data_source,
        })
        return {
            "allowed": True,
            "requested_precision": requested_precision,
            "effective_precision": requested_precision,
            "block_reason": None,
            "attestation_required": [],
        }

    # Precision >= 8 -- check attestations
    missing = []
    if not own_data:
        missing.append("own_data=True (attest this is data you collected yourself)")
    if not pre_sanitized:
        missing.append("pre_sanitized=True (attest PII has been removed before import)")

    if missing:
        _audit("GEO_GATE_BLOCK", {
            "requested_precision": requested_precision,
            "data_source": data_source,
            "own_data": own_data,
            "pre_sanitized": pre_sanitized,
        })
        return {
            "allowed": False,
            "requested_precision": requested_precision,
            "effective_precision": MAIDENHEAD_DEFAULT_PRECISION,
            "block_reason": (
                f"Precision {requested_precision} chars requires full attestation. "
                f"Missing: {'; '.join(missing)}. "
                f"Falling back to {MAIDENHEAD_DEFAULT_PRECISION}-char resolution."
            ),
            "attestation_required": missing,
        }

    _audit("GEO_GATE_ATTESTED", {
        "requested_precision": requested_precision,
        "data_source": data_source,
        "own_data": True,
        "pre_sanitized": True,
    })
    return {
        "allowed": True,
        "requested_precision": requested_precision,
        "effective_precision": requested_precision,
        "block_reason": None,
        "attestation_required": [],
    }
