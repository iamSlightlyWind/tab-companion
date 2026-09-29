# SPDX-License-Identifier: MIT
"""Validation and discovery for user-selected X810 boot fallback snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from .updates import UpdateError


KIND = "tab-companion-x810-boot-backup"
MODEL = "SM-X810"
CODENAME = "gts9pwifi"
IMAGES = {
    "boot.img": 100_663_296,
    "init_boot.img": 8_388_608,
    "vendor_boot.img": 100_663_296,
    "dtbo.img": 16_777_216,
}
BUILD = re.compile(r"^[0-9]{1,20}$")
HEX = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class FallbackSnapshot:
    build_number: str
    path: Path
    created_utc: str
    source_kernel_release: str


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular(path: Path, *, owners: tuple[int, ...], max_size: int | None = None):
    try:
        info = path.lstat()
    except OSError as exc:
        raise UpdateError(f"Missing fallback file: {path.name}") from exc
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid not in owners
            or (max_size is not None and info.st_size > max_size)):
        raise UpdateError(f"Unsafe fallback file: {path.name}")
    return info


def verify_snapshot(path: str | Path, *, root: str | Path | None = None,
                    expected_device: int | None = None,
                    verify_hashes: bool = True) -> FallbackSnapshot:
    """Validate a numbered snapshot's structure and every recorded digest.

    This is a UI-side filter only. The privileged restore helper repeats these
    checks immediately before staging and writing fixed X810 partitions.
    """
    path = Path(path)
    if not BUILD.fullmatch(path.name):
        raise UpdateError("Invalid fallback build folder")
    path = Path(os.path.abspath(path))
    if path.is_symlink() or not path.is_dir():
        raise UpdateError("Fallback build is not a real directory")
    info = path.lstat()
    user = os.getuid()
    if info.st_uid not in (user, 0) or info.st_mode & 0o022:
        raise UpdateError("Fallback build directory has unsafe ownership or permissions")
    if root is not None:
        root_path = Path(os.path.abspath(root))
        if root_path.is_symlink() or not root_path.is_dir():
            raise UpdateError("Fallback folder is unavailable")
        if path.parent != root_path:
            raise UpdateError("Fallback build is outside the selected folder")
        if expected_device is not None and root_path.stat().st_dev != expected_device:
            raise UpdateError("The fallback drive is no longer mounted at the selected folder")

    metadata = path / "backup.json"
    metadata_info = _regular(metadata, owners=(user, 0), max_size=2 * 1024 * 1024)
    if metadata_info.st_size < 2:
        raise UpdateError("Fallback metadata is empty")
    try:
        record = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("Fallback metadata is invalid") from exc
    tag = f"x810-fedora-port-build-{path.name}"
    if (not isinstance(record, dict) or record.get("schema_version") != 1
            or record.get("kind") != KIND or record.get("device") != MODEL
            or record.get("target_release_tag") != tag):
        raise UpdateError("Fallback metadata does not identify this SM-X810 build")
    files = record.get("files")
    if not isinstance(files, dict) or set(files) != set(IMAGES):
        raise UpdateError("Fallback metadata has an unexpected boot-image list")
    files_dir = path / "files"
    if files_dir.is_symlink() or not files_dir.is_dir():
        raise UpdateError("Fallback files folder is missing or unsafe")
    for name, size in IMAGES.items():
        item = files[name]
        if not isinstance(item, dict) or item.get("size_bytes") != size:
            raise UpdateError(f"Fallback metadata has invalid size for {name}")
        digest = item.get("sha256")
        if not isinstance(digest, str) or not HEX.fullmatch(digest):
            raise UpdateError(f"Fallback metadata has invalid checksum for {name}")
        file_path = files_dir / name
        file_info = _regular(file_path, owners=(user, 0), max_size=size)
        if file_info.st_size != size or (verify_hashes and _hash(file_path) != digest):
            raise UpdateError(f"Fallback image failed integrity validation: {name}")

    module = record.get("module_backup")
    if not isinstance(module, dict):
        raise UpdateError("Fallback has no matching kernel module archive")
    release, archive = module.get("release"), module.get("archive")
    if (not isinstance(release, str) or not re.fullmatch(r"[A-Za-z0-9._+-]{1,120}", release)
            or archive != f"modules-{release}.tar.gz"):
        raise UpdateError("Fallback kernel-module metadata is invalid")
    archive_path = files_dir / archive
    archive_info = _regular(archive_path, owners=(user, 0), max_size=1024 * 1024 * 1024)
    module_hash = module.get("sha256")
    if (archive_info.st_size != module.get("size_bytes")
            or not isinstance(module_hash, str) or not HEX.fullmatch(module_hash)
            or (verify_hashes and _hash(archive_path) != module_hash)):
        raise UpdateError("Fallback kernel-module archive failed integrity validation")

    script = path / "restore-modules-in-twrp.sh"
    script_info = _regular(script, owners=(user, 0), max_size=1024 * 1024)
    script_hash = record.get("restore_script_sha256")
    if (not isinstance(script_hash, str) or not HEX.fullmatch(script_hash)
            or (verify_hashes and _hash(script) != script_hash)):
        raise UpdateError("Fallback TWRP module-restore script failed integrity validation")
    return FallbackSnapshot(path.name, path, str(record.get("created_utc", "")),
                            str(record.get("source_kernel_release", "")))


def list_snapshots(root: str | Path, *, expected_device: int | None = None) -> list[FallbackSnapshot]:
    """Return only complete, hash-verified numeric build snapshots."""
    root = Path(os.path.abspath(root))
    if root.is_symlink() or not root.is_dir() or os.path.realpath(root) != str(root):
        raise UpdateError("Choose an existing fallback folder")
    if expected_device is not None and root.stat().st_dev != expected_device:
        raise UpdateError("The fallback drive is no longer mounted at the selected folder")
    results = []
    for entry in root.iterdir():
        if not BUILD.fullmatch(entry.name):
            continue
        try:
            results.append(verify_snapshot(entry, root=root, expected_device=expected_device))
        except UpdateError:
            continue
    return sorted(results, key=lambda snapshot: int(snapshot.build_number), reverse=True)


def list_snapshot_candidates(root: str | Path, *,
                              expected_device: int | None = None) -> list[FallbackSnapshot]:
    """Quick UI listing; the privileged restorer verifies all content hashes.

    Hashing hundreds of MiB per snapshot on GTK's main loop made the picker
    appear frozen. This pass checks identity, structure, ownership, sizes and
    checksum-field syntax only. It does not authorize flashing: the privileged
    helper revalidates every digest immediately before writing partitions.
    """
    root = Path(os.path.abspath(root))
    if root.is_symlink() or not root.is_dir() or os.path.realpath(root) != str(root):
        raise UpdateError("Choose an existing fallback folder")
    if expected_device is not None and root.stat().st_dev != expected_device:
        raise UpdateError("The fallback drive is no longer mounted at the selected folder")
    results = []
    for entry in root.iterdir():
        if not BUILD.fullmatch(entry.name):
            continue
        try:
            results.append(verify_snapshot(entry, root=root, expected_device=expected_device,
                                           verify_hashes=False))
        except UpdateError:
            continue
    return sorted(results, key=lambda snapshot: int(snapshot.build_number), reverse=True)
