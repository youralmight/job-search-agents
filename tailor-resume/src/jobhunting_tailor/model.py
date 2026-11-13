from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import jobhunting_core  # noqa: F401 - loads the repository .env

# Model for every Tailor call, set in the repository .env
MODEL = os.environ["JOBHUNTING_TAILOR_MODEL"]
# Marks a failure of the model service itself (quota, Paseo down), not of the tailoring: callers retry later.
# Apply matches this exact text in Tailor's stderr; change both together.
UNAVAILABLE = "model unavailable"
TEMPORARY = ("usage_limit", "session limit", "ECONNREFUSED", UNAVAILABLE)
# Each call is one tab in this Paseo workspace; Apply's prepare sets it, a standalone run creates its own.
WORKSPACE_ENV = "JOBHUNTING_PASEO_WORKSPACE"
ROOT = Path(__file__).resolve().parents[2]
# Tab titles start with the job being tailored; pipeline.run sets it.
JOB_LABEL = "Tailor"


def _paseo(*args: str, timeout: int = 120) -> str:
    # Drop a calling Paseo agent's identity: the CLI would put new tabs in that agent's workspace.
    env = {key: value for key, value in os.environ.items() if key not in ("PASEO_AGENT_ID", "PASEO_AGENT_CWD")}
    try:
        result = subprocess.run(["paseo", *args], capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError as error:
        raise RuntimeError(f"{UNAVAILABLE}: paseo command not found ({error})") from error
    if result.returncode:
        raise RuntimeError(f"paseo {args[0]} failed: exit code {result.returncode}; {(result.stderr or result.stdout)[-1000:]}")
    return result.stdout


def workspace() -> str:
    if not os.environ.get(WORKSPACE_ENV):
        os.environ[WORKSPACE_ENV] = json.loads(_paseo("workspace", "create", "--path", str(ROOT), "--isolation", "local",
                                                      "--title", f"tailor · {datetime.now():%m-%d %H:%M:%S}", "--json"))["workspaceId"]
    return os.environ[WORKSPACE_ENV]


def model_json(model: str, prompt: str, timeout: int = 900, step: str = "", schema: dict | None = None) -> dict:
    """One Paseo tab that must answer with a JSON object; retried once."""
    # The prompt carries the JD, résumé and career facts: too long for one command-line argument, so it goes in a file.
    with tempfile.TemporaryDirectory(prefix="tailor-prompt-") as directory:
        path = Path(directory) / "prompt.md"
        path.write_text(prompt, encoding="utf-8")
        instruction = f"Your task is in the file {path}. Read it all and do what it says; it tells you which files and commands to use."
        return _run(model, instruction, timeout, step, schema or {"type": "object"})


def _run(model: str, prompt: str, timeout: int, step: str, schema: dict) -> dict:
    for attempt in range(2):
        try:
            output = _paseo("run", "--json", "--provider", model, "--mode", "bypassPermissions",
                            "--workspace", workspace(), "--cwd", str(ROOT), "--title", f"{JOB_LABEL} · {step}" if step else JOB_LABEL, "--label", "app=tailor-resume",
                            "--wait-timeout", f"{timeout}s", "--output-schema", json.dumps(schema), prompt, timeout=timeout + 60)
            return json.loads(output[output.index("{"):output.rindex("}") + 1])
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
            if any(marker in str(error) for marker in TEMPORARY):
                raise RuntimeError(f"{UNAVAILABLE} ({model}): {error}") from error
            if attempt == 1:
                raise RuntimeError(f"Tailor model call failed ({model}): {error}") from error
    raise AssertionError("unreachable")
