import importlib.util
from importlib.machinery import SourceFileLoader
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ChargeLimitClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = load_module(
            "charge_limit_client", ROOT / "src/tab_companion/charge_limit.py"
        )
        cls.admin = load_module(
            "tab_companion_admin",
            ROOT / "ports/fedora-x810/usr/libexec/tab-companion-admin",
        )

    def test_valid_supported_status(self):
        self.assertEqual(
            self.client.parse_status(json.dumps({
                "supported": True, "start_percent": 70, "end_percent": 80,
            })),
            {"supported": True, "start_percent": 70, "end_percent": 80},
        )

    def test_unsupported_kernel_is_distinct_from_malformed_status(self):
        self.assertEqual(self.client.parse_status('{"supported":false}'), {"supported": False})
        self.assertIsNone(self.client.parse_status('{"supported":true,"start_percent":1,"end_percent":120}'))
        self.assertIsNone(self.client.parse_status('{"supported":true,"start_percent":70.0,"end_percent":80}'))

    def test_dispatcher_only_accepts_bounded_set(self):
        expected = ("/usr/libexec/x810-charge-limit", ["set", "80"])
        self.assertEqual(self.admin.validate("charge-limit", ["set", "80"]), expected)
        for args in ([], ["get"], ["apply"], ["set", "49"], ["set", "101"], ["set", "80;id"]):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.admin.validate("charge-limit", args)

    def test_charging_page_has_charge_limit_control(self):
        page = (ROOT / "src/tab_companion/charging_page.py").read_text(encoding="utf-8")
        self.assertIn('title=_("Battery charge limit")', page)
        self.assertIn('admin_command("charge-limit", CHARGE_LIMIT_HELPER, "set", str(selected))', page)
        self.assertIn("DEFAULT_LIMIT_PERCENT", page)
        performance = (ROOT / "src/tab_companion/power_profiles_page.py").read_text(encoding="utf-8")
        self.assertNotIn("charge_limit_group", performance)


if __name__ == "__main__":
    unittest.main()
