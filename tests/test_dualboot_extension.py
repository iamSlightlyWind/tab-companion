from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "ports/fedora-x810/usr/share/gnome-shell/extensions/dualboot@agcarbajo.github.io/extension.js"


class DualBootPowerMenuTests(unittest.TestCase):
    def test_switch_is_only_in_power_menu_and_cleans_legacy_tiles(self):
        source = EXTENSION.read_text()
        window = (ROOT / "src/tab_companion/window.py").read_text()
        schema = (ROOT / "ports/fedora-x810/usr/share/glib-2.0/schemas/"
                  "io.github.agcarbajo.TabCompanion.gschema.xml").read_text()

        self.assertIn("_system?.menu", source)
        self.assertIn("menu.addAction(_('Restart into Android')", source)
        self.assertIn("removeLegacyTiles(this._quickSettings)", source)
        self.assertNotIn("QuickToggle", source)
        self.assertNotIn("addExternalIndicator", source)
        self.assertNotIn("Show in quick settings", window)
        self.assertNotIn("quick-settings-boot-switch", schema)

    def test_switch_action_authenticates_without_keep_authorization(self):
        policy = (ROOT / "ports/fedora-x810/packaging/"
                  "io.github.agcarbajo.TabCompanion.X810.policy").read_text()
        self.assertIn("<allow_active>auth_admin</allow_active>", policy)
        self.assertNotIn("auth_admin_keep", policy)


if __name__ == "__main__":
    unittest.main()
