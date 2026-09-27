# SPDX-License-Identifier: MIT
"""Discover and safely stage updates from successful public GitHub Actions runs."""

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import urllib.parse
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


APP_REPO = "https://github.com/iamSlightlyWind/tab-companion"
APP_PROJECT = "tab-companion"
APP_BUILD_ARTIFACTS = {
    "deb": "tab-companion-ubuntu",
    "rpm": "tab-companion-fedora",
    "pacman": "tab-companion-arch",
}
MANIFEST_NAME = "tab-companion-update.json"
MAX_API_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_BUILD_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_BUILD_ARCHIVE_BYTES = 4 * 1024**3
MAX_ASSET_BYTES = 4 * 1024**3
MAX_ZIP_MEMBERS = 128
MAX_ZIP_UNCOMPRESSED_BYTES = MAX_ASSET_BYTES + MAX_BUILD_MANIFEST_BYTES
MAX_INSTALLED_BUILD_INFO_BYTES = 64 * 1024
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
HEX_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,180}$")
SAFE_PACKAGE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:.+_~-]{0,127}$")
SAFE_PACKAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_@-]{0,127}$")
FORMATS = {"rpm", "deb", "pacman-local", "aur-source"}
_USER_AGENT = "Tab-Companion-Updater/1"
_API_BASE = "https://api.github.com"


class UpdateError(ValueError):
    """Invalid update source, workflow run, build manifest, or downloaded asset."""


def _https_url(value, label="URL"):
    if not isinstance(value, str) or len(value) > 4096:
        raise UpdateError(f"Invalid {label}")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise UpdateError(f"{label} must be an HTTPS URL")
    return value


def _github_repo(repo_url):
    """Return (owner, repo) for a public github.com repository URL only."""
    _https_url(repo_url, "Repository URL")
    parsed = urllib.parse.urlsplit(repo_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise UpdateError("Invalid GitHub repository URL") from exc
    if parsed.hostname.lower() != "github.com" or port not in (None, 443):
        raise UpdateError("Updates are supported only from public github.com repositories")
    if parsed.query or parsed.fragment:
        raise UpdateError("Repository URL must not contain a query or fragment")
    path = parsed.path.strip("/")
    parts = path.split("/") if path else []
    if len(parts) == 2 and parts[1].endswith(".git"):
        parts[1] = parts[1][:-4]
    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", p) for p in parts):
        raise UpdateError("Repository URL must identify one GitHub owner and repository")
    if parts[0] in ("", ".", "..") or parts[1] in ("", ".", ".."):
        raise UpdateError("Invalid GitHub repository URL")
    return parts[0], parts[1]


def _cache_dir():
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    cache = root / "tab-companion" / "updates"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        info = cache.lstat()
    except OSError as exc:
        raise UpdateError("Cannot access the update cache") from exc
    if (not stat.S_ISDIR(info.st_mode) or cache.is_symlink()
            or info.st_uid != os.getuid() or info.st_mode & 0o022):
        raise UpdateError("Unsafe update cache directory")
    return cache


def _request(url, *, api=False):
    headers = {"User-Agent": _USER_AGENT}
    if api:
        headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    # Deliberately no authorization header: this client supports public repos only.
    return urllib.request.Request(_https_url(url), headers=headers)


def _read_response(response, limit, *, label, require_json=False):
    final = _https_url(response.geturl(), f"{label} URL")
    if require_json:
        if urllib.parse.urlsplit(final).hostname.lower() != "api.github.com":
            raise UpdateError(f"{label} redirected away from GitHub's API")
        if response.headers.get_content_type() != "application/json":
            raise UpdateError(f"{label} is not JSON")
    length = response.headers.get("Content-Length")
    if length:
        try:
            if int(length) < 0 or int(length) > limit:
                raise UpdateError(f"{label} is too large")
        except ValueError as exc:
            if isinstance(exc, UpdateError):
                raise
            raise UpdateError(f"Invalid {label} Content-Length") from exc
    body = response.read(limit + 1)
    if len(body) > limit:
        raise UpdateError(f"{label} is too large")
    return body


def _api_json(url, *, label="GitHub API response"):
    try:
        with urllib.request.urlopen(_request(url, api=True), timeout=30) as response:
            body = _read_response(response, MAX_API_RESPONSE_BYTES, label=label, require_json=True)
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError(f"Could not fetch {label}: {exc}") from exc
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"{label} is not valid JSON") from exc


def parse_os_release(path="/etc/os-release"):
    values = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z0-9_]+", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def device_id(port_file="/usr/share/tab-companion/port.json",
              model_file="/proc/device-tree/model"):
    try:
        record = json.loads(Path(port_file).read_text(encoding="utf-8"))
        value = record.get("device_id")
        if isinstance(value, str) and value:
            return value
    except (OSError, ValueError, AttributeError):
        pass
    try:
        model = Path(model_file).read_bytes().rstrip(b"\0").decode("utf-8", "replace")
    except OSError:
        model = ""
    if "GTS9PWIFI" in model.upper():
        return "SM-X810"
    return model


def host_target():
    os_release = parse_os_release()
    arch = {"aarch64": "aarch64", "arm64": "aarch64", "x86_64": "x86_64"}.get(
        platform.machine(), platform.machine()
    )
    return {
        "os_id": os_release.get("ID", "").lower(),
        "os_version": os_release.get("VERSION_ID", ""),
        "arch": arch,
        "device": device_id(),
    }


def installed_build_info(path="/usr/share/tab-companion/app-build.json"):
    """Read bounded, regular, non-symlink build metadata; return {} if unavailable."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return {}
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size < 1 or info.st_size > MAX_INSTALLED_BUILD_INFO_BYTES:
            return {}
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(MAX_INSTALLED_BUILD_INFO_BYTES + 1)
        if len(raw) > MAX_INSTALLED_BUILD_INFO_BYTES:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    finally:
        os.close(fd)


@dataclass(frozen=True)
class BuildAsset:
    project: str
    version: str
    name: str
    sha256: str
    format: str
    package_name: str
    package_version: str
    target: dict
    size: int


@dataclass(frozen=True)
class BuildArtifact:
    run_id: int
    run_number: int
    head_sha: str
    branch: str
    asset: BuildAsset
    archive_path: Path

    @property
    def commit(self):
        """Alias matching the build manifest terminology."""
        return self.head_sha


def _asset_matches(asset, target):
    candidate = asset.get("target")
    if not isinstance(candidate, dict):
        return False
    for key in ("os_id", "arch"):
        value = candidate.get(key)
        if value not in (target.get(key), "*"):
            return False
    for key in ("os_version", "device"):
        value = candidate.get(key, "*")
        if value not in (target.get(key), "*"):
            return False
    return True


def _parse_build_manifest(raw, *, expected_project, target, run):
    schema = raw.get("schema_version") if isinstance(raw, dict) else None
    if isinstance(schema, bool) or schema != 1:
        raise UpdateError("Unsupported build manifest")
    if raw.get("project") != expected_project:
        raise UpdateError("Build manifest belongs to a different project")
    version = raw.get("version")
    if (not isinstance(version, str) or not version
            or len(version) > 80 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]*", version)):
        raise UpdateError("Invalid build version metadata")
    for field, expected in (("run_id", run["id"]), ("run_number", run["run_number"])):
        value = raw.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value != expected:
            raise UpdateError("Build manifest does not match the workflow run")
    if raw.get("commit") != run["head_sha"] or raw.get("branch") != run["head_branch"]:
        raise UpdateError("Build manifest does not match the workflow source")
    assets = raw.get("assets")
    if not isinstance(assets, list) or not assets or len(assets) > MAX_ZIP_MEMBERS:
        raise UpdateError("Build manifest has no valid assets")
    matches = []
    for item in assets:
        if not isinstance(item, dict) or not _asset_matches(item, target):
            continue
        name = item.get("name")
        digest = item.get("sha256", "").lower() if isinstance(item.get("sha256"), str) else ""
        fmt = item.get("format")
        package_name = item.get("package_name", "")
        package_version = item.get("package_version", "")
        size = item.get("size")
        if not isinstance(name, str) or not SAFE_NAME.fullmatch(name) or name == MANIFEST_NAME:
            continue
        if not HEX_SHA256.fullmatch(digest) or fmt not in FORMATS:
            continue
        if not isinstance(package_name, str) or not SAFE_PACKAGE_NAME.fullmatch(package_name):
            continue
        if not isinstance(package_version, str) or not SAFE_PACKAGE_VERSION.fullmatch(package_version):
            continue
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_ASSET_BYTES:
            continue
        score = sum(item["target"].get(key) not in (None, "*") for key in ("os_version", "device"))
        matches.append((score, BuildAsset(expected_project, version, name, digest, fmt,
                                          package_name, package_version, item["target"], size)))
    if not matches:
        raise UpdateError("No build asset matches this OS, version, architecture, and device")
    matches.sort(key=lambda pair: pair[0], reverse=True)
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        raise UpdateError("Build manifest has ambiguous assets for this device")
    return matches[0][1]


def _validate_run(run, *, branch, workflow_file):
    if not isinstance(run, dict):
        return False
    path = run.get("path")
    expected = ".github/workflows/" + workflow_file
    if not isinstance(path, str) or path.lstrip("/") != expected:
        return False
    return (
        run.get("event") == "push"
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("head_branch") == branch
        and isinstance(run.get("id"), int) and not isinstance(run.get("id"), bool) and run["id"] > 0
        and isinstance(run.get("run_number"), int) and not isinstance(run.get("run_number"), bool) and run["run_number"] > 0
        and isinstance(run.get("head_sha"), str) and bool(HEX_COMMIT.fullmatch(run["head_sha"].lower()))
    )


def _latest_successful_run(owner, repo, workflow_file, branch):
    workflow = urllib.parse.quote(workflow_file, safe="")
    query = urllib.parse.urlencode({
        "branch": branch,
        "event": "push",
        "status": "completed",
        "per_page": 100,
    })
    url = f"{_API_BASE}/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(repo, safe='')}/actions/workflows/{workflow}/runs?{query}"
    data = _api_json(url, label="workflow runs")
    if not isinstance(data, dict) or not isinstance(data.get("workflow_runs"), list):
        raise UpdateError("GitHub returned an invalid workflow-runs response")
    runs = [run for run in data["workflow_runs"] if _validate_run(run, branch=branch, workflow_file=workflow_file)]
    if not runs:
        raise UpdateError(f"No successful push build was found for branch {branch}")
    # Workflow run numbers are monotonic per workflow. Attempt resolves reruns.
    return max(runs, key=lambda run: (run["run_number"], run.get("run_attempt", 1), run["id"]))


def _artifact_for_run(owner, repo, run_id, artifact_name):
    url = (f"{_API_BASE}/repos/{urllib.parse.quote(owner, safe='')}/"
           f"{urllib.parse.quote(repo, safe='')}/actions/runs/{run_id}/artifacts?per_page=100")
    data = _api_json(url, label="workflow artifacts")
    if not isinstance(data, dict) or not isinstance(data.get("artifacts"), list):
        raise UpdateError("GitHub returned an invalid artifact-list response")
    matches = [item for item in data["artifacts"] if isinstance(item, dict) and item.get("name") == artifact_name]
    if len(matches) != 1:
        if not matches:
            raise UpdateError(f"Successful build has no artifact named {artifact_name}")
        raise UpdateError(f"Successful build has duplicate artifacts named {artifact_name}")
    artifact = matches[0]
    if artifact.get("expired") is not False:
        raise UpdateError("The latest build artifact has expired or has no expiration status")
    expires_at = artifact.get("expires_at")
    if not isinstance(expires_at, str):
        raise UpdateError("The latest build artifact has no expiration time")
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise UpdateError("The latest build artifact has expired")
    except ValueError as exc:
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("GitHub returned an invalid artifact expiration time") from exc
    artifact_id = artifact.get("id")
    size = artifact.get("size_in_bytes")
    if isinstance(artifact_id, bool) or not isinstance(artifact_id, int) or artifact_id < 1:
        raise UpdateError("GitHub returned an invalid artifact ID")
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_BUILD_ARCHIVE_BYTES:
        raise UpdateError("Workflow artifact is empty or exceeds the size limit")
    download_url = artifact.get("archive_download_url")
    expected_path = f"/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/zip"
    _https_url(download_url, "Artifact URL")
    parsed = urllib.parse.urlsplit(download_url)
    if (parsed.hostname.lower() != "api.github.com" or parsed.path.lower() != expected_path.lower()
            or parsed.query or parsed.fragment):
        raise UpdateError("GitHub returned an unexpected artifact download URL")
    return artifact, size


def _download_action_artifact(url, expected_size, *, run_id, artifact_id, cache):
    destination = cache / f"run-{run_id}-artifact-{artifact_id}.zip"
    temporary = cache / f".run-{run_id}-artifact-{artifact_id}.part"
    try:
        with urllib.request.urlopen(_request(url), timeout=90) as response:
            final = _https_url(response.geturl(), "Artifact download URL")
            if not urllib.parse.urlsplit(final).hostname:
                raise UpdateError("Artifact download redirected to an invalid host")
            length = response.headers.get("Content-Length")
            if length:
                try:
                    if int(length) != expected_size:
                        raise UpdateError("Artifact download size differs from GitHub metadata")
                except ValueError as exc:
                    if isinstance(exc, UpdateError):
                        raise
                    raise UpdateError("Invalid artifact download Content-Length") from exc
            total = 0
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(temporary, flags, 0o600)
            try:
                with os.fdopen(fd, "wb") as output:
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        total += len(block)
                        if total > MAX_BUILD_ARCHIVE_BYTES or total > expected_size:
                            raise UpdateError("Workflow artifact exceeds its declared size")
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        if total != expected_size:
            raise UpdateError("Downloaded workflow artifact size does not match GitHub metadata")
        os.replace(temporary, destination)
        return destination
    except UpdateError:
        temporary.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise UpdateError(f"Could not download workflow artifact: {exc}") from exc


def _release_asset_for_run(owner, repo, run_id, artifact_name, release_tag_prefix):
    tag = f"{release_tag_prefix}-{run_id}"
    url = (f"{_API_BASE}/repos/{urllib.parse.quote(owner, safe='')}/"
           f"{urllib.parse.quote(repo, safe='')}/releases/tags/{urllib.parse.quote(tag, safe='')}")
    release = _api_json(url, label="public build release")
    if (not isinstance(release, dict) or release.get("tag_name") != tag
            or release.get("draft") is not False or release.get("prerelease") is not False
            or not isinstance(release.get("assets"), list)):
        raise UpdateError("GitHub returned an invalid public build release")
    name = artifact_name + ".zip"
    matches = [item for item in release["assets"]
               if isinstance(item, dict) and item.get("name") == name]
    if len(matches) != 1:
        raise UpdateError(f"Build release does not contain exactly one {name}")
    asset = matches[0]
    size = asset.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_BUILD_ARCHIVE_BYTES:
        raise UpdateError("Public build bundle is empty or exceeds the size limit")
    download_url = asset.get("browser_download_url")
    _https_url(download_url, "Release asset URL")
    parsed = urllib.parse.urlsplit(download_url)
    expected_path = f"/{owner}/{repo}/releases/download/{tag}/{name}"
    if (parsed.hostname.lower() != "github.com" or parsed.path.lower() != expected_path.lower()
            or parsed.query or parsed.fragment):
        raise UpdateError("GitHub returned an unexpected release asset URL")
    return download_url, size


def _download_public_bundle(url, expected_size, *, run_id, artifact_name, cache):
    destination = cache / f"run-{run_id}-{artifact_name}.zip"
    temporary = cache / f".run-{run_id}-{artifact_name}.part"
    try:
        with urllib.request.urlopen(_request(url), timeout=90) as response:
            final = urllib.parse.urlsplit(_https_url(response.geturl(), "Public build URL"))
            expected = urllib.parse.urlsplit(url)
            if (final.hostname.lower() not in ("github.com", "release-assets.githubusercontent.com")
                    or (final.hostname.lower() == "github.com" and final.path != expected.path)):
                raise UpdateError("Public build bundle redirected to an unexpected URL")
            length = response.headers.get("Content-Length")
            if length:
                try:
                    if int(length) != expected_size:
                        raise UpdateError("Release asset size differs from GitHub metadata")
                    if not 1 <= int(length) <= MAX_BUILD_ARCHIVE_BYTES:
                        raise UpdateError("Public build bundle is empty or exceeds the size limit")
                except ValueError as exc:
                    if isinstance(exc, UpdateError):
                        raise
                    raise UpdateError("Invalid public build Content-Length") from exc
            digest_size = 0
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(temporary, flags, 0o600)
            try:
                with os.fdopen(fd, "wb") as output:
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        digest_size += len(block)
                        if digest_size > MAX_BUILD_ARCHIVE_BYTES or digest_size > expected_size:
                            raise UpdateError("Public build bundle exceeds its declared size")
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
            except Exception:
                try:
                    temporary.unlink()
                except OSError:
                    pass
                raise
        if digest_size != expected_size:
            raise UpdateError("Downloaded public build bundle size differs from GitHub metadata")
        os.replace(temporary, destination)
        return destination
    except UpdateError:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    except Exception as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise UpdateError(f"Could not download the public build bundle: {exc}") from exc


def _read_zip_manifest(archive_path):
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ZIP_MEMBERS:
                raise UpdateError("Workflow artifact ZIP has an invalid member count")
            names = set()
            total = 0
            manifest_info = None
            for info in infos:
                name = info.filename
                # Action artifacts are flat: disallow directories and every path component.
                if (not isinstance(name, str) or not SAFE_NAME.fullmatch(name)
                        or name in names or info.is_dir()):
                    raise UpdateError("Workflow artifact ZIP contains an unsafe or duplicate member")
                names.add(name)
                mode = (info.external_attr >> 16) & 0o170000
                if mode not in (0, stat.S_IFREG):
                    raise UpdateError("Workflow artifact ZIP contains a non-regular file")
                if info.file_size < 0 or info.file_size > MAX_ASSET_BYTES:
                    raise UpdateError("Workflow artifact ZIP member exceeds the size limit")
                total += info.file_size
                if total > MAX_ZIP_UNCOMPRESSED_BYTES:
                    raise UpdateError("Workflow artifact ZIP expands beyond the size limit")
                if info.filename == MANIFEST_NAME:
                    manifest_info = info
            if manifest_info is None:
                raise UpdateError(f"Workflow artifact does not contain {MANIFEST_NAME}")
            if manifest_info.file_size > MAX_BUILD_MANIFEST_BYTES:
                raise UpdateError("Build manifest is too large")
            with archive.open(manifest_info, "r") as handle:
                body = handle.read(MAX_BUILD_MANIFEST_BYTES + 1)
            if len(body) > MAX_BUILD_MANIFEST_BYTES:
                raise UpdateError("Build manifest is too large")
            try:
                manifest = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise UpdateError("Build manifest is not valid JSON") from exc
            return manifest, names
    except UpdateError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError, zlib.error) as exc:
        raise UpdateError(f"Workflow artifact is not a valid ZIP: {exc}") from exc


def fetch_latest_build(repo_url, *, expected_project, target,
                       workflow_file="build-updates.yml", branch="main",
                       artifact_name="tab-companion-build", public_release=False,
                       release_tag_prefix="tab-companion-build"):
    """Fetch the newest successful build package from a workflow artifact or public release.

    Public release assets do not require login. The artifact-API mode remains
    for compatibility with older port feeds, but public-release mode is the
    preferred channel because Actions artifacts expire and may require auth.
    """
    owner, repo = _github_repo(repo_url)
    if not isinstance(expected_project, str) or not expected_project or len(expected_project) > 100:
        raise UpdateError("Invalid expected project ID")
    if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,200}", branch) or ".." in branch.split("/"):
        raise UpdateError("Invalid build branch")
    if (not isinstance(workflow_file, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}\.ya?ml", workflow_file)):
        raise UpdateError("Workflow file must be a YAML filename")
    if not isinstance(artifact_name, str) or not SAFE_NAME.fullmatch(artifact_name):
        raise UpdateError("Invalid build asset name")
    if (not isinstance(release_tag_prefix, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,150}", release_tag_prefix)):
        raise UpdateError("Invalid public build release tag prefix")

    run = _latest_successful_run(owner, repo, workflow_file, branch)
    cache = _cache_dir()
    if public_release:
        download_url, declared_size = _release_asset_for_run(
            owner, repo, run["id"], artifact_name, release_tag_prefix
        )
        archive_path = _download_public_bundle(download_url, declared_size,
                                               run_id=run["id"], artifact_name=artifact_name, cache=cache)
    else:
        artifact, declared_size = _artifact_for_run(owner, repo, run["id"], artifact_name)
        archive_path = _download_action_artifact(artifact["archive_download_url"], declared_size,
                                                 run_id=run["id"], artifact_id=artifact["id"], cache=cache)
    manifest, names = _read_zip_manifest(archive_path)
    asset = _parse_build_manifest(manifest, expected_project=expected_project, target=target, run=run)
    if asset.name not in names:
        raise UpdateError("Build manifest asset is not present in the artifact ZIP")
    return BuildArtifact(run["id"], run["run_number"], run["head_sha"].lower(),
                         run["head_branch"], asset, archive_path)


def extract_verified_asset(build):
    """Extract the selected package from a fetched Actions ZIP and verify SHA/size."""
    if not isinstance(build, BuildArtifact):
        raise UpdateError("Invalid build artifact")
    try:
        info = build.archive_path.lstat()
    except OSError as exc:
        raise UpdateError("Downloaded workflow artifact is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or build.archive_path.is_symlink() or info.st_uid != os.getuid():
        raise UpdateError("Unsafe downloaded workflow artifact")
    manifest, names = _read_zip_manifest(build.archive_path)
    # Ensure the cached ZIP's manifest still describes the selected immutable run/asset.
    if (not isinstance(manifest, dict) or manifest.get("project") != build.asset.project
            or manifest.get("run_id") != build.run_id or manifest.get("run_number") != build.run_number
            or manifest.get("commit") != build.head_sha or manifest.get("branch") != build.branch):
        raise UpdateError("Cached build artifact metadata changed")
    cache = _cache_dir()
    destination_dir = cache / f"run-{build.run_id}"
    destination_dir.mkdir(mode=0o700, exist_ok=True)
    dest_info = destination_dir.lstat()
    if (not stat.S_ISDIR(dest_info.st_mode) or destination_dir.is_symlink()
            or dest_info.st_uid != os.getuid() or dest_info.st_mode & 0o022):
        raise UpdateError("Unsafe extracted package directory")
    destination = destination_dir / build.asset.name
    temporary = destination_dir / ("." + build.asset.name + ".part")
    digest = hashlib.sha256()
    total = 0
    try:
        with zipfile.ZipFile(build.archive_path, "r") as archive:
            with archive.open(build.asset.name, "r") as source:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(temporary, flags, 0o600)
                try:
                    with os.fdopen(fd, "wb") as output:
                        while True:
                            block = source.read(1024 * 1024)
                            if not block:
                                break
                            total += len(block)
                            if total > build.asset.size or total > MAX_ASSET_BYTES:
                                raise UpdateError("Package is larger than the build manifest declares")
                            digest.update(block)
                            output.write(block)
                        output.flush()
                        os.fsync(output.fileno())
                except Exception:
                    try:
                        temporary.unlink()
                    except OSError:
                        pass
                    raise
        if total != build.asset.size:
            raise UpdateError("Package size does not match the build manifest")
        if digest.hexdigest() != build.asset.sha256:
            raise UpdateError("Package SHA-256 verification failed")
        os.replace(temporary, destination)
        return destination
    except UpdateError:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError, zlib.error) as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise UpdateError(f"Could not extract workflow package: {exc}") from exc


class UpdateConfig:
    """Small per-user config; system origin remains package-owned/read-only."""
    def __init__(self, path=None):
        default = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        self.path = Path(path or default / "tab-companion" / "updates.json")
        self.values = self._read()

    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, ValueError):
            pass
        return {}

    def get(self, key, default):
        value = self.values.get(key, default)
        try:
            _github_repo(value)
            return value.rstrip("/")
        except UpdateError:
            return default

    def set(self, key, value):
        _github_repo(value)
        self.values[key] = value.rstrip("/")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            os.chmod(temp, 0o600)
            json.dump(self.values, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.path)


def get_port_record(path="/usr/share/tab-companion/port.json"):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(value, dict) and value.get("port_id"):
            return value
    except (OSError, ValueError):
        pass
    return {}


def installed_port_version(record=None):
    record = get_port_record() if record is None else record
    return str(record.get("version", "unknown"))


def package_manager():
    os_id = parse_os_release().get("ID", "").lower()
    if os_id in ("ubuntu", "debian") and shutil.which("apt-get") and shutil.which("dpkg"):
        return "deb"
    if os_id in ("fedora", "rhel", "centos") and (shutil.which("dnf5") or shutil.which("dnf")) and shutil.which("rpm"):
        return "rpm"
    if os_id == "arch" and shutil.which("pacman"):
        return "pacman"
    return None


def asset_supported_by_manager(asset, manager):
    supported = {
        "deb": {"deb"},
        "rpm": {"rpm"},
        "pacman": {"pacman-local", "aur-source"},
    }.get(manager, set())
    return asset.format in supported
