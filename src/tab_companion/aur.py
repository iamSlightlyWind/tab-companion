# SPDX-License-Identifier: MIT
"""Build a verified AUR source release without privilege."""

import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from .updates import UpdateError

MAX_ARCHIVE_MEMBERS = 20000
MAX_ARCHIVE_SIZE = 512 * 1024 * 1024


def _safe_extract(archive_path, destination):
    total = 0
    with tarfile.open(archive_path, "r:*") as archive:
        members = archive.getmembers()
        if not members or len(members) > MAX_ARCHIVE_MEMBERS:
            raise UpdateError("AUR source archive has an invalid file count")
        root = destination.resolve()
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                raise UpdateError("AUR source archive contains an unsafe path or file type")
            total += max(member.size, 0)
            if total > MAX_ARCHIVE_SIZE:
                raise UpdateError("AUR source archive is too large")
            target = (destination / path).resolve()
            if target != root and root not in target.parents:
                raise UpdateError("AUR source archive escapes its build directory")
        archive.extractall(destination, members=members, filter="data")


def build_aur_package(source_archive):
    if os.geteuid() == 0:
        raise UpdateError("AUR builds must run as the logged-in user, never as root")
    makepkg = shutil.which("makepkg")
    if not makepkg:
        raise UpdateError("makepkg is not installed")
    with tempfile.TemporaryDirectory(prefix="tab-companion-aur-") as temp:
        build = Path(temp)
        _safe_extract(source_archive, build)
        pkgbuilds = list(build.rglob("PKGBUILD"))
        if len(pkgbuilds) != 1:
            raise UpdateError("AUR archive must contain exactly one PKGBUILD")
        workdir = pkgbuilds[0].parent
        result = subprocess.run(
            [makepkg, "--clean", "--cleanbuild", "--noconfirm", "--log"],
            cwd=workdir,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**os.environ, "LC_ALL": "C"},
        )
        if result.returncode:
            detail = (result.stdout or "")[-4000:]
            raise UpdateError("makepkg failed (built as your user; dependencies may be missing).\n" + detail)
        packages = sorted(p for p in workdir.iterdir() if p.is_file() and ".pkg.tar." in p.name and not p.name.endswith(".sig"))
        if len(packages) != 1:
            raise UpdateError("Expected exactly one built Arch package")
        # Keep a private, durable copy after the temporary build directory closes.
        cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "tab-companion" / "updates"
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        output = cache / packages[0].name
        shutil.copyfile(packages[0], output)
        output.chmod(0o600)
        return output
