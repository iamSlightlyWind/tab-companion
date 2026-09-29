# SPDX-License-Identifier: MIT
"""Small text-mode updater for installations without a usable GUI."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from .admin_auth import (
    available as admin_available,
    authorize_at_startup,
    command as admin_command,
)
from .updates import (
    APP_BUILD_ARTIFACTS,
    APP_PROJECT,
    APP_REPO,
    UpdateConfig,
    UpdateError,
    asset_supported_by_manager,
    extract_verified_asset,
    fetch_latest_build,
    get_port_record,
    host_target,
    installed_build_info,
    package_manager,
)
from .x810_fallbacks import list_snapshots
from .x810_kernel_update import (
    DEFAULT_REPOSITORY,
    download_x810_release,
    fetch_latest_x810_release,
    load_backup_folder_info,
    save_backup_folder,
)


INSTALL_HELPER = "/usr/libexec/tab-companion-install-package"
KERNEL_HELPER = "/usr/local/libexec/tab-companion-kernel-update"


def menu_actions(target: dict, manager: str | None) -> tuple[tuple[str, str], ...]:
    """The TUI deliberately exposes only package/app/kernel update actions."""
    actions = [("app", "Check/install Tab Companion update")]
    actions.append(("port", "Check/install Fedora port update"))
    if target.get("device") == "SM-X810" and manager == "rpm":
        actions.append(("kernel", "Check/install X810 kernel + boot update"))
        actions.append(("fallback", "List/restore cached X810 boot fallback"))
    actions.append(("quit", "Quit"))
    return tuple(actions)


def _ask_confirm(prompt: str, *, input_fn=input, output_fn=print) -> bool:
    output_fn(prompt)
    return input_fn("Type INSTALL to continue (anything else cancels): ").strip() == "INSTALL"


def _run_package_channel(key: str, *, input_fn=input, output_fn=print):
    manager = package_manager()
    if manager not in ("deb", "rpm", "pacman"):
        raise UpdateError("This system has no supported native package manager")
    target = host_target()
    config = UpdateConfig()
    if key == "app":
        project, repo = APP_PROJECT, config.get("app_repo", APP_REPO)
        workflow, branch = "build-updates.yml", "main"
        artifact = APP_BUILD_ARTIFACTS.get(manager, "")
        public, tag_prefix = True, "tab-companion-build"
        info_path = "/usr/share/tab-companion/app-build.json"
    else:
        record = get_port_record()
        project, repo = record.get("port_id") or "", record.get("repo_url") or ""
        if not project or not repo:
            raise UpdateError("No Linux port repository is configured")
        workflow = record.get("workflow_file", "build-updates.yml")
        branch = record.get("branch", "main")
        artifact = record.get("artifact_name", "port-build")
        public = record.get("public_release") is True
        tag_prefix = record.get("release_tag_prefix", f"{project}-build")
        info_path = record.get("build_info_path", "/usr/share/tab-companion/port-build.json")
    build = fetch_latest_build(
        repo, expected_project=project, target=target, workflow_file=workflow,
        branch=branch, artifact_name=artifact, public_release=public,
        release_tag_prefix=tag_prefix,
    )
    asset = build.asset
    if not asset_supported_by_manager(asset, manager):
        raise UpdateError(f"Latest package ({asset.format}) does not match detected package manager ({manager})")
    installed = installed_build_info(info_path)
    try:
        installed_run = int(installed.get("run_id", 0))
    except (TypeError, ValueError):
        installed_run = 0
    if installed_run == build.run_id:
        output_fn(f"Already running build #{build.run_number} ({build.head_sha[:12]}).")
        return
    if installed_run > build.run_id:
        output_fn(f"Installed build is newer (run {installed_run}); refusing downgrade to #{build.run_number}.")
        return
    output_fn(f"{key.title()} update: build #{build.run_number}, {asset.name}, {asset.size} bytes")
    if not _ask_confirm("The package SHA-256 will be verified before privileged installation.",
                        input_fn=input_fn, output_fn=output_fn):
        output_fn("Cancelled; no package was installed.")
        return
    package = extract_verified_asset(build)
    if asset.format == "aur-source":
        raise UpdateError("TUI does not build or install AUR source packages; use the GUI updater instead")
    digest = hashlib.sha256()
    with package.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    command = admin_command(
        "install-package", INSTALL_HELPER, "--path", str(package), "--sha256", digest.hexdigest(),
        "--format", asset.format, "--package-name", asset.package_name,
        "--package-version", asset.package_version, "--expected-arch", target["arch"],
    )
    subprocess.run(command, check=True, env={**os.environ, "LC_ALL": "C"})
    output_fn("Package update installed. Restart Tab Companion to load an app update.")


def _fallback_folder(*, input_fn=input, output_fn=print):
    info = load_backup_folder_info()
    if info:
        folder, device = info
        output_fn(f"Fallback folder: {folder}")
        if os.stat(folder).st_dev == device:
            return folder, device
        output_fn("The saved drive identity changed; choose the currently mounted folder.")
    folder = input_fn("Absolute path to the mounted fallback folder (blank cancels): ").strip()
    if not folder:
        return None
    path = Path(folder)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise UpdateError("Choose an existing absolute folder without a symbolic-link path")
    real = os.path.realpath(path)
    if real != str(path) or not os.access(real, os.W_OK | os.X_OK):
        raise UpdateError("Fallback folder must be a writable real directory")
    device = save_backup_folder(real)
    output_fn(f"Saved fallback location: {real} (filesystem {device})")
    return real, device


def _run_kernel_update(*, input_fn=input, output_fn=print):
    folder_info = _fallback_folder(input_fn=input_fn, output_fn=output_fn)
    if not folder_info:
        output_fn("Cancelled; no update started.")
        return
    folder, device = folder_info
    release = fetch_latest_x810_release(DEFAULT_REPOSITORY)
    output_fn(f"Latest X810 build: {release.build_number} · kernel.rpm and four boot images")
    if not _ask_confirm(
        f"This asks polkit for admin authentication, snapshots the present boot set under {folder}/"
        f"{release.build_number}, installs kernel.rpm, and writes boot/init_boot/vendor_boot/dtbo. "
        "It does not write vbmeta/recovery/firmware/GPT/user data and does not reboot.",
        input_fn=input_fn, output_fn=output_fn,
    ):
        output_fn("Cancelled; no update started.")
        return
    def progress(name, complete, total, cached):
        percentage = 100 if not total else int(100 * complete / total)
        output_fn(f"{'Cached' if cached else 'Fetching'} {name}: {percentage}%")
    release_dir = download_x810_release(release, progress=progress)
    subprocess.run(admin_command("kernel-update", KERNEL_HELPER, "apply", "--release-dir",
                                 str(release_dir), "--backup-root", folder, "--backup-device",
                                 str(device)),
                   check=True, env={**os.environ, "LC_ALL": "C"})
    output_fn("Kernel and boot images installed and read-back verified. Reboot only when ready.")


def _run_fallback_restore(*, input_fn=input, output_fn=print):
    folder_info = _fallback_folder(input_fn=input_fn, output_fn=output_fn)
    if not folder_info:
        output_fn("Cancelled; no restore started.")
        return
    folder, device = folder_info
    snapshots = list_snapshots(folder, expected_device=device)
    if not snapshots:
        output_fn("No complete, integrity-verified X810 fallback snapshots found.")
        return
    output_fn("Verified cached fallback builds:")
    for item in snapshots:
        output_fn(f"  {item.build_number}  {item.created_utc}  kernel {item.source_kernel_release}")
    wanted = input_fn("Build number to restore (blank cancels): ").strip()
    snapshot = next((item for item in snapshots if item.build_number == wanted), None)
    if snapshot is None:
        output_fn("No matching verified build selected; no writes were made.")
        return
    phrase = f"RESTORE X810 BUILD {snapshot.build_number}"
    output_fn("This will restore the four verified boot images and matching kernel modules. It will not reboot.")
    if input_fn(f"Type exactly `{phrase}` to confirm: ").strip() != phrase:
        output_fn("Cancelled; no restore started.")
        return
    subprocess.run(admin_command("kernel-update", KERNEL_HELPER, "restore", "--backup-root",
                                 folder, "--backup-device", str(device), "--build-number",
                                 snapshot.build_number),
                   check=True, env={**os.environ, "LC_ALL": "C"})
    output_fn("Fallback images and matching kernel modules restored and read-back verified. No reboot was performed.")


def main(argv=None, *, input_fn=input, output_fn=print):
    del argv  # Reserved for future TUI-only flags; menu actions stay deliberately narrow.
    if admin_available():
        output_fn("Requesting administrator authorization for this Tab Companion session…")
        if not authorize_at_startup():
            output_fn("Authorization cancelled; privileged actions will request it if needed.")
    target, manager = host_target(), package_manager()
    handlers = {
        "app": lambda: _run_package_channel("app", input_fn=input_fn, output_fn=output_fn),
        "port": lambda: _run_package_channel("port", input_fn=input_fn, output_fn=output_fn),
        "kernel": lambda: _run_kernel_update(input_fn=input_fn, output_fn=output_fn),
        "fallback": lambda: _run_fallback_restore(input_fn=input_fn, output_fn=output_fn),
    }
    actions = menu_actions(target, manager)
    while True:
        output_fn("\nTab Companion — text updater")
        for number, (_action, label) in enumerate(actions, 1):
            output_fn(f" {number}. {label}")
        selected = input_fn("Select an action: ").strip()
        try:
            choice = int(selected)
            if not 1 <= choice <= len(actions):
                raise ValueError
        except ValueError:
            output_fn("Choose one of the displayed numbers.")
            continue
        action = actions[choice - 1][0]
        if action == "quit":
            return 0
        try:
            handlers[action]()
        except (UpdateError, OSError, subprocess.CalledProcessError) as exc:
            output_fn(f"Operation stopped: {exc}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
