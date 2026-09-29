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
        self.device = root / "bus/i2c/devices/9-002a"
        self.driver = root / "bus/i2c/drivers/samsung-gts9u-stm32-pogo"
        self.device.mkdir(parents=True)
        self.driver.mkdir(parents=True)
        (self.driver / "9-002a").symlink_to(self.device)
        (self.device / "name").write_text("gts9pwifi-stm32-pog\n")
        (self.device / "diagnostics").write_text(
            "attached=1 model=0xfb connected=1 data_ready=0\n")
        (self.driver / "unbind").touch()
        (self.driver / "bind").touch()
        (self.device / "driver").symlink_to(self.driver)
        keyboard_recover.DEVICE_ROOT = root / "bus/i2c/devices"
        keyboard_recover.DRIVER_PATH = self.driver

    def tearDown(self):
        self.temp.cleanup()

    def run_as_root(self):
        original_write_text = Path.write_text

        def sysfs_write(path, data, *args, **kwargs):
            if path == self.driver / "unbind":
                device_id = data.strip()
                device = keyboard_recover.DEVICE_ROOT / device_id
                (device / "driver").unlink()
                (self.driver / device_id).unlink()
                return len(data)
            if path == self.driver / "bind":
                device_id = data.strip()
                device = keyboard_recover.DEVICE_ROOT / device_id
                (device / "driver").symlink_to(self.driver)
                (self.driver / device_id).symlink_to(device)
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
        self.assertIn("9-002a", result)
        self.assertIn("reset and reinitialized", result)

    def test_supports_dynamic_i2c_bus_enumeration(self):
        (self.driver / "9-002a").unlink()
        (self.device / "driver").unlink()
        self.device = self.device.parent / "10-002a"
        self.device.mkdir()
        (self.driver / "10-002a").symlink_to(self.device)
        (self.device / "name").write_text("gts9pwifi-stm32-pog\n")
        (self.device / "diagnostics").write_text(
            "attached=1 model=0xfb connected=1 data_ready=0\n")
        (self.device / "driver").symlink_to(self.driver)
        result = self.run_as_root()
        self.assertIn("10-002a", result)

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

    def test_rejects_ambiguous_multiple_ef_dx815_devices(self):
        other = self.device.parent / "10-002a"
        other.mkdir()
        (other / "name").write_text("gts9pwifi-stm32-pog\n")
        (other / "diagnostics").write_text(
            "attached=1 model=0xfb connected=1 data_ready=0\n")
        (other / "driver").symlink_to(self.driver)
        (self.driver / "10-002a").symlink_to(other)
        with self.assertRaises(SystemExit) as error:
            self.run_as_root()
        self.assertEqual(error.exception.code, 1)
        self.assertTrue((self.device / "driver").is_symlink())
        self.assertTrue((other / "driver").is_symlink())

    def test_rejects_controller_bound_to_a_different_driver(self):
        other_driver = self.device.parent.parent / "drivers/other-driver"
        other_driver.mkdir()
        (self.device / "driver").unlink()
        (self.device / "driver").symlink_to(other_driver)
        with self.assertRaises(SystemExit) as error:
            self.run_as_root()
        self.assertEqual(error.exception.code, 1)
        self.assertTrue((self.device / "driver").is_symlink())


if __name__ == "__main__":
    unittest.main()
