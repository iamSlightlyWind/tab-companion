# SPDX-License-Identifier: MIT
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP_ID = "dev.themajorones.slightlywind.companion"


class AppIdentityAndHapticsTests(unittest.TestCase):
    def test_application_id_and_desktop_metadata_match(self):
        source = (ROOT / "src/tab_companion/__init__.py").read_text(encoding="utf-8")
        self.assertIn(f'APP_ID = "{APP_ID}"', source)

        bases = (
            ROOT / "ports/fedora-x810/usr/share",
            ROOT / "ports/fedora-x810/packaging/tab-companion-x810/usr/share",
            ROOT / "ports/ubuntu-x910/usr/share",
        )
        for base in bases:
            desktop = base / f"applications/{APP_ID}.desktop"
            metainfo = base / f"metainfo/{APP_ID}.metainfo.xml"
            self.assertTrue(desktop.is_file(), desktop)
            self.assertTrue(metainfo.is_file(), metainfo)
            self.assertIn(f"Icon={APP_ID}", desktop.read_text(encoding="utf-8"))
            metadata = metainfo.read_text(encoding="utf-8")
            self.assertIn(f"<id>{APP_ID}</id>", metadata)
            self.assertIn(f"{APP_ID}.desktop</launchable>", metadata)

        for helper in (
            ROOT / "ports/fedora-x810/usr/libexec/tab-companion-hardware",
            ROOT / "ports/fedora-x810/packaging/tab-companion-x810/usr/libexec/tab-companion-hardware",
            ROOT / "ports/ubuntu-x910/usr/libexec/tab-companion-hardware",
        ):
            helper_text = helper.read_text(encoding="utf-8")
            self.assertIn(f"{APP_ID}.desktop", helper_text)
            self.assertNotIn("io.github.agcarbajo.TabCompanion.desktop", helper_text)

    def test_test_button_uses_a_long_motor_pulse_without_changing_key_pulses(self):
        source = (ROOT / "src/tab_companion/window.py").read_text(encoding="utf-8")
        self.assertIn("KEYBOARD_HAPTIC_DURATIONS_MS = (24, 42, 66)", source)
        self.assertIn("HAPTICS_TEST_DURATION_MS = 500", source)
        self.assertIn(
            "self.hardware.vibrate(HAPTICS_TEST_DURATION_MS, 65535)", source
        )
        self.assertIn("Runs one clearly noticeable 500 ms vibration pulse.", source)


if __name__ == "__main__":
    unittest.main()
