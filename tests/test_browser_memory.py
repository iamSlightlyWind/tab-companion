# SPDX-License-Identifier: MIT
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tab_companion.browser_memory import (  # noqa: E402
    DEFAULT_SETTINGS,
    firefox_scope_names,
    read_settings,
    read_status,
    valid_settings,
)


def load_script(name, path):
    spec = importlib.util.spec_from_loader(name, SourceFileLoader(name, str(path)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AGENT = load_script(
    "browser_memory_agent",
    ROOT / "ports/fedora-x810/usr/libexec/tab-companion-browser-memory-agent",
)
SETTING = load_script(
    "browser_memory_setting",
    ROOT / "ports/fedora-x810/usr/libexec/tab-companion-browser-memory-setting",
)


class FakeSystemctl:
    def __init__(self):
        self.properties = {}
        self.commands = []
        self.scope_output = (
            "app-gnome-org.mozilla.firefox-123.scope loaded active running Firefox\n"
            "app-flatpak-org.mozilla.firefox-456.scope loaded active running Flatpak Firefox\n"
            "app-gnome-org.chromium.Chromium-789.scope loaded active running Chromium\n"
        )

    def __call__(self, argv, **kwargs):
        args = argv[2:]
        self.commands.append(args)
        if args[0] == "list-units":
            return subprocess.CompletedProcess(argv, 0, stdout=self.scope_output)
        if args[0] == "show":
            unit = args[-1]
            high, maximum = self.properties.get(unit, ("infinity", "infinity"))
            return subprocess.CompletedProcess(argv, 0, stdout=f"{high}\n{maximum}\n")
        if args[0] == "set-property":
            unit = args[2]
            values = dict(item.split("=", 1) for item in args[3:])
            self.properties[unit] = (values["MemoryHigh"], values["MemoryMax"])
            return subprocess.CompletedProcess(argv, 0, stdout="")
        raise AssertionError(args)


class BrowserMemoryTests(unittest.TestCase):
    def test_settings_are_bounded_and_default_off(self):
        self.assertEqual(DEFAULT_SETTINGS, {"enabled": False, "limit_deci_gb": 20})
        self.assertEqual(valid_settings({"enabled": True, "limit_deci_gb": 10}),
                         {"enabled": True, "limit_deci_gb": 10})
        self.assertEqual(valid_settings({"enabled": True, "limit_deci_gb": 23}),
                         {"enabled": True, "limit_deci_gb": 23})
        self.assertEqual(valid_settings({"enabled": True, "limit_gb": 2}),
                         {"enabled": True, "limit_deci_gb": 20})
        self.assertIsNone(valid_settings({"enabled": True, "limit_deci_gb": 9}))
        self.assertIsNone(valid_settings({"enabled": True, "limit_deci_gb": 101}))
        self.assertIsNone(valid_settings({"enabled": 1, "limit_deci_gb": 20}))
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "browser-memory.json"
            self.assertEqual(read_settings(config), DEFAULT_SETTINGS)
            config.write_text("{}", encoding="utf-8")
            self.assertEqual(read_settings(config), DEFAULT_SETTINGS)

    def test_only_native_gnome_firefox_scopes_are_supported(self):
        self.assertEqual(firefox_scope_names("""
app-gnome-org.mozilla.firefox-123.scope loaded active running Firefox
app-org.mozilla.firefox-124.scope loaded active running Firefox
app-flatpak-org.mozilla.firefox-125.scope loaded active running Flatpak Firefox
app-gnome-org.chromium.Chromium-126.scope loaded active running Chromium
"""), ["app-gnome-org.mozilla.firefox-123.scope",
      "app-org.mozilla.firefox-124.scope"])

    def test_agent_caps_firefox_updates_limit_and_clears_only_its_own_settings(self):
        fake = FakeSystemctl()
        settings = {"enabled": True, "limit_deci_gb": 20}
        with tempfile.TemporaryDirectory() as directory:
            agent = AGENT.FirefoxMemoryAgent(
                run=fake, config_reader=lambda: settings,
                state_path=Path(directory) / "managed.json",
                report_path=Path(directory) / "status.json",
            )
            status = agent.step()
            unit = "app-gnome-org.mozilla.firefox-123.scope"
            self.assertEqual(status["applied_sessions"], 1)
            self.assertEqual(fake.properties[unit], ("2000000000", "2000000000"))
            self.assertTrue(read_status(Path(directory) / "status.json")["enabled"])

            settings["limit_deci_gb"] = 23
            agent.step()
            self.assertEqual(fake.properties[unit], ("2300000000", "2300000000"))

            settings["enabled"] = False
            agent.step()
            self.assertEqual(fake.properties[unit], ("infinity", "infinity"))
            self.assertNotIn("app-flatpak-org.mozilla.firefox-456.scope", fake.properties)

    def test_agent_caps_every_current_and_future_native_firefox_session(self):
        fake = FakeSystemctl()
        second = "app-org.mozilla.firefox-124.scope"
        future = "app-gnome-org.mozilla.firefox-125.scope"
        fake.scope_output += f"{second} loaded active running Firefox private\n"
        settings = {"enabled": True, "limit_deci_gb": 27}
        with tempfile.TemporaryDirectory() as directory:
            agent = AGENT.FirefoxMemoryAgent(
                run=fake, config_reader=lambda: settings,
                state_path=Path(directory) / "managed.json",
                report_path=Path(directory) / "status.json",
            )
            status = agent.step()
            first = "app-gnome-org.mozilla.firefox-123.scope"
            self.assertEqual(status["firefox_sessions"], 2)
            self.assertEqual(status["applied_sessions"], 2)
            for unit in (first, second):
                self.assertEqual(fake.properties[unit], ("2700000000", "2700000000"))

            # A browser session started after the agent is running is picked up
            # by the next polling pass, alongside sessions that were already open.
            fake.scope_output += f"{future} loaded active running Firefox second window\n"
            status = agent.step()
            self.assertEqual(status["firefox_sessions"], 3)
            self.assertEqual(status["applied_sessions"], 3)
            self.assertEqual(fake.properties[future], ("2700000000", "2700000000"))
            self.assertEqual(fake.properties[first], ("2700000000", "2700000000"))

    def test_setting_helper_persists_then_enables_user_service_without_root(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "tab-companion/browser-memory.json"
            with mock.patch.object(SETTING, "config_path", return_value=config), \
                    mock.patch.object(SETTING.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, stdout="")
                self.assertEqual(SETTING.main(['{"enabled":true,"limit_deci_gb":23}']), 0)
            self.assertEqual(json.loads(config.read_text()), {"enabled": True, "limit_deci_gb": 23})
            self.assertEqual(run.call_count, 2)
            self.assertIn("tab-companion-browser-memory.service", run.call_args_list[1].args[0])

    def test_helper_rejects_out_of_range_limits_without_running_systemctl(self):
        with mock.patch.object(SETTING.subprocess, "run") as run:
            self.assertEqual(SETTING.main(['{"enabled":true,"limit_deci_gb":101}']), 2)
        run.assert_not_called()

    def test_performance_page_and_fedora_bundle_explain_and_install_support(self):
        page = (ROOT / "src/tab_companion/power_profiles_page.py").read_text(encoding="utf-8")
        self.assertIn('title=_("Browser RAM limit")', page)
        self.assertIn("Flatpak browsers are unsupported", page)
        self.assertIn("contribute a pull request", page)
        self.assertIn("Maximum Firefox RAM", page)
        self.assertIn("0.1,", page)
        self.assertIn("limit_deci_gb", page)
        self.assertIn("BROWSER_MEMORY_HELPER, json.dumps", page)
        self.assertNotIn('admin_command("browser-memory"', page)
        build = (ROOT / "ports/fedora-x810/tools/build-tab-companion-x810-bundle.sh").read_text()
        self.assertIn("tab-companion-browser-memory-agent", build)
        self.assertIn("tab-companion-browser-memory-setting", build)
        self.assertIn("tab-companion-browser-memory.service", build)


if __name__ == "__main__":
    unittest.main()
