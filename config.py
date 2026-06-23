"""
config.py -- Runtime configuration for agentic-management-tooling-mcp.

AGENTIC_MCP_STATE_DIR must be set in the environment before the server
starts. No default is provided. On first run the server confirms the
path and writes a sentinel file before any tool is registered.

Public API base URLs require no credentials.
"""

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# State directory -- required, no default
# ---------------------------------------------------------------------------

_STATE_DIR_ENV = "AGENTIC_MCP_STATE_DIR"


def get_state_dir(confirm: bool = True) -> Path:
    """
    Return the operator-configured state directory.

    Exits with a clear message if the env var is not set.
    On first access, prints a trust-confirmation prompt and waits
    for acknowledgement (unless confirm=False, e.g. in tests).
    """
    raw = os.environ.get(_STATE_DIR_ENV, "").strip()
    if not raw:
        print(
            f"[FAIL] {_STATE_DIR_ENV} is not set.\n"
            f"       Set it before starting the server:\n"
            f"         export {_STATE_DIR_ENV}=/path/to/your/state/directory\n"
            f"       The server will store session state, watchlists, budget\n"
            f"       accumulators, snapshots, and audit logs at that path.",
            file=sys.stderr,
        )
        sys.exit(1)

    state_dir = Path(raw).expanduser().resolve()
    sentinel = state_dir / ".agentic_mcp_trusted"

    if not sentinel.exists():
        if confirm:
            print(
                f"\n[TRUST] State directory: {state_dir}\n"
                f"\n"
                f"  The server will read and write files at this location,\n"
                f"  including session snapshots, budget accumulators,\n"
                f"  watchlists, idempotency records, and an audit log.\n"
                f"\n"
                f"  Confirm you trust this directory for agentic state storage.\n"
                f"  Type 'yes' to continue, anything else to abort: ",
                end="",
                flush=True,
            )
            answer = input().strip().lower()
            if answer != "yes":
                print("[FAIL] Aborted. Set a trusted state directory and retry.")
                sys.exit(1)

        state_dir.mkdir(parents=True, exist_ok=True)
        sentinel.touch()
        print(f"[OK] State directory trusted and initialized: {state_dir}")
    else:
        state_dir.mkdir(parents=True, exist_ok=True)

    return state_dir


# ---------------------------------------------------------------------------
# Public API endpoints -- no credentials required
# ---------------------------------------------------------------------------

AIRPLANES_LIVE_URL = "https://api.airplanes.live/v2"
AMTRAK_URL = "https://api-v3.amtraker.com/v3"
AVIATIONWEATHER_URL = "https://aviationweather.gov/api/data"
NWS_URL = "https://api.weather.gov"
FAA_TFR_URL = "https://tfr.faa.gov/tfr2/xml_files/fr.xml"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"

# ---------------------------------------------------------------------------
# Maidenhead resolution policy
# ---------------------------------------------------------------------------

# Default maximum resolution for all geographic tools.
# Neighborhood-level (~4.6km x 2.3km). Never exceeded without attestation.
MAIDENHEAD_DEFAULT_PRECISION = 6

# Hard guardrail threshold. 8 chars or higher requires own_data + pre_sanitized.
MAIDENHEAD_GUARDRAIL_PRECISION = 8
