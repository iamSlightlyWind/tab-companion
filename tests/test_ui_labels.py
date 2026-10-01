# SPDX-License-Identifier: MIT
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UiLabelTests(unittest.TestCase):
    def test_updates_tab_uses_plural_label(self):
        source = (ROOT / "src/tab_companion/window.py").read_text(encoding="utf-8")
        self.assertIn('UpdatesPage(self), "updates", _("Updates")', source)

    def test_update_actions_only_override_vertical_spacing(self):
        source = (ROOT / "src/tab_companion/updates_page.py").read_text(encoding="utf-8")
        css = next(line for line in source.splitlines() if "button.update-action {" in line)
        self.assertIn("min-height", css)
        self.assertIn("padding-top", css)
        self.assertIn("padding-bottom", css)
        self.assertNotIn("padding:", css)
        self.assertIn("28px", css)

    def test_folder_dialog_dismissal_is_treated_as_cancellation(self):
        source = (ROOT / "src/tab_companion/x810_kernel_update_page.py").read_text(encoding="utf-8")
        self.assertIn("Gtk.DialogError.CANCELLED", source)
        self.assertIn("Gtk.DialogError.DISMISSED", source)
        self.assertIn("Gtk.dialog_error_quark()", source)

    def test_app_and_port_update_buttons_do_not_fill_tall_rows(self):
        source = (ROOT / "src/tab_companion/updates_page.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("valign=Gtk.Align.CENTER, vexpand=False"), 3)

    def test_swap_priority_spin_buttons_do_not_stretch_to_action_row_height(self):
        source = (ROOT / "src/tab_companion/power_profiles_page.py").read_text(encoding="utf-8")
        self.assertIn("spin.set_valign(Gtk.Align.CENTER)", source)
        self.assertIn("spin.set_vexpand(False)", source)

    def test_zram_size_controls_do_not_stretch_vertically(self):
        source = (ROOT / "src/tab_companion/power_profiles_page.py").read_text(encoding="utf-8")
        self.assertIn("self.zram_scale.set_valign(Gtk.Align.CENTER)", source)
        self.assertIn("self.zram_scale.set_vexpand(False)", source)
        self.assertIn("self.zram_editor.set_valign(Gtk.Align.CENTER)", source)
        self.assertIn("zram_controls.set_vexpand(False)", source)

    def test_power_mode_reports_actual_cpu_governors(self):
        source = (ROOT / "src/tab_companion/power_profiles_page.py").read_text(encoding="utf-8")
        self.assertIn("def cpu_governor_summary", source)
        self.assertIn("scaling_governor", source)
        self.assertIn("self._profile_status(profile)", source)
        self.assertIn("GLib.timeout_add(750, self._refresh_profile_status", source)
