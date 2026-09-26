#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Create the index shipped alongside one successful Actions build."""

import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path


def fail(message):
    raise SystemExit(message)


def one(root, pattern):
    matches = sorted(root.glob(pattern))
    if len(matches) != 1 or not matches[0].is_file():
        fail(f"Expected exactly one {pattern} in {root}; found {len(matches)}")
    return matches[0]


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fields(command, path):
    """Run a package metadata query with the archive at its explicit position.

    dpkg-deb expects ``-f ARCHIVE FIELD...`` while rpm expects the archive at
    the end of its query arguments.  Requiring a single placeholder keeps the
    caller's command order unambiguous.
    """
    if command.count("{archive}") != 1:
        fail("Package metadata command must contain exactly one {archive} placeholder")
    args = [str(path) if item == "{archive}" else item for item in command]
    return subprocess.check_output(args, text=True).splitlines()


def deb_package_fields(path):
    """Read Debian control fields, which dpkg-deb prints as ``Field: value``."""
    output = subprocess.check_output(
        ["dpkg-deb", "-f", str(path), "Package", "Version", "Architecture"],
        text=True,
    )
    parsed = {}
    for line in output.splitlines():
        name, separator, value = line.partition(":")
        if not separator or name in parsed:
            fail("dpkg-deb returned invalid package metadata")
        parsed[name] = value.strip()
    required = ("Package", "Version", "Architecture")
    if any(not parsed.get(name) for name in required):
        fail("dpkg-deb omitted required package metadata")
    return tuple(parsed[name] for name in required)


def aur_metadata(path):
    with tarfile.open(path, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.name == "PKGBUILD"]
        bundle = [member for member in archive.getmembers() if member.name == "tab-companion-x810.tar.gz"]
        if len(members) != 1 or not members[0].isfile() or len(bundle) != 1 or not bundle[0].isfile():
            fail("Arch source artifact must contain one PKGBUILD and its self-contained app bundle")
        source = archive.extractfile(members[0]).read().decode("utf-8")
    values = {}
    for key in ("pkgname", "pkgver", "pkgrel"):
        match = re.search(rf"^{key}=([^\n]+)$", source, re.MULTILINE)
        if not match:
            fail(f"Generated PKGBUILD is missing {key}")
        values[key] = match.group(1).strip("'\"")
    if values["pkgname"] != "tab-companion" or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", values["pkgver"]):
        fail("Generated Arch package identity is invalid")
    if not re.fullmatch(r"[1-9][0-9]*", values["pkgrel"]):
        fail("Generated Arch package release is invalid")
    return values


def main():
    if len(sys.argv) != 2:
        fail("Usage: tools/make-actions-index.py BUILD_DIRECTORY")
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        fail(f"Build directory does not exist: {root}")

    run_id = int(os.environ["APP_BUILD_RUN_ID"])
    run_number = int(os.environ["APP_BUILD_RUN_NUMBER"])
    commit = os.environ["APP_BUILD_HEAD_SHA"]
    branch = os.environ["APP_BUILD_BRANCH"]
    if run_id <= 0 or run_number <= 0:
        fail("Actions run ID and run number must be positive")
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        fail("Actions commit SHA is invalid")
    if not branch or len(branch) > 255 or any(ord(ch) < 32 for ch in branch):
        fail("Actions branch name is invalid")

    deb = one(root, "*.deb")
    rpm = one(root, "*.rpm")
    aur = one(root, "tab-companion-arch-source.tar.gz")
    deb_name, deb_version, deb_arch = deb_package_fields(deb)
    rpm_name, rpm_version, rpm_arch = fields(
        ["rpm", "-qp", "--queryformat", "%{NAME}\\n%{VERSION}-%{RELEASE}\\n%{ARCH}", "{archive}"], rpm
    )
    aur_values = aur_metadata(aur)
    if deb_arch != "all" or deb_name != "ubuntu-gts9u-companion":
        fail(f"Unexpected DEB metadata: {deb_name} {deb_version} {deb_arch}")
    if rpm_arch != "noarch" or rpm_name != "tab-companion":
        fail(f"Unexpected RPM metadata: {rpm_name} {rpm_version} {rpm_arch}")

    base_version = re.sub(r"[^0-9.].*$", "", deb_version)
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", base_version):
        fail(f"Could not derive numeric app version from DEB {deb_version}")
    version = f"{base_version}.{run_number}"

    def asset(path, fmt, package_name, package_version, target):
        return {
            "name": path.name,
            "sha256": checksum(path),
            "size": path.stat().st_size,
            "format": fmt,
            "package_name": package_name,
            "package_version": package_version,
            "target": target,
        }

    document = {
        "schema_version": 1,
        "project": "tab-companion",
        "version": version,
        "run_id": run_id,
        "run_number": run_number,
        "commit": commit,
        "branch": branch,
        "assets": [
            asset(deb, "deb", deb_name, deb_version, {
                "os_id": "ubuntu", "os_version": "24.04", "arch": "aarch64", "device": "SM-X910",
            }),
            asset(rpm, "rpm", rpm_name, rpm_version, {
                "os_id": "fedora", "os_version": "44", "arch": "aarch64", "device": "SM-X810",
            }),
            asset(aur, "aur-source", aur_values["pkgname"],
                  f"{aur_values['pkgver']}-{aur_values['pkgrel']}", {
                "os_id": "arch", "os_version": "*", "arch": "aarch64", "device": "SM-X810",
            }),
        ],
    }
    output = root / "tab-companion-update.json"
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output}: app build {version}, run {run_number}")


if __name__ == "__main__":
    main()
