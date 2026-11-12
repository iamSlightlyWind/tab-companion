# SPDX-License-Identifier: MIT
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tab_companion import platform_backend as backend


class PlatformBackendTests(unittest.TestCase):
    def test_selects_native_backend_for_supported_distributions(self):
        cases = (
            ({"ID": "ubuntu", "ID_LIKE": "debian"}, {"apt-get", "dpkg-deb"}, "deb"),
            ({"ID": "fedora"}, {"dnf5", "rpm"}, "rpm"),
            ({"ID": "archarm", "ID_LIKE": "arch"}, {"pacman"}, "pacman"),
            ({"ID": "endeavouros", "ID_LIKE": "arch"}, {"pacman"}, "pacman"),
        )
        for release, installed, expected in cases:
            with self.subTest(release=release):
                selected = backend.detect_backend(
                    release,
                    which=lambda name, installed=installed: f"/usr/bin/{name}" if name in installed else None,
                )
                self.assertEqual(selected.package_manager, expected)

    def test_unsupported_distro_or_missing_query_tool_has_no_adapter(self):
        unsupported = backend.detect_backend({"ID": "unknown"}, which=lambda _name: None)
        self.assertIsNone(unsupported.package_manager)
        missing_query = backend.detect_backend(
            {"ID": "fedora"},
            which=lambda name: "/usr/bin/dnf5" if name == "dnf5" else None,
        )
        self.assertIsNone(missing_query.package_manager)

    def test_reads_os_release_without_sourcing_shell(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "os-release"
            path.write_text("ID=archarm\nID_LIKE=\"arch\"\nPRETTY_NAME='Arch Linux ARM'\n", encoding="utf-8")
            self.assertEqual(backend.read_os_release(path), {
                "ID": "archarm", "ID_LIKE": "arch", "PRETTY_NAME": "Arch Linux ARM",
            })

    def test_arch_native_update_is_a_full_upgrade_before_local_install(self):
        selected = backend.backend_for_manager("pacman")
        self.assertIsNotNone(selected)
        with patch("tab_companion.platform_backend.shutil.which", return_value="/usr/bin/pacman"), \
             patch("tab_companion.platform_backend._run") as run:
            selected.install_package("pacman-local", Path("/tmp/tab-companion.pkg.tar.zst"))
        self.assertEqual(run.call_args_list[0].args[0], ["/usr/bin/pacman", "-Syu", "--noconfirm"])
        self.assertEqual(run.call_args_list[1].args[0], [
            "/usr/bin/pacman", "-U", "--noconfirm", "/tmp/tab-companion.pkg.tar.zst",
        ])

    def test_backend_format_registry_does_not_build_aur_in_the_privileged_flow(self):
        arch = backend.backend_for_manager("pacman")
        self.assertTrue(arch.supports_format("pacman-local"))
        self.assertFalse(arch.supports_format("aur-source"))
        self.assertEqual(arch.install_notice(),
                         "Arch Linux does not support partial upgrades; the update performs a full system upgrade first.")

    def test_kernel_update_is_opted_in_by_port_capability_not_os_id(self):
        fedora = backend.backend_for_manager("rpm")
        record = {"kernel_update": {"backend": "rpm", "available": True}}
        self.assertTrue(fedora.has_kernel_update({"device": "SM-X810", "os_id": "fedora"}, record))
        self.assertFalse(fedora.has_kernel_update({"device": "SM-X810", "os_id": "archarm"}, {}))
        self.assertFalse(fedora.has_kernel_update({"device": "SM-X910", "os_id": "fedora"}, record))

    def test_port_update_feed_is_opted_in_by_backend_capability(self):
        fedora = backend.backend_for_manager("rpm")
        arch = backend.backend_for_manager("pacman")
        self.assertTrue(fedora.has_port_update({"port_update": {"backend": "rpm", "available": True}}))
        self.assertFalse(arch.has_port_update({"port_update": {"backend": "rpm", "available": True}}))
        self.assertFalse(arch.has_port_update({"port_update": {"backend": "pacman", "available": False}}))

    def test_legacy_updater_ui_is_port_declared_and_helper_checked(self):
        debian = backend.backend_for_manager("deb")
        record = {"legacy_update": {
            "backend": "deb", "available": True,
            "helper": "/usr/libexec/tab-companion-update",
        }}
        with patch.object(Path, "is_file", return_value=True):
            self.assertTrue(debian.has_legacy_update_ui(record))
        self.assertFalse(debian.has_legacy_update_ui({
            "legacy_update": {"backend": "rpm", "available": True,
                              "helper": "/usr/libexec/tab-companion-update"},
        }))
        self.assertFalse(debian.has_legacy_update_ui({
            "legacy_update": {"backend": "deb", "available": False,
                              "helper": "/usr/libexec/tab-companion-update"},
        }))


if __name__ == "__main__":
    unittest.main()
