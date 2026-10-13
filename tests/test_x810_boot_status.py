import contextlib
import importlib.util
import importlib.machinery
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


CORE_PATH = Path(__file__).resolve().parents[1] / "ports/fedora-x810/tools/x810-boot-switch-core"
LOADER = importlib.machinery.SourceFileLoader("x810_boot_switch_core", str(CORE_PATH))
SPEC = importlib.util.spec_from_loader("x810_boot_switch_core", LOADER)
core = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core)


class BootStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.parts = {"boot": 4, "dtbo": 4}
        for system in ("fedora", "android"):
            directory = self.base / system
            directory.mkdir()
            for part in self.parts:
                (directory / f"{part}.img").write_bytes((system + part).encode()[:4].ljust(4, b"_"))

    def test_status_needs_no_manifest_or_image_hashes(self):
        # Old manifest files may remain on disk, but changing an image's bytes
        # must not make it incomplete when it still fits its partition.
        (self.base / "android" / "boot.img").write_bytes(b"MOD!")
        with patch.object(core, "BASE", self.base), patch.object(core, "PARTS", self.parts), \
                patch.object(core, "current_system", return_value="fedora"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                core.status()
            status = json.loads(output.getvalue())

        self.assertEqual(status["current"], "fedora")
        self.assertTrue(status["ready"]["fedora"])
        self.assertTrue(status["ready"]["android"])
        self.assertNotIn("sets", status)

    def test_status_still_rejects_missing_or_wrong_size_images(self):
        (self.base / "android" / "dtbo.img").write_bytes(b"short")
        with patch.object(core, "BASE", self.base), patch.object(core, "PARTS", self.parts), \
                patch.object(core, "current_system", return_value="fedora"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                core.status()
            status = json.loads(output.getvalue())

        self.assertTrue(status["ready"]["fedora"])
        self.assertFalse(status["ready"]["android"])
        self.assertIn("wrong-size", status["errors"]["android"])

    def test_stage_copies_modified_images_and_compares_readback_bytes(self):
        destinations = {part: self.base / f"device-{part}" for part in self.parts}
        for dest in destinations.values():
            dest.write_bytes(b"----")
        with patch.object(core, "BASE", self.base), patch.object(core, "PARTS", self.parts), \
                patch.object(core, "validate_device"), \
                patch.object(core, "snapshot_current_fedora") as snapshot, \
                patch.object(core, "device_path", side_effect=lambda part: destinations[part]), \
                patch.object(core.os, "sync"):
            core.stage("android")

        for part in self.parts:
            self.assertEqual(destinations[part].read_bytes(),
                             (self.base / "android" / f"{part}.img").read_bytes())
        snapshot.assert_called_once_with()

    def test_switching_away_snapshots_exact_live_fedora_images_without_fingerprint_validation(self):
        test_parts = {part: 64 for part in self.parts}
        live = {part: (b"custom-kernelsu-or-local-" + part.encode()).ljust(size, b"!")
                for part, size in test_parts.items()}
        current = self.base / "current"
        current.mkdir()
        devices = {}
        for part, data in live.items():
            device = current / part
            device.write_bytes(data)
            devices[part] = device
        destination = self.base / "sets"
        old = destination / "fedora"
        old.mkdir(parents=True)
        (old / "boot.img").write_bytes(b"stale image")

        with patch.object(core, "BASE", destination), patch.object(core, "PARTS", test_parts), \
                patch.object(core, "device_path", side_effect=lambda part: devices[part]), \
                patch.object(core, "emit"):
            core.snapshot_current_fedora()

        for part, data in live.items():
            self.assertEqual((destination / "fedora" / f"{part}.img").read_bytes(), data)
        self.assertEqual((destination / "fedora" / "name.txt").read_text(), "Fedora\n")
        self.assertFalse((destination / "fedora" / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
