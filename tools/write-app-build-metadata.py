#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Write the build provenance record inside a staged app package."""

import json
import os
import re
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: tools/write-app-build-metadata.py STAGING_ROOT")
    root = Path(sys.argv[1])
    try:
        run_id = int(os.environ.get("APP_BUILD_RUN_ID", "0"))
        run_number = int(os.environ.get("APP_BUILD_RUN_NUMBER", "0"))
    except ValueError as exc:
        raise SystemExit("APP_BUILD_RUN_ID and APP_BUILD_RUN_NUMBER must be integers") from exc
    if run_id < 0 or run_number < 0:
        raise SystemExit("Build identifiers cannot be negative")
    commit = os.environ.get("APP_BUILD_HEAD_SHA", "local")
    branch = os.environ.get("APP_BUILD_BRANCH", "local")
    if commit != "local" and not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise SystemExit("APP_BUILD_HEAD_SHA must be a hexadecimal commit SHA")
    if not branch or len(branch) > 255 or any(ord(ch) < 32 for ch in branch):
        raise SystemExit("APP_BUILD_BRANCH is invalid")

    destination = root / "usr/share/tab-companion/app-build.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({
        "run_id": run_id,
        "run_number": run_number,
        "head_sha": commit,
        "branch": branch,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {destination}")


if __name__ == "__main__":
    main()
