import json
import subprocess

from jobhunting_core import paseo as runtime


def fake_paseo(monkeypatch, status, returncode=0):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert "PASEO_AGENT_ID" not in kwargs["env"]
        return subprocess.CompletedProcess(command, returncode, json.dumps({"status": status}), "")

    monkeypatch.setenv("PASEO_AGENT_ID", "caller")
    monkeypatch.setattr(runtime.subprocess, "run", run)
    return calls


def test_finished_agent_is_archived(monkeypatch):
    calls = fake_paseo(monkeypatch, "idle")
    assert runtime.wait_for_agent("agent", 90) == "idle"
    assert calls == [["paseo", "wait", "agent", "--timeout", "90s", "--json"], ["paseo", "archive", "--force", "agent"]]


def test_agent_past_its_limit_is_interrupted_and_archived(monkeypatch):
    calls = fake_paseo(monkeypatch, "timeout")
    assert runtime.wait_for_agent("agent", 90) == "timeout"
    assert calls[-1] == ["paseo", "archive", "--force", "agent"]


def test_failed_wait_counts_as_error_and_archives_the_agent(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1 if command[1] == "wait" else 0, "", "boom")

    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert runtime.wait_for_agent("agent", 90) == "error"
    assert calls[-1] == ["paseo", "archive", "--force", "agent"]
