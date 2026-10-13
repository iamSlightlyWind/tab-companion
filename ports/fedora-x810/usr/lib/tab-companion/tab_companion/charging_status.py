# SPDX-License-Identifier: MIT
"""Read-only charging telemetry for Linux power_supply sysfs."""

from __future__ import annotations

import re
from pathlib import Path

from .fast_charge import POWER_SUPPLY_ROOT, read_contract_status


BATTERY_NAMES = ("sm5714-battery", "battery")
USB_NAMES = ("sm5714-usb", "usb", "ac")


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").strip(), 10)
    except (OSError, UnicodeError, ValueError):
        return None


def _supply(root: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        path = root / name
        if path.is_dir():
            return path
    return None


def _active_usb_type(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        value = (path / "usb_type").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    match = re.search(r"\[([^\]]+)\]", value)
    return (match.group(1) if match else value).strip().upper() or None


def classify_charging_technology(
    contract_state: str, usb_type: str | None, online: int | None, status: str | None
) -> str:
    """Describe the active connection; never infer PPS from source capability."""
    if contract_state == "pps":
        return "USB PD PPS"
    if contract_state == "fixed-pd":
        return "USB Power Delivery (fixed voltage)"
    if online == 0 or status in ("Discharging", "Not charging") and online != 1:
        return "Not charging"
    names = {
        "SDP": "USB (slow charging, SDP)",
        "CDP": "USB data/charging (CDP)",
        "DCP": "Dedicated USB charger (DCP)",
        "PD": "USB Power Delivery",
        "PD_PPS": "USB PD PPS",
        "C": "USB Type-C current",
        "UNKNOWN": "USB connected (type unknown)",
    }
    if usb_type in names:
        return names[usb_type]
    if online == 1 or status == "Charging":
        return "USB charging (type unknown)"
    return "No active charger detected"


def read_charging_status(root: Path = POWER_SUPPLY_ROOT) -> dict:
    """Read battery current/voltage and active charger technology.

    Battery current and power are fuel-gauge readings, not USB input current or
    adapter wattage. Contract voltage is shown separately when TCPM reports it.
    """
    battery = _supply(root, BATTERY_NAMES)
    usb = _supply(root, USB_NAMES)
    status = None
    if battery is not None:
        try:
            status = (battery / "status").read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            pass

    current_ua = _read_int(battery / "current_now") if battery else None
    battery_uv = _read_int(battery / "voltage_now") if battery else None
    usb_online = _read_int(usb / "online") if usb else None
    contract = read_contract_status(root)
    tech = classify_charging_technology(
        contract.get("state", "unknown"), _active_usb_type(usb), usb_online, status
    )
    current_a = current_ua / 1_000_000 if current_ua is not None else None
    voltage_v = battery_uv / 1_000_000 if battery_uv is not None else None
    power_w = current_a * voltage_v if current_a is not None and voltage_v is not None else None
    return {
        "technology": tech,
        "battery_status": status,
        "battery_current_a": current_a,
        "battery_voltage_v": voltage_v,
        "battery_power_w": power_w,
        "contract_voltage_v": (
            contract["voltage_mv"] / 1000
            if contract.get("voltage_mv") is not None else None
        ),
    }
