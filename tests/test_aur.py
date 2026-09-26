# SPDX-License-Identifier: MIT
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tab_companion.aur import _safe_extract
from tab_companion.updates import UpdateError


class AurArchiveTests(unittest.TestCase):
    def test_rejects_parent_path(self):
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "source.tar"
            with tarfile.open(archive_path, "w") as archive:
                payload = b"owned"
                member = tarfile.TarInfo("../escape")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            with self.assertRaises(UpdateError):
                _safe_extract(archive_path, Path(temp) / "out")
            self.assertFalse((Path(temp) / "escape").exists())


if __name__ == "__main__":
    unittest.main()
