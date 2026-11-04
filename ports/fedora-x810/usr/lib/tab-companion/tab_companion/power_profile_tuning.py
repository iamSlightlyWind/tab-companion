# SPDX-License-Identifier: MIT
"""Settings for per-profile CPU/GPU limits on Fedora X810."""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_PATH = Path("/etc/x810-power-profile-tuning.json")
PROFILE_IDS = ("power-saver", "balanced", "performance")
MIN_MAX_PERCENT = 30
MAX_MAX_PERCENT = 100

_DEFAULT_PROFILES = {
    "power-saver": {"max_percent": 55, "cpu_boost": False, "gpu_boost": False},
    "balanced": {"max_percent": 75, "cpu_boost": False, "gpu_boost": False},
    "performance": {"max_percent": 100, "cpu_boost": True, "gpu_boost": True},
}
DEFAULT_SETTINGS = {
    "version": 1,
    "profiles": {name: dict(settings) for name, settings in _DEFAULT_PROFILES.items()},
}


def valid_settings(value: object) -> dict | None:
    if (not isinstance(value, dict) or set(value) != {"version", "profiles"}
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["profiles"], dict)
            or set(value["profiles"]) != set(PROFILE_IDS)):
        return None
    profiles = {}
    for name in PROFILE_IDS:
        settings = value["profiles"][name]
        if (not isinstance(settings, dict)
                or set(settings) != {"max_percent", "cpu_boost", "gpu_boost"}
                or type(settings["max_percent"]) is not int
                or not MIN_MAX_PERCENT <= settings["max_percent"] <= MAX_MAX_PERCENT
                or type(settings["cpu_boost"]) is not bool
                or type(settings["gpu_boost"]) is not bool):
            return None
        profiles[name] = dict(settings)
    return {"version": 1, "profiles": profiles}


def read_settings(path: Path = CONFIG_PATH) -> dict:
    try:
        parsed = valid_settings(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        parsed = None
    if parsed is None:
        return {"version": 1, "profiles": {
            name: dict(settings) for name, settings in DEFAULT_SETTINGS["profiles"].items()
        }}
    return parsed
