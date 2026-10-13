# SPDX-License-Identifier: MIT
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tab_companion.fast_charge import (  # noqa: E402
    helper_command,
    parse_contract_online,
    parse_enabled,
    read_contract_status,
    read_enabled,
)


class FastChargeTests(unittest.TestCase):
    def test_toggle_parser_accepts_only_boolean_kernel_values(self):
        self.assertIs(parse_enabled("0\n"), False)
        self.assertIs(parse_enabled("1\n"), True)
        for value in ("", "2", "1;reboot", "enabled", "0\n1"):
            with self.subTest(value=value):
                self.assertIsNone(parse_enabled(value))

    def test_switch_uses_only_the_fixed_device_control_api(self):
        self.assertEqual(
            helper_command(True, "/usr/libexec/gts9wifi-device-control"),
            ["/usr/libexec/gts9wifi-device-control", "set", "fast-charge", "1"],
        )
        self.assertEqual(
            helper_command(False, "/usr/libexec/gts9wifi-device-control")[-1], "0"
        )

    def test_switch_observation_is_a_read_only_sysfs_read(self):
        with tempfile.TemporaryDirectory() as directory:
            switch = Path(directory) / "fast_charge"
            switch.write_text("1\n", encoding="ascii")
            self.assertIs(read_enabled(switch), True)
            switch.write_text("0\n", encoding="ascii")
            self.assertIs(read_enabled(switch), False)
            switch.write_text("invalid\n", encoding="ascii")
            self.assertIsNone(read_enabled(switch))
        self.assertIsNone(read_enabled(Path("/no/such/x810-fast-charge-control")))

    def test_tcpms_online_state_is_active_contract_not_capability(self):
        self.assertEqual(parse_contract_online("0\n"), "offline")
        self.assertEqual(parse_contract_online("1\n"), "fixed-pd")
        self.assertEqual(parse_contract_online("2\n"), "pps")
        self.assertEqual(parse_contract_online("PD_PPS"), "unknown")
        self.assertEqual(parse_contract_online("3"), "unknown")

    def test_active_contract_reader_uses_online_and_actual_voltage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tcpm-source-psy-7-0033"
            source.mkdir()
            (source / "type").write_text("USB\n", encoding="ascii")
            (source / "online").write_text("2\n", encoding="ascii")
            (source / "voltage_now").write_text("9360000\n", encoding="ascii")
            # A capability-list string must not override an active fixed-PD
            # state: only the active TCPM ONLINE enum determines PPS status.
            (source / "usb_type").write_text("PD [PD_PPS]\n", encoding="ascii")
            self.assertEqual(
                read_contract_status(root), {"state": "pps", "voltage_mv": 9360}
            )
            (source / "online").write_text("1\n", encoding="ascii")
            self.assertEqual(read_contract_status(root)["state"], "fixed-pd")
            (source / "online").write_text("0\n", encoding="ascii")
            self.assertEqual(read_contract_status(root)["state"], "offline")

    def test_missing_or_unreadable_contract_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                read_contract_status(root), {"state": "unknown", "voltage_mv": None}
            )
            source = root / "tcpm-source-psy-0-0033"
            source.mkdir()
            (source / "type").write_text("USB\n", encoding="ascii")
            (source / "online").write_text("nonsense\n", encoding="ascii")
            self.assertEqual(read_contract_status(root)["state"], "unknown")

    def test_charging_ui_explains_guards_and_uses_existing_switch(self):
        page = (ROOT / "src/tab_companion/charging_page.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('title=_("Fast charging and USB-PD PPS")', page)
        self.assertIn("charge-limit policy allow it", page)
        self.assertIn("does not send manual PD requests", page)
        self.assertIn('title=_("Charging technology")', page)
        self.assertIn("fast_charge_command(selected)", page)
        self.assertIn("read_charging_status()", page)
        performance = (ROOT / "src/tab_companion/power_profiles_page.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("charge_limit_group", performance)
        self.assertNotIn("fast_charge_group", performance)


if __name__ == "__main__":
    unittest.main()
