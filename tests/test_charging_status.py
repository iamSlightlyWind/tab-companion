# SPDX-License-Identifier: MIT
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tab_companion.charging_status import (  # noqa: E402
    classify_charging_technology,
    read_charging_status,
)


class ChargingStatusTests(unittest.TestCase):
    def test_technology_uses_active_contract_not_advertised_capabilities(self):
        self.assertEqual(
            classify_charging_technology("pps", "SDP", 1, "Charging"),
            "USB PD PPS",
        )
        self.assertEqual(
            classify_charging_technology("fixed-pd", "PD_PPS", 1, "Charging"),
            "USB Power Delivery (fixed voltage)",
        )
        self.assertEqual(
            classify_charging_technology("offline", "SDP", 1, "Charging"),
            "USB (slow charging, SDP)",
        )
        self.assertEqual(
            classify_charging_technology("unknown", "DCP", 1, "Charging"),
            "Dedicated USB charger (DCP)",
        )
        self.assertEqual(
            classify_charging_technology("unknown", "SDP", 0, "Not charging"),
            "Not charging",
        )

    def test_status_reads_battery_values_and_real_pd_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            battery = root / "sm5714-battery"
            battery.mkdir()
            (battery / "status").write_text("Charging\n", encoding="ascii")
            (battery / "current_now").write_text("2500000\n", encoding="ascii")
            (battery / "voltage_now").write_text("4000000\n", encoding="ascii")
            usb = root / "sm5714-usb"
            usb.mkdir()
            (usb / "online").write_text("1\n", encoding="ascii")
            (usb / "usb_type").write_text("Unknown SDP DCP [PD]\n", encoding="ascii")
            tcpm = root / "tcpm-source-psy-7-0033"
            tcpm.mkdir()
            (tcpm / "type").write_text("USB\n", encoding="ascii")
            (tcpm / "online").write_text("2\n", encoding="ascii")
            (tcpm / "voltage_now").write_text("8880000\n", encoding="ascii")

            status = read_charging_status(root)
            self.assertEqual(status["technology"], "USB PD PPS")
            self.assertEqual(status["battery_current_a"], 2.5)
            self.assertEqual(status["battery_voltage_v"], 4.0)
            self.assertEqual(status["battery_power_w"], 10.0)
            self.assertEqual(status["contract_voltage_v"], 8.88)

    def test_missing_metrics_are_unavailable_not_fabricated(self):
        with tempfile.TemporaryDirectory() as directory:
            status = read_charging_status(Path(directory))
        self.assertIsNone(status["battery_current_a"])
        self.assertIsNone(status["battery_voltage_v"])
        self.assertIsNone(status["battery_power_w"])
        self.assertEqual(status["technology"], "No active charger detected")


if __name__ == "__main__":
    unittest.main()
