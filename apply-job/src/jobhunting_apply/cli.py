"""Worker CLI over one Jobs row; prints one JSON object, or an error on stderr with exit 1.

    echo '{"to":"Closed","note":"page shows This job is no longer available"}' | uv run jobhunting-apply transition --job-id job-abc
"""
from __future__ import annotations

import argparse
import json
import sys

from jobhunting_core import sheet
from jobhunting_core.google_api import build_service


def _stdin_object() -> dict:
    payload = json.loads(sys.stdin.read())
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object on stdin")
    return payload


def _transition(service, args):
    payload = _stdin_object()
    if set(payload) - {"to", "note", "from"} or not payload.get("to") or not str(payload.get("note", "")).strip():
        raise ValueError('expected {"to": state, "note": one-line explanation, "from"?: current state}')
    return sheet.transition(service, args.job_id, payload["to"], payload["note"],
                            expect=(payload["from"],) if payload.get("from") else None)


COMMANDS = {
    "get-job": ("read one job row", lambda service, args: sheet.get_job(service, args.job_id)),
    "update-job": ("update ordinary columns (not state/log/progress); JSON changes on stdin",
                   lambda service, args: sheet.update_job(service, args.job_id, _stdin_object())),
    "transition": ('move one job along the state machine; JSON {"to","note","from"?} on stdin', _transition),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobhunting-apply")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, (help_text, _) in COMMANDS.items():
        subparsers.add_parser(name, help=help_text).add_argument("--job-id", required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(COMMANDS[args.command][1](build_service(), args), ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report and exit non-zero
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
