# SPDX-License-Identifier: MIT
"""Read-only client helpers for the Fedora X810 thermal-limit service."""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_PATH = Path("/etc/x810-thermal-limit.json")
STATUS_PATH = Path("/run/x810-thermal-limit/status.json")
MIN_THRESHOLD_C = 50
MAX_THRESHOLD_C = 85
DEFAULT_SETTINGS = {"enabled": True, "threshold_c": 55}


def valid_settings(value: object) -> dict | None:
    if (not isinstance(value, dict) or set(value) != {"enabled", "threshold_c"}
            or type(value["enabled"]) is not bool
            or type(value["threshold_c"]) is not int
            or not MIN_THRESHOLD_C <= value["threshold_c"] <= MAX_THRESHOLD_C):
        return None
    return dict(value)


def read_settings(path: Path = CONFIG_PATH) -> dict:
    try:
        parsed = valid_settings(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        parsed = None
    return parsed or dict(DEFAULT_SETTINGS)


def read_status(path: Path = STATUS_PATH) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if type(value.get("enabled")) is not bool or type(value.get("limited")) is not bool:
        return None
    if type(value.get("threshold_c")) is not int or not MIN_THRESHOLD_C <= value["threshold_c"] <= MAX_THRESHOLD_C:
        return None
    for key in ("current_c", "average_c", "peak_c"):
        number = value.get(key)
        if number is not None and (type(number) not in (int, float) or not 0 <= number <= 200):
            return None
    profile = value.get("profile")
    if profile is not None and profile not in ("power-saver", "balanced", "performance"):
        return None
    maximum = value.get("max_percent")
    if maximum is not None and (type(maximum) is not int or not 30 <= maximum <= 100):
        return None
    for key in ("cpu_boost", "gpu_boost"):
        if key in value and type(value[key]) is not bool:
            return None
    applied_boost = value.get("cpu_boost_applied")
    if applied_boost is not None and type(applied_boost) is not bool:
        return None
    gpu_max = value.get("gpu_max_mhz")
    if gpu_max is not None and (type(gpu_max) not in (int, float) or not 0 <= gpu_max <= 1000):
        return None
    return value
