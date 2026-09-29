# SPDX-License-Identifier: MIT
"""X810-only manual updater for the kernel RPM and four boot partitions."""

import json
import os
import subprocess
import threading

from gi.repository import Adw, Gio, GLib, Gtk

from .i18n import _
from .updates import UpdateError
from .x810_fallbacks import list_snapshots
from .x810_kernel_update import (
    DEFAULT_REPOSITORY,
    download_x810_release,
    fetch_latest_x810_release,
    load_backup_folder_info,
    save_backup_folder,
)


KERNEL_HELPER = "/usr/local/libexec/tab-companion-kernel-update"


class X810KernelUpdateSection(Adw.PreferencesGroup):
    """Download a release, snapshot the live images, then install with readback."""

    def __init__(self, target):
        super().__init__(
            title=_("Kernel and boot images"),
            description=_("SM-X810 kernel updates. A verified snapshot is saved before the boot partitions are changed."),
        )
        self.target = target
        self.release = None
        backup_info = load_backup_folder_info()
        self.backup_folder = backup_info[0] if backup_info else None
        self.backup_device = backup_info[1] if backup_info else None
        self._busy = False

        self.notice = Gtk.Label(
            label=_("Choose a fallback folder first. Prefer a microSD or USB-OTG drive: TWRP may not be able to read Fedora’s internal Linux partition. If you choose internal storage, copy the numbered backup folder to external storage before relying on it for recovery."),
            wrap=True,
            xalign=0,
        )
        self.notice.add_css_class("dim-label")
        self.notice.set_margin_start(12)
        self.notice.set_margin_end(12)
        self.notice.set_margin_top(4)
        self.notice.set_margin_bottom(8)
        self.add(self.notice)

        folder_row = Adw.ActionRow(
            title=_("Fallback folder"),
            subtitle=_("Not selected — kernel updates are disabled until you choose a folder."),
        )
        self.folder_button = Gtk.Button(label=_("Choose folder…"), css_classes=["pill"])
        self.folder_button.connect("clicked", self._choose_folder)
        folder_row.add_suffix(self.folder_button)
        self.add(folder_row)
        self.folder_row = folder_row
        if self.backup_folder:
            self.folder_row.set_subtitle(
                _("Backups will be saved under {path}/BUILD_NUMBER/files.").format(path=self.backup_folder)
            )

        self.fallback_status = Adw.ActionRow(
            title=_("Cached fallback boot sets"),
            subtitle=_("Choose a fallback folder to list verified snapshots by build number."),
        )
        self.fallback_button = Gtk.Button(label=_("List and restore…"), css_classes=["pill"], sensitive=False)
        self.fallback_button.connect("clicked", self._show_fallbacks)
        self.fallback_status.add_suffix(self.fallback_button)
        self.add(self.fallback_status)
        self._fallbacks = []
        self._refresh_fallbacks()

        self.status = Adw.ActionRow(title=_("Not checked"), subtitle=_("Latest kernel release has not been checked."))
        self.status_icon = Gtk.Image(icon_name="software-update-available-symbolic")
        self.status.add_prefix(self.status_icon)
        self.add(self.status)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.check_button = Gtk.Button(label=_("Check"), css_classes=["pill"])
        self.apply_button = Gtk.Button(label=_("Install kernel update"),
                                       css_classes=["pill", "suggested-action"], sensitive=False)
        self.check_button.connect("clicked", self._check)
        self.apply_button.connect("clicked", self._confirm_apply)
        actions.append(self.check_button)
        actions.append(self.apply_button)
        action_row = Adw.ActionRow(title=_("Build actions"))
        action_row.add_suffix(actions)
        self.add(action_row)

    def _choose_folder(self, _button):
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Choose a fallback folder for X810 boot images"))
        if self.backup_folder:
            try:
                dialog.set_initial_folder(Gio.File.new_for_path(self.backup_folder))
            except Exception:
                pass
        self._folder_dialog = dialog
        dialog.select_folder(self.get_root(), None, self._folder_selected, None)

    def _folder_selected(self, dialog, result, *_args):
        try:
            selected = dialog.select_folder_finish(result)
            path = selected.get_path()
            if not path or not os.path.isabs(path) or not os.path.isdir(path):
                raise UpdateError(_("The selected location is not an accessible local folder."))
            path = os.path.realpath(path)
            if not os.access(path, os.W_OK | os.X_OK):
                raise UpdateError(_("The selected folder is not writable."))
            self.backup_device = save_backup_folder(path)
            self.backup_folder = path
            self.folder_row.set_subtitle(_("Backups will be saved under {path}/BUILD_NUMBER/files.").format(path=path))
            self._refresh_fallbacks()
            self._refresh_apply()
        except Exception as error:
            # The native chooser reports cancellation as a GLib error. Keep the
            # previous selection intact and show only real validation errors.
            cancelled = (isinstance(error, GLib.Error)
                         and error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED))
            if not cancelled:
                self._set_status(_("Fallback folder not accepted"), str(error), warning=True)
        finally:
            self._folder_dialog = None

    def _check(self, _button):
        if self._busy:
            return
        self._set_busy(True)
        self._set_status(_("Checking the latest X810 release…"), _("Only the manifest is downloaded until you confirm installation."))

        def work():
            try:
                release = fetch_latest_x810_release(DEFAULT_REPOSITORY)
                result = (release, None)
            except Exception as error:
                result = (None, str(error))
            GLib.idle_add(self._checked, *result)

        threading.Thread(target=work, daemon=True).start()

    def _checked(self, release, error):
        self._set_busy(False)
        if error:
            self.release = None
            self._set_status(_("Couldn't check kernel updates"), error, warning=True)
        else:
            self.release = release
            self._set_status(
                _("X810 build {number} is available").format(number=release.build_number),
                _("Fedora {version} · commit {commit} · kernel.rpm plus four boot images").format(
                    version=release.port_version, commit=release.source_commit[:12]),
            )
        self._refresh_apply()
        return GLib.SOURCE_REMOVE

    def _refresh_apply(self):
        self.apply_button.set_sensitive(bool(self.release and self.backup_folder
                                             and self.backup_device is not None and not self._busy))

    def _refresh_fallbacks(self):
        self._fallbacks = []
        if not self.backup_folder or self.backup_device is None:
            self.fallback_status.set_subtitle(
                _("Choose a fallback folder to list verified snapshots by build number."))
            self.fallback_button.set_sensitive(False)
            return
        try:
            self._fallbacks = list_snapshots(self.backup_folder, expected_device=self.backup_device)
            self.fallback_status.set_subtitle(
                _("{count} complete, checksum-verified fallback set(s) found.").format(
                    count=len(self._fallbacks)))
            self.fallback_button.set_sensitive(bool(self._fallbacks) and not self._busy)
        except Exception as error:
            self.fallback_status.set_subtitle(str(error))
            self.fallback_button.set_sensitive(False)

    def _show_fallbacks(self, _button):
        self._refresh_fallbacks()
        if not self._fallbacks:
            return
        window = Adw.Window(transient_for=self.get_root(), modal=True,
                           title=_("Choose a cached fallback build"))
        window.set_default_size(520, min(620, 140 + 78 * len(self._fallbacks)))
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        note = Gtk.Label(
            label=_("Only complete snapshots whose four images, kernel modules, and metadata pass checksum validation are shown."),
            wrap=True, xalign=0,
        )
        note.set_margin_top(12)
        note.set_margin_start(12)
        note.set_margin_end(12)
        content.append(note)
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        rows.set_margin_start(12)
        rows.set_margin_end(12)
        rows.set_margin_bottom(12)
        for snapshot in self._fallbacks:
            row = Adw.ActionRow(
                title=_("Build {number}").format(number=snapshot.build_number),
                subtitle=_("Saved {date} · kernel {release}").format(
                    date=snapshot.created_utc or _("date unavailable"),
                    release=snapshot.source_kernel_release or _("unknown")),
                activatable=True,
            )
            row.add_prefix(Gtk.Image(icon_name="drive-harddisk-symbolic"))
            row.connect("activated", lambda _row, item=snapshot: (window.close(), self._confirm_restore(item)))
            rows.append(row)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(rows)
        content.append(scroll)
        toolbar.set_content(content)
        window.set_content(toolbar)
        window.present()

    def _confirm_restore(self, snapshot):
        if not self.backup_folder or self.backup_device is None or self._busy:
            return
        body = _(
            "Restore the four checksum-verified boot images and their matching kernel-module tree from build {number}? "
            "Tab Companion will request administrator authentication, stage and revalidate the selected files, "
            "write only boot, init_boot, vendor_boot and dtbo with read-back verification, and restore matching "
            "modules under /usr/lib/modules. It does not write vbmeta, recovery, firmware, GPT or user data, and "
            "does not reboot. Keep the fallback drive connected until the operation finishes."
        ).format(number=snapshot.build_number)
        dialog = Adw.AlertDialog(
            heading=_("Restore X810 build {number}?").format(number=snapshot.build_number), body=body
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Restore boot set"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _dialog, response:
                       self._restore_snapshot(snapshot) if response == "restore" else None)
        dialog.present(self.get_root())

    def _restore_snapshot(self, snapshot):
        if not self.backup_folder or self.backup_device is None or self._busy:
            return
        self._set_busy(True)
        self._set_status(_("Restoring cached X810 boot set…"),
                         _("Build {number} · no automatic reboot").format(number=snapshot.build_number))

        def work():
            try:
                result = self._run_helper(
                    ["pkexec", KERNEL_HELPER, "restore", "--backup-root", self.backup_folder,
                     "--backup-device", str(self.backup_device), "--build-number", snapshot.build_number],
                    _("Restoring cached boot images and matching modules…"),
                )
                outcome = (True, result)
            except Exception as error:
                outcome = (False, str(error))
            GLib.idle_add(self._restore_completed, *outcome)

        threading.Thread(target=work, daemon=True).start()

    def _restore_completed(self, success, detail):
        self._set_busy(False)
        if success:
            self._set_status(
                _("Fallback build restored and verified"),
                _("Four boot partitions and matching kernel modules restored. No reboot was performed."),
            )
        else:
            self._set_status(_("Fallback restore stopped"), detail, warning=True)
        self._refresh_fallbacks()
        return GLib.SOURCE_REMOVE

    def _set_busy(self, busy):
        self._busy = busy
        self.check_button.set_sensitive(not busy)
        self.folder_button.set_sensitive(not busy)
        self._refresh_fallbacks()
        self._refresh_apply()

    def _set_status(self, title, subtitle="", warning=False):
        self.status.set_title(title)
        self.status.set_subtitle(subtitle)
        self.status.set_tooltip_text(subtitle or title)
        self.status_icon.set_from_icon_name(
            "dialog-warning-symbolic" if warning else "software-update-available-symbolic"
        )

    def _confirm_apply(self, _button):
        if not self.release or not self.backup_folder or self.backup_device is None or self._busy:
            return
        body = _(
            "Tab Companion will download and checksum kernel.rpm and all four X810 boot images. In one privileged update operation it will first save the currently running boot partitions and matching kernel-module tree to {path}/{number}/files, then reinstall the kernel RPM and write boot, init_boot, vendor_boot and dtbo with read-back verification. If the RPM or a partition write fails, it attempts to restore the previous matching module tree and, if partition writes began, all four saved images. It will not touch vbmeta, recovery, firmware, GPT or user data, and will not reboot automatically.\n\n"
            "Keep the selected drive connected throughout the update. TWRP may not see Fedora’s internal Linux filesystem. Prefer microSD/USB-OTG, or copy the numbered folder there before relying on it."
        ).format(path=self.backup_folder, number=self.release.build_number)
        dialog = Adw.AlertDialog(
            heading=_("Install X810 kernel build {number}?").format(number=self.release.build_number),
            body=body,
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("install", _("Back up and install"))
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _dialog, response: self._apply() if response == "install" else None)
        dialog.present(self.get_root())

    def _apply(self):
        if not self.release or not self.backup_folder or self.backup_device is None or self._busy:
            return
        release = self.release
        backup_folder = self.backup_folder
        backup_device = self.backup_device
        self._set_busy(True)
        self._set_status(_("Downloading and verifying kernel update…"), release.tag)

        def progress(name, complete, total, cached):
            percent = 100 if not total else min(100, int(complete * 100 / total))
            label = _("Using verified cached {name}").format(name=name) if cached else _("Downloading {name}: {percent}%").format(name=name, percent=percent)
            GLib.idle_add(self._set_status, _("Preparing X810 kernel update…"), label)

        def work():
            try:
                release_dir = download_x810_release(release, progress=progress)
                update_result = self._run_helper(
                    ["pkexec", KERNEL_HELPER, "apply", "--release-dir", str(release_dir),
                     "--backup-root", backup_folder, "--backup-device", str(backup_device)],
                    _("Saving fallback and applying the kernel update…"),
                )
                result = (True, update_result)
            except Exception as error:
                result = (False, str(error))
            GLib.idle_add(self._completed, *result)

        threading.Thread(target=work, daemon=True).start()

    def _run_helper(self, command, title):
        GLib.idle_add(self._set_status, title, "")
        process = subprocess.Popen(command, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, env={**os.environ, "LC_ALL": "C"})
        output = []
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                message = event.get("message", line)
                kind = event.get("kind")
            except json.JSONDecodeError:
                message, kind = line, "step"
            output.append(message)
            GLib.idle_add(self._set_status, title, message, kind == "error")
        code = process.wait()
        if code:
            raise UpdateError("\n".join(output[-20:]) or _("Privileged kernel updater failed."))
        return "\n".join(output)

    def _completed(self, success, detail):
        self._set_busy(False)
        if success:
            self.release = None
            self._set_status(
                _("Kernel update staged successfully"),
                _("All four partition writes were read-back verified. No reboot was performed. The saved fallback is under {path}. Restart only when ready.").format(
                    path=self.backup_folder or ""),
            )
        else:
            self._set_status(_("Kernel update stopped"), detail, warning=True)
        self._refresh_apply()
        return GLib.SOURCE_REMOVE
