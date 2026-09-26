# SPDX-License-Identifier: MIT
import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "make_legacy_release_assets.py"
SPEC = importlib.util.spec_from_file_location("make_legacy_release_assets", SCRIPT)
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class LegacyReleaseAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "release"
        self.identity = {
            "run_id": 12345,
            "run_number": 9,
            "commit": "a" * 40,
            "branch": "main",
        }
        for kind, fmt in BUILDER.FORMATS.items():
            directory = self.root / f"tab-companion-{kind}"
            directory.mkdir()
            name = {"ubuntu": "companion.deb", "fedora": "companion.rpm",
                    "arch": "companion-aur.tar.gz"}[kind]
            package = b"package bytes: " + kind.encode()
            (directory / name).write_bytes(package)
            manifest = {
                "schema_version": 1,
                "project": "tab-companion",
                "version": "1.4.2.9",
                **self.identity,
                "assets": [{
                    "name": name,
                    "sha256": hashlib.sha256(package).hexdigest(),
                    "size": len(package),
                    "format": fmt,
                    "package_name": "tab-companion",
                    "package_version": "1.4.2.9",
                    "target": {"os_id": kind, "arch": "aarch64", "device": "SM-X810"},
                }],
            }
            (directory / BUILDER.MANIFEST).write_text(json.dumps(manifest))

    def build(self):
        return BUILDER.create_release_assets(
            self.root, self.output, repo="iamSlightlyWind/tab-companion",
            tag=f"tab-companion-build-{self.identity['run_id']}", **self.identity,
        )

    def test_builds_legacy_index_and_standalone_packages_for_old_installs(self):
        index = self.build()
        self.assertEqual(index["version"], "1.4.2.9")
        self.assertEqual({asset["format"] for asset in index["assets"]}, set(BUILDER.FORMATS.values()))
        for asset in index["assets"]:
            self.assertEqual(asset["url"],
                f"https://github.com/iamSlightlyWind/tab-companion/releases/download/"
                f"tab-companion-build-12345/{asset['name']}")
            self.assertEqual((self.output / asset["name"]).read_bytes(),
                             (self.root / f"tab-companion-"
                              f"{next(k for k, v in BUILDER.FORMATS.items() if v == asset['format'])}"
                              / asset["name"]).read_bytes())
        self.assertEqual(json.loads((self.output / "tab-companion-release.json").read_text()), index)
        for kind in BUILDER.FORMATS:
            with zipfile.ZipFile(self.output / f"tab-companion-{kind}.zip") as archive:
                self.assertIn(BUILDER.MANIFEST, archive.namelist())

    def test_rejects_artifact_from_different_workflow_run(self):
        directory = self.root / "tab-companion-fedora"
        manifest_path = directory / BUILDER.MANIFEST
        document = json.loads(manifest_path.read_text())
        document["run_id"] += 1
        manifest_path.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "run_id"):
            self.build()

    def test_rejects_package_checksum_mismatch(self):
        package = self.root / "tab-companion-ubuntu" / "companion.deb"
        original = package.read_bytes()
        package.write_bytes(b"X" + original[1:])
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.build()


if __name__ == "__main__":
    unittest.main()
