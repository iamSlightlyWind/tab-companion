# SPDX-License-Identifier: MIT
"""Root-only, fixed-command native package installer used through polkit."""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

MAX_PACKAGE_SIZE = 4 * 1024**3
DIGEST = re.compile(r"^[0-9a-f]{64}$")
PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_@-]{0,127}$")
STAGING = Path("/var/cache/tab-companion/update-staging")
RPM_TRANSACTION_MANIFEST = "rpm-transaction.json"
MAX_TRANSACTION_PACKAGES = 16
PACKAGE_SUFFIXES = {
    "deb": ".deb", "rpm": ".rpm", "rpm-transaction": ".zip",
    "pacman-local": ".pkg.tar.zst",
}


def _run(argv):
    subprocess.run(argv, check=True, env={**os.environ, "LC_ALL": "C", "DEBIAN_FRONTEND": "noninteractive"})


def _copy_verified(source, expected, uid, fmt):
    suffix = PACKAGE_SUFFIXES.get(fmt)
    if suffix is None:
        raise ValueError("Unsupported native package format")
    source = Path(source)
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    temp_path = None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or not 0 < info.st_size <= MAX_PACKAGE_SIZE:
            raise ValueError("Update file must be a regular file owned by the requesting user and below 4 GiB")
        STAGING.mkdir(mode=0o755, parents=True, exist_ok=True)
        st = STAGING.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
            raise ValueError("Unsafe root-owned update staging directory")
        # Native package managers infer local package files from their suffix.
        # Without it, DNF5 treats the staging path as a package name/NEVRA.
        out_fd, temp_path = tempfile.mkstemp(prefix="package-", suffix=suffix, dir=STAGING)
        digest = hashlib.sha256()
        total = 0
        with os.fdopen(out_fd, "wb") as output, os.fdopen(fd, "rb", closefd=False) as input_file:
            while True:
                block = input_file.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > MAX_PACKAGE_SIZE:
                    raise ValueError("Update package is too large")
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if total != info.st_size or digest.hexdigest() != expected:
            raise ValueError("Update package changed or failed SHA-256 verification")
        os.chmod(temp_path, 0o600)
        return Path(temp_path)
    except Exception:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        raise
    finally:
        os.close(fd)


def _os_id():
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("ID="):
                return line[3:].strip().strip("\"'").lower()
    except OSError:
        pass
    return ""


def _verify_package(path, fmt, package_name, package_version, expected_arch):
    if not PACKAGE.fullmatch(package_name):
        raise ValueError("Invalid package name")
    if fmt == "deb":
        if _os_id() not in ("ubuntu", "debian") or not shutil.which("dpkg-deb"):
            raise ValueError("DEB packages can only be installed on Debian/Ubuntu")
        raw = subprocess.check_output(["dpkg-deb", "-f", str(path), "Package", "Version", "Architecture"], text=True)
        fields = dict(line.split(": ", 1) for line in raw.splitlines() if ": " in line)
        found, found_version, found_arch = (fields[key] for key in ("Package", "Version", "Architecture"))
        expected_arch = {"aarch64": "arm64"}.get(expected_arch, expected_arch)
    elif fmt == "rpm":
        if _os_id() not in ("fedora", "rhel", "centos") or not shutil.which("rpm"):
            raise ValueError("RPM packages can only be installed on Fedora/RHEL")
        fields = subprocess.check_output(["rpm", "-qp", "--queryformat", "%{NAME}\\n%{VERSION}-%{RELEASE}\\n%{ARCH}", str(path)], text=True).splitlines()
        found, found_version, found_arch = fields
    elif fmt == "rpm-transaction":
        # A transaction archive is verified and expanded as a set immediately
        # before DNF installs it; it is not itself an RPM package.
        if _os_id() not in ("fedora", "rhel", "centos") or not shutil.which("rpm"):
            raise ValueError("RPM transactions can only be installed on Fedora/RHEL")
        if package_name != "x810-fedora-update" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_~-]{0,127}", package_version):
            raise ValueError("Invalid X810 Fedora transaction identity")
        return
    elif fmt == "pacman-local":
        if _os_id() != "arch" or not shutil.which("pacman"):
            raise ValueError("Arch packages can only be installed on Arch Linux")
        fields = subprocess.check_output(["pacman", "-Qp", "--print-format", "%n\\n%v\\n%a", str(path)], text=True).splitlines()
        found, found_version, found_arch = fields
    else:
        raise ValueError("Unsupported native package format")
    if found != package_name:
        raise ValueError("Package name does not match the build manifest")
    if found_version != package_version:
        raise ValueError("Package version does not match the build manifest")
    if found_arch not in (expected_arch, "all", "any", "noarch"):
        raise ValueError("Package architecture does not match this device")


def _extract_rpm_transaction(path, destination, expected_arch):
    """Verify and safely extract all RPMs from one bounded X810 update transaction."""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not 2 <= len(infos) <= MAX_TRANSACTION_PACKAGES + 1:
                raise ValueError("RPM transaction has an invalid package count")
            members = {}
            total_size = 0
            for info in infos:
                name = info.filename
                mode = (info.external_attr >> 16) & 0o170000
                if (not name or name in members or "/" in name or "\\" in name
                        or name in (".", "..") or info.is_dir()
                        or mode not in (0, stat.S_IFREG)):
                    raise ValueError("RPM transaction contains an unsafe or duplicate member")
                total_size += info.file_size
                if info.file_size <= 0 or total_size > MAX_PACKAGE_SIZE:
                    raise ValueError("RPM transaction exceeds its uncompressed size limit")
                members[name] = info

            manifest_info = members.get(RPM_TRANSACTION_MANIFEST)
            if manifest_info is None or manifest_info.file_size > 64 * 1024:
                raise ValueError("RPM transaction manifest is missing or oversized")
            manifest = json.loads(archive.read(manifest_info))
            if not isinstance(manifest, dict):
                raise ValueError("RPM transaction manifest must be a JSON object")
            records = manifest.get("packages")
            if (manifest.get("schema_version") != 1 or manifest.get("project") != "x810-fedora"
                    or not isinstance(records, list) or not 1 <= len(records) <= MAX_TRANSACTION_PACKAGES):
                raise ValueError("Unsupported RPM transaction manifest")
            if any(not isinstance(item, dict) for item in records):
                raise ValueError("RPM transaction package records must be objects")
            if set(members) != {RPM_TRANSACTION_MANIFEST} | {
                    item.get("name") for item in records if isinstance(item, dict)}:
                raise ValueError("RPM transaction files do not match its manifest")

            extracted = []
            seen_names = set()
            for record in records:
                name = record.get("name")
                digest = record.get("sha256")
                size = record.get("size")
                package_name = record.get("package_name")
                package_version = record.get("package_version")
                if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,180}\.rpm", name)
                        or name in seen_names or not isinstance(digest, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", digest)
                        or isinstance(size, bool) or not isinstance(size, int)
                        or size != members[name].file_size or not PACKAGE.fullmatch(package_name or "")
                        or not isinstance(package_version, str)
                        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:.+_~-]{0,127}", package_version)):
                    raise ValueError("Invalid RPM package record")
                seen_names.add(name)
                target = destination / name
                hasher = hashlib.sha256()
                written = 0
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                try:
                    with archive.open(members[name]) as source, os.fdopen(fd, "wb") as output:
                        while True:
                            block = source.read(1024 * 1024)
                            if not block:
                                break
                            written += len(block)
                            if written > size:
                                raise ValueError("RPM package exceeded declared size")
                            hasher.update(block)
                            output.write(block)
                        output.flush()
                        os.fsync(output.fileno())
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
                if written != size or hasher.hexdigest() != digest:
                    target.unlink(missing_ok=True)
                    raise ValueError("RPM package checksum or size mismatch")
                fields = subprocess.check_output(
                    ["rpm", "-qp", "--queryformat", "%{NAME}\\n%{VERSION}-%{RELEASE}\\n%{ARCH}", str(target)],
                    text=True,
                ).splitlines()
                if len(fields) != 3 or fields[0] != package_name or fields[1] != package_version:
                    raise ValueError("RPM metadata does not match transaction manifest")
                if fields[2] not in (expected_arch, "noarch"):
                    raise ValueError("RPM architecture does not match this tablet")
                extracted.append(target)
            if len({record["package_name"] for record in records}) != len(records):
                raise ValueError("RPM transaction contains duplicate package names")
            return extracted
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"Invalid RPM transaction archive: {exc}") from exc


def install(source, expected_hash, fmt, package_name, package_version, expected_arch, requesting_uid):
    if not DIGEST.fullmatch(expected_hash):
        raise ValueError("Invalid SHA-256 value")
    path = _copy_verified(source, expected_hash, requesting_uid, fmt)
    try:
        _verify_package(path, fmt, package_name, package_version, expected_arch)
        if fmt == "deb":
            _run(["apt-get", "install", "--yes", "--no-remove", str(path)])
        elif fmt == "rpm":
            manager = shutil.which("dnf5") or shutil.which("dnf")
            if not manager:
                raise ValueError("DNF is not installed")
            _run([manager, "install", "--assumeyes", "--setopt=clean_requirements_on_remove=False", str(path)])
        elif fmt == "rpm-transaction":
            manager = shutil.which("dnf5") or shutil.which("dnf")
            if not manager:
                raise ValueError("DNF is not installed")
            with tempfile.TemporaryDirectory(prefix="rpm-transaction-", dir=STAGING) as temporary:
                rpms = _extract_rpm_transaction(path, Path(temporary), expected_arch)
                _run([manager, "install", "--assumeyes", "--setopt=clean_requirements_on_remove=False",
                      *map(str, rpms)])
        else:
            # Arch does not support partial upgrades. Bring the installed system
            # fully in sync before installing this verified local package.
            _run(["pacman", "-Syu", "--noconfirm"])
            _run(["pacman", "-U", "--noconfirm", str(path)])
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--format", required=True, choices=("deb", "rpm", "rpm-transaction", "pacman-local"))
    parser.add_argument("--package-name", required=True)
    parser.add_argument("--package-version", required=True)
    parser.add_argument("--expected-arch", required=True, choices=("aarch64", "x86_64"))
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("This helper must be run through polkit", file=sys.stderr)
        return 1
    try:
        uid = int(os.environ.get("PKEXEC_UID", "-1"))
        if uid < 0:
            raise ValueError("Cannot identify the requesting user")
        install(args.path, args.sha256, args.format, args.package_name,
                args.package_version, args.expected_arch, uid)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Package update installed successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
