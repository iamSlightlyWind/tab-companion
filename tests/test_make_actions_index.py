import importlib.util
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


if __name__ == "__main__":
    unittest.main()
