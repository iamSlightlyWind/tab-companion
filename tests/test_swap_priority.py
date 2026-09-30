# SPDX-License-Identifier: MIT
import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tab_companion.swap_priority import active_swaps


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "ports/fedora-x810/usr/libexec/tab-companion-swap-priority"
loader = importlib.machinery.SourceFileLoader("swap_priority_helper", str(HELPER))
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)


class SwapPriorityTests(unittest.TestCase):
    def test_proc_swaps_is_converted_to_decimal_bytes_and_file_mount(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary) / "swaps"
            proc.write_text(
                "Filename Type Size Used Priority\n/dev/zram0 partition 4194304 256 100\n"
                "/swap\\040file file 2097152 0 -2\n",
                encoding="utf-8",
            )
            with patch("tab_companion.swap_priority.subprocess.run") as run:
                run.return_value.stdout = "/home\n"
                rows = active_swaps(proc)
            self.assertEqual(rows[0]["size"], 4194304 * 1024)
            self.assertEqual(rows[0]["type"], "zram")
            self.assertEqual(rows[0]["priority"], 100)
            self.assertEqual(rows[1]["source"], "/swap file")
            self.assertEqual(rows[1]["mountpoint"], "/home")

    def test_fstab_priority_is_added_replaced_or_removed(self):
        fstab = "UUID=abcd none swap defaults 0 0 # swap data\n"
        with patch.object(helper, "_same_source", return_value=True):
            changed = helper._set_fstab_priority(fstab, "/dev/sda10", 50)
            self.assertIn("pri=50", changed)
            self.assertIn("# swap data", changed)
            replaced = helper._set_fstab_priority(changed, "/dev/sda10", 10)
            self.assertIn("pri=10", replaced)
            self.assertNotIn("pri=50", replaced)
            automatic = helper._set_fstab_priority(replaced, "/dev/sda10", -1)
            self.assertNotIn("pri=", automatic)

    def test_priority_request_rejects_unrecognized_devices_and_range(self):
        with patch.object(helper, "active_swaps", return_value=[
            {"source": "/dev/zram0", "type": "partition"}
        ]):
            self.assertEqual(
                helper._validate('[{"source":"/dev/zram0","priority":32767}]')[0]["priority"],
                32767,
            )
            for payload in (
                '[{"source":"/dev/other","priority":4}]',
                '[{"source":"/dev/zram0","priority":32768}]',
                '[{"source":"/dev/zram0","priority":true}]',
            ):
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    helper._validate(payload)

    def test_apply_writes_separate_zram_and_fstab_settings_without_swapoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fstab = root / "fstab"
            zram_conf = root / "systemd/zram-generator.conf.d/91-priority.conf"
            fstab.write_text("UUID=swap-part none swap defaults 0 0\n", encoding="utf-8")
            entries = [
                {"source": "/dev/zram0", "type": "partition"},
                {"source": "/dev/sda9", "type": "partition"},
            ]
            payload = (
                '[{"source":"/dev/zram0","priority":150},'
                '{"source":"/dev/sda9","priority":20}]'
            )
            with (
                patch.object(helper, "FSTAB", fstab),
                patch.object(helper, "ZRAM_PRIORITY", zram_conf),
                patch.object(helper, "active_swaps", return_value=entries),
                patch.object(helper, "_same_source", return_value=True),
                patch.object(helper.subprocess, "run") as run,
            ):
                helper.apply(payload)
            self.assertIn("pri=20", fstab.read_text(encoding="utf-8"))
            self.assertEqual(
                zram_conf.read_text(encoding="utf-8"),
                "# Managed by Tab Companion. Applies when swap is next activated.\n"
                "# -1 asks zram-generator to use its default priority.\n"
                "[zram0]\nswap-priority = 150\n",
            )
            self.assertEqual(run.call_args.args[0], ["/usr/bin/systemctl", "daemon-reload"])


if __name__ == "__main__":
    unittest.main()
