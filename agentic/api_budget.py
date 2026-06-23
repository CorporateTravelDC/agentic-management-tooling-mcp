"""
agentic/api_budget.py -- Vendor-agnostic API cost controls.

Tracks cumulative spend per session against a configurable ceiling.
Pricing data comes from a user-maintained registry file -- no rates
are hardcoded here. Supports any provider: Anthropic, OpenAI,
Perplexity, self-hosted, or custom.

Registry file: $AGENTIC_MCP_STATE_DIR/pricing_registry.json
Template:      pricing_registry.template.json (copy and fill in)
"""

import json
import datetime
from pathlib import Path
from config import get_state_dir

_REGISTRY_FILENAME = "pricing_registry.json"
_BUDGET_FILENAME = "budget_state.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _registry_path() -> Path:
    return get_state_dir(confirm=False) / _REGISTRY_FILENAME


def _budget_path() -> Path:
    return get_state_dir(confirm=False) / _BUDGET_FILENAME


def _load_registry() -> dict:
    path = _registry_path()
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    return data.get("providers", {})


def _load_budget() -> dict:
    path = _budget_path()
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def _save_budget(state: dict) -> None:
    with open(_budget_path(), "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# api_cost_estimate
# ---------------------------------------------------------------------------

def api_cost_estimate(
    provider: str,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
) -> dict:
    """
    Estimate the cost of a single LLM API call from the pricing registry.

    Provider and model_id must match keys in pricing_registry.json.
    Returns zero-cost estimate with a warning if the model is not found.

    Args:
        provider:      Provider key (e.g. 'anthropic', 'openai', 'perplexity', 'custom').
        model_id:      Model identifier as it appears in the pricing registry.
        input_tokens:  Number of input tokens.
        output_tokens: Number of output tokens.

    Returns:
        {
          "provider": str,
          "model_id": str,
          "input_tokens": int,
          "output_tokens": int,
          "estimated_cost_usd": float,
          "registry_found": bool,
          "warning": str | None,
        }
    """
    registry = _load_registry()
    provider_data = registry.get(provider, {})
    model_data = provider_data.get(model_id)

    if not model_data:
        return {
            "provider": provider,
            "model_id": model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": 0.0,
            "registry_found": False,
            "warning": (
                f"Model '{provider}/{model_id}' not found in pricing registry. "
                f"Add it with pricing_registry_update or edit pricing_registry.json. "
                f"Cost estimate is $0.00 until registry is populated."
            ),
        }

    input_cost = (input_tokens / 1_000_000) * model_data.get("input_per_mtok", 0)
    output_cost = (output_tokens / 1_000_000) * model_data.get("output_per_mtok", 0)
    total = input_cost + output_cost

    return {
        "provider": provider,
        "model_id": model_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": round(total, 8),
        "registry_found": True,
        "warning": None,
    }


# ---------------------------------------------------------------------------
# api_budget_check
# ---------------------------------------------------------------------------

def api_budget_check(
    session_id: str,
    cost_to_add: float,
    ceiling: float,
) -> dict:
    """
    Check and update cumulative spend for a session against a ceiling.

    Accumulates cost_to_add into the session total. Returns whether the
    resulting total is within the ceiling. Does NOT block the call --
    the calling agent decides whether to proceed.

    Args:
        session_id:   Arbitrary string identifying the session or workflow.
        cost_to_add:  Cost of the operation about to be performed, in USD.
        ceiling:      Maximum allowed cumulative spend for this session, in USD.

    Returns:
        {
          "session_id": str,
          "allowed": bool,
          "cost_added": float,
          "cumulative_usd": float,
          "ceiling_usd": float,
          "remaining_usd": float,
          "updated_at": str,
        }
    """
    state = _load_budget()
    session = state.get(session_id, {"cumulative_usd": 0.0, "created_at": datetime.datetime.utcnow().isoformat() + "Z"})

    new_total = session["cumulative_usd"] + cost_to_add
    allowed = new_total <= ceiling

    session["cumulative_usd"] = round(new_total, 8)
    session["updated_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    state[session_id] = session
    _save_budget(state)

    return {
        "session_id": session_id,
        "allowed": allowed,
        "cost_added": round(cost_to_add, 8),
        "cumulative_usd": round(new_total, 8),
        "ceiling_usd": ceiling,
        "remaining_usd": round(max(0.0, ceiling - new_total), 8),
        "updated_at": session["updated_at"],
    }


# ---------------------------------------------------------------------------
# api_budget_reset
# ---------------------------------------------------------------------------

def api_budget_reset(session_id: str) -> dict:
    """
    Reset the cumulative cost accumulator for a session.

    Args:
        session_id: Session identifier to reset.

    Returns:
        {"session_id": str, "reset": bool, "previous_cumulative_usd": float}
    """
    state = _load_budget()
    previous = state.pop(session_id, {}).get("cumulative_usd", 0.0)
    _save_budget(state)
    return {
        "session_id": session_id,
        "reset": True,
        "previous_cumulative_usd": round(previous, 8),
    }


# ---------------------------------------------------------------------------
# pricing_registry_update
# ---------------------------------------------------------------------------

def pricing_registry_update(
    provider: str,
    model_id: str,
    input_per_mtok: float,
    output_per_mtok: float,
    notes: str = "",
) -> dict:
    """
    Add or update a model entry in the pricing registry at runtime.

    Creates pricing_registry.json in the state directory if it does not exist.
    Does not modify pricing_registry.template.json.

    Args:
        provider:         Provider key (e.g. 'anthropic', 'openai', 'custom').
        model_id:         Model identifier string.
        input_per_mtok:   Input cost in USD per million tokens.
        output_per_mtok:  Output cost in USD per million tokens.
        notes:            Optional free-text notes (pricing source, date, etc.).

    Returns:
        {"provider": str, "model_id": str, "updated": bool}
    """
    path = _registry_path()
    if path.exists():
        with open(path) as f:
            data = json.load(f)
    else:
        data = {"providers": {}}

    providers = data.setdefault("providers", {})
    provider_block = providers.setdefault(provider, {})
    provider_block[model_id] = {
        "input_per_mtok": input_per_mtok,
        "output_per_mtok": output_per_mtok,
        "notes": notes,
        "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
    }

    with open(path, "w") as f:
        json.dump(data, f, indent=2)

    return {"provider": provider, "model_id": model_id, "updated": True}
