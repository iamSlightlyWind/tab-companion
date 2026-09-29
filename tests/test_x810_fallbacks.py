# SPDX-License-Identifier: MIT
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tab_companion import x810_fallbacks as fallbacks


class X810FallbackValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.build = self.root / "36390000000"
        self.files = self.build / "files"
        self.files.mkdir(parents=True)
        self.original_images = fallbacks.IMAGES
        fallbacks.IMAGES = {name: 24 for name in ("boot.img", "init_boot.img", "vendor_boot.img", "dtbo.img")}
        self.addCleanup(setattr, fallbacks, "IMAGES", self.original_images)
        image_info = {}
        for name in fallbacks.IMAGES:
            data = ("fallback-" + name).encode().ljust(24, b"!")
            (self.files / name).write_bytes(data)
            image_info[name] = {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        module_data = b"test kernel module archive"
        (self.files / "modules-7.2.0-gts9wifi.tar.gz").write_bytes(module_data)
        script = b"#!/bin/sh\necho restore\n"
        (self.build / "restore-modules-in-twrp.sh").write_bytes(script)
        record = {
            "schema_version": 1,
            "kind": fallbacks.KIND,
            "device": fallbacks.MODEL,
            "target_release_tag": "x810-fedora-port-build-36390000000",
            "created_utc": "2026-09-29T12:00:00+00:00",
            "source_kernel_release": "7.2.0-gts9wifi",
            "files": image_info,
            "module_backup": {
                "release": "7.2.0-gts9wifi",
                "archive": "modules-7.2.0-gts9wifi.tar.gz",
                "size_bytes": len(module_data),
                "sha256": hashlib.sha256(module_data).hexdigest(),
            },
            "restore_script_sha256": hashlib.sha256(script).hexdigest(),
        }
        (self.build / "backup.json").write_text(json.dumps(record))

    def test_lists_complete_hash_verified_snapshot_by_build_number(self):
        snapshots = fallbacks.list_snapshots(self.root, expected_device=self.root.stat().st_dev)
        self.assertEqual([item.build_number for item in snapshots], ["36390000000"])
        self.assertEqual(snapshots[0].source_kernel_release, "7.2.0-gts9wifi")

    def test_fast_candidate_listing_does_not_hash_large_files(self):
        with patch.object(fallbacks, "_hash", side_effect=AssertionError("unexpected slow hash")):
            snapshots = fallbacks.list_snapshot_candidates(
                self.root, expected_device=self.root.stat().st_dev
            )
        self.assertEqual([item.build_number for item in snapshots], ["36390000000"])

    def test_rejects_modified_boot_image_and_omits_it_from_listing(self):
        (self.files / "boot.img").write_bytes(b"tampered".ljust(24, b"?"))
        self.assertEqual(fallbacks.list_snapshots(self.root), [])
        # The GUI may display a structurally valid candidate quickly; its
        # privileged restore helper performs the full content-hash check.
        self.assertEqual(
            [item.build_number for item in fallbacks.list_snapshot_candidates(self.root)],
            ["36390000000"],
        )
        with self.assertRaisesRegex(Exception, "failed integrity validation"):
            fallbacks.verify_snapshot(self.build, root=self.root)

    def test_rejects_wrong_device_build_id_symlink_and_mount_change(self):
        record_path = self.build / "backup.json"
        record = json.loads(record_path.read_text())
        record["device"] = "SM-X810-OTHER"
        record_path.write_text(json.dumps(record))
        self.assertEqual(fallbacks.list_snapshots(self.root), [])
        record["device"] = fallbacks.MODEL
        record_path.write_text(json.dumps(record))
        with self.assertRaisesRegex(Exception, "drive is no longer mounted"):
            fallbacks.list_snapshots(self.root, expected_device=self.root.stat().st_dev + 1)
        link_root = self.root / "link"
        link_root.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(Exception, "Choose an existing"):
            fallbacks.list_snapshots(link_root)

    def test_ignores_unrelated_and_incomplete_directories(self):
        (self.root / "notes").mkdir()
        (self.root / "12345").mkdir()
        self.assertEqual([item.build_number for item in fallbacks.list_snapshots(self.root)],
                         ["36390000000"])


if __name__ == "__main__":
    unittest.main()
