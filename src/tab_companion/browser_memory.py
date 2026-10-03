# SPDX-License-Identifier: MIT
"""Settings and status helpers for native Firefox memory limits."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re

MIN_LIMIT_DECI_GB = 10
MAX_LIMIT_DECI_GB = 100
DEFAULT_SETTINGS = {"enabled": False, "limit_deci_gb": 20}
FIREFOX_SCOPE = re.compile(r"^app-(?:gnome-)?org\.mozilla\.firefox-[0-9]+\.scope$")


def config_path() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(root) / "tab-companion" / "browser-memory.json"


def status_path() -> Path:
    root = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(root) / "tab-companion-browser-memory" / "status.json"


def valid_settings(value: object) -> dict | None:
    if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
        return None
    if set(value) == {"enabled", "limit_deci_gb"}:
        limit = value["limit_deci_gb"]
    elif (set(value) == {"enabled", "limit_gb"}
          and type(value["limit_gb"]) is int):
        # Migrate settings written by the initial whole-GB version.
        limit = value["limit_gb"] * 10
    else:
        return None
    if type(limit) is not int or not MIN_LIMIT_DECI_GB <= limit <= MAX_LIMIT_DECI_GB:
        return None
    return {"enabled": value["enabled"], "limit_deci_gb": limit}


def read_settings(path: Path | None = None) -> dict:
    try:
        value = json.loads((path or config_path()).read_text(encoding="utf-8"))
        parsed = valid_settings(value)
    except (OSError, ValueError, json.JSONDecodeError):
        parsed = None
    return parsed or dict(DEFAULT_SETTINGS)


def read_status(path: Path | None = None) -> dict | None:
    try:
        value = json.loads((path or status_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if (not isinstance(value, dict) or type(value.get("enabled")) is not bool
            or type(value.get("limit_deci_gb")) is not int
            or not MIN_LIMIT_DECI_GB <= value["limit_deci_gb"] <= MAX_LIMIT_DECI_GB
            or type(value.get("firefox_sessions")) is not int
            or value["firefox_sessions"] < 0
            or type(value.get("applied_sessions")) is not int
            or value["applied_sessions"] < 0):
        return None
    if value.get("error") is not None and not isinstance(value["error"], str):
        return None
    return value


def firefox_scope_names(output: str) -> list[str]:
    """Return only GNOME system-package Firefox app scopes, never Flatpaks."""
    names = set()
    for line in output.splitlines():
        fields = line.split()
        if fields and FIREFOX_SCOPE.fullmatch(fields[0]):
            names.add(fields[0])
    return sorted(names)
