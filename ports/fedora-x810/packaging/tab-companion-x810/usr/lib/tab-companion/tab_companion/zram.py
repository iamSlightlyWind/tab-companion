# SPDX-License-Identifier: MIT
"""X810 Fedora zram sizing policy and configuration helpers."""

from __future__ import annotations

import re
from pathlib import Path


SIZES_MIB = (1024, 2048, 3072, 4096, 6144, 8192)
DEFAULT_SIZE_MIB = 4096
CONFIG_DIR = Path("/etc/systemd/zram-generator.conf.d")
CONFIG_FILE = CONFIG_DIR / "90-tab-companion.conf"


def validate_size(value: str | int) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Select a supported zram size") from error
    if size not in SIZES_MIB:
        raise ValueError("Select a supported zram size")
    return size


def render_config(size_mib: str | int) -> str:
    size = validate_size(size_mib)
    return (
        "# Managed by Tab Companion. Changes apply after reboot.\n"
        "[zram0]\n"
        f"zram-size = {size}\n"
    )


def parse_configured_size(paths: tuple[Path, ...] = (
    Path("/etc/systemd/zram-generator.conf"), CONFIG_FILE,
)) -> int:
    """Return effective size for supported fixed-size settings; otherwise default."""
    effective = None
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        in_zram0 = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                in_zram0 = stripped == "[zram0]"
            elif in_zram0:
                match = re.fullmatch(r"zram-size\s*=\s*(\d+)\s*", stripped)
                if match:
                    try:
                        effective = validate_size(match.group(1))
                    except ValueError:
                        effective = None
    return effective or DEFAULT_SIZE_MIB


def active_size_bytes(path: Path = Path("/sys/block/zram0/disksize")) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
