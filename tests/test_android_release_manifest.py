import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "make_android_release_manifest.py"
SPEC = importlib.util.spec_from_file_location("make_android_release_manifest", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AndroidReleaseManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.apk = self.root / "switcher.apk"
        with zipfile.ZipFile(self.apk, "w") as archive:
            archive.writestr("AndroidManifest.xml", b"binary manifest")
            archive.writestr("classes.dex", b"test dex")
        self.gradle = self.root / "build.gradle.kts"
        self.gradle.write_text(
            'applicationId = "com.tabcompanion.x810.bootswitcher"\n'
            'versionCode = 1\nversionName = "1.0.0-x810.1"\n',
            encoding="utf-8",
        )
        self.env = {
            "APP_BUILD_RUN_ID": "123456",
            "APP_BUILD_RUN_NUMBER": "47",
            "APP_BUILD_HEAD_SHA": "a" * 40,
            "APP_BUILD_BRANCH": "main",
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_manifest_contains_provenance_and_apk_digest(self):
        output = self.root / "dist" / "manifest.json"
        document = MODULE.create_manifest(self.apk, output, self.gradle, self.env)
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(saved, document)
        self.assertEqual(document["project"], "tab-companion-android")
        self.assertEqual(document["run_id"], 123456)
        self.assertEqual(document["application"]["version_name"], "1.0.0-x810.1")
        self.assertEqual(document["asset"]["name"], "switcher.apk")
        self.assertEqual(len(document["asset"]["sha256"]), 64)

    def test_rejects_invalid_apk(self):
        self.apk.write_bytes(b"not an apk")
        with self.assertRaisesRegex(ValueError, "valid ZIP"):
            MODULE.create_manifest(self.apk, self.root / "manifest.json", self.gradle, self.env)

    def test_rejects_non_main_or_invalid_provenance(self):
        env = dict(self.env, APP_BUILD_BRANCH="feature")
        with self.assertRaisesRegex(ValueError, "build identity"):
            MODULE.create_manifest(self.apk, self.root / "manifest.json", self.gradle, env)


if __name__ == "__main__":
    unittest.main()
