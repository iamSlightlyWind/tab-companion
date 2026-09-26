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
