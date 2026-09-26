# SPDX-License-Identifier: MIT
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tab_companion.package_installer import _verify_package


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


if __name__ == "__main__":
    unittest.main()
