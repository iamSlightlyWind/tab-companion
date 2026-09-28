# SPDX-License-Identifier: MIT
"""Fetch and verify the current X810 kernel/boot set from its public release."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .updates import (
    MAX_API_RESPONSE_BYTES,
    UpdateError,
    _API_BASE,
    _api_json,
    _cache_dir,
    _github_repo,
    _request,
    _https_url,
)


PROJECT = "x810-fedroid"
DEFAULT_REPOSITORY = "https://github.com/iamSlightlyWind/x810-fedroid"
MANIFEST_NAME = "manifest.json"
RELEASE_TAG = re.compile(r"^x810-fedora-port-build-([0-9]{1,20})$")
COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PARTITIONS = {
    "boot.img": 100_663_296,
    "init_boot.img": 8_388_608,
    "vendor_boot.img": 100_663_296,
    "dtbo.img": 16_777_216,
}
KERNEL_ASSETS = (*PARTITIONS, "kernel.rpm")
MAX_KERNEL_RPM = 1024 * 1024 * 1024
MAX_BOOT_BUNDLE_BYTES = MAX_KERNEL_RPM + sum(PARTITIONS.values())
ALLOWED_DOWNLOAD_HOSTS = {"github.com", "release-assets.githubusercontent.com"}


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    size: int
    sha256: str
    url: str


@dataclass(frozen=True)
class X810KernelRelease:
    tag: str
    build_number: str
    source_commit: str
    port_version: str
    assets: dict[str, ReleaseAsset]


def backup_folder_config_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "tab-companion" / "x810-kernel-backup.json"


def load_backup_folder_info() -> tuple[str, int] | None:
    """Read the previously user-selected folder only if mount identity is stable."""
    path = backup_folder_config_path()
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or path.is_symlink()
                or info.st_uid != os.getuid() or info.st_mode & 0o077):
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        folder = record.get("path")
        device = record.get("st_dev")
        if not isinstance(folder, str) or not os.path.isabs(folder) or isinstance(device, bool) or not isinstance(device, int):
            return None
        if not os.path.isdir(folder) or not os.access(folder, os.W_OK | os.X_OK):
            return None
        if os.stat(folder).st_dev != device:
            return None
        return folder, device
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def load_backup_folder() -> str | None:
    """Compatibility helper returning only the user-selected directory."""
    record = load_backup_folder_info()
    return record[0] if record else None


def save_backup_folder(folder: str) -> int:
    """Persist the user-selected location, including its filesystem identity."""
    if not isinstance(folder, str) or not os.path.isabs(folder) or not os.path.isdir(folder):
        raise UpdateError("Backup folder must be an existing absolute local directory")
    resolved = os.path.realpath(folder)
    if resolved == "/" or resolved != folder or not os.access(resolved, os.W_OK | os.X_OK):
        raise UpdateError("Choose a writable folder without symbolic links")
    path = backup_folder_config_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_info = path.parent.lstat()
    if (not stat.S_ISDIR(parent_info.st_mode) or path.parent.is_symlink()
            or parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o022):
        raise UpdateError("Tab Companion configuration folder has unsafe permissions")
    if parent_info.st_mode & 0o077:
        os.chmod(path.parent, 0o700)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.unlink(missing_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            device = os.stat(resolved).st_dev
            json.dump({"path": resolved, "st_dev": device}, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return device
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _safe_repo(repository: str) -> tuple[str, str]:
    owner, name = _github_repo(repository)
    if (owner.lower(), name.lower()) != ("iamslightlywind", PROJECT):
        raise UpdateError("X810 kernel updates must come from the configured x810-fedroid repository")
    return owner, name


def _asset_records(manifest: dict, release: dict, owner: str, repository: str) -> dict[str, ReleaseAsset]:
    if (manifest.get("schema_version") != 1
            or manifest.get("type") != "x810-fedora-release"
            or manifest.get("device") != {"model": "SM-X810", "codename": "gts9pwifi"}):
        raise UpdateError("The latest release manifest is not for the SM-X810 (gts9pwifi)")
    tag = release.get("tag_name")
    match = RELEASE_TAG.fullmatch(tag) if isinstance(tag, str) else None
    if match is None or manifest.get("release_tag") != tag:
        raise UpdateError("The release tag and X810 manifest do not match the expected build format")
    commit = manifest.get("source_commit")
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise UpdateError("The X810 release manifest has an invalid source commit")
    version = manifest.get("port_version")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise UpdateError("The X810 release manifest has an invalid port version")

    release_assets = release.get("assets")
    if not isinstance(release_assets, list):
        raise UpdateError("GitHub returned an invalid X810 release asset list")
    remote_by_name = {}
    for item in release_assets:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        if item["name"] in remote_by_name:
            raise UpdateError(f"The release contains duplicate {item['name']} assets")
        remote_by_name[item["name"]] = item

    records = manifest.get("assets")
    if not isinstance(records, list) or len(records) > 64:
        raise UpdateError("The X810 release manifest has an invalid asset list")
    by_name = {}
    for item in records:
        if not isinstance(item, dict):
            raise UpdateError("The X810 release manifest contains an invalid asset entry")
        name, digest, size = item.get("name"), item.get("sha256"), item.get("size_bytes")
        if name in by_name:
            raise UpdateError(f"The X810 manifest contains duplicate {name} entries")
        by_name[name] = item

    required = set(KERNEL_ASSETS)
    if not required.issubset(by_name):
        raise UpdateError("The X810 release is missing one or more required kernel/boot assets")

    result = {}
    for name in KERNEL_ASSETS:
        item = by_name[name]
        digest, size = item.get("sha256"), item.get("size_bytes")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise UpdateError(f"The release manifest has an invalid SHA-256 for {name}")
        expected_size = PARTITIONS.get(name)
        if expected_size is not None and size != expected_size:
            raise UpdateError(f"The release manifest reports an unexpected partition size for {name}")
        if name == "kernel.rpm" and (isinstance(size, bool) or not isinstance(size, int)
                                       or not 1 <= size <= MAX_KERNEL_RPM):
            raise UpdateError("The release manifest reports an invalid kernel RPM size")
        remote = remote_by_name.get(name)
        if not isinstance(remote, dict) or remote.get("size") != size:
            raise UpdateError(f"GitHub release metadata disagrees with the manifest for {name}")
        url = remote.get("browser_download_url")
        _https_url(url, f"{name} download URL")
        parsed = urllib.parse.urlsplit(url)
        expected_path = f"/{owner}/{repository}/releases/download/{tag}/{name}"
        if (parsed.hostname.lower() != "github.com" or parsed.path != expected_path
                or parsed.query or parsed.fragment):
            raise UpdateError(f"GitHub returned an unexpected download URL for {name}")
        result[name] = ReleaseAsset(name, size, digest, url)
    return result


def fetch_latest_x810_release(repository: str = DEFAULT_REPOSITORY) -> X810KernelRelease:
    """Fetch the latest complete public release and validate its manifest."""
    owner, repo = _safe_repo(repository)
    url = f"{_API_BASE}/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(repo, safe='')}/releases/latest"
    release = _api_json(url, label="latest X810 kernel release")
    if (not isinstance(release, dict) or release.get("draft") is not False
            or release.get("prerelease") is not False):
        raise UpdateError("GitHub did not return a published stable X810 release")
    tag = release.get("tag_name")
    if not isinstance(tag, str) or RELEASE_TAG.fullmatch(tag) is None:
        raise UpdateError("The latest release is not a complete x810-fedroid build")
    remote_manifest = [item for item in release.get("assets", [])
                       if isinstance(item, dict) and item.get("name") == MANIFEST_NAME]
    if len(remote_manifest) != 1:
        raise UpdateError("The latest X810 release must contain exactly one manifest.json")
    asset = remote_manifest[0]
    size = asset.get("size")
    manifest_url = asset.get("browser_download_url")
    expected_path = f"/{owner}/{repo}/releases/download/{tag}/{MANIFEST_NAME}"
    _https_url(manifest_url, "X810 release manifest URL")
    parsed = urllib.parse.urlsplit(manifest_url)
    if (isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_API_RESPONSE_BYTES
            or parsed.hostname.lower() != "github.com" or parsed.path != expected_path
            or parsed.query or parsed.fragment):
        raise UpdateError("GitHub returned invalid metadata for the X810 release manifest")
    try:
        with urllib.request.urlopen(_request(manifest_url), timeout=45) as response:
            final = urllib.parse.urlsplit(_https_url(response.geturl(), "X810 manifest redirect"))
            if final.hostname.lower() not in ALLOWED_DOWNLOAD_HOSTS:
                raise UpdateError("X810 release manifest redirected to an unexpected host")
            length = response.headers.get("Content-Length")
            if length and int(length) != size:
                raise UpdateError("Downloaded manifest size differs from GitHub release metadata")
            body = response.read(MAX_API_RESPONSE_BYTES + 1)
        if len(body) != size or len(body) > MAX_API_RESPONSE_BYTES:
            raise UpdateError("Downloaded manifest size is invalid")
        manifest = json.loads(body)
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError(f"Could not fetch the X810 release manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise UpdateError("The X810 release manifest is not a JSON object")
    assets = _asset_records(manifest, release, owner, repo)
    return X810KernelRelease(tag, RELEASE_TAG.fullmatch(tag).group(1),
                              manifest["source_commit"], manifest["port_version"], assets)


def _safe_cache_directory(release: X810KernelRelease) -> Path:
    root = _cache_dir() / "x810-kernel-updates"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for path in (root, root / release.tag):
        path.mkdir(mode=0o700, exist_ok=True)
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or path.is_symlink()
                or info.st_uid != os.getuid() or info.st_mode & 0o022):
            raise UpdateError("The X810 kernel update cache has unsafe permissions")
    return root / release.tag


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_x810_release(release: X810KernelRelease, progress=None) -> Path:
    """Download all five update assets, reusing only owner-safe hash matches."""
    if not isinstance(release, X810KernelRelease):
        raise UpdateError("Invalid X810 release record")
    directory = _safe_cache_directory(release)
    total = sum(asset.size for asset in release.assets.values())
    done = 0
    for name in KERNEL_ASSETS:
        asset = release.assets[name]
        destination = directory / name
        try:
            info = destination.lstat()
            valid = (stat.S_ISREG(info.st_mode) and not destination.is_symlink()
                     and info.st_uid == os.getuid() and info.st_size == asset.size
                     and _file_hash(destination) == asset.sha256)
        except OSError:
            valid = False
        if valid:
            done += asset.size
            if progress:
                progress(name, done, total, True)
            continue
        try:
            destination.unlink(missing_ok=True)
        except OSError as exc:
            raise UpdateError(f"Cannot replace unsafe cached {name}: {exc}") from exc
        temporary = directory / ("." + name + ".part")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
            digest = hashlib.sha256()
            received = 0
            with urllib.request.urlopen(_request(asset.url), timeout=90) as response, os.fdopen(fd, "wb") as output:
                final = urllib.parse.urlsplit(_https_url(response.geturl(), f"{name} redirect"))
                if final.hostname.lower() not in ALLOWED_DOWNLOAD_HOSTS:
                    raise UpdateError(f"{name} redirected to an unexpected host")
                length = response.headers.get("Content-Length")
                if length and int(length) != asset.size:
                    raise UpdateError(f"{name} size differs from GitHub release metadata")
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    received += len(block)
                    if received > asset.size:
                        raise UpdateError(f"{name} exceeded its declared size")
                    output.write(block)
                    digest.update(block)
                    if progress:
                        progress(name, done + received, total, False)
                output.flush()
                os.fsync(output.fileno())
            if received != asset.size or digest.hexdigest() != asset.sha256:
                raise UpdateError(f"{name} failed release size/SHA-256 validation")
            os.replace(temporary, destination)
            done += received
        except UpdateError:
            temporary.unlink(missing_ok=True)
            raise
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise UpdateError(f"Could not download {name}: {exc}") from exc
        if progress:
            progress(name, done, total, False)

    manifest_path = directory / MANIFEST_NAME
    temporary_manifest = directory / ("." + MANIFEST_NAME + ".part")
    raw = {
        "schema_version": 1,
        "type": "x810-fedora-release",
        "device": {"model": "SM-X810", "codename": "gts9pwifi"},
        "release_tag": release.tag,
        "source_commit": release.source_commit,
        "port_version": release.port_version,
        "assets": [
            {"name": asset.name, "sha256": asset.sha256, "size_bytes": asset.size}
            for asset in release.assets.values()
        ],
    }
    fd = os.open(temporary_manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(raw, output, sort_keys=True, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary_manifest, manifest_path)
    return directory
