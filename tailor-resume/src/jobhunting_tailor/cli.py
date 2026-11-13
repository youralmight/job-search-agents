from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .pipeline import master_only, run


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a one-page résumé tailored to the JD")
    parser.add_argument("job_json", type=Path)
    output_root = os.environ.get("JOBHUNTING_TAILOR_OUTPUT")
    parser.add_argument("--output-root", type=Path, default=Path(output_root) if output_root else None)
    parser.add_argument("--master", action="store_true", help="Do not tailor; output only the master with the tracked link")
    args = parser.parse_args()
    if not args.output_root:
        parser.error("Set JOBHUNTING_TAILOR_OUTPUT or pass --output-root")
    print((master_only if args.master else run)(json.loads(args.job_json.read_text()), args.output_root.resolve()))
