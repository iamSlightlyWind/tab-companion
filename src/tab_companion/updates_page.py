# SPDX-License-Identifier: MIT
"""Cross-distro app and port updater UI."""

import hashlib
import os
import subprocess
import threading
from pathlib import Path

from gi.repository import Adw, Gdk, GLib, Gtk

from . import VERSION
from .admin_auth import command as admin_command
from .aur import build_aur_package
from .i18n import _
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
    installed_port_version,
    parse_os_release,
    package_manager,
)

INSTALL_HELPER = "/usr/libexec/tab-companion-install-package"


def legacy_ubuntu_update_available():
    """Keep the upstream APT/dpkg X910 update UI available on its target only."""
    if parse_os_release().get("ID", "").lower() != "ubuntu":
        return False
    try:
        model = Path("/proc/device-tree/model").read_bytes().rstrip(b"\0").decode()
    except OSError:
        return False
    return (
        model == "Samsung Galaxy Tab S9 Ultra Wi-Fi"
        and os.path.isfile("/usr/libexec/tab-companion-update")
    )


class UpdatesPage(Adw.PreferencesPage):
    """Independent latest-successful-build channels for app and port."""

    def __init__(self, _window):
        super().__init__()
        self.set_title(_("Updates"))
        self.set_margin_top(18)
        self.set_margin_bottom(18)
        self.config = UpdateConfig()
        self.record = get_port_record()
        self.target = host_target()
        self.manager = package_manager()
        self.sources = {}
        # Keep these secondary actions visually compact without shrinking
        # the surrounding preference rows or changing their labels.
        self._button_css = Gtk.CssProvider()
        self._button_css.load_from_data(
            b"button.update-action { min-height: 28px; padding-top: 1px; padding-bottom: 1px; padding-left: 10px; padding-right: 10px; }"
        )
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, self._button_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        self._build_source(
            key="app",
            title=_("Tab Companion"),
            project=APP_PROJECT,
            default_repo=APP_REPO,
            workflow_file="build-updates.yml",
            branch="main",
            artifact_name=APP_BUILD_ARTIFACTS.get(self.manager, "tab-companion-fedora"),
            public_release=True,
            release_tag_prefix="tab-companion-build",
            build_info_path="/usr/share/tab-companion/app-build.json",
            version=VERSION,
            description=_("Update the companion app without changing the installed Linux port."),
        )
        # Ubuntu X910 already has the upstream APT/dpkg offline port updater;
        # keep it as the system channel instead of showing a second generic
        # port feed that would not understand its legacy update format.
        if not legacy_ubuntu_update_available():
            port_id = self.record.get("port_id") or ""
            port_repo = self.record.get("repo_url") or ""
            port_version = installed_port_version(self.record)
            has_x810_kernel_updater = (
                self.manager == "rpm"
                and self.target.get("device") == "SM-X810"
                and port_id == "x810-fedora"
            )
            self._build_source(
                key="port",
                title=self.record.get("name", _("Linux port")),
                project=port_id,
                default_repo=port_repo,
                workflow_file=self.record.get("workflow_file", "build-updates.yml"),
                branch=self.record.get("branch", "main"),
                artifact_name=self.record.get("artifact_name", "port-build"),
                public_release=self.record.get("public_release") is True,
                release_tag_prefix=self.record.get(
                    "release_tag_prefix", f"{port_id}-build"
                ),
                build_info_path=self.record.get("build_info_path", "/usr/share/tab-companion/port-build.json"),
                version=port_version,
                description=(
                    _("Update the Fedora port support package. Kernel and boot-image updates use the guarded X810 updater below.")
                    if has_x810_kernel_updater
                    else _("Update the Linux port packages. Kernel and boot images remain manual TWRP updates.")
                ),
            )
            if has_x810_kernel_updater:
                from .x810_kernel_update_page import X810KernelUpdateSection
                self.add(X810KernelUpdateSection(self.target))
        if not self.manager:
            self._set_status("app", _("No supported package manager was detected."))
            self._set_status("port", _("No supported package manager was detected."))

    def _build_source(self, *, key, title, project, default_repo, workflow_file, branch,
                      artifact_name, build_info_path, version, description, public_release=False,
                      release_tag_prefix="tab-companion-build"):
        group = Adw.PreferencesGroup(title=title, description=description)
        self.add(group)
        if default_repo:
            repo = self.config.get(key + "_repo", default_repo)
        else:
            saved_repo = self.config.values.get(key + "_repo", "")
            repo = saved_repo if isinstance(saved_repo, str) else ""
        repo_row = Adw.ActionRow(title=_("Build repository"), subtitle=_("Public GitHub repository; latest successful build"))
        repo_entry = Gtk.Entry(text=repo, hexpand=True, width_chars=42, valign=Gtk.Align.CENTER,
                               placeholder_text="https://github.com/owner/repository")
        repo_entry.set_tooltip_text(repo)
        repo_entry.connect("changed", lambda entry: entry.set_tooltip_text(entry.get_text()))
        repo_row.add_suffix(repo_entry)
        group.add(repo_row)

        status = Adw.ActionRow(title=_("Not checked"), subtitle=_("Installed app version: {version}").format(version=version))
        status_icon = Gtk.Image(icon_name="software-update-available-symbolic")
        status.add_prefix(status_icon)
        group.add(status)
        buttons = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
            valign=Gtk.Align.CENTER, vexpand=False,
        )
        check = Gtk.Button(
            label=_("Check"), css_classes=["pill", "update-action"],
            valign=Gtk.Align.CENTER, vexpand=False,
        )
        install = Gtk.Button(
            label=_("Install update"),
            css_classes=["pill", "suggested-action", "update-action"],
            sensitive=False, valign=Gtk.Align.CENTER, vexpand=False,
        )
        buttons.append(check)
        buttons.append(install)
        actions = Adw.ActionRow(title=_("Build actions"))
        actions.add_suffix(buttons)
        group.add(actions)

        state = {"project": project, "workflow_file": workflow_file, "branch": branch,
                 "artifact_name": artifact_name, "build_info_path": build_info_path,
                 "public_release": public_release, "release_tag_prefix": release_tag_prefix,
                 "version": version,
                 "repo_entry": repo_entry, "status": status, "check": check,
                 "status_icon": status_icon, "install": install, "build": None,
                 "default_repo": default_repo}
        self.sources[key] = state
        check.connect("clicked", self._check, key)
        install.connect("clicked", self._confirm_install, key)
        repo_entry.connect("activate", self._check, key)

    def _set_status(self, key, message, detail=None, icon="software-update-available-symbolic"):
        state = self.sources.get(key)
        if not state:
            return
        state["status"].set_title(message)
        state["status"].set_subtitle(detail or "")
        state["status"].set_tooltip_text(detail or message)
        state["status_icon"].set_from_icon_name(icon)

    def _repo_url(self, key):
        state = self.sources[key]
        repo = state["repo_entry"].get_text().strip().rstrip("/")
        self.config.set(key + "_repo", repo)
        return repo

    def _check(self, _button, key):
        state = self.sources[key]
        if not state["project"]:
            self._set_status(key, _("No port origin is configured."), _("Set a port identity and repository in the port package."), "dialog-warning-symbolic")
            return
        if not self.manager:
            self._set_status(key, _("Unsupported package manager."), icon="dialog-warning-symbolic")
            return
        try:
            repo_url = self._repo_url(key)
        except UpdateError as exc:
            self._set_status(key, _("Couldn't check the latest build."), str(exc), "dialog-warning-symbolic")
            return
        state["check"].set_sensitive(False)
        state["install"].set_sensitive(False)
        state["build"] = None
        self._set_status(key, _("Checking latest successful build…"), icon="content-loading-symbolic")

        def work():
            try:
                build = fetch_latest_build(
                    repo_url, expected_project=state["project"], target=self.target,
                    workflow_file=state["workflow_file"], branch=state["branch"],
                    artifact_name=state["artifact_name"],
                    public_release=state["public_release"],
                    release_tag_prefix=state["release_tag_prefix"],
                )
                asset = build.asset
                if not asset_supported_by_manager(asset, self.manager):
                    raise UpdateError(f"This build asset ({asset.format}) does not match package manager {self.manager}")
                local = installed_build_info(state["build_info_path"])
                try:
                    local_run_id = int(local.get("run_id", 0))
                except (TypeError, ValueError):
                    local_run_id = 0
                comparison = (0 if local_run_id == build.run_id else
                              -1 if local_run_id > build.run_id else 1)
                result = (build, comparison, None)
            except Exception as exc:
                result = (None, None, str(exc))
            GLib.idle_add(self._checked, key, *result)
        threading.Thread(target=work, daemon=True).start()

    def _checked(self, key, build, comparison, error):
        state = self.sources[key]
        state["check"].set_sensitive(True)
        if error:
            self._set_status(key, _("Couldn't check the latest build."), error, "dialog-warning-symbolic")
            state["install"].set_sensitive(False)
        elif comparison == 0:
            state["build"] = None
            self._set_status(key, _("Up to date."),
                             _("Already running successful build #{number} ({commit}).").format(
                                 number=build.run_number, commit=build.head_sha[:8]), "emblem-ok-symbolic")
            state["install"].set_sensitive(False)
        elif comparison < 0:
            state["build"] = None
            installed = installed_build_info(state["build_info_path"])
            self._set_status(key, _("Installed build is newer than the latest successful build."),
                             _("Installed build #{installed}; latest successful build #{latest}.").format(
                                 installed=installed.get("run_number", "?"),
                                 latest=build.run_number), "emblem-ok-symbolic")
            state["install"].set_sensitive(False)
        else:
            state["build"] = build
            self._set_status(key, _("New successful build #{number}").format(number=build.run_number),
                             _("{name} · {format} · commit {commit} · checksum verified").format(
                                 name=build.asset.name, format=build.asset.format,
                                 commit=build.head_sha[:8]), "software-update-available-symbolic")
            state["install"].set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def _confirm_install(self, _button, key):
        build = self.sources[key]["build"]
        if build is None:
            return
        asset = build.asset
        body = _("Tab Companion will download the package from successful build #{number}, verify its SHA-256, then ask the system package manager to install it.").format(number=build.run_number)
        if self.manager == "pacman":
            body += "\n\n" + _("Arch Linux requires a full system upgrade before installing a local package; pacman will upgrade the system first.")
        if asset.format == "aur-source":
            body += "\n\n" + _("This AUR PKGBUILD runs as your logged-in user and can access your user files. Review the source if you do not trust this repository.")
        dialog = Adw.AlertDialog(heading=_("Install build #{number}?").format(number=build.run_number), body=body)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("install", _("Install"))
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _dialog, response: self._install(key) if response == "install" else None)
        dialog.present(self.get_root())

    def _install(self, key):
        state = self.sources[key]
        build = state["build"]
        if build is None:
            return
        asset = build.asset
        state["check"].set_sensitive(False)
        state["install"].set_sensitive(False)
        self._set_status(key, _("Downloading and verifying build…"), asset.name, "content-loading-symbolic")

        def work():
            try:
                package = extract_verified_asset(build)
                fmt = asset.format
                if fmt == "aur-source":
                    package = build_aur_package(package)
                    fmt = "pacman-local"
                if not os.path.isfile(INSTALL_HELPER):
                    raise UpdateError(_("The privileged package installer is not installed in this build."))
                digest_obj = hashlib.sha256()
                with package.open("rb") as package_file:
                    for block in iter(lambda: package_file.read(1024 * 1024), b""):
                        digest_obj.update(block)
                digest = digest_obj.hexdigest()
                command = admin_command(
                    "install-package", INSTALL_HELPER, "--path", str(package), "--sha256", digest,
                    "--format", fmt, "--package-name", asset.package_name,
                    "--package-version", asset.package_version,
                    "--expected-arch", self.target["arch"],
                )
                result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, env={**os.environ, "LC_ALL": "C"})
                if result.returncode:
                    raise UpdateError((result.stdout or _("Package manager failed."))[-4000:])
                outcome = (True, result.stdout.strip())
            except Exception as exc:
                outcome = (False, str(exc))
            GLib.idle_add(self._installed, key, *outcome)
        threading.Thread(target=work, daemon=True).start()

    def _installed(self, key, success, detail):
        state = self.sources[key]
        state["check"].set_sensitive(True)
        if success:
            state["build"] = None
            if key == "port":
                # A port RPM/DEB owns port.json, so read back what the package
                # manager actually installed. This keeps another Check in the
                # same app session from offering the just-installed version.
                refreshed = get_port_record()
                if refreshed.get("port_id") == state["project"]:
                    self.record = refreshed
                    state["version"] = installed_port_version(refreshed)
                    self._set_status(key, _("Port package update completed."),
                                     _("Installed version: {version}. Restart may be required for updated services and device settings.").format(
                                         version=state["version"]), "emblem-ok-symbolic")
                else:
                    self._set_status(key, _("Package update completed."),
                                     _("Restart Tab Companion to reload port metadata. A system restart may be required."),
                                     "emblem-ok-symbolic")
            else:
                self._set_status(key, _("Package update completed."),
                                 _("Restart Tab Companion to load this build."),
                                 "emblem-ok-symbolic")
        else:
            self._set_status(key, _("Update not applied."), detail, "dialog-warning-symbolic")
            state["install"].set_sensitive(True)
        return GLib.SOURCE_REMOVE
