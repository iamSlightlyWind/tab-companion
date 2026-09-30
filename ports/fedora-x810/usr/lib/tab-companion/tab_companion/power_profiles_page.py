# SPDX-License-Identifier: MIT
"""Small UI for the system power-profile D-Bus API.

Fedora X810 uses tuned-ppd to implement the standard PowerProfiles API. This
page deliberately changes only one of the three advertised profiles; it does
not write CPU sysfs values directly or invent per-policy governor settings.
"""

import re
import subprocess

from gi.repository import Adw, Gio, GLib, Gtk

from .admin_auth import command as admin_command
from .i18n import _
from .zram import (
    DEFAULT_SIZE_DECI_GB,
    MAX_SIZE_DECI_GB,
    MIN_SIZE_DECI_GB,
    active_size_bytes,
    parse_configured_size,
)


BUSCTL = "/usr/bin/busctl"
HELPER = "/usr/libexec/tab-companion-power-profile"
ZRAM_HELPER = "/usr/libexec/tab-companion-zram-size"
SERVICE = "net.hadess.PowerProfiles"
OBJECT = "/net/hadess/PowerProfiles"
INTERFACE = "net.hadess.PowerProfiles"
PROFILES = (
    ("power-saver", _("Power saver")),
    ("balanced", _("Balanced")),
    ("performance", _("Performance")),
)
PROFILE_IDS = tuple(profile_id for profile_id, _label in PROFILES)
PROFILE_INDEX = {profile_id: index for index, (profile_id, _label) in enumerate(PROFILES)}


def active_profile_from_busctl(output: str) -> str:
    """Parse `busctl get-property` output without accepting unknown profiles."""
    match = re.fullmatch(r'\s*s\s+"([^"]+)"\s*', output)
    if not match or match.group(1) not in PROFILE_INDEX:
        raise ValueError("PowerProfiles returned an unsupported active profile")
    return match.group(1)


def available() -> bool:
    """Only show controls when both the standard API client and allowlisted helper ship."""
    import os

    return os.path.isfile(BUSCTL) and os.path.isfile(HELPER)


class PowerProfilesPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__(title=_("Performance"), icon_name="battery-level-60-symbolic")
        self._updating = False
        self._active = None

        group = Adw.PreferencesGroup(
            title=_("Power mode"),
            description=_("Choose how the tablet balances responsiveness and power use."),
        )
        self.mode_row = Adw.ComboRow(
            title=_("Mode"),
            model=Gtk.StringList.new([label for _profile_id, label in PROFILES]),
        )
        self.mode_row.connect("notify::selected", self._mode_changed)
        group.add(self.mode_row)

        self.status_row = Adw.ActionRow(title=_("Current profile"))
        group.add(self.status_row)

        refresh = Gtk.Button(label=_("Refresh"), valign=Gtk.Align.CENTER)
        refresh.connect("clicked", lambda *_args: self.refresh())
        self.status_row.add_suffix(refresh)
        self.add(group)

        self.zram_row = None
        self.zram_status_row = None
        if __import__("os").path.isfile(ZRAM_HELPER):
            zram_group = Adw.PreferencesGroup(
                title=_("Memory compression"),
                description=_("Choose the maximum compressed swap size. The change takes effect after reboot."),
            )
            self.zram_row = Adw.ActionRow(title=_("ZRAM size"))
            self.zram_adjustment = Gtk.Adjustment.new(
                DEFAULT_SIZE_DECI_GB / 10,
                MIN_SIZE_DECI_GB / 10,
                MAX_SIZE_DECI_GB / 10,
                0.1,
                1.0,
                0.0,
            )
            self.zram_adjustment.connect("value-changed", self._zram_value_changed)
            self.zram_scale = Gtk.Scale.new(Gtk.Orientation.HORIZONTAL, self.zram_adjustment)
            self.zram_scale.set_draw_value(False)
            self.zram_scale.set_round_digits(1)
            self.zram_scale.set_hexpand(True)
            self.zram_editor = Gtk.SpinButton.new(self.zram_adjustment, 0.1, 1)
            self.zram_editor.set_numeric(True)
            self.zram_editor.set_width_chars(5)
            self.zram_units = Gtk.Label(label=_("GB"))
            zram_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            zram_controls.set_hexpand(True)
            zram_controls.append(self.zram_scale)
            zram_controls.append(self.zram_editor)
            zram_controls.append(self.zram_units)
            self.zram_row.add_suffix(zram_controls)
            zram_group.add(self.zram_row)
            self.zram_status_row = Adw.ActionRow(title=_("ZRAM status"))
            self.zram_apply_button = Gtk.Button(label=_("Apply"), valign=Gtk.Align.CENTER)
            self.zram_apply_button.connect("clicked", self._apply_zram)
            self.zram_status_row.add_suffix(self.zram_apply_button)
            zram_group.add(self.zram_status_row)
            self.add(zram_group)
        self.refresh()

    def _read_active(self):
        result = subprocess.run(
            [BUSCTL, "--system", "get-property", SERVICE, OBJECT, INTERFACE, "ActiveProfile"],
            check=True,
            capture_output=True,
            text=True,
            timeout=4,
        )
        return active_profile_from_busctl(result.stdout)

    def refresh(self):
        self._updating = True
        try:
            profile = self._read_active()
        except (OSError, subprocess.SubprocessError, ValueError):
            self._active = None
            self.mode_row.set_sensitive(False)
            self.status_row.set_subtitle(_("Power profile service is unavailable."))
            self._updating = False
            self._refresh_zram()
            return
        self._active = profile
        self.mode_row.set_selected(PROFILE_INDEX[profile])
        self.mode_row.set_sensitive(True)
        self.status_row.set_subtitle(dict(PROFILES)[profile])
        self._updating = False
        self._refresh_zram()

    def _refresh_zram(self):
        if self.zram_row is None:
            return
        configured = parse_configured_size()
        self._zram_configured = configured
        self._updating = True
        self.zram_adjustment.set_value(configured / 10)
        self._updating = False
        self.zram_apply_button.set_sensitive(False)
        active = active_size_bytes()
        if active is None:
            self.zram_status_row.set_subtitle(
                _("Configured for next boot; active zram device is not present.")
            )
        else:
            active_gb = active / 1_000_000_000
            self.zram_status_row.set_subtitle(
                _("Active now: {size:.1f} GB. A new setting applies after reboot.").format(size=active_gb)
            )

    def _apply_zram(self, *_args):
        if self._updating or self.zram_row is None:
            return
        size = round(self.zram_adjustment.get_value() * 10)
        if size == getattr(self, "_zram_configured", None):
            return
        self.zram_apply_button.set_sensitive(False)
        self.zram_status_row.set_subtitle(_("Authorizing ZRAM size change…"))
        try:
            self._zram_process = Gio.Subprocess.new(
                admin_command("zram-size", ZRAM_HELPER, str(size)),
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
            )
            self._zram_process.communicate_utf8_async(None, None, self._zram_finished, size)
        except (GLib.Error, OSError):
            self._zram_failed()

    def _zram_value_changed(self, adjustment):
        if self._updating or self.zram_status_row is None:
            return
        selected = round(adjustment.get_value() * 10)
        configured = getattr(self, "_zram_configured", DEFAULT_SIZE_DECI_GB)
        self.zram_apply_button.set_sensitive(selected != configured)
        if selected != configured:
            self.zram_status_row.set_subtitle(
                _("Selected: {size:.1f} GB. Apply, then reboot to use it.").format(
                    size=selected / 10
                )
            )

    def _zram_finished(self, process, result, size):
        try:
            _stdout, _stderr = process.communicate_utf8_finish(result)
        except (GLib.Error, TypeError):
            self._zram_failed()
            return
        if not process.get_successful():
            self._zram_failed()
            return
        self._zram_configured = size
        self.zram_apply_button.set_sensitive(False)
        self._refresh_zram()

    def _zram_failed(self):
        self._updating = True
        self.zram_adjustment.set_value(
            getattr(self, "_zram_configured", DEFAULT_SIZE_DECI_GB) / 10
        )
        self._updating = False
        self.zram_apply_button.set_sensitive(False)
        self.zram_status_row.set_subtitle(_("Change cancelled or unavailable; previous setting remains."))

    def _mode_changed(self, row, _param):
        if self._updating:
            return
        selected = row.get_selected()
        if selected >= len(PROFILES):
            return
        profile_id = PROFILES[selected][0]
        if profile_id == self._active:
            return

        row.set_sensitive(False)
        self.status_row.set_subtitle(_("Authorizing profile change…"))
        try:
            self._process = Gio.Subprocess.new(
                admin_command("power-profile", HELPER, profile_id),
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
            )
            self._process.communicate_utf8_async(None, None, self._change_finished, profile_id)
        except (GLib.Error, OSError):
            self._switch_failed()

    def _change_finished(self, process, result, profile_id):
        try:
            process.communicate_utf8_finish(result)
        except (GLib.Error, TypeError):
            self._switch_failed()
            return
        if not process.get_successful():
            self._switch_failed()
            return
        self._active = profile_id
        self._updating = True
        self.mode_row.set_selected(PROFILE_INDEX[profile_id])
        self._updating = False
        self.mode_row.set_sensitive(True)
        self.status_row.set_subtitle(dict(PROFILES)[profile_id])

    def _switch_failed(self):
        self._updating = True
        if self._active in PROFILE_INDEX:
            self.mode_row.set_selected(PROFILE_INDEX[self._active])
        self._updating = False
        self.mode_row.set_sensitive(True)
        self.status_row.set_subtitle(_("Change cancelled or unavailable; the previous profile remains active."))
