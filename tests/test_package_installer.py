# SPDX-License-Identifier: MIT
import tempfile
import unittest
from pathlib import Path
import sys
import hashlib
import json
import zipfile
import os
import stat
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tab_companion.package_installer import (
    RPM_TRANSACTION_MANIFEST, _copy_verified, _extract_rpm_transaction, _verify_package, install
)


class PackageMetadataTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.gettempdir()) / "test-package"

    def _check(self, os_id, fmt, result, expected_version, arch):
        with patch("tab_companion.package_installer._os_id", return_value=os_id), \
             patch("tab_companion.package_installer.shutil.which", return_value="/usr/bin/tool"), \
             patch("tab_companion.package_installer.subprocess.check_output", return_value=result):
            _verify_package(self.path, fmt, "tab-companion", expected_version, arch)

    def test_rpm_metadata_matches(self):
        self._check("fedora", "rpm", "tab-companion\n1.2.0-1.fc44\nnoarch", "1.2.0-1.fc44", "aarch64")

    def test_deb_maps_aarch64_to_arm64(self):
        self._check("ubuntu", "deb", "Package: tab-companion\nVersion: 1.2.0-1\nArchitecture: arm64", "1.2.0-1", "aarch64")

    def test_arch_local_package_metadata_matches(self):
        self._check("arch", "pacman-local", "tab-companion\n1.2.0-1\naarch64", "1.2.0-1", "aarch64")

    def test_wrong_release_version_rejected(self):
        with patch("tab_companion.package_installer._os_id", return_value="fedora"), \
             patch("tab_companion.package_installer.shutil.which", return_value="/usr/bin/rpm"), \
             patch("tab_companion.package_installer.subprocess.check_output", return_value="tab-companion\n1.0.0-1.fc44\naarch64"):
            with self.assertRaisesRegex(ValueError, "version"):
                _verify_package(self.path, "rpm", "tab-companion", "2.0.0-1.fc44", "aarch64")


class RpmTransactionTests(unittest.TestCase):
    def _archive(self, root, *, member_name="mutter.rpm", payload=b"fake rpm"):
        path = root / "transaction.zip"
        record = {
            "name": member_name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
            "package_name": "mutter",
            "package_version": "50.5-1000000.123.fc44",
        }
        manifest = {"schema_version": 1, "project": "x810-fedora", "packages": [record]}
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(RPM_TRANSACTION_MANIFEST, json.dumps(manifest))
            archive.writestr(member_name, payload)
        return path

    def test_transaction_extract_verifies_hash_and_rpm_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transaction = self._archive(root)
            destination = root / "out"
            destination.mkdir()
            with patch("tab_companion.package_installer.subprocess.check_output",
                       return_value="mutter\n50.5-1000000.123.fc44\naarch64"):
                files = _extract_rpm_transaction(transaction, destination, "aarch64")
            self.assertEqual([path.name for path in files], ["mutter.rpm"])
            self.assertEqual(files[0].read_bytes(), b"fake rpm")

    def test_transaction_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transaction = self._archive(root, member_name="../mutter.rpm")
            with self.assertRaisesRegex(ValueError, "unsafe or duplicate"):
                _extract_rpm_transaction(transaction, root / "out", "aarch64")

    def test_transaction_rejects_wrong_rpm_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transaction = self._archive(root)
            destination = root / "out"
            destination.mkdir()
            with patch("tab_companion.package_installer.subprocess.check_output",
                       return_value="mutter\n50.4-1.fc44\naarch64"):
                with self.assertRaisesRegex(ValueError, "metadata"):
                    _extract_rpm_transaction(transaction, destination, "aarch64")

    def test_install_uses_one_atomic_dnf_transaction_for_all_rpms(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "verified.zip"
            staged.write_bytes(b"bundle")
            rpms = [root / "port.rpm", root / "mutter.rpm", root / "common.rpm"]
            with patch("tab_companion.package_installer._copy_verified", return_value=staged), \
                 patch("tab_companion.package_installer.STAGING", root), \
                 patch("tab_companion.package_installer._os_id", return_value="fedora"), \
                 patch("tab_companion.package_installer.shutil.which", return_value="/usr/bin/dnf5"), \
                 patch("tab_companion.package_installer._extract_rpm_transaction", return_value=rpms), \
                 patch("tab_companion.package_installer._run") as run:
                install(root / "download.zip", "a" * 64, "rpm-transaction",
                        "x810-fedora-update", "0.1.0+123", "aarch64", os.getuid())
            run.assert_called_once_with([
                "/usr/bin/dnf5", "install", "--assumeyes",
                "--setopt=clean_requirements_on_remove=False",
                *(str(path) for path in rpms),
            ])
            self.assertFalse(staged.exists())


class StagedPackageTests(unittest.TestCase):
    def test_staged_package_keeps_native_manager_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source-file"
            source.write_bytes(b"verified package contents")
            staging = root / "staging"
            staging.mkdir()
            cases = (
                ("rpm", ".rpm"),
                ("rpm-transaction", ".zip"),
                ("deb", ".deb"),
                ("pacman-local", ".pkg.tar.zst"),
            )
            for fmt, suffix in cases:
                with self.subTest(fmt=fmt), \
                     patch("tab_companion.package_installer.STAGING", staging), \
                     patch.object(Path, "lstat", return_value=SimpleNamespace(
                         st_uid=0, st_mode=stat.S_IFDIR | 0o755)):
                    path = _copy_verified(
                        source,
                        hashlib.sha256(source.read_bytes()).hexdigest(),
                        os.getuid(),
                        fmt,
                    )
                    try:
                        self.assertTrue(path.name.endswith(suffix))
                        self.assertEqual(path.read_bytes(), source.read_bytes())
                    finally:
                        path.unlink()


if __name__ == "__main__":
    unittest.main()
