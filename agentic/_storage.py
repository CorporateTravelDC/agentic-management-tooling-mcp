"""
agentic/_storage.py -- Cross-process-safe JSON state storage.

Multiple MCP clients (Claude Desktop, Claude Code remote-control, Cowork/
mobile dispatch) can each spawn their own instance of this server against
the same AGENTIC_MCP_STATE_DIR at once. Every named JSON state file gets
an exclusive advisory lock (via a `<path>.lock` sidecar) held for the full
read-modify-write critical section, and writes go to a temp file in the
same directory, fsynced, then atomically renamed over the target -- so a
concurrent reader never observes a partial write, and two concurrent
writers never clobber each other's update.
"""

import contextlib
import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable


@contextlib.contextmanager
def locked(path: Path):
    """Hold an exclusive advisory lock scoped to `path` for the duration
    of the `with` block. Blocks until acquired. Keep the critical section
    to in-memory JSON load/mutate/save -- no network calls inside it."""
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def atomic_write_json(path: Path, data: Any) -> None:
    """Write `data` as JSON to `path` atomically: temp file in the same
    directory, fsynced, then os.replace() over the target. Callers that
    need read-modify-write safety (not just write atomicity) must call
    this from inside a `locked(path)` block."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
