# SPDX-License-Identifier: MIT
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import tempfile
import unittest

from tab_companion.thermal_limit import DEFAULT_SETTINGS, read_settings, read_status, valid_settings


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "ports/fedora-x810/usr/libexec/tab-companion-thermal-setting"
SPEC = importlib.util.spec_from_loader(
    "thermal_setting_helper", SourceFileLoader("thermal_setting_helper", str(HELPER))
)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class ThermalLimitUiTests(unittest.TestCase):
    def test_default_is_enabled_at_55_c_and_bounds_are_enforced(self):
        self.assertEqual(DEFAULT_SETTINGS, {"enabled": True, "threshold_c": 55})
        self.assertEqual(valid_settings({"enabled": True, "threshold_c": 50})["threshold_c"], 50)
        self.assertIsNone(valid_settings({"enabled": True, "threshold_c": 49}))
        self.assertIsNone(valid_settings({"enabled": True, "threshold_c": 86}))
        self.assertIsNone(valid_settings({"enabled": 1, "threshold_c": 55}))

    def test_settings_and_status_parsers_fail_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            self.assertEqual(read_settings(path), DEFAULT_SETTINGS)
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(read_settings(path), DEFAULT_SETTINGS)
            status = Path(directory) / "status.json"
            status.write_text(json.dumps({
                "enabled": True, "threshold_c": 55, "limited": False,
                "current_c": 52.1, "average_c": 51.7, "peak_c": 58.0,
            }), encoding="utf-8")
            self.assertEqual(read_status(status)["peak_c"], 58.0)
            status.write_text('{"enabled":true,"limited":"yes","threshold_c":55}', encoding="utf-8")
            self.assertIsNone(read_status(status))

    def test_settings_helper_persists_only_bounded_boolean_and_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "x810-thermal-limit.json"
            helper.CONFIG = config
            self.assertEqual(helper.main(['{"enabled":false,"threshold_c":62}']), 0)
            self.assertEqual(json.loads(config.read_text()), {"enabled": False, "threshold_c": 62})
            self.assertEqual(helper.main(['{"enabled":true,"threshold_c":100}']), 2)

    def test_performance_page_exposes_temp_status_controls_and_privileged_save(self):
        page = (ROOT / "src/tab_companion/power_profiles_page.py").read_text(encoding="utf-8")
        self.assertIn('title=_("SoC temperature limit")', page)
        self.assertIn("Adw.SwitchRow", page)
        self.assertIn("_save_thermal_settings", page)
        self.assertIn('admin_command(\n                    "thermal-limit"', page)
        self.assertIn("Current {current} · Average {average} · Peak {peak}", page)
        bundle = (ROOT / "ports/fedora-x810/tools/build-tab-companion-x810-bundle.sh").read_text()
        dispatcher = (ROOT / "ports/fedora-x810/packaging/tab-companion-x810/usr/libexec/tab-companion-admin").read_text()
        self.assertIn("tab-companion-thermal-setting", bundle)
        self.assertIn('"thermal-limit": "/usr/libexec/tab-companion-thermal-setting"', dispatcher)


if __name__ == "__main__":
    unittest.main()
