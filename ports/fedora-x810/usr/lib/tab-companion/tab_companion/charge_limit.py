# SPDX-License-Identifier: MIT
"""Read-only client for the X810 charge-limit support helper."""

from __future__ import annotations

import json
import subprocess


HELPER = "/usr/libexec/x810-charge-limit"
MIN_LIMIT_PERCENT = 50
MAX_LIMIT_PERCENT = 100
DEFAULT_LIMIT_PERCENT = 100


def parse_status(output: str) -> dict | None:
    """Validate the support helper's small JSON status contract."""
    try:
        value = json.loads(output)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict) or type(value.get("supported")) is not bool:
        return None
    if not value["supported"]:
        return {"supported": False}
    start = value.get("start_percent")
    end = value.get("end_percent")
    if (type(start) is not int or type(end) is not int
            or not 0 <= start <= 100 or not MIN_LIMIT_PERCENT <= end <= 100
            or start > end):
        return None
    return {"supported": True, "start_percent": start, "end_percent": end}


def read_status() -> dict | None:
    try:
        result = subprocess.run(
            [HELPER, "get"], check=True, capture_output=True, text=True, timeout=3
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_status(result.stdout)
