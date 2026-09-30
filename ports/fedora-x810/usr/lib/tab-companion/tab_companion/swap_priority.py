# SPDX-License-Identifier: MIT
"""Read active swap devices for the Performance page."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

PROC_SWAPS = Path("/proc/swaps")
FINDMNT = "/usr/bin/findmnt"


def _unescape_proc_path(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def active_swaps(proc_swaps: Path = PROC_SWAPS) -> list[dict]:
    """Return active swap rows with byte counts and a file's containing mount."""
    rows = []
    lines = proc_swaps.read_text(encoding="utf-8").splitlines()
    for line in lines[1:]:
        fields = line.split()
        if len(fields) != 5:
            continue
        source, kind, size_kib, used_kib, priority = fields
        source = _unescape_proc_path(source)
        if re.fullmatch(r"/dev/zram[0-9]+", source):
            kind = "zram"
        mountpoint = ""
        if kind == "file":
            try:
                result = subprocess.run(
                    [FINDMNT, "-n", "-T", source, "-o", "TARGET"],
                    check=True, capture_output=True, text=True, timeout=2,
                )
                mountpoint = result.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                mountpoint = ""
        rows.append({
            "source": source,
            "type": kind,
            "size": int(size_kib) * 1024,
            "used": int(used_kib) * 1024,
            "priority": int(priority),
            "mountpoint": mountpoint,
        })
    return rows
