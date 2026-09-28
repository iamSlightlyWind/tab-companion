# SPDX-License-Identifier: MIT
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch


HELPER = Path(__file__).resolve().parents[1] / "ports/fedora-x810/tools/tab-companion-boot-status"
LOADER = importlib.machinery.SourceFileLoader("x810_tab_companion_status", str(HELPER))
SPEC = importlib.util.spec_from_loader("x810_tab_companion_status", LOADER)
status = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(status)


class X810TabCompanionStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / "android").mkdir()
        (self.base / "android/name.txt").write_text("One UI\n")

    def test_stale_android_image_fingerprints_and_missing_storage_do_not_hide_status(self):
        # Core status now validates image sizes, not historical image hashes.
        # This simulates that valid boot-set response plus an unavailable
        # optional Android storage probe (e.g. changed partition numbering).
        backend = {"current": "fedora", "ready": {"fedora": True, "android": True},
                   "errors": {}}
        runner = Mock(return_value=SimpleNamespace(
            returncode=0, stdout=json.dumps(backend), stderr=""))

        def unavailable_storage(*_args, **_kwargs):
            raise subprocess.CalledProcessError(1, "blockdev")

        result = status.build_status(
            backend="backend", base=str(self.base), runner=runner,
            statvfs=lambda _path: SimpleNamespace(f_blocks=100, f_frsize=4096,
                                                    f_bavail=25),
            check_output=unavailable_storage,
        )

        self.assertEqual(result["current"], "fedora")
        self.assertEqual([entry["complete"] for entry in result["sets"]], [True, True])
        self.assertEqual(result["sets"][1]["label"], "One UI")
        self.assertNotIn("android", result["storage"])

    def test_storage_probe_errors_are_nonfatal(self):
        backend = {"current": "fedora", "ready": {"fedora": True}, "errors": {}}
        result = status.build_status(
            backend="backend", base=str(self.base),
            runner=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0, stdout=json.dumps(backend), stderr=""),
            statvfs=lambda _path: (_ for _ in ()).throw(OSError("statvfs unavailable")),
            check_output=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                FileNotFoundError("blockdev")),
        )
        self.assertEqual(result["current"], "fedora")
        self.assertTrue(result["sets"][0]["complete"])
        self.assertEqual(result["storage"], {})

    def test_backend_json_error_is_reported_as_readable_diagnostic(self):
        failure = SimpleNamespace(
            returncode=1,
            stdout=json.dumps({"current": None, "sets": [], "error": "wrong linuxroot"}),
            stderr="",
        )
        with self.assertRaisesRegex(RuntimeError, "wrong linuxroot"):
            status.build_status(backend="backend", runner=lambda *_a, **_k: failure)

    def test_malformed_backend_json_is_not_treated_as_success(self):
        failure = SimpleNamespace(returncode=0, stdout="not-json", stderr="")
        with self.assertRaises(json.JSONDecodeError):
            status.build_status(backend="backend", runner=lambda *_a, **_k: failure)

    def test_top_level_failure_is_written_to_stderr_for_the_gui(self):
        message = StringIO()
        with patch.object(status, "build_status", side_effect=RuntimeError("partition check failed")), \
                redirect_stderr(message):
            exit_code = status.main()
        self.assertEqual(exit_code, 1)
        self.assertIn("partition check failed", message.getvalue())


if __name__ == "__main__":
    unittest.main()
