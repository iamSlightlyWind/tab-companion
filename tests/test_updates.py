# SPDX-License-Identifier: MIT
import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from email.message import Message
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tab_companion import updates
from tab_companion.updates import (
    APP_BUILD_ARTIFACTS,
    BuildArtifact,
    UpdateConfig,
    UpdateError,
    asset_supported_by_manager,
    device_id,
    extract_verified_asset,
    fetch_latest_build,
    host_target,
    installed_build_info,
    package_manager,
)


class MemoryResponse:
    def __init__(self, body, url, content_type="application/json", headers=None):
        self.body = body
        self.url = url
        self.offset = 0
        self.headers = Message()
        self.headers["Content-Type"] = content_type
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
        data = self.body[self.offset:self.offset + size]
        self.offset += len(data)
        return data


class UpdatesTests(unittest.TestCase):
    target = {"os_id": "fedora", "os_version": "44", "arch": "aarch64", "device": "SM-X810"}
    repo = "https://github.com/example/tab-companion"
    workflow_url = "https://api.github.com/repos/example/tab-companion/actions/workflows/build-updates.yml/runs?branch=main&event=push&status=completed&per_page=100"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache_home = Path(self.temp.name) / "cache-home"
        self.cache_home.mkdir(mode=0o700)
        self.run = self._run(run_id=101, run_number=7)
        self.package = b"native package bytes"
        self.manifest = self._manifest(self.run, self.package)
        self.archive = self._zip(self.manifest, {"tab-companion.rpm": self.package})
        self.artifact_url = "https://api.github.com/repos/example/tab-companion/actions/artifacts/9001/zip"
        self.release_url = "https://api.github.com/repos/example/tab-companion/releases/tags/tab-companion-build-101"
        self.release_asset_url = "https://github.com/example/tab-companion/releases/download/tab-companion-build-101/tab-companion-build.zip"

    @staticmethod
    def _run(*, run_id, run_number, conclusion="success", event="push", branch="main", status="completed", commit=None):
        return {
            "id": run_id,
            "run_number": run_number,
            "run_attempt": 1,
            "event": event,
            "status": status,
            "conclusion": conclusion,
            "head_branch": branch,
            "head_sha": commit or (format(run_id, "040x")),
            "path": ".github/workflows/build-updates.yml",
        }

    @staticmethod
    def _manifest(run, package, *, project="tab-companion", target=None, sha=None, size=None):
        return {
            "schema_version": 1,
            "project": project,
            "version": "1.5.0+ci",
            "run_id": run["id"],
            "run_number": run["run_number"],
            "commit": run["head_sha"],
            "branch": run["head_branch"],
            "assets": [{
                "name": "tab-companion.rpm",
                "sha256": sha or hashlib.sha256(package).hexdigest(),
                "size": len(package) if size is None else size,
                "format": "rpm",
                "package_name": "tab-companion",
                "package_version": "1.5.0-7.fc44",
                "target": target or UpdatesTests.target,
            }],
        }

    @staticmethod
    def _zip(manifest, files, extra_members=()):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("tab-companion-update.json", json.dumps(manifest).encode())
            for name, body in files.items():
                archive.writestr(name, body)
            for name, body in extra_members:
                archive.writestr(name, body)
        return stream.getvalue()

    def _release_record(self, archive, *, asset_name="tab-companion-build.zip", asset_size=None):
        return {
            "tag_name": "tab-companion-build-101",
            "draft": False,
            "prerelease": False,
            "assets": [{
                "name": asset_name,
                "size": len(archive) if asset_size is None else asset_size,
                "browser_download_url": self.release_asset_url,
            }],
        }

    def _responses(self, runs=None, archive=None, download_size=None, asset_name="tab-companion-build.zip",
                   asset_size=None):
        runs = [self.run] if runs is None else runs
        archive = self.archive if archive is None else archive
        release = self._release_record(archive, asset_name=asset_name, asset_size=asset_size)

        def open_url(request, timeout=None):
            url = request.full_url
            if url.startswith(self.workflow_url.split("?", 1)[0]):
                return MemoryResponse(json.dumps({"workflow_runs": runs}).encode(), url)
            if url == self.release_url:
                return MemoryResponse(json.dumps(release).encode(), url)
            if url == self.release_asset_url:
                headers = {"Content-Length": len(archive) if download_size is None else download_size}
                return MemoryResponse(archive, "https://release-assets.githubusercontent.com/secure-release.zip?sig=x",
                                      "application/zip", headers)
            raise AssertionError(f"Unexpected URL: {url}")

        return open_url

    def _fetch(self, **kwargs):
        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache_home)}), \
                patch("urllib.request.urlopen", side_effect=self._responses(**kwargs)):
            return fetch_latest_build(self.repo, expected_project="tab-companion", target=self.target,
                                      public_release=True)

    def test_fetch_selects_latest_successful_exact_push_build(self):
        older = self._run(run_id=99, run_number=6)
        newer_failed = self._run(run_id=102, run_number=8, conclusion="failure")
        wrong_event = self._run(run_id=103, run_number=9, event="workflow_dispatch")
        wrong_branch = self._run(run_id=104, run_number=10, branch="dev")
        build = self._fetch(runs=[newer_failed, wrong_event, wrong_branch, older, self.run])
        self.assertIsInstance(build, BuildArtifact)
        self.assertEqual(build.run_id, 101)
        self.assertEqual(build.run_number, 7)
        self.assertEqual(build.commit, self.run["head_sha"])
        self.assertEqual(build.asset.name, "tab-companion.rpm")
        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache_home)}):
            self.assertEqual(extract_verified_asset(build).read_bytes(), self.package)

    def test_public_api_and_release_asset_requests_need_no_authentication(self):
        seen = []
        response = self._responses()

        def inspect(request, timeout=None):
            seen.append(request)
            return response(request, timeout)

        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache_home)}), \
                patch("urllib.request.urlopen", side_effect=inspect):
            fetch_latest_build(self.repo, expected_project="tab-companion", target=self.target,
                               public_release=True)
        requests = [request for request in seen if "api.github.com" in request.full_url]
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(request.get_header("Authorization") is None for request in seen))

    def test_action_artifact_mode_remains_available_for_port_feeds(self):
        archive_path = self.cache_home / "test-action-artifact.zip"
        archive_path.write_bytes(self.archive)
        record = {"id": 9001, "archive_download_url": self.artifact_url}
        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache_home)}), \
                patch.object(updates, "_latest_successful_run", return_value=self.run), \
                patch.object(updates, "_artifact_for_run", return_value=(record, len(self.archive))), \
                patch.object(updates, "_download_action_artifact", return_value=archive_path) as download:
            result = fetch_latest_build(self.repo, expected_project="tab-companion", target=self.target)
            cache = updates._cache_dir()
        self.assertEqual(result.asset.name, "tab-companion.rpm")
        download.assert_called_once_with(self.artifact_url, len(self.archive), run_id=101,
                                         artifact_id=9001, cache=cache)

    def test_rejects_when_no_successful_push_run_exists(self):
        failed = self._run(run_id=102, run_number=8, conclusion="failure")
        with self.assertRaisesRegex(UpdateError, "No successful push build"):
            self._fetch(runs=[failed])

    def test_rejects_build_for_mismatched_target(self):
        wrong_target = dict(self.target, device="SM-X910")
        manifest = self._manifest(self.run, self.package, target=wrong_target)
        archive = self._zip(manifest, {"tab-companion.rpm": self.package})
        with self.assertRaisesRegex(UpdateError, "No build asset matches"):
            self._fetch(archive=archive)

    def test_release_asset_name_is_selected_exactly(self):
        with self.assertRaisesRegex(UpdateError, "does not contain exactly one tab-companion-build.zip"):
            self._fetch(asset_name="tab-companion-fedora.zip")

    def test_rejects_artifact_with_wrong_project_or_run_identity(self):
        mismatched = self._manifest(self.run, self.package, project="another-project")
        with self.assertRaisesRegex(UpdateError, "different project"):
            self._fetch(archive=self._zip(mismatched, {"tab-companion.rpm": self.package}))
        wrong_run = dict(self.manifest, run_id=555)
        with self.assertRaisesRegex(UpdateError, "does not match the workflow run"):
            self._fetch(archive=self._zip(wrong_run, {"tab-companion.rpm": self.package}))

    def test_rejects_path_traversal_or_nested_zip_members(self):
        archive = self._zip(self.manifest, {"tab-companion.rpm": self.package}, [("../escape", b"x")])
        with self.assertRaisesRegex(UpdateError, "unsafe or duplicate"):
            self._fetch(archive=archive)
        nested = self._zip(self.manifest, {"subdir/tab-companion.rpm": self.package})
        with self.assertRaisesRegex(UpdateError, "unsafe or duplicate"):
            self._fetch(archive=nested)

    def test_extract_verifies_digest_and_size(self):
        bad_digest = self._manifest(self.run, self.package, sha="0" * 64)
        build = self._fetch(archive=self._zip(bad_digest, {"tab-companion.rpm": self.package}))
        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache_home)}):
            with self.assertRaisesRegex(UpdateError, "SHA-256"):
                extract_verified_asset(build)

        bad_size = self._manifest(self.run, self.package, size=len(self.package) + 2)
        build = self._fetch(archive=self._zip(bad_size, {"tab-companion.rpm": self.package}))
        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache_home)}):
            with self.assertRaisesRegex(UpdateError, "size does not match"):
                extract_verified_asset(build)

    def test_rejects_release_asset_size_mismatch_and_over_limit(self):
        with self.assertRaisesRegex(UpdateError, "size differs from GitHub metadata"):
            self._fetch(download_size=len(self.archive) + 1)
        oversized = updates.MAX_BUILD_ARCHIVE_BYTES + 1
        with self.assertRaisesRegex(UpdateError, "exceeds the size limit"):
            self._fetch(asset_size=oversized)

    def test_config_persists_only_github_repositories(self):
        with tempfile.TemporaryDirectory() as temp:
            config = UpdateConfig(Path(temp) / "updates.json")
            config.set("app_repo", "https://github.com/example/app")
            config.set("port_repo", "https://github.com/example/port.git/")
            again = UpdateConfig(Path(temp) / "updates.json")
            self.assertEqual(again.get("app_repo", ""), "https://github.com/example/app")
            self.assertEqual(again.get("port_repo", ""), "https://github.com/example/port.git")
            self.assertEqual(Path(temp, "updates.json").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(UpdateError):
                config.set("bad", "https://example.invalid/owner/repo")

    def test_installed_build_info_is_bounded_and_safe(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "app-build.json"
            self.assertEqual(installed_build_info(path), {})
            expected = {"project": "tab-companion", "run_id": 51, "commit": "a" * 40}
            path.write_text(json.dumps(expected), encoding="utf-8")
            self.assertEqual(installed_build_info(path), expected)
            path.write_text("[]", encoding="utf-8")
            self.assertEqual(installed_build_info(path), {})
            path.write_text("{" + (" " * updates.MAX_INSTALLED_BUILD_INFO_BYTES) + "}", encoding="utf-8")
            self.assertEqual(installed_build_info(path), {})
            link = Path(temp) / "link.json"
            link.symlink_to(path)
            self.assertEqual(installed_build_info(link), {})

    def test_build_versions_are_informational_not_ordered(self):
        # Freshness is identified by the workflow run/commit, not a release-version comparator.
        self.assertFalse(hasattr(updates, "compare_versions"))
        self.assertEqual(self._fetch().asset.version, "1.5.0+ci")

    def test_device_target_and_package_manager_utilities(self):
        self.assertEqual(APP_BUILD_ARTIFACTS, {
            "deb": "tab-companion-ubuntu",
            "rpm": "tab-companion-fedora",
            "pacman": "tab-companion-arch",
        })
        with tempfile.TemporaryDirectory() as temp:
            port_file = Path(temp) / "port.json"
            port_file.write_text(json.dumps({"device_id": "SM-X810"}), encoding="utf-8")
            self.assertEqual(device_id(port_file), "SM-X810")
            model_file = Path(temp) / "model"
            model_file.write_bytes(b"Samsung GTS9PWIFI PROJECT (board-id,04)\0")
            self.assertEqual(device_id(str(Path(temp) / "missing.json"), str(model_file)), "SM-X810")
        with patch("tab_companion.updates.parse_os_release", return_value={"ID": "fedora", "VERSION_ID": "44"}), \
                patch("tab_companion.updates.device_id", return_value="SM-X810"), \
                patch("tab_companion.updates.platform.machine", return_value="aarch64"):
            self.assertEqual(host_target(), self.target)
        cases = (
            ({"ID": "ubuntu"}, {"apt-get", "dpkg"}, "deb"),
            ({"ID": "fedora"}, {"dnf5", "rpm"}, "rpm"),
            ({"ID": "arch"}, {"pacman"}, "pacman"),
        )
        for release, installed, expected in cases:
            with self.subTest(os_id=release["ID"]), \
                    patch("tab_companion.updates.parse_os_release", return_value=release), \
                    patch("tab_companion.updates.shutil.which",
                          side_effect=lambda name, installed=installed: f"/usr/bin/{name}" if name in installed else None):
                self.assertEqual(package_manager(), expected)
        self.assertTrue(asset_supported_by_manager(type("Asset", (), {"format": "rpm"})(), "rpm"))

    def test_repository_and_workflow_inputs_are_restricted(self):
        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache_home)}), \
                patch("urllib.request.urlopen", side_effect=self._responses()):
            with self.assertRaisesRegex(UpdateError, "public github.com"):
                fetch_latest_build("https://example.invalid/o/r", expected_project="tab-companion", target=self.target)
            with self.assertRaisesRegex(UpdateError, "YAML filename"):
                fetch_latest_build(self.repo, expected_project="tab-companion", target=self.target,
                                   workflow_file="../not-safe.yml")


if __name__ == "__main__":
    unittest.main()
