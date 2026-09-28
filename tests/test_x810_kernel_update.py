# SPDX-License-Identifier: MIT
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

sys_path = Path(__file__).resolve().parents[1] / "src"
import sys
sys.path.insert(0, str(sys_path))

from tab_companion import x810_kernel_update as update
from tab_companion.updates import UpdateError


class MemoryResponse:
    def __init__(self, body, url, headers=None):
        self.body = body
        self.url = url
        self.offset = 0
        self.headers = Message()
        self.headers["Content-Type"] = "application/json"
        for key, value in (headers or {}).items():
            self.headers[key] = str(value)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.url

    def read(self, size=-1):
        if size < 0:
            size = len(self.body) - self.offset
        body = self.body[self.offset:self.offset + size]
        self.offset += len(body)
        return body


class X810ReleaseTests(unittest.TestCase):
    def test_fallback_path_placeholder_is_safe_for_gtk_markup(self):
        page = Path(__file__).resolve().parents[1] / "src/tab_companion/x810_kernel_update_page.py"
        source = page.read_text(encoding="utf-8")
        self.assertNotIn("<build number>", source)
        self.assertIn("BUILD_NUMBER/files", source)

    def setUp(self):
        self.old_partitions = update.PARTITIONS
        update.PARTITIONS = {name: 128 for name in self.old_partitions}
        self.addCleanup(setattr, update, "PARTITIONS", self.old_partitions)
        self.tag = "x810-fedora-port-build-12345"
        self.asset_data = {}
        self.asset_records = []
        for name, size in update.PARTITIONS.items():
            data = (name.encode() * ((size + len(name) - 1) // len(name)))[:size]
            self._record(name, data)
        self._record("kernel.rpm", b"kernel rpm payload")
        self.manifest = {
            "schema_version": 1,
            "type": "x810-fedora-release",
            "device": {"model": "SM-X810", "codename": "gts9pwifi"},
            "source_commit": "a" * 40,
            "release_tag": self.tag,
            "port_version": "1.2.3",
            "assets": self.asset_records,
        }
        body = json.dumps(self.manifest).encode()
        manifest_url = self._url("manifest.json")
        self.remote_assets = [
            {"name": name, "size": len(data), "browser_download_url": self._url(name)}
            for name, data in self.asset_data.items()
        ]
        self.remote_assets.append({"name": "manifest.json", "size": len(body),
                                  "browser_download_url": manifest_url})
        self.release = {"tag_name": self.tag, "draft": False, "prerelease": False,
                        "assets": self.remote_assets}
        self.manifest_body = body

    def _url(self, name):
        return f"https://github.com/iamSlightlyWind/x810-fedroid/releases/download/{self.tag}/{name}"

    def _record(self, name, data):
        self.asset_data[name] = data
        self.asset_records.append({"name": name, "sha256": hashlib.sha256(data).hexdigest(),
                                   "size_bytes": len(data)})

    def test_fetch_validates_x810_identity_release_tag_and_required_assets(self):
        def open_url(request, timeout=None):
            self.assertEqual(request.full_url, self._url("manifest.json"))
            return MemoryResponse(self.manifest_body, request.full_url,
                                  {"Content-Length": len(self.manifest_body)})

        with patch.object(update, "_api_json", return_value=self.release), \
             patch("urllib.request.urlopen", side_effect=open_url):
            release = update.fetch_latest_x810_release()
        self.assertEqual(release.build_number, "12345")
        self.assertEqual(release.tag, self.tag)
        self.assertEqual(set(release.assets), set(update.KERNEL_ASSETS))

    def test_rejects_wrong_device_before_any_download(self):
        self.manifest["device"]["model"] = "SM-X910"
        self.manifest_body = json.dumps(self.manifest).encode()
        self.remote_assets[-1]["size"] = len(self.manifest_body)
        with patch.object(update, "_api_json", return_value=self.release), \
             patch("urllib.request.urlopen", return_value=MemoryResponse(
                 self.manifest_body, self._url("manifest.json"),
                 {"Content-Length": len(self.manifest_body)})):
            with self.assertRaisesRegex(UpdateError, "not for the SM-X810"):
                update.fetch_latest_x810_release()

    def test_rejects_manifest_digest_or_remote_size_disagreement(self):
        self.remote_assets[0]["size"] += 1
        with patch.object(update, "_api_json", return_value=self.release), \
             patch("urllib.request.urlopen", return_value=MemoryResponse(
                 self.manifest_body, self._url("manifest.json"),
                 {"Content-Length": len(self.manifest_body)})):
            with self.assertRaisesRegex(UpdateError, "disagrees with the manifest"):
                update.fetch_latest_x810_release()

    def test_download_verifies_hashes_and_reuses_only_matching_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "cache"
            cache.mkdir(mode=0o700)

            def open_url(request, timeout=None):
                url = request.full_url
                if url == self._url("manifest.json"):
                    data = self.manifest_body
                else:
                    data = self.asset_data[Path(url).name]
                return MemoryResponse(data, "https://release-assets.githubusercontent.com/asset?token=x",
                                      {"Content-Length": len(data)})

            with patch.object(update, "_api_json", return_value=self.release), \
                 patch.object(update, "_cache_dir", return_value=cache), \
                 patch("urllib.request.urlopen", side_effect=open_url):
                release = update.fetch_latest_x810_release()
                directory = update.download_x810_release(release)
                self.assertEqual((directory / "boot.img").read_bytes(), self.asset_data["boot.img"])
                # A corrupt cached image is replaced, not trusted by name alone.
                (directory / "boot.img").write_bytes(b"bad")
                directory = update.download_x810_release(release)
            self.assertEqual((directory / "boot.img").read_bytes(), self.asset_data["boot.img"])
            cached_manifest = json.loads((directory / "manifest.json").read_text())
            self.assertEqual(cached_manifest["release_tag"], self.tag)

    def test_rejects_unexpected_release_repository(self):
        with self.assertRaisesRegex(UpdateError, "configured x810-fedroid"):
            update.fetch_latest_x810_release("https://github.com/example/not-x810")

    def test_fallback_folder_persists_and_rejects_changed_mount_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "fallback"
            folder.mkdir()
            config = root / "config" / "tab-companion" / "x810-kernel-backup.json"
            with patch.object(update, "backup_folder_config_path", return_value=config):
                mount_id = update.save_backup_folder(str(folder))
                self.assertEqual(update.load_backup_folder_info(), (str(folder), mount_id))
                record = json.loads(config.read_text())
                record["st_dev"] = mount_id + 1
                config.write_text(json.dumps(record))
                self.assertIsNone(update.load_backup_folder_info())


class X810KernelUiContractTests(unittest.TestCase):
    def test_page_is_gated_to_fedora_x810_and_requires_backup_before_apply(self):
        page = (Path(__file__).resolve().parents[1] / "src/tab_companion/updates_page.py").read_text()
        self.assertIn('self.manager == "rpm"', page)
        self.assertIn('self.target.get("device") == "SM-X810"', page)
        self.assertIn('port_id == "x810-fedora"', page)
        kernel_page = (Path(__file__).resolve().parents[1] / "src/tab_companion/x810_kernel_update_page.py").read_text()
        self.assertIn("bool(self.release and self.backup_folder", kernel_page)
        self.assertIn('"apply"', kernel_page)
        self.assertIn("backup_device", kernel_page)
        self.assertIn("microSD or USB-OTG", kernel_page)

    def test_narrow_polkit_writer_is_packaged_for_fedora_x810(self):
        root = Path(__file__).resolve().parents[1] / "ports/fedora-x810"
        builder = (root / "tools/build-tab-companion-x810-bundle.sh").read_text()
        policy = (root / "packaging/io.github.agcarbajo.TabCompanion.X810.policy").read_text()
        self.assertIn("x810-kernel-update-core", builder)
        self.assertIn("tab-companion-kernel-update", builder)
        self.assertIn("io.github.agcarbajo.TabCompanion.X810.kernel-update", policy)
        self.assertIn("/usr/local/libexec/tab-companion-kernel-update", policy)


if __name__ == "__main__":
    unittest.main()
