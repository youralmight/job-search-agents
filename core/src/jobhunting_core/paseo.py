"""Paseo agent tabs: every workflow starts and waits for its agents through these."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _env() -> dict[str, str]:
    # Drop a calling Paseo agent's identity: the CLI would put new agents in that agent's workspace.
    return {key: value for key, value in os.environ.items() if key not in ("PASEO_AGENT_ID", "PASEO_AGENT_CWD")}


def paseo(*args: str) -> str:
    result = subprocess.run(["paseo", *args], capture_output=True, text=True, timeout=120, env=_env())
    if result.returncode:
        raise RuntimeError(f"paseo {args[0]} failed: exit {result.returncode}; {result.stderr[-400:]}")
    return result.stdout


def create_workspace(path: Path, title: str) -> str:
    return json.loads(paseo("workspace", "create", "--path", str(path), "--isolation", "local", "--title", title, "--json"))["workspaceId"]


def wait_for_agent(agent_id: str, seconds: float) -> str:
    """Wait until the agent stops, then archive it (interrupting it past its time limit).
    Returns Paseo's status: idle, timeout, permission or error."""
    # `paseo wait` is the only long call: paseo() keeps its short limit for everything else.
    result = subprocess.run(["paseo", "wait", agent_id, "--timeout", f"{max(1, int(seconds))}s", "--json"],
                            capture_output=True, text=True, timeout=max(1, seconds) + 60, env=_env())
    status = json.loads(result.stdout)["status"] if result.returncode == 0 else "error"
    # An idle agent keeps its claude process (~0.3 GB) until archived; a Discover night left 90 of them (2026-11-12).
    paseo("archive", "--force", agent_id)
    return status
