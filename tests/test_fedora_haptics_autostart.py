# SPDX-License-Identifier: MIT
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
UNIT = Path("usr/lib/systemd/user/tab-companion-haptics-enable.service")
WANTED = Path(
    "usr/lib/systemd/user/graphical-session.target.wants/"
    "tab-companion-haptics-enable.service"
)
STAGES = (
    ROOT / "ports/fedora-x810" / WANTED,
    ROOT / "ports/fedora-x810/packaging/tab-companion-x810" / WANTED,
)


class FedoraHapticsAutostartTests(unittest.TestCase):
    def test_haptics_enabler_is_started_for_every_graphical_user_session(self):
        for link in STAGES:
            with self.subTest(path=link):
                self.assertTrue(link.is_symlink())
                self.assertEqual(
                    link.resolve(),
                    (link.parent.parent / "tab-companion-haptics-enable.service").resolve(),
                )
                self.assertTrue((link.parents[1] / "tab-companion-haptics-enable.service").is_file())

        service = (ROOT / "ports/fedora-x810/packaging/tab-companion-x810" / UNIT).read_text()
        self.assertIn("After=graphical-session.target", service)
        self.assertIn("PartOf=graphical-session.target", service)
        self.assertIn("ExecStart=/usr/libexec/tab-companion-enable-haptics", service)

    def test_enable_helper_preserves_other_gnome_extensions(self):
        helper = (ROOT / "ports/fedora-x810/packaging/tab-companion-x810/usr/libexec/tab-companion-enable-haptics").read_text()
        self.assertIn('get_strv("enabled-extensions")', helper)
        self.assertIn("enabled.append(UUID)", helper)
        self.assertNotIn('set_strv("enabled-extensions", [UUID])', helper)


if __name__ == "__main__":
    unittest.main()
