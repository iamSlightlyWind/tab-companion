# SPDX-License-Identifier: MIT
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch


HELPER = Path(__file__).resolve().parents[1] / "ports/fedora-x810/tools/x810-kernel-update-core"
loader = importlib.machinery.SourceFileLoader("x810_kernel_update_core", str(HELPER))
spec = importlib.util.spec_from_loader(loader.name, loader)
core = importlib.util.module_from_spec(spec)
loader.exec_module(core)


class X810KernelUpdateCoreTests(unittest.TestCase):
    def test_raw_write_allowlist_excludes_recovery_vbmeta_and_other_partitions(self):
        self.assertEqual(set(self.original_partitions), {"boot", "init_boot", "vendor_boot", "dtbo"})
        self.assertEqual(
            {name: Path(device).name for name, (device, _size) in self.original_partitions.items()},
            {"boot": "sda21", "init_boot": "sda22", "vendor_boot": "sda24", "dtbo": "sda30"},
        )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.selected = self.root / "chosen-fallback-folder"
        self.selected.mkdir()
        self.devices_dir = self.root / "devices"
        self.devices_dir.mkdir()
        self.original_partitions = core.PARTITIONS
        self.original_images = core.IMAGES
        self.original_modules_root = core.MODULES_ROOT
        core.PARTITIONS = {}
        for name in ("boot", "init_boot", "vendor_boot", "dtbo"):
            device = self.devices_dir / name
            device.write_bytes(("old-" + name).encode().ljust(128, b"O"))
            core.PARTITIONS[name] = (str(device), 128)
        core.IMAGES = {name + ".img": 128 for name in core.PARTITIONS}
        core.MODULES_ROOT = self.root / "usr-lib-modules"
        current_modules = core.MODULES_ROOT / os.uname().release
        current_modules.mkdir(parents=True)
        (current_modules / "sample.ko").write_bytes(b"old known-good module")
        self.addCleanup(setattr, core, "PARTITIONS", self.original_partitions)
        self.addCleanup(setattr, core, "IMAGES", self.original_images)
        self.addCleanup(setattr, core, "MODULES_ROOT", self.original_modules_root)

    @staticmethod
    def _digest(data):
        return hashlib.sha256(data).hexdigest()

    def _release(self, build="101"):
        root = self.root / ("release-" + build)
        root.mkdir()
        assets = []
        for name in core.IMAGES:
            data = ("new-" + name).encode().ljust(128, b"N")
            (root / name).write_bytes(data)
            assets.append({"name": name, "size_bytes": len(data), "sha256": self._digest(data)})
        rpm_data = b"mock kernel RPM bytes"
        (root / "kernel.rpm").write_bytes(rpm_data)
        assets.append({"name": "kernel.rpm", "size_bytes": len(rpm_data), "sha256": self._digest(rpm_data)})
        manifest = {
            "schema_version": 1,
            "type": "x810-fedora-release",
            "device": {"model": "SM-X810", "codename": "gts9pwifi"},
            "release_tag": "x810-fedora-port-build-" + build,
            "source_commit": "b" * 40,
            "port_version": "1.2.3",
            "assets": assets,
        }
        (root / "manifest.json").write_text(json.dumps(manifest))
        return root, manifest, {asset["name"]: asset for asset in assets}

    def _make_backup(self, manifest, build):
        return core._create_backup(self.selected, manifest, build, os.getuid())

    def test_apply_refuses_a_backup_mount_that_changed_since_folder_selection(self):
        release_dir, manifest, assets = self._release("150")
        candidate = (manifest, "150", assets)
        with patch.object(core, "_requesting_uid", return_value=os.getuid()), \
             patch.object(core, "validate_tablet"), \
             patch.object(core, "_safe_candidate", return_value=candidate), \
             patch.object(core, "_verify_x810_rpm"), \
             patch.object(core, "_create_backup") as create_backup:
            with self.assertRaisesRegex(ValueError, "drive is no longer mounted"):
                core.apply(str(release_dir), str(self.selected), self.selected.stat().st_dev + 1)
        create_backup.assert_not_called()

    def test_backup_is_grouped_by_build_number_under_files_and_has_twrp_note(self):
        _release, manifest, _assets = self._release("101")
        path = self._make_backup(manifest, "101")
        self.assertEqual(path, self.selected / core.BACKUP_CONTAINER / "101")
        self.assertEqual({p.name for p in (path / "files").iterdir()},
                         {name + ".img" for name in core.PARTITIONS}
                         | {f"modules-{os.uname().release}.tar.gz"})
        note = (path / "READ-ME-TWRP.txt").read_text()
        self.assertIn("microSD card or USB-OTG", note)
        self.assertIn("TWRP", note)
        self.assertIn("boot.img -> Boot", note)
        self.assertIn("vbmeta", note)
        self.assertIn("restore-modules-in-twrp.sh", note)
        restore_script = path / "restore-modules-in-twrp.sh"
        record = json.loads((path / "backup.json").read_text())
        self.assertEqual(core.sha256_file(restore_script), record["restore_script_sha256"])
        self.assertIn("/dev/block/sda35", restore_script.read_text())
        subprocess.run(["sh", "-n", str(restore_script)], check=True)
        for name, (device, _size) in core.PARTITIONS.items():
            self.assertEqual((path / "files" / (name + ".img")).read_bytes(), Path(device).read_bytes())

    def test_same_build_backup_is_reused_only_if_live_bytes_still_match(self):
        _release, manifest, _assets = self._release("102")
        first = self._make_backup(manifest, "102")
        self.assertEqual(self._make_backup(manifest, "102"), first)
        device = Path(core.PARTITIONS["boot"][0])
        device.write_bytes(b"changed live partition".ljust(128, b"!"))
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite an existing backup"):
            self._make_backup(manifest, "102")

    def test_retention_prunes_only_owned_verified_snapshots_to_five(self):
        for build in range(100, 106):
            _release, manifest, _assets = self._release(str(build))
            self._make_backup(manifest, str(build))
        container = self.selected / core.BACKUP_CONTAINER
        core._prune_backups(container, keep=5)
        remaining = sorted(entry.name for entry in container.iterdir()
                           if entry.is_dir() and entry.name.isdigit())
        self.assertEqual(remaining, ["101", "102", "103", "104", "105"])
        user_dir = container / "unmanaged"
        user_dir.mkdir()
        core._prune_backups(container, keep=5)
        self.assertTrue(user_dir.is_dir())

    def _prepare_apply(self, build="201"):
        release_dir, manifest, assets = self._release(build)
        backup_dir = self._make_backup(manifest, build)
        stage = self.root / "root-owned-staging"
        stage.mkdir(mode=0o700)
        return release_dir, manifest, assets, backup_dir, stage

    def _apply_images(self, release_dir, selected, backup_dir, stage, write_image=None, install=None):
        original_write = core._write_image
        with patch.object(core, "_verify_x810_rpm"), \
             patch.object(core, "_install_x810_rpm", side_effect=install), \
             patch.object(core, "_partition_path_safe", side_effect=lambda name: core.PARTITIONS[name]), \
             patch.object(core, "STAGING_ROOT", stage), \
             patch.object(core, "ROOT_UID", os.getuid()), \
             patch.object(core, "_update_lock", return_value=nullcontext()), \
             patch.object(core, "_write_image", side_effect=write_image or original_write):
            return core._apply_locked(release_dir, selected,
                                      self._candidate_data[0], self._candidate_data[1],
                                      self._candidate_data[2], backup_dir, selected.stat().st_dev)

    def test_apply_updates_all_partitions_after_snapshot_and_does_not_reboot(self):
        release_dir, manifest, assets, backup_dir, stage = self._prepare_apply()
        self._candidate_data = (manifest, "201", assets)
        self._apply_images(release_dir, self.selected, backup_dir, stage)
        for name, (device, _size) in core.PARTITIONS.items():
            self.assertEqual(Path(device).read_bytes(), (release_dir / (name + ".img")).read_bytes())

    def test_failed_partition_write_restores_all_four_known_good_images(self):
        release_dir, manifest, assets, backup_dir, stage = self._prepare_apply("202")
        self._candidate_data = (manifest, "202", assets)
        original = {name: Path(device).read_bytes() for name, (device, _size) in core.PARTITIONS.items()}
        (core.MODULES_ROOT / os.uname().release / "sample.ko").write_bytes(b"new RPM module tree")
        real_write = core._write_image
        injected = {"failed": False}

        def fail_once(name, source_path, digest):
            if name == "init_boot" and source_path.name == "init_boot.img" and not injected["failed"]:
                injected["failed"] = True
                raise OSError("simulated interruption before init_boot write")
            return real_write(name, source_path, digest)

        with self.assertRaisesRegex(RuntimeError, "all four saved images were restored and verified"):
            self._apply_images(release_dir, self.selected, backup_dir, stage, write_image=fail_once)
        for name, (device, _size) in core.PARTITIONS.items():
            self.assertEqual(Path(device).read_bytes(), original[name])
        restored_module = core.MODULES_ROOT / os.uname().release / "sample.ko"
        self.assertEqual(restored_module.read_bytes(), b"old known-good module")

    def test_dnf_failure_restores_matching_module_tree_without_writing_partitions(self):
        release_dir, manifest, assets, backup_dir, stage = self._prepare_apply("203")
        self._candidate_data = (manifest, "203", assets)
        module_file = core.MODULES_ROOT / os.uname().release / "sample.ko"

        def failed_install(_rpm):
            module_file.write_bytes(b"partially installed module tree")
            raise RuntimeError("simulated dnf failure")

        with self.assertRaisesRegex(RuntimeError, "kernel modules were restored"):
            self._apply_images(release_dir, self.selected, backup_dir, stage, install=failed_install)
        self.assertEqual(module_file.read_bytes(), b"old known-good module")
        for name, (device, _size) in core.PARTITIONS.items():
            self.assertEqual(Path(device).read_bytes(), (b"old-" + name.encode()).ljust(128, b"O"))


if __name__ == "__main__":
    unittest.main()
