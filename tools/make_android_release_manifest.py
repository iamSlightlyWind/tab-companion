#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Write and validate provenance metadata for the Android switcher APK."""

import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path


PROJECT = "tab-companion-android"
MAX_APK_BYTES = 256 * 1024 * 1024
COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")


def _gradle_identity(path):
    source = path.read_text(encoding="utf-8")
    fields = {}
    patterns = {
        "application_id": r'^\s*applicationId\s*=\s*"([^"]+)"\s*$',
        "version_name": r'^\s*versionName\s*=\s*"([^"]+)"\s*$',
        "version_code": r"^\s*versionCode\s*=\s*([0-9]+)\s*$",
    }
    for field, pattern in patterns.items():
        matches = re.findall(pattern, source, re.MULTILINE)
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one {field} in {path}")
        fields[field] = matches[0]
    if not PACKAGE.fullmatch(fields["application_id"]):
        raise ValueError("Invalid Android application ID")
    fields["version_code"] = int(fields["version_code"])
    if fields["version_code"] < 1 or not re.fullmatch(r"[A-Za-z0-9.+_-]{1,80}", fields["version_name"]):
        raise ValueError("Invalid Android version metadata")
    return fields


def create_manifest(apk_path, output_path, gradle_path, environ=None):
    env = os.environ if environ is None else environ
    apk_path = Path(apk_path)
    output_path = Path(output_path)
    gradle_path = Path(gradle_path)
    try:
        info = apk_path.lstat()
    except OSError as exc:
        raise ValueError("APK is unavailable") from exc
    if not apk_path.is_file() or apk_path.is_symlink() or not 1 <= info.st_size <= MAX_APK_BYTES:
        raise ValueError("APK is not a regular file of an acceptable size")
    try:
        with zipfile.ZipFile(apk_path) as archive:
            names = archive.namelist()
            if "AndroidManifest.xml" not in names or archive.testzip() is not None:
                raise ValueError("APK ZIP contents are invalid")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("APK is not a valid ZIP archive") from exc

    try:
        run_id = int(env["APP_BUILD_RUN_ID"])
        run_number = int(env["APP_BUILD_RUN_NUMBER"])
    except (KeyError, ValueError) as exc:
        raise ValueError("Build run ID and number must be integers") from exc
    commit = env.get("APP_BUILD_HEAD_SHA", "")
    branch = env.get("APP_BUILD_BRANCH", "")
    if run_id < 1 or run_number < 1 or not COMMIT.fullmatch(commit) or branch != "main":
        raise ValueError("Invalid GitHub Actions build identity")

    digest = hashlib.sha256()
    with apk_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    document = {
        "schema_version": 1,
        "project": PROJECT,
        "run_id": run_id,
        "run_number": run_number,
        "commit": commit,
        "branch": branch,
        "application": _gradle_identity(gradle_path),
        "asset": {
            "name": apk_path.name,
            "format": "apk",
            "size": info.st_size,
            "sha256": digest.hexdigest(),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return document


def main():
    if len(sys.argv) != 4:
        raise SystemExit("Usage: make_android_release_manifest.py APK OUTPUT_JSON APP_BUILD_GRADLE")
    try:
        document = create_manifest(*sys.argv[1:])
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Wrote {sys.argv[2]} for {document['application']['application_id']} build {document['run_number']}")


if __name__ == "__main__":
    main()
