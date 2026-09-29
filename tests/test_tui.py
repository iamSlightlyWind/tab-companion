# SPDX-License-Identifier: MIT
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from tab_companion import tui
from tab_companion.main import main as app_main
from tab_companion.x810_fallbacks import FallbackSnapshot


class CompanionTuiTests(unittest.TestCase):
    def test_cli_tui_flag_bypasses_gtk_and_enters_text_interface(self):
        with patch("tab_companion.tui.main", return_value=7) as run_tui:
            self.assertEqual(app_main(["tab-companion", "--tui"]), 7)
        run_tui.assert_called_once_with([])

    def test_menu_is_limited_to_requested_update_actions_and_gates_x810(self):
        actions = tui.menu_actions({"device": "SM-X810"}, "rpm")
        self.assertEqual([name for name, _label in actions], ["app", "port", "kernel", "fallback", "quit"])
        actions = tui.menu_actions({"device": "other"}, "rpm")
        self.assertEqual([name for name, _label in actions], ["app", "port", "quit"])

    def test_menu_invalid_choice_recovers_and_quit_exits_cleanly(self):
        answers = iter(("bad", "99", "5"))
        output = io.StringIO()
        with patch.object(tui, "host_target", return_value={"device": "SM-X810"}), \
             patch.object(tui, "package_manager", return_value="rpm"), \
             patch.object(tui, "_run_kernel_update") as kernel:
            self.assertEqual(tui.main(input_fn=lambda _prompt: next(answers),
                                      output_fn=lambda text: output.write(text + "\n")), 0)
        kernel.assert_not_called()
        self.assertEqual(output.getvalue().count("Choose one of the displayed numbers."), 2)

    def test_restore_requires_exact_build_confirmation_and_uses_guarded_helper(self):
        snapshot = FallbackSnapshot("36390000000", Path("/fallback/36390000000"),
                                    "2026-09-29T12:00:00Z", "7.2.0-gts9wifi")
        outputs = []
        answers = iter(("36390000000", "RESTORE X810 BUILD 36390000000"))
        with patch.object(tui, "_fallback_folder", return_value=("/media/fallback", 123)), \
             patch.object(tui, "list_snapshots", return_value=[snapshot]), \
             patch.object(tui.subprocess, "run") as run:
            tui._run_fallback_restore(input_fn=lambda _prompt: next(answers), output_fn=outputs.append)
        run.assert_called_once_with(
            ["pkexec", tui.KERNEL_HELPER, "restore", "--backup-root", "/media/fallback",
             "--backup-device", "123", "--build-number", "36390000000"],
            check=True, env=unittest.mock.ANY,
        )
        self.assertTrue(any("No reboot" in line for line in outputs))

    def test_restore_cancel_does_not_invoke_privileged_helper(self):
        snapshot = FallbackSnapshot("123", Path("/fallback/123"), "", "7.2")
        answers = iter(("123", "RESTORE X810 BUILD 999"))
        with patch.object(tui, "_fallback_folder", return_value=("/media/fallback", 123)), \
             patch.object(tui, "list_snapshots", return_value=[snapshot]), \
             patch.object(tui.subprocess, "run") as run:
            tui._run_fallback_restore(input_fn=lambda _prompt: next(answers), output_fn=lambda _text: None)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
