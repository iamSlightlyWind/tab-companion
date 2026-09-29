# SPDX-License-Identifier: MIT
import contextlib
import importlib.util
from importlib.machinery import SourceFileLoader
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


HELPER = Path(__file__).resolve().parents[1] / "ports/fedora-x810/usr/libexec/tab-companion-keyboard-recover"
SPEC = importlib.util.spec_from_loader("keyboard_recover", SourceFileLoader("keyboard_recover", str(HELPER)))
keyboard_recover = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(keyboard_recover)


class KeyboardRecoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        # I2C bus numbering varies between boots/releases; discovery must use
        # the X810 device-tree compatible rather than hardcoding bus 10.
        self.device = root / "bus/i2c/devices/9-002a"
        self.driver = root / "bus/i2c/drivers/samsung-gts9u-stm32-pogo"
        self.device.mkdir(parents=True)
        self.driver.mkdir(parents=True)
        (self.device / "uevent").write_text(
            "DRIVER=samsung-gts9u-stm32-pogo\n"
            "OF_COMPATIBLE_0=samsung,gts9pwifi-stm32-pogo\n")
        (self.device / "diagnostics").write_text(
            "attached=1 powered=1 model=0xfb connected=1 data_ready=0\n")
        (self.driver / "unbind").touch()
        (self.driver / "bind").touch()
        (self.device / "driver").symlink_to(self.driver)
        keyboard_recover.DEVICES_ROOT = self.device.parent
        keyboard_recover.DRIVER_PATH = self.driver

    def tearDown(self):
        self.temp.cleanup()

    def run_as_root(self):
        original_write_text = Path.write_text

        def sysfs_write(path, data, *args, **kwargs):
            if path == self.driver / "unbind":
                (self.device / "driver").unlink()
                return len(data)
            if path == self.driver / "bind":
                (self.device / "driver").symlink_to(self.driver)
                fields = keyboard_recover.diagnostics(self.device)
                if fields.get("bootloader") == "1":
                    (self.device / "diagnostics").write_text(
                        "attached=1 powered=1 connected=1 model=0xfb "
                        "bootloader=0 flash_version=00370037 data_ready=0\n")
                return len(data)
            return original_write_text(path, data, *args, **kwargs)

        output = io.StringIO()
        with mock.patch.object(keyboard_recover.os, "geteuid", return_value=0), \
             mock.patch.object(keyboard_recover.time, "sleep"), \
             mock.patch.object(Path, "write_text", sysfs_write), \
             mock.patch.object(sys, "argv", [str(HELPER)]), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            keyboard_recover.main()
        return output.getvalue()

    def test_rebinds_only_attached_ef_dx815_and_reports_success(self):
        result = self.run_as_root()
        self.assertTrue((self.device / "driver").is_symlink())
        self.assertIn("controller reset and reinitialized", result)

    def test_rejects_other_keyboard_before_driver_write(self):
        (self.device / "diagnostics").write_text(
            "attached=1 model=0xfc connected=1\n")
        with self.assertRaises(SystemExit) as error:
            self.run_as_root()
        self.assertEqual(error.exception.code, 1)
        self.assertTrue((self.device / "driver").is_symlink())

    def test_rejects_detached_keyboard_before_driver_write(self):
        (self.device / "diagnostics").write_text(
            "attached=0 model=0xfb connected=0\n")
        with self.assertRaises(SystemExit) as error:
            self.run_as_root()
        self.assertEqual(error.exception.code, 1)
        self.assertTrue((self.device / "driver").is_symlink())

    def test_recovers_expected_x810_bootloader_even_before_app_model_probe(self):
        (self.device / "diagnostics").write_text(
            "attached=0 powered=1 connected=1 bootloader=1 "
            "flash_version=00370037 model=0x00 data_ready=0\n")
        result = self.run_as_root()
        self.assertIn("controller reset and reinitialized", result)

    def test_refuses_ambiguous_or_non_x810_i2c_clients(self):
        other = self.device.parent / "10-002a"
        other.mkdir()
        (other / "uevent").write_text("OF_COMPATIBLE_0=other,pogo\n")
        (other / "diagnostics").write_text("attached=1 model=0xfb connected=1\n")
        result = self.run_as_root()
        self.assertIn("controller reset and reinitialized", result)


if __name__ == "__main__":
    unittest.main()
