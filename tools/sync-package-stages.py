#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Copy canonical app code into native package staging trees.

Fedora uses the shared X810 app source. Ubuntu keeps its upstream fingerprint
and offline APT/dpkg UI, with the new cross-distro app update page added beside
it; its legacy updater backend is not replaced.
"""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/tab_companion"
FEDORA_STAGES = (
    ROOT / "ports/fedora-x810/packaging/tab-companion-x810/usr/lib/tab-companion/tab_companion",
    ROOT / "ports/fedora-x810/usr/lib/tab-companion/tab_companion",
)
UBUNTU_PACKAGE = ROOT / "ports/ubuntu-x910/usr/lib/tab-companion/tab_companion"


def replace_tree(source: Path, target: Path):
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def sync_ubuntu():
    for name in (
        "__init__.py", "aur.py", "updates.py", "updates_page.py", "package_installer.py",
        "update_languages.py", "update_translations.py",
    ):
        shutil.copy2(SOURCE / name, UBUNTU_PACKAGE / name)
    window = UBUNTU_PACKAGE / "window.py"
    text = window.read_text(encoding="utf-8")
    import_line = "from .update_page import UpdatePage\n"
    if "from .updates_page import UpdatesPage" not in text:
        if import_line not in text:
            raise SystemExit("Ubuntu package window no longer matches expected source")
        text = text.replace(import_line, import_line + "from .updates_page import UpdatesPage\n", 1)
    old = '''        self.update_page = UpdatePage(self)\n        self.view_stack.add_titled_with_icon(\n            self.update_page, "updates", _("Updates"), "software-update-available-symbolic"\n        )'''
    new = '''        self.view_stack.add_titled_with_icon(\n            UpdatesPage(self), "updates", _("Updates"), "software-update-available-symbolic"\n        )\n        self.update_page = UpdatePage(self)\n        self.view_stack.add_titled_with_icon(\n            self.update_page, "ubuntu-system-updates", _("Ubuntu system"),\n            "software-update-available-symbolic"\n        )'''
    if '"ubuntu-system-updates"' not in text:
        if old not in text:
            raise SystemExit("Ubuntu package update page block no longer matches expected source")
        text = text.replace(old, new, 1)
    window.write_text(text, encoding="utf-8")


def main():
    for target in FEDORA_STAGES:
        replace_tree(SOURCE, target)
    sync_ubuntu()
    print("Synchronized Tab Companion source into Fedora and Ubuntu package stages")


if __name__ == "__main__":
    main()
