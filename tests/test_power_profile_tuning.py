# SPDX-License-Identifier: MIT
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import tempfile
import unittest

from tab_companion.power_profile_tuning import (
    DEFAULT_SETTINGS,
    read_settings,
    valid_settings,
)


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "ports/fedora-x810/tools/tab-companion-performance-setting"
SPEC = importlib.util.spec_from_loader(
    "performance_setting_helper", SourceFileLoader("performance_setting_helper", str(HELPER))
)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class PowerProfileTuningTests(unittest.TestCase):
    def test_defaults_are_conservative_for_balanced_and_power_saver(self):
        self.assertEqual(DEFAULT_SETTINGS["profiles"]["balanced"], {
            "max_percent": 75, "cpu_boost": False, "gpu_boost": False,
        })
        self.assertEqual(DEFAULT_SETTINGS["profiles"]["power-saver"], {
            "max_percent": 55, "cpu_boost": False, "gpu_boost": False,
        })
        self.assertEqual(DEFAULT_SETTINGS["profiles"]["performance"], {
            "max_percent": 100, "cpu_boost": True, "gpu_boost": True,
        })
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(read_settings(Path(directory) / "missing.json"), DEFAULT_SETTINGS)

    def test_settings_validate_exact_profiles_and_bounded_typed_values(self):
        self.assertEqual(valid_settings(DEFAULT_SETTINGS), DEFAULT_SETTINGS)
        invalid = json.loads(json.dumps(DEFAULT_SETTINGS))
        invalid["profiles"]["balanced"]["max_percent"] = 101
        self.assertIsNone(valid_settings(invalid))
        invalid = json.loads(json.dumps(DEFAULT_SETTINGS))
        invalid["profiles"]["balanced"]["gpu_boost"] = 1
        self.assertIsNone(valid_settings(invalid))
        invalid = json.loads(json.dumps(DEFAULT_SETTINGS))
        del invalid["profiles"]["performance"]
        self.assertIsNone(valid_settings(invalid))

    def test_privileged_helper_persists_only_valid_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            helper.CONFIG = Path(directory) / "x810-power-profile-tuning.json"
            payload = json.dumps(DEFAULT_SETTINGS, separators=(",", ":"))
            self.assertEqual(helper.main([payload]), 0)
            self.assertEqual(json.loads(helper.CONFIG.read_text()), DEFAULT_SETTINGS)
            invalid = json.loads(payload)
            invalid["profiles"]["balanced"]["max_percent"] = 120
            self.assertEqual(helper.main([json.dumps(invalid)]), 2)

    def test_performance_page_uses_profile_slider_and_two_boost_switches(self):
        page = (ROOT / "src/tab_companion/power_profiles_page.py").read_text(encoding="utf-8")
        self.assertIn('title=_("Maximum CPU/GPU level")', page)
        self.assertIn('title=_("CPU boost")', page)
        self.assertIn('title=_("GPU boost")', page)
        self.assertIn('"performance-tuning", PERFORMANCE_TUNING_HELPER', page)

    def test_bundle_installs_fixed_helper_and_both_dispatchers_allowlist_it(self):
        build = (ROOT / "ports/fedora-x810/tools/build-tab-companion-x810-bundle.sh").read_text()
        self.assertIn("tools/tab-companion-performance-setting", build)
        for relative in (
            "ports/fedora-x810/usr/libexec/tab-companion-admin",
            "ports/fedora-x810/packaging/tab-companion-x810/usr/libexec/tab-companion-admin",
        ):
            dispatcher = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn('"performance-tuning": "/usr/libexec/tab-companion-performance-setting"', dispatcher)
            self.assertIn('operation == "performance-tuning"', dispatcher)


if __name__ == "__main__":
    unittest.main()
