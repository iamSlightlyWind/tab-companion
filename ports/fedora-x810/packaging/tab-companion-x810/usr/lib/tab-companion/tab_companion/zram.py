# SPDX-License-Identifier: MIT
"""X810 Fedora zram sizing policy and configuration helpers."""

from __future__ import annotations

import re
from pathlib import Path


MIN_SIZE_DECI_GB = 1
MAX_SIZE_DECI_GB = 120
DEFAULT_SIZE_DECI_GB = 43  # Existing 4 GiB Fedora default is about 4.3 decimal GB.
CONFIG_DIR = Path("/etc/systemd/zram-generator.conf.d")
CONFIG_FILE = CONFIG_DIR / "90-tab-companion.conf"


def validate_size(value: str | int) -> int:
    """Return a size in tenths of decimal GB (e.g. 43 means 4.3 GB)."""
    try:
        size = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Select a supported zram size") from error
    if not MIN_SIZE_DECI_GB <= size <= MAX_SIZE_DECI_GB:
        raise ValueError("Select a supported zram size")
    return size


def render_config(size_deci_gb: str | int) -> str:
    size = validate_size(size_deci_gb)
    gb = size / 10
    # zram-generator evaluates zram-size in MiB. Express decimal bytes as a
    # MiB expression so the UI's GB unit stays decimal (1 GB = 1e9 bytes).
    bytes_value = size * 100_000_000
    return (
        f"# Managed by Tab Companion; configured size: {gb:.1f} GB.\n"
        "# Changes apply after reboot.\n"
        "[zram0]\n"
        f"zram-size = {bytes_value} / 1048576\n"
    )


def parse_configured_size(paths: tuple[Path, ...] = (
    Path("/etc/systemd/zram-generator.conf"), CONFIG_FILE,
)) -> int:
    """Return effective size in 0.1 GB units; accept old MiB config values."""
    effective = None
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        in_zram0 = False
        for line in content.splitlines():
            stripped = line.strip()
            display = re.fullmatch(r"# Managed by Tab Companion; configured size: ([0-9]+\.[0-9]) GB\.", stripped)
            if display:
                try:
                    effective = validate_size(round(float(display.group(1)) * 10))
                except ValueError:
                    effective = None
            if stripped.startswith("[") and stripped.endswith("]"):
                in_zram0 = stripped == "[zram0]"
            elif in_zram0:
                match = re.fullmatch(r"zram-size\s*=\s*(\d+)\s*", stripped)
                if match:
                    try:
                        # Legacy values are MiB; convert to nearest 0.1 decimal GB.
                        effective = validate_size(round(int(match.group(1)) * 1_048_576 / 100_000_000 * 10))
                    except ValueError:
                        effective = None
    return effective if effective is not None else DEFAULT_SIZE_DECI_GB


def active_size_bytes(path: Path = Path("/sys/block/zram0/disksize")) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
