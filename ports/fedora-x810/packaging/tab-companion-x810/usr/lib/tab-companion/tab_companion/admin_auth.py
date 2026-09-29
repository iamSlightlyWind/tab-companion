# SPDX-License-Identifier: MIT
"""Shared Polkit entry point for Fedora's narrowly allowlisted admin actions."""

from __future__ import annotations

import os
import subprocess


ADMIN_HELPER = "/usr/libexec/tab-companion-admin"


def available() -> bool:
    return os.path.isfile(ADMIN_HELPER) and os.access(ADMIN_HELPER, os.X_OK)


def command(operation: str, fallback_helper: str, *args: str) -> list[str]:
    """Build a fixed-helper command, using one shared Polkit action on Fedora."""
    if available():
        return ["pkexec", ADMIN_HELPER, "--run", operation, *map(str, args)]
    return ["pkexec", fallback_helper, *map(str, args)]


def authorize_at_startup() -> bool:
    """Prompt once at app startup; later helper calls reuse Polkit's kept grant."""
    if not available():
        return True
    try:
        result = subprocess.run(
            ["pkexec", ADMIN_HELPER, "--authorize"],
            timeout=120,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
