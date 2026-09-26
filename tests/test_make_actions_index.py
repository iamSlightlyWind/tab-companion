import importlib.util
import json
import os
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
                else:
                    with patch.object(INDEX, "fields", return_value=("tab-companion", "1.4.2.7-1000007.fc44", "noarch")), \
                            patch.object(INDEX, "aur_metadata", return_value={
                                "pkgname": "tab-companion", "pkgver": "1.4.2", "pkgrel": "1000007",
                            }):
                        INDEX.main()
            return json.loads((root / "tab-companion-update.json").read_text())

    def test_ubuntu_index_contains_only_ubuntu_deb(self):
        manifest = self.make_index("ubuntu", ["companion.deb"])
        self.assertEqual([asset["format"] for asset in manifest["assets"]], ["deb"])
        self.assertEqual(manifest["assets"][0]["target"]["os_id"], "ubuntu")

    def test_fedora_artifact_indexes_fedora_and_arch_packages(self):
        manifest = self.make_index("fedora", ["companion.rpm", "tab-companion-arch-source.tar.gz"])
        self.assertEqual([asset["format"] for asset in manifest["assets"]], ["rpm", "aur-source"])
        self.assertEqual([asset["target"]["os_id"] for asset in manifest["assets"]], ["fedora", "arch"])


if __name__ == "__main__":
    unittest.main()
