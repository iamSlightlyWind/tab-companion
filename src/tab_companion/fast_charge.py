# SPDX-License-Identifier: MIT
"""Read X810's fast-charge toggle and active USB-PD contract, without PD writes."""

from pathlib import Path


HELPER = "/usr/libexec/gts9wifi-device-control"
FAST_CHARGE_PATH = Path("/sys/bus/i2c/devices/6-0049/fast_charge")
POWER_SUPPLY_ROOT = Path("/sys/class/power_supply")


def helper_command(enabled: bool, helper: str = HELPER) -> list[str]:
    """Return the fixed device-control command; never constructs a PD request."""
    return [helper, "set", "fast-charge", "1" if enabled else "0"]


def parse_enabled(output: str) -> bool | None:
    """Parse the device-control helper's strict 0/1 response."""
    value = output.strip()
    if value == "0":
        return False
    if value == "1":
        return True
    return None


def read_enabled(path: Path = FAST_CHARGE_PATH) -> bool | None:
    """Read the live kernel switch directly; sysfs reads do not need privilege."""
    try:
        output = path.read_text(encoding="ascii")
    except (OSError, UnicodeError):
        return None
    return parse_enabled(output)


def parse_contract_online(value: str) -> str:
    """Interpret the X810 TCPM supply's ONLINE enum, not advertised APDOs.

    On this port's TCPM source supply, 2 means an active PPS contract, 1 a
    fixed-voltage PD contract, and 0 no active PD contract. Other values are
    deliberately unknown rather than guessed from charger capability strings.
    """
    try:
        online = int(value.strip(), 10)
    except (AttributeError, ValueError):
        return "unknown"
    return {0: "offline", 1: "fixed-pd", 2: "pps"}.get(online, "unknown")


def _read_voltage_mv(supply: Path) -> int | None:
    try:
        microvolts = int((supply / "voltage_now").read_text(encoding="ascii").strip())
    except (OSError, UnicodeError, ValueError):
        return None
    if microvolts <= 0:
        return None
    return round(microvolts / 1000)


def read_contract_status(root: Path = POWER_SUPPLY_ROOT) -> dict[str, int | str | None]:
    """Report active TCPM contract state; never requests or changes a contract."""
    try:
        supplies = sorted(
            root.glob("tcpm-source-psy-*"), key=lambda path: path.name
        )
    except OSError:
        return {"state": "unknown", "voltage_mv": None}

    observed = []
    for supply in supplies:
        try:
            if (supply / "type").read_text(encoding="ascii").strip() != "USB":
                continue
            state = parse_contract_online(
                (supply / "online").read_text(encoding="ascii")
            )
        except (OSError, UnicodeError):
            continue
        observed.append((state, _read_voltage_mv(supply)))

    # A live PPS observation takes precedence if multiple TCPM supplies exist;
    # otherwise a fixed-PD contract is more informative than an offline port.
    for state in ("pps", "fixed-pd", "offline"):
        for observed_state, voltage_mv in observed:
            if observed_state == state:
                return {"state": state, "voltage_mv": voltage_mv}
    return {"state": "unknown", "voltage_mv": None}
