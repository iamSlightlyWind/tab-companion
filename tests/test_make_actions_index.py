import importlib.util
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "make-actions-index.py"
SPEC = importlib.util.spec_from_file_location("make_actions_index", SCRIPT)
INDEX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INDEX)


class PackageFieldCommandTests(unittest.TestCase):
    def test_dpkg_archive_precedes_requested_fields(self):
        archive = Path("/tmp/example.deb")
        expected = [
            "dpkg-deb", "-f", str(archive), "Package", "Version", "Architecture"
        ]
        output = "Package: pkg\nVersion: 1.0\nArchitecture: all\n"
        with patch.object(INDEX.subprocess, "check_output", return_value=output) as run:
            values = INDEX.deb_package_fields(archive)
        run.assert_called_once_with(expected, text=True)
        self.assertEqual(values, ("pkg", "1.0", "all"))

    def test_deb_metadata_rejects_missing_or_malformed_fields(self):
        for output in ("Package pkg\nVersion: 1.0\nArchitecture: all\n",
                       "Package: pkg\nVersion: 1.0\n"):
            with self.subTest(output=output), \
                    patch.object(INDEX.subprocess, "check_output", return_value=output), \
                    self.assertRaises(SystemExit):
                INDEX.deb_package_fields(Path("/tmp/example.deb"))

    def test_rpm_archive_remains_after_query_format(self):
        archive = Path("/tmp/example.rpm")
        expected = ["rpm", "-qp", "--queryformat", "%{NAME}\\n%{ARCH}", str(archive)]
        with patch.object(INDEX.subprocess, "check_output", return_value="pkg\nnoarch\n") as run:
            values = INDEX.fields(
                ["rpm", "-qp", "--queryformat", "%{NAME}\\n%{ARCH}", "{archive}"],
                archive,
            )
        run.assert_called_once_with(expected, text=True)
        self.assertEqual(values, ["pkg", "noarch"])

    def test_requires_exactly_one_archive_placeholder(self):
        with self.assertRaisesRegex(SystemExit, "exactly one"):
            INDEX.fields(["dpkg-deb", "-f", "Package"], Path("x.deb"))
        with self.assertRaisesRegex(SystemExit, "exactly one"):
            INDEX.fields(["rpm", "{archive}", "{archive}"], Path("x.rpm"))

    def test_arch_metadata_is_read_during_streaming_tar_iteration(self):
        contents = b"pkgname = tab-companion\npkgver = 1.4.2-1000007\narch = any\n"
        tar_data = io.BytesIO()
        with tarfile.open(fileobj=tar_data, mode="w") as archive:
            metadata = tarfile.TarInfo(".PKGINFO")
            metadata.size = len(contents)
            archive.addfile(metadata, io.BytesIO(contents))
            later = tarfile.TarInfo("usr/share/tab-companion/marker")
            later.size = 1
            archive.addfile(later, io.BytesIO(b"x"))

        class Decoder:
            def __init__(self, stream):
                self.stdout = stream

            def wait(self):
                return 0

        package = Path("/tmp/tab-companion.pkg.tar.zst")
        with patch.object(INDEX.subprocess, "Popen", return_value=Decoder(io.BytesIO(tar_data.getvalue()))) as run:
            values = INDEX.pacman_package_fields(package)
        run.assert_called_once_with(["zstd", "-q", "-dc", str(package)], stdout=INDEX.subprocess.PIPE)
        self.assertEqual(values, ("tab-companion", "1.4.2-1000007", "any"))


class BuildIndexSelectionTests(unittest.TestCase):
    def make_index(self, target, files):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in files:
                (root / name).write_bytes(b"test")
            env = {
                "APP_BUILD_RUN_ID": "123",
                "APP_BUILD_RUN_NUMBER": "7",
                "APP_BUILD_HEAD_SHA": "0123456789abcdef0123456789abcdef01234567",
                "APP_BUILD_BRANCH": "main",
            }
            with patch.object(INDEX.sys, "argv", [str(SCRIPT), str(root), target]), \
                    patch.dict(os.environ, env, clear=False):
                if target == "ubuntu":
                    with patch.object(INDEX, "deb_package_fields",
                                      return_value=("ubuntu-gts9u-companion", "1.4.2+build.7", "all")):
                        INDEX.main()
                elif target == "fedora":
                    with patch.object(INDEX, "fields", return_value=("tab-companion", "1.4.2.7-1000007.fc44", "noarch")), \
                            patch.object(INDEX, "aur_metadata", return_value={
                                "pkgname": "tab-companion", "pkgver": "1.4.2", "pkgrel": "1000007",
                            }):
                        INDEX.main()
                else:
                    with patch.object(INDEX, "pacman_package_fields",
                                      return_value=("tab-companion", "1.4.2-1000007", "any")):
                        INDEX.main()
            return json.loads((root / "tab-companion-update.json").read_text())

    def test_ubuntu_index_contains_only_ubuntu_deb(self):
        manifest = self.make_index("ubuntu", ["companion.deb"])
        self.assertEqual([asset["format"] for asset in manifest["assets"]], ["deb"])
        self.assertEqual(manifest["assets"][0]["target"]["os_id"], "ubuntu")

    def test_fedora_index_contains_only_rpm(self):
        manifest = self.make_index("fedora", ["companion.rpm"])
        self.assertEqual([asset["format"] for asset in manifest["assets"]], ["rpm"])
        self.assertEqual(manifest["assets"][0]["target"]["os_id"], "fedora")

    def test_arch_index_contains_only_native_pacman_package(self):
        manifest = self.make_index("arch", ["tab-companion-1.4.2-1000007-any.pkg.tar.zst"])
        self.assertEqual([asset["format"] for asset in manifest["assets"]], ["pacman-local"])
        self.assertEqual(manifest["assets"][0]["target"]["os_id"], "archarm")

    def test_arch_workflow_builds_and_publishes_native_package(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/build-updates.yml").read_text()
        arch_job = workflow.split("\n  arch:\n", 1)[1].split("\n  publish:\n", 1)[0]
        self.assertIn("makepkg --nodeps --noconfirm --cleanbuild", arch_job)
        self.assertIn('find /home/builder/build -maxdepth 1 -type f -name "*.pkg.tar.zst"', arch_job)
        self.assertIn('one(root, "*.pkg.tar.zst")', (SCRIPT.read_text()))
        self.assertNotIn("--outdir", arch_job)


if __name__ == "__main__":
    unittest.main()
