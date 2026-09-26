# SPDX-License-Identifier: MIT
import tempfile
import unittest
from pathlib import Path
import sys
import hashlib
import os
import stat
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tab_companion.package_installer import _copy_verified, _verify_package


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
