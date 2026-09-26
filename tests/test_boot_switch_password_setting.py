from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BootSwitchPasswordSettingTests(unittest.TestCase):
    def test_passwordless_switch_ui_and_helpers_are_removed(self):
        window = (ROOT / "src/tab_companion/window.py").read_text()
        self.assertNotIn("Switch systems without the password", window)
        self.assertNotIn("boot_noask", window)
        self.assertFalse((ROOT / "ports/fedora-x810/tools/tab-companion-boot-noask").exists())
        self.assertFalse((ROOT / "ports/ubuntu-x910/usr/libexec/tab-companion-boot-noask").exists())

    def test_boot_switch_still_requires_admin_authentication(self):
        policies = (
            ROOT / "ports/ubuntu-x910/usr/share/polkit-1/actions/io.github.agcarbajo.TabCompanion.BootSwitch.policy",
            ROOT / "ports/fedora-x810/packaging/io.github.agcarbajo.TabCompanion.X810.policy",
        )
        for path in policies:
            with self.subTest(path=path):
                policy = path.read_text()
                self.assertIn("<allow_active>auth_admin</allow_active>", policy)
                self.assertNotIn("auth_admin_keep", policy)

    def test_upgrade_hooks_remove_old_passwordless_rules(self):
        postinst = (ROOT / "ports/ubuntu-x910/DEBIAN/postinst").read_text()
        fedora = (ROOT / "packaging/fedora/tab-companion.spec").read_text()
        arch = (ROOT / "packaging/arch/tab-companion.install").read_text()
        self.assertIn("49-gts9u-boot-switch.rules", postinst)
        self.assertIn("49-tab-companion-boot-switch.rules", postinst)
        self.assertIn("49-tab-companion-boot-switch.rules", fedora)
        self.assertIn("49-tab-companion-boot-switch.rules", arch)


if __name__ == "__main__":
    unittest.main()
