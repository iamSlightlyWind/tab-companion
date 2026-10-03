# SPDX-License-Identifier: MIT
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import subprocess
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "ports/fedora-x810/tools/tab-companion-power-profile"
SPEC = importlib.util.spec_from_loader(
    "power_profile_helper", SourceFileLoader("power_profile_helper", str(HELPER))
)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class FedoraPowerProfileTests(unittest.TestCase):
    def test_helper_accepts_only_standard_profiles_and_sets_dbus_property(self):
        with mock.patch.object(helper.subprocess, "run") as run:
            self.assertEqual(helper.main([str(HELPER), "performance"]), 0)
        run.assert_called_once_with(
            (*helper.DESTINATION, "performance"), check=True
        )

    def test_helper_rejects_untrusted_arguments_without_privileged_command(self):
        with mock.patch.object(helper.subprocess, "run") as run:
            self.assertEqual(helper.main([str(HELPER), "performance;id"]), 2)
        run.assert_not_called()

    def test_bundle_installs_allowlisted_helper_and_its_polkit_action(self):
        build = (ROOT / "ports/fedora-x810/tools/build-tab-companion-x810-bundle.sh").read_text()
        self.assertIn("tools/tab-companion-power-profile", build)

    def test_bundle_installs_swap_priority_helper_and_dispatches_it(self):
        build = (ROOT / "ports/fedora-x810/tools/build-tab-companion-x810-bundle.sh").read_text(encoding="utf-8")
        dispatcher = (ROOT / "ports/fedora-x810/usr/libexec/tab-companion-admin").read_text(encoding="utf-8")
        self.assertIn("tab-companion-swap-priority", build)
        self.assertIn('"swap-priority": "/usr/libexec/tab-companion-swap-priority"', dispatcher)
        self.assertIn("swap-priority", (ROOT / "src/tab_companion/power_profiles_page.py").read_text(encoding="utf-8"))
        self.assertIn("TabCompanion.PowerProfile.policy", build)
        policy = ROOT / "ports/fedora-x810/packaging/tab-companion-x810/usr/share/polkit-1/actions/io.github.agcarbajo.TabCompanion.PowerProfile.policy"
        self.assertTrue(policy.is_file())
        self.assertIn("auth_admin", policy.read_text())

    def test_thermal_limit_helper_is_allowlisted_in_both_fedora_stages(self):
        for relative in (
            "ports/fedora-x810/usr/libexec/tab-companion-admin",
            "ports/fedora-x810/packaging/tab-companion-x810/usr/libexec/tab-companion-admin",
        ):
            dispatcher = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn('"thermal-limit": "/usr/libexec/tab-companion-thermal-setting"', dispatcher)
            self.assertIn('operation == "thermal-limit"', dispatcher)


if __name__ == "__main__":
    unittest.main()
