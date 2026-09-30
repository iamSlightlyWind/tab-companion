# SPDX-License-Identifier: MIT
import importlib.machinery
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

from tab_companion import admin_auth


ROOT = Path(__file__).resolve().parents[1]
ADMIN_HELPER = ROOT / "ports/fedora-x810/usr/libexec/tab-companion-admin"
POLICY = ROOT / "ports/fedora-x810/packaging/tab-companion-x810/usr/share/polkit-1/actions/io.github.agcarbajo.TabCompanion.Admin.policy"

loader = importlib.machinery.SourceFileLoader("tab_companion_admin_helper", str(ADMIN_HELPER))
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class AdminAuthTests(unittest.TestCase):
    def test_all_fedora_privileged_requests_share_one_pkexec_action(self):
        with patch.object(admin_auth, "available", return_value=True):
            command = admin_auth.command("kernel-update", "/old/helper", "apply", "--release-dir", "/cache/x")
        self.assertEqual(command[:4], ["pkexec", admin_auth.ADMIN_HELPER, "--run", "kernel-update"])
        self.assertEqual(command[4:], ["apply", "--release-dir", "/cache/x"])

    def test_non_fedora_keeps_its_existing_fixed_helper(self):
        with patch.object(admin_auth, "available", return_value=False):
            self.assertEqual(admin_auth.command("install-package", "/old/installer", "--path", "/tmp/a.rpm"),
                             ["pkexec", "/old/installer", "--path", "/tmp/a.rpm"])

    def test_dispatcher_accepts_only_fixed_actions_and_validates_simple_arguments(self):
        path, args = helper.validate("power-profile", ["balanced"])
        self.assertEqual(path, "/usr/libexec/tab-companion-power-profile")
        self.assertEqual(args, ["balanced"])
        with self.assertRaisesRegex(ValueError, "Unsupported privileged operation"):
            helper.validate("/bin/sh", ["-c", "id"])
        with self.assertRaisesRegex(ValueError, "Invalid power-profile"):
            helper.validate("power-profile", ["--help"])
        with self.assertRaisesRegex(ValueError, "Invalid boot-switch"):
            helper.validate("boot-switch", ["../../etc/passwd"])

    def test_zram_dispatch_is_fixed_and_accepts_only_allowlisted_sizes(self):
        path, args = helper.validate("zram-size", ["43"])
        self.assertEqual(path, "/usr/libexec/tab-companion-zram-size")
        self.assertEqual(args, ["43"])
        for args in ([], ["43", ";id"], ["0"], ["121"], ["4.3"]):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, "Invalid zram-size"):
                helper.validate("zram-size", args)

    def test_shared_policy_keeps_one_authenticated_session_for_all_operations(self):
        policy = POLICY.read_text(encoding="utf-8")
        self.assertIn("<allow_active>auth_admin_keep</allow_active>", policy)
        self.assertIn("/usr/libexec/tab-companion-admin", policy)
        helper_text = ADMIN_HELPER.read_text(encoding="utf-8")
        self.assertIn('if argv == ["--authorize"]:', helper_text)


if __name__ == "__main__":
    unittest.main()
