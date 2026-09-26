#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Assemble public update assets, including the legacy release-index format.

Older installed Tab Companion versions fetch ``tab-companion-release.json``
from GitHub's latest-release URL. Keep that bootstrap path working alongside
the per-build ZIP bundles consumed by newer versions.
"""

import argparse
import hashlib
import json
import re
import shutil
import urllib.parse
import zipfile
from pathlib import Path


PROJECT = "tab-companion"
MANIFEST = "tab-companion-update.json"
FORMATS = {"ubuntu": "deb", "fedora": "rpm", "arch": "aur-source"}
SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,180}$")
SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,80}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_for(root, kind, identity):
    directory = root / f"tab-companion-{kind}"
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError(f"Missing or unsafe {kind} artifact directory")
    members = sorted(directory.iterdir())
    if not members or any(not path.is_file() or path.is_symlink() for path in members):
        raise ValueError(f"Invalid or empty {kind} build artifact")
    path = directory / MANIFEST
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid {kind} build manifest") from exc
    if not isinstance(document, dict):
        raise ValueError(f"Invalid {kind} build manifest")
    for key, value in identity.items():
        if document.get(key) != value:
            raise ValueError(f"{kind} manifest has mismatched {key}")
    version = document.get("version")
    if document.get("schema_version") != 1 or document.get("project") != PROJECT:
        raise ValueError(f"Unexpected {kind} build manifest identity")
    if not isinstance(version, str) or not SAFE_VERSION.fullmatch(version):
        raise ValueError(f"Invalid {kind} app version")
    assets = document.get("assets")
    if not isinstance(assets, list) or len(assets) != 1 or not isinstance(assets[0], dict):
        raise ValueError(f"Expected one package in the {kind} artifact")
    asset = assets[0]
    name = asset.get("name")
    if not isinstance(name, str) or not SAFE_FILE.fullmatch(name) or Path(name).name != name:
        raise ValueError(f"Invalid package name in {kind} manifest")
    package = directory / name
    if not package.is_file() or package.is_symlink() or package not in members:
        raise ValueError(f"{kind} package is missing from its artifact")
    if asset.get("format") != FORMATS[kind]:
        raise ValueError(f"Unexpected package format in {kind} manifest")
    if not isinstance(asset.get("package_name"), str) or not asset["package_name"]:
        raise ValueError(f"Invalid package identity in {kind} manifest")
    if not isinstance(asset.get("package_version"), str) or not asset["package_version"]:
        raise ValueError(f"Invalid package version in {kind} manifest")
    if not isinstance(asset.get("target"), dict):
        raise ValueError(f"Invalid package target in {kind} manifest")
    size = asset.get("size")
    digest = asset.get("sha256")
    if isinstance(size, bool) or not isinstance(size, int) or size != package.stat().st_size:
        raise ValueError(f"Package size differs from the {kind} manifest")
    if not isinstance(digest, str) or not SHA256.fullmatch(digest) or _digest(package) != digest:
        raise ValueError(f"Package checksum differs from the {kind} manifest")
    return directory, members, document, asset


def create_release_assets(root, output, *, repo, tag, run_id, run_number, commit, branch):
    if repo != "iamSlightlyWind/tab-companion":
        raise ValueError("Unexpected release repository")
    if tag != f"tab-companion-build-{run_id}":
        raise ValueError("Release tag must identify this workflow run")
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit) or branch != "main":
        raise ValueError("Invalid workflow commit or branch")
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    identity = {"run_id": run_id, "run_number": run_number,
                "commit": commit, "branch": branch}
    index_assets = []
    app_version = None
    package_names = set()

    for kind in FORMATS:
        directory, members, document, asset = _manifest_for(root, kind, identity)
        version = document["version"]
        if app_version is None:
            app_version = version
        elif version != app_version:
            raise ValueError("Platform manifests have different app versions")
        name = asset["name"]
        if name in package_names or name == "tab-companion-release.json":
            raise ValueError(f"Duplicate release asset name: {name}")
        package_names.add(name)
        shutil.copyfile(directory / name, output / name)
        index_assets.append({
            **asset,
            "url": f"https://github.com/{repo}/releases/download/{tag}/"
                  f"{urllib.parse.quote(name, safe='')}",
        })
        with zipfile.ZipFile(output / f"tab-companion-{kind}.zip", "w",
                             compression=zipfile.ZIP_DEFLATED) as bundle:
            for member in members:
                bundle.write(member, member.name)

    index = {"schema_version": 1, "project": PROJECT,
             "version": app_version, "assets": index_assets}
    (output / "tab-companion-release.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="directory containing the downloaded build artifacts")
    parser.add_argument("output", type=Path, help="directory for public release assets")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-number", required=True, type=int)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--branch", required=True)
    args = parser.parse_args()
    create_release_assets(args.root, args.output, repo=args.repo, tag=args.tag,
                          run_id=args.run_id, run_number=args.run_number,
                          commit=args.commit, branch=args.branch)
    print(f"Prepared legacy index and app packages for {args.tag}")


if __name__ == "__main__":
    main()
