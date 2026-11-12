import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PKGBUILD = ROOT / "packaging/arch/PKGBUILD.in"


class ArchPackagingTests(unittest.TestCase):
    def test_package_keeps_x810_dualboot_and_removes_only_fedora_origin(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            srcdir = temp / "src"
            pkgdir = temp / "pkg"
            srcdir.mkdir()
            pkgdir.mkdir()
            files = (
                "usr/bin/tab-companion",
                "usr/share/glib-2.0/schemas/gschemas.compiled",
                "usr/share/glib-2.0/schemas/io.github.agcarbajo.TabCompanion.gschema.xml",
                "usr/share/tab-companion/port.json",
                "etc/xdg/autostart/tab-companion-polkit-agent.desktop",
                "usr/local/libexec/x810-boot-switch-core",
                "usr/local/libexec/tab-companion-boot-status",
                "usr/local/libexec/tab-companion-boot-switch",
                "usr/libexec/tab-companion-boot-status",
                "usr/libexec/tab-companion-boot-switch",
                "usr/share/gnome-shell/extensions/dualboot@agcarbajo.github.io/extension.js",
                "usr/share/gnome-shell/extensions/dualboot@agcarbajo.github.io/metadata.json",
                "usr/libexec/tab-companion-admin",
                "usr/share/polkit-1/actions/io.github.agcarbajo.TabCompanion.Admin.policy",
                "usr/share/polkit-1/actions/io.github.agcarbajo.TabCompanion.X810.policy",
                "usr/libexec/tab-companion-keyboard-recover",
            )
            bundle_root = temp / "bundle"
            for relative in files:
                path = bundle_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture\n", encoding="utf-8")
            with tarfile.open(srcdir / "tab-companion-x810.tar.gz", "w:gz") as archive:
                for path in bundle_root.rglob("*"):
                    if path.is_file():
                        archive.add(path, arcname=path.relative_to(bundle_root))

            build_file = temp / "PKGBUILD"
            build_file.write_text(
                PKGBUILD.read_text(encoding="utf-8").replace("@VERSION@", "1.0")
                .replace("@PKGREL@", "1").replace("@BUNDLE_SHA256@", "0" * 64),
                encoding="utf-8",
            )
            environment = os.environ | {"srcdir": str(srcdir), "pkgdir": str(pkgdir)}
            subprocess.run(
                ["bash", "-c", 'source "$1"; package', "test-pkgbuild", str(build_file)],
                check=True, env=environment, capture_output=True, text=True,
            )

            self.assertTrue((pkgdir / "usr/bin/tab-companion").is_file())
            self.assertTrue((pkgdir / "usr/libexec/tab-companion-keyboard-recover").is_file())
            self.assertTrue((pkgdir / "usr/share/glib-2.0/schemas/io.github.agcarbajo.TabCompanion.gschema.xml").is_file())
            self.assertFalse((pkgdir / "usr/share/glib-2.0/schemas/gschemas.compiled").exists())
            removed = (
                "usr/share/tab-companion/port.json",
                "etc/xdg/autostart/tab-companion-polkit-agent.desktop",
            )
            for relative in removed:
                self.assertFalse((pkgdir / relative).exists(), relative)
            retained = (
                "usr/local/libexec/x810-boot-switch-core",
                "usr/local/libexec/tab-companion-boot-status",
                "usr/local/libexec/tab-companion-boot-switch",
                "usr/libexec/tab-companion-boot-status",
                "usr/libexec/tab-companion-boot-switch",
                "usr/share/gnome-shell/extensions/dualboot@agcarbajo.github.io/extension.js",
                "usr/share/gnome-shell/extensions/dualboot@agcarbajo.github.io/metadata.json",
                "usr/libexec/tab-companion-admin",
                "usr/share/polkit-1/actions/io.github.agcarbajo.TabCompanion.Admin.policy",
                "usr/share/polkit-1/actions/io.github.agcarbajo.TabCompanion.X810.policy",
            )
            for relative in retained:
                self.assertTrue((pkgdir / relative).is_file(), relative)

    def test_dualboot_extension_declares_arch_gnome_shell_51(self):
        metadata = ROOT / "ports/fedora-x810/usr/share/gnome-shell/extensions/dualboot@agcarbajo.github.io/metadata.json"
        import json
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        self.assertIn("51", payload["shell-version"])


if __name__ == "__main__":
    unittest.main()
