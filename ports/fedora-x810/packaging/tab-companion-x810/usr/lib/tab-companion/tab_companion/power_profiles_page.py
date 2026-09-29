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


BUSCTL = "/usr/bin/busctl"
HELPER = "/usr/libexec/tab-companion-power-profile"
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
            return
        self._active = profile
        self.mode_row.set_selected(PROFILE_INDEX[profile])
        self.mode_row.set_sensitive(True)
        self.status_row.set_subtitle(dict(PROFILES)[profile])
        self._updating = False

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
