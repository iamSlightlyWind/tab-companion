# SPDX-License-Identifier: MIT
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FedoraPolkitPackagingTests(unittest.TestCase):
    def test_fedora_package_installs_the_authentication_agent(self):
        spec = (ROOT / "packaging/fedora/tab-companion.spec").read_text(encoding="utf-8")
        autostart = (ROOT / "ports/fedora-x810/packaging/tab-companion-polkit-agent.desktop").read_text(encoding="utf-8")
        self.assertIn("Requires:       mate-polkit", spec)
        self.assertIn("Exec=/usr/libexec/polkit-mate-authentication-agent-1", autostart)
        self.assertIn("OnlyShowIn=GNOME;", autostart)
