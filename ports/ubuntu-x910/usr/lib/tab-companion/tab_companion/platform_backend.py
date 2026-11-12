# SPDX-License-Identifier: MIT
"""Distribution adapters for package and service operations.

The GTK/TUI layers use one application implementation. Distribution-specific
package inspection and installation live behind this interface so adding a
package format does not fork app pages or device features.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class PlatformBackend:
    name: str
    os_ids: frozenset[str]
    package_manager: str | None
    package_formats: frozenset[str]
    app_artifact: str | None
    manager_programs: tuple[str, ...] = ()
    query_programs: tuple[str, ...] = ()
    system_manager: str = "systemd"

    def available(self, which: Callable[[str], str | None] = shutil.which) -> bool:
        if self.package_manager is None:
            return False
        return (any(which(program) for program in self.manager_programs)
                and all(which(program) for program in self.query_programs))

    def supports_format(self, package_format: str) -> bool:
        return package_format in self.package_formats

    def install_notice(self) -> str:
        if self.package_manager == "pacman":
            return "Arch Linux does not support partial upgrades; the update performs a full system upgrade first."
        return ""

    def has_kernel_update(self, target: dict, port_record: dict) -> bool:
        """Whether this port record names a kernel updater implemented by this adapter."""
        feature = port_record.get("kernel_update")
        return bool(
            target.get("device") == "SM-X810"
            and isinstance(feature, dict)
            and feature.get("backend") == self.package_manager
            and feature.get("available") is True
        )

    def has_port_update(self, port_record: dict) -> bool:
        """Whether the installed port advertises a native package update feed."""
        feature = port_record.get("port_update")
        return bool(
            isinstance(feature, dict)
            and feature.get("backend") == self.package_manager
            and feature.get("available") is True
        )

    def has_legacy_update_ui(self, port_record: dict) -> bool:
        """Whether the port package advertises an installed legacy updater UI."""
        feature = port_record.get("legacy_update")
        helper = feature.get("helper") if isinstance(feature, dict) else None
        return bool(
            isinstance(feature, dict)
            and feature.get("backend") == self.package_manager
            and feature.get("available") is True
            and isinstance(helper, str)
            and Path(helper).is_absolute()
            and Path(helper).is_file()
        )

    def inspect_package(self, package_format: str, path: Path) -> tuple[str, str, str]:
        """Return native package name, version, and architecture."""
        if not self.supports_format(package_format):
            raise ValueError(f"{self.name} does not support {package_format} packages")
        if package_format == "deb":
            if not shutil.which("dpkg-deb"):
                raise ValueError("dpkg-deb is not installed")
            output = subprocess.check_output(
                ["dpkg-deb", "-f", str(path), "Package", "Version", "Architecture"],
                text=True,
            )
            fields = dict(line.split(": ", 1) for line in output.splitlines() if ": " in line)
            return fields["Package"], fields["Version"], fields["Architecture"]
        if package_format == "rpm":
            rpm = shutil.which("rpm")
            if not rpm:
                raise ValueError("rpm query tool is not installed")
            fields = subprocess.check_output(
                [rpm, "-qp", "--queryformat", "%{NAME}\\n%{VERSION}-%{RELEASE}\\n%{ARCH}", str(path)],
                text=True,
            ).splitlines()
            if len(fields) != 3:
                raise ValueError("rpm returned malformed package metadata")
            return tuple(fields)  # type: ignore[return-value]
        if package_format == "pacman-local":
            pacman = shutil.which("pacman")
            if not pacman:
                raise ValueError("pacman is not installed")
            fields = subprocess.check_output(
                [pacman, "-Qp", "--print-format", "%n\\n%v\\n%a", str(path)], text=True
            ).splitlines()
            if len(fields) != 3:
                raise ValueError("pacman returned malformed package metadata")
            return tuple(fields)  # type: ignore[return-value]
        raise ValueError(f"No package inspector is registered for {package_format}")

    def install_package(self, package_format: str, path: Path) -> None:
        if not self.supports_format(package_format):
            raise ValueError(f"{self.name} does not support {package_format} packages")
        if package_format == "deb":
            _run(["apt-get", "install", "--yes", "--no-remove", str(path)])
        elif package_format == "rpm":
            manager = shutil.which("dnf5") or shutil.which("dnf")
            if not manager:
                raise ValueError("DNF is not installed")
            _run([manager, "install", "--assumeyes", "--setopt=clean_requirements_on_remove=False", str(path)])
        elif package_format == "pacman-local":
            pacman = shutil.which("pacman")
            if not pacman:
                raise ValueError("pacman is not installed")
            _run([pacman, "-Syu", "--noconfirm"])
            _run([pacman, "-U", "--noconfirm", str(path)])
        else:
            raise ValueError(f"No package installer is registered for {package_format}")

    def install_rpm_transaction(self, paths: list[Path]) -> None:
        if self.package_manager != "rpm":
            raise ValueError(f"{self.name} does not support RPM transactions")
        manager = shutil.which("dnf5") or shutil.which("dnf")
        if not manager:
            raise ValueError("DNF is not installed")
        _run([manager, "install", "--assumeyes", "--setopt=clean_requirements_on_remove=False",
              *map(str, paths)])


def _run(argv: list[str]) -> None:
    subprocess.run(
        argv, check=True,
        env={**os.environ, "LC_ALL": "C", "DEBIAN_FRONTEND": "noninteractive"},
    )


_BACKENDS = (
    PlatformBackend(
        "Debian family", frozenset({"debian", "ubuntu", "linuxmint", "pop", "elementary"}),
        "deb", frozenset({"deb"}), "tab-companion-ubuntu", ("apt-get",), ("dpkg-deb",),
    ),
    PlatformBackend(
        "Fedora family", frozenset({"fedora", "rhel", "centos", "rocky", "almalinux"}),
        "rpm", frozenset({"rpm", "rpm-transaction"}), "tab-companion-fedora", ("dnf5", "dnf"), ("rpm",),
    ),
    PlatformBackend(
        "Arch family", frozenset({"arch", "archarm", "manjaro", "endeavouros"}),
        "pacman", frozenset({"pacman-local"}), "tab-companion-arch", ("pacman",), ("pacman",),
    ),
)


def read_os_release(path: str | Path = "/etc/os-release") -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key not in {"ID", "ID_LIKE", "VERSION_ID", "PRETTY_NAME"}:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key] = value
    except OSError:
        pass
    return values


def detect_backend(
    os_release: dict[str, str] | None = None,
    *,
    os_release_path: str | Path = "/etc/os-release",
    which: Callable[[str], str | None] = shutil.which,
) -> PlatformBackend:
    """Select one adapter from ID/ID_LIKE and verify its native tools exist."""
    release = read_os_release(os_release_path) if os_release is None else os_release
    ids = {word.lower() for word in (release.get("ID", "") + " " + release.get("ID_LIKE", "")).split()}
    for backend in _BACKENDS:
        if ids & backend.os_ids and backend.available(which):
            return backend
    return PlatformBackend(
        "Unsupported Linux", frozenset(ids), None, frozenset(), None,
        system_manager="systemd" if shutil.which("systemctl") else "unknown",
    )


def package_manager(**kwargs) -> str | None:
    """Compatibility API; new code should use the selected adapter."""
    return detect_backend(**kwargs).package_manager


def backend_for_manager(manager: str | None) -> PlatformBackend | None:
    return next((backend for backend in _BACKENDS if backend.package_manager == manager), None)


def supported_architecture() -> str:
    return {"aarch64": "aarch64", "arm64": "aarch64", "x86_64": "x86_64"}.get(
        platform.machine(), platform.machine()
    )
