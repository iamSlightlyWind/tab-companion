# SPDX-License-Identifier: MIT
"""Power-profile, CPU temperature, and memory controls for Fedora X810."""

import json
import os
import re
import subprocess
from pathlib import Path

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
from .thermal_limit import MAX_THRESHOLD_C, MIN_THRESHOLD_C, read_settings, read_status
from .power_profile_tuning import read_settings as read_power_tuning_settings
from .browser_memory import (
    SUPPORTED_BROWSERS,
    MAX_LIMIT_DECI_GB,
    MIN_LIMIT_DECI_GB,
    read_settings as read_browser_settings,
    read_status as read_browser_status,
)
BUSCTL = "/usr/bin/busctl"
HELPER = "/usr/libexec/tab-companion-power-profile"
ZRAM_HELPER = "/usr/libexec/tab-companion-zram-size"
SWAP_HELPER = "/usr/libexec/tab-companion-swap-priority"
THERMAL_HELPER = "/usr/libexec/tab-companion-thermal-setting"
PERFORMANCE_TUNING_HELPER = "/usr/libexec/tab-companion-performance-setting"
BROWSER_MEMORY_HELPER = "/usr/libexec/tab-companion-browser-memory-setting"
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
CPUFREQ_ROOT = Path("/sys/devices/system/cpu/cpufreq")


def cpu_governor_summary(root: Path = CPUFREQ_ROOT) -> str:
    """Report the governors actually attached to CPUFreq policies, if any."""
    values = []
    for policy in sorted(root.glob("policy[0-9]*"), key=lambda path: path.name):
        try:
            governor = (policy / "scaling_governor").read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            continue
        if governor:
            values.append((policy.name, governor))
    if not values:
        return _("CPUFreq policies unavailable")
    governors = {governor for _policy, governor in values}
    if len(governors) == 1:
        return _("CPU governor: {governor} ({count} policies)").format(
            governor=values[0][1], count=len(values)
        )
    return _("CPU governors: {values}").format(
        values=", ".join(f"{policy}={governor}" for policy, governor in values)
    )


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

        self.tuning_group = None
        if os.path.isfile(PERFORMANCE_TUNING_HELPER):
            self._tuning_saved = read_power_tuning_settings()
            self._tuning_profile_id = "balanced"
            balanced = self._tuning_saved["profiles"]["balanced"]
            self.tuning_group = Adw.PreferencesGroup(
                title=_("Profile tuning"),
                description=_("Set the maximum CPU and GPU level for each power mode. Lower values reduce heat; kernel thermal protection remains active."),
            )
            self.tuning_row = Adw.ActionRow(title=_("Maximum CPU/GPU level"))
            self.tuning_adjustment = Gtk.Adjustment.new(
                balanced["max_percent"], 30, 100, 5, 10, 0
            )
            self.tuning_adjustment.connect("value-changed", self._tuning_value_changed)
            self.tuning_scale = Gtk.Scale.new(Gtk.Orientation.HORIZONTAL, self.tuning_adjustment)
            self.tuning_scale.set_draw_value(False)
            self.tuning_scale.set_digits(0)
            self.tuning_scale.set_round_digits(0)
            self.tuning_scale.set_hexpand(True)
            self.tuning_scale.set_valign(Gtk.Align.CENTER)
            self.tuning_scale.set_vexpand(False)
            self.tuning_editor = Gtk.SpinButton.new(self.tuning_adjustment, 1, 0)
            self.tuning_editor.set_numeric(True)
            self.tuning_editor.set_width_chars(3)
            self.tuning_editor.set_valign(Gtk.Align.CENTER)
            self.tuning_editor.set_vexpand(False)
            self.tuning_units = Gtk.Label(label="%")
            tuning_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            tuning_controls.set_hexpand(True)
            tuning_controls.set_valign(Gtk.Align.CENTER)
            tuning_controls.set_vexpand(False)
            tuning_controls.append(self.tuning_scale)
            tuning_controls.append(self.tuning_editor)
            tuning_controls.append(self.tuning_units)
            self.tuning_row.add_suffix(tuning_controls)
            self.tuning_group.add(self.tuning_row)

            self.cpu_boost_row = Adw.SwitchRow(
                title=_("CPU boost"),
                subtitle=_("Allow this profile to use the CPU's highest frequency states."),
            )
            self.cpu_boost_row.set_active(balanced["cpu_boost"])
            self.cpu_boost_row.connect("notify::active", self._tuning_value_changed)
            self.tuning_group.add(self.cpu_boost_row)

            self.gpu_boost_row = Adw.SwitchRow(
                title=_("GPU boost"),
                subtitle=_("Allow this profile to use the GPU's highest frequency states."),
            )
            self.gpu_boost_row.set_active(balanced["gpu_boost"])
            self.gpu_boost_row.connect("notify::active", self._tuning_value_changed)
            self.tuning_group.add(self.gpu_boost_row)

            self.tuning_status_row = Adw.ActionRow(title=_("Profile tuning status"))
            self.tuning_apply_button = Gtk.Button(label=_("Apply"), valign=Gtk.Align.CENTER)
            self.tuning_apply_button.connect("clicked", self._save_tuning_settings)
            self.tuning_status_row.add_suffix(self.tuning_apply_button)
            self.tuning_group.add(self.tuning_status_row)
            self.add(self.tuning_group)
            self._tuning_timer = GLib.timeout_add_seconds(2, self._refresh_tuning_status)

        self.thermal_group = None
        if os.path.isfile(THERMAL_HELPER):
            self._thermal_saved = read_settings()
            self.thermal_group = Adw.PreferencesGroup(
                title=_("SoC temperature limit"),
                description=_("Reduce CPU and GPU frequency when the hottest SoC sensor stays above the limit. Kernel thermal protection remains active."),
            )
            self.thermal_enabled_row = Adw.SwitchRow(
                title=_("Temperature-based performance limit"),
                subtitle=_("Temporarily cap CPU and GPU performance while hot."),
            )
            self.thermal_enabled_row.set_active(self._thermal_saved["enabled"])
            self.thermal_enabled_row.connect("notify::active", self._thermal_value_changed)
            self.thermal_group.add(self.thermal_enabled_row)

            self.thermal_threshold_row = Adw.ActionRow(title=_("Temperature limit"))
            self.thermal_adjustment = Gtk.Adjustment.new(
                self._thermal_saved["threshold_c"], MIN_THRESHOLD_C, MAX_THRESHOLD_C, 1, 5, 0
            )
            self.thermal_adjustment.connect("value-changed", self._thermal_value_changed)
            self.thermal_scale = Gtk.Scale.new(Gtk.Orientation.HORIZONTAL, self.thermal_adjustment)
            self.thermal_scale.set_draw_value(False)
            self.thermal_scale.set_digits(0)
            self.thermal_scale.set_hexpand(True)
            self.thermal_scale.set_valign(Gtk.Align.CENTER)
            self.thermal_scale.set_vexpand(False)
            self.thermal_editor = Gtk.SpinButton.new(self.thermal_adjustment, 1, 0)
            self.thermal_editor.set_numeric(True)
            self.thermal_editor.set_width_chars(3)
            self.thermal_editor.set_valign(Gtk.Align.CENTER)
            self.thermal_editor.set_vexpand(False)
            self.thermal_units = Gtk.Label(label=_("°C"))
            controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            controls.set_hexpand(True)
            controls.set_valign(Gtk.Align.CENTER)
            controls.set_vexpand(False)
            controls.append(self.thermal_scale)
            controls.append(self.thermal_editor)
            controls.append(self.thermal_units)
            self.thermal_threshold_row.add_suffix(controls)
            self.thermal_group.add(self.thermal_threshold_row)

            self.thermal_status_row = Adw.ActionRow(title=_("Temperature status"))
            self.thermal_save_button = Gtk.Button(label=_("Save"), valign=Gtk.Align.CENTER)
            self.thermal_save_button.connect("clicked", self._save_thermal_settings)
            self.thermal_status_row.add_suffix(self.thermal_save_button)
            self.thermal_group.add(self.thermal_status_row)
            self.add(self.thermal_group)
            self._refresh_thermal_status()
            self._thermal_timer = GLib.timeout_add_seconds(2, self._refresh_thermal_status)

        self.browser_memory_group = None
        if os.path.isfile(BROWSER_MEMORY_HELPER):
            self._browser_saved = read_browser_settings()
            self.browser_memory_group = Adw.PreferencesGroup(
                title=_("Browser RAM limit"),
                description=_(
                    "Limit native Firefox or Brave RAM use. Flatpak browsers are unsupported. "
                    "A hard limit can cause browser tabs to crash if they need more memory."
                ),
            )
            browser_names = [_("Firefox"), _("Brave")]
            self.browser_memory_browser_row = Adw.ComboRow(
                title=_("Browser"),
                model=Gtk.StringList.new(browser_names),
            )
            self.browser_memory_browser_row.set_selected(
                SUPPORTED_BROWSERS.index(self._browser_saved["browser"])
            )
            self.browser_memory_browser_row.connect(
                "notify::selected", self._browser_memory_value_changed
            )
            self.browser_memory_group.add(self.browser_memory_browser_row)
            self.browser_memory_enabled_row = Adw.SwitchRow(
                title=_("Limit selected browser memory"),
                subtitle=_("Applies to all current and future native sessions; Flatpak is excluded."),
            )
            self.browser_memory_enabled_row.set_active(self._browser_saved["enabled"])
            self.browser_memory_enabled_row.connect("notify::active", self._browser_memory_value_changed)
            self.browser_memory_group.add(self.browser_memory_enabled_row)

            self.browser_memory_row = Adw.ActionRow(title=_("Maximum browser RAM"))
            self.browser_memory_adjustment = Gtk.Adjustment.new(
                self._browser_saved["limit_deci_gb"] / 10,
                MIN_LIMIT_DECI_GB / 10,
                MAX_LIMIT_DECI_GB / 10,
                0.1,
                1.0,
                0,
            )
            self.browser_memory_adjustment.connect("value-changed", self._browser_memory_value_changed)
            self.browser_memory_scale = Gtk.Scale.new(
                Gtk.Orientation.HORIZONTAL, self.browser_memory_adjustment
            )
            self.browser_memory_scale.set_draw_value(False)
            self.browser_memory_scale.set_digits(1)
            self.browser_memory_scale.set_round_digits(1)
            self.browser_memory_scale.set_hexpand(True)
            self.browser_memory_scale.set_valign(Gtk.Align.CENTER)
            self.browser_memory_scale.set_vexpand(False)
            self.browser_memory_editor = Gtk.SpinButton.new(self.browser_memory_adjustment, 0.1, 1)
            self.browser_memory_editor.set_numeric(True)
            self.browser_memory_editor.set_width_chars(3)
            self.browser_memory_editor.set_valign(Gtk.Align.CENTER)
            self.browser_memory_editor.set_vexpand(False)
            self.browser_memory_units = Gtk.Label(label=_("GB"))
            browser_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            browser_controls.set_hexpand(True)
            browser_controls.set_valign(Gtk.Align.CENTER)
            browser_controls.set_vexpand(False)
            browser_controls.append(self.browser_memory_scale)
            browser_controls.append(self.browser_memory_editor)
            browser_controls.append(self.browser_memory_units)
            self.browser_memory_row.add_suffix(browser_controls)
            self.browser_memory_group.add(self.browser_memory_row)

            self.browser_memory_status_row = Adw.ActionRow(title=_("Browser limit status"))
            self.browser_memory_save_button = Gtk.Button(label=_("Apply"), valign=Gtk.Align.CENTER)
            self.browser_memory_save_button.connect("clicked", self._save_browser_memory_settings)
            self.browser_memory_status_row.add_suffix(self.browser_memory_save_button)
            self.browser_memory_group.add(self.browser_memory_status_row)
            self.add(self.browser_memory_group)
            self._refresh_browser_memory_status()
            self._browser_memory_timer = GLib.timeout_add_seconds(
                3, self._refresh_browser_memory_status
            )

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
            self.zram_scale.set_valign(Gtk.Align.CENTER)
            self.zram_scale.set_vexpand(False)
            self.zram_editor = Gtk.SpinButton.new(self.zram_adjustment, 0.1, 1)
            self.zram_editor.set_numeric(True)
            self.zram_editor.set_width_chars(5)
            self.zram_editor.set_valign(Gtk.Align.CENTER)
            self.zram_editor.set_vexpand(False)
            self.zram_units = Gtk.Label(label=_("GB"))
            zram_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            zram_controls.set_hexpand(True)
            zram_controls.set_valign(Gtk.Align.CENTER)
            zram_controls.set_vexpand(False)
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
        self._swap_rows = {}
        if __import__("os").path.isfile(SWAP_HELPER):
            swap_group = Adw.PreferencesGroup(
                title=_("Swap Priority"),
                description=_("Set a separate priority for each swap device. Saved values apply the next time swap is activated; active swap is not interrupted."),
            )
            self.swap_group = swap_group
            self.swap_status_row = Adw.ActionRow(title=_("Swap devices"))
            swap_group.add(self.swap_status_row)
            self.swap_save_button = Gtk.Button(label=_("Save"), valign=Gtk.Align.CENTER)
            self.swap_save_button.connect("clicked", self._save_swap_priorities)
            self.swap_status_row.add_suffix(self.swap_save_button)
            self.add(swap_group)
        self.refresh()

    def _tuning_selected(self):
        if self.tuning_group is None:
            return None
        profiles = {
            name: dict(settings)
            for name, settings in self._tuning_saved["profiles"].items()
        }
        profiles[self._tuning_profile_id] = {
            "max_percent": round(self.tuning_adjustment.get_value()),
            "cpu_boost": self.cpu_boost_row.get_active(),
            "gpu_boost": self.gpu_boost_row.get_active(),
        }
        return {"version": 1, "profiles": profiles}

    def _load_tuning_profile(self, profile_id):
        if self.tuning_group is None:
            return
        if profile_id not in self._tuning_saved["profiles"]:
            profile_id = "balanced"
        self._tuning_profile_id = profile_id
        value = self._tuning_saved["profiles"][profile_id]
        self._updating = True
        self.tuning_adjustment.set_value(value["max_percent"])
        self.cpu_boost_row.set_active(value["cpu_boost"])
        self.gpu_boost_row.set_active(value["gpu_boost"])
        self._updating = False
        self._tuning_value_changed()
        self._refresh_tuning_status()

    def _tuning_value_changed(self, *_args):
        if self.tuning_group is None or self._updating:
            return
        selected = self._tuning_selected()
        self.tuning_apply_button.set_sensitive(selected != self._tuning_saved)
        if selected != self._tuning_saved:
            self.tuning_status_row.set_subtitle(
                _("Unsaved changes for {profile}.").format(
                    profile=dict(PROFILES).get(self._tuning_profile_id, self._tuning_profile_id)
                )
            )

    def _save_tuning_settings(self, *_args):
        selected = self._tuning_selected()
        if selected is None or selected == self._tuning_saved:
            return
        self.tuning_apply_button.set_sensitive(False)
        self.tuning_status_row.set_subtitle(_("Saving profile limits…"))
        try:
            self._tuning_process = Gio.Subprocess.new(
                admin_command(
                    "performance-tuning", PERFORMANCE_TUNING_HELPER,
                    json.dumps(selected, separators=(",", ":")),
                ),
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
            )
            self._tuning_process.communicate_utf8_async(
                None, None, self._tuning_save_finished, selected
            )
        except (GLib.Error, OSError):
            self.tuning_status_row.set_subtitle(
                _("Could not save profile limits; previous settings remain.")
            )
            self._tuning_value_changed()

    def _tuning_save_finished(self, process, result, selected):
        try:
            success, _stdout, stderr = process.communicate_utf8_finish(result)
        except (GLib.Error, TypeError, ValueError):
            success, stderr = False, ""
        if not success or not process.get_successful():
            self.tuning_status_row.set_subtitle(
                (stderr or _("Could not save profile limits; previous settings remain.")).strip()
            )
            self._tuning_value_changed()
            return
        self._tuning_saved = selected
        self.tuning_apply_button.set_sensitive(False)
        self.tuning_status_row.set_subtitle(
            _("Saved for {profile}; the CPU and GPU limits apply shortly.").format(
                profile=dict(PROFILES).get(self._tuning_profile_id, self._tuning_profile_id)
            )
        )

    def _refresh_tuning_status(self):
        if self.tuning_group is None:
            return GLib.SOURCE_REMOVE
        if self._tuning_selected() != self._tuning_saved:
            self.tuning_status_row.set_subtitle(
                _("Unsaved changes for {profile}.").format(
                    profile=dict(PROFILES).get(self._tuning_profile_id, self._tuning_profile_id)
                )
            )
            return GLib.SOURCE_CONTINUE
        status = read_status()
        if status is None or status.get("profile") not in PROFILE_INDEX:
            self.tuning_status_row.set_subtitle(
                _("Saved settings; waiting for the system performance service.")
            )
            return GLib.SOURCE_CONTINUE
        profile = status["profile"]
        name = dict(PROFILES)[profile]
        maximum = status.get("max_percent", self._tuning_saved["profiles"][profile]["max_percent"])
        cpu = _("on") if status.get("cpu_boost") else _("off")
        gpu = _("on") if status.get("gpu_boost") else _("off")
        gpu_max = status.get("gpu_max_mhz")
        gpu_text = _("GPU boost {state}").format(state=gpu)
        if type(gpu_max) in (int, float) and gpu_max > 0:
            gpu_text += _(" · GPU max {value:.0f} MHz").format(value=gpu_max)
        self.tuning_status_row.set_subtitle(
            _("{profile} · CPU/GPU ceiling {maximum}% · CPU boost {cpu} · {gpu}").format(
                profile=name, maximum=maximum, cpu=cpu, gpu=gpu_text
            )
        )
        return GLib.SOURCE_CONTINUE

    def _thermal_selected(self):
        return {
            "enabled": self.thermal_enabled_row.get_active(),
            "threshold_c": round(self.thermal_adjustment.get_value()),
        }

    def _thermal_value_changed(self, *_args):
        if self.thermal_group is None:
            return
        self.thermal_save_button.set_sensitive(self._thermal_selected() != self._thermal_saved)

    def _refresh_thermal_status(self):
        if self.thermal_group is None:
            return GLib.SOURCE_REMOVE
        status = read_status()
        if status is None:
            self.thermal_status_row.set_subtitle(_("Thermal limiter service is unavailable."))
        else:
            def fmt(value):
                return _("—") if value is None else _("{value:.1f}°C").format(value=value)

            summary = _("Current {current} · Average {average} · Peak {peak} · {state}").format(
                current=fmt(status.get("current_c")),
                average=fmt(status.get("average_c")),
                peak=fmt(status.get("peak_c")),
                state=_("limiting") if status.get("limited") else _("not limiting"),
            )
            if status.get("error"):
                summary += " · " + str(status["error"])
            self.thermal_status_row.set_subtitle(summary)
        self._thermal_value_changed()
        return GLib.SOURCE_CONTINUE

    def _browser_memory_selected(self):
        return {
            "browser": SUPPORTED_BROWSERS[self.browser_memory_browser_row.get_selected()],
            "enabled": self.browser_memory_enabled_row.get_active(),
            "limit_deci_gb": round(self.browser_memory_adjustment.get_value() * 10),
        }

    def _browser_memory_value_changed(self, *_args):
        if self.browser_memory_group is None:
            return
        self.browser_memory_save_button.set_sensitive(
            self._browser_memory_selected() != self._browser_saved
        )

    def _refresh_browser_memory_status(self):
        if self.browser_memory_group is None:
            return GLib.SOURCE_REMOVE
        status = read_browser_status()
        browser_id = status.get("browser", self._browser_saved["browser"]) if status else self._browser_saved["browser"]
        browser_name = _("Firefox") if browser_id == "firefox" else _("Brave")
        if status is None:
            summary = _("Save to start the per-user browser limiter.")
        elif status.get("error"):
            summary = str(status["error"])
        elif not status["enabled"]:
            summary = _("Disabled; Tab Companion-managed browser limits are cleared.")
        elif status["browser_sessions"] == 0:
            summary = _("Enabled · waiting for native {browser}; Flatpak is not managed.").format(browser=browser_name)
        else:
            summary = _("{applied} of {running} native {browser} session(s) capped at {limit:.1f} GB.").format(
                applied=status["applied_sessions"],
                running=status["browser_sessions"],
                browser=browser_name,
                limit=status["limit_deci_gb"] / 10,
            )
        self.browser_memory_status_row.set_subtitle(summary)
        self._browser_memory_value_changed()
        return GLib.SOURCE_CONTINUE

    def _save_browser_memory_settings(self, *_args):
        selected = self._browser_memory_selected()
        if selected == self._browser_saved:
            return
        self.browser_memory_save_button.set_sensitive(False)
        self.browser_memory_status_row.set_subtitle(_("Saving browser memory limit…"))
        try:
            self._browser_memory_process = Gio.Subprocess.new(
                [BROWSER_MEMORY_HELPER, json.dumps(selected, separators=(",", ":"))],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
            )
            self._browser_memory_process.communicate_utf8_async(
                None, None, self._browser_memory_save_finished, selected
            )
        except (GLib.Error, OSError):
            self.browser_memory_status_row.set_subtitle(
                _("Could not start browser memory limiter; previous setting remains.")
            )
            self.browser_memory_save_button.set_sensitive(True)

    def _browser_memory_save_finished(self, process, result, selected):
        try:
            success, stdout, stderr = process.communicate_utf8_finish(result)
        except (GLib.Error, TypeError, ValueError):
            success, stdout, stderr = False, "", ""
        if not success or not process.get_successful():
            self.browser_memory_status_row.set_subtitle(
                (stderr or _("Could not save browser memory limit; previous setting remains.")).strip()
            )
            self._browser_memory_value_changed()
            return
        self._browser_saved = selected
        self.browser_memory_save_button.set_sensitive(False)
        self._refresh_browser_memory_status()

    def _save_thermal_settings(self, *_args):
        selected = self._thermal_selected()
        if selected == self._thermal_saved:
            return
        self.thermal_save_button.set_sensitive(False)
        self.thermal_status_row.set_subtitle(_("Authorizing and saving thermal limit…"))
        try:
            self._thermal_process = Gio.Subprocess.new(
                admin_command(
                    "thermal-limit", THERMAL_HELPER,
                    json.dumps(selected, separators=(",", ":")),
                ),
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
            )
            self._thermal_process.communicate_utf8_async(
                None, None, self._thermal_save_finished, selected
            )
        except (GLib.Error, OSError):
            self.thermal_status_row.set_subtitle(_("Could not save thermal limit; previous setting remains."))
            self.thermal_save_button.set_sensitive(True)

    def _thermal_save_finished(self, process, result, selected):
        try:
            success, _stdout, stderr = process.communicate_utf8_finish(result)
        except (GLib.Error, TypeError, ValueError):
            success, stderr = False, ""
        if not success or not process.get_successful():
            self.thermal_status_row.set_subtitle(
                (stderr or _("Could not save thermal limit; previous setting remains.")).strip()
            )
            self._thermal_value_changed()
            return
        self._thermal_saved = selected
        self.thermal_save_button.set_sensitive(False)
        self._refresh_thermal_status()

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
            self._refresh_swap_priorities()
            return
        self._active = profile
        self.mode_row.set_selected(PROFILE_INDEX[profile])
        self.mode_row.set_sensitive(True)
        self._load_tuning_profile(profile)
        self.status_row.set_subtitle(self._profile_status(profile))
        self._updating = False
        self._refresh_zram()
        self._refresh_swap_priorities()

    def _profile_status(self, profile):
        return f"{dict(PROFILES)[profile]} · {cpu_governor_summary()}"

    def _refresh_swap_priorities(self):
        if not hasattr(self, "swap_group"):
            return
        for row in self._swap_rows.values():
            self.swap_group.remove(row["row"])
        self._swap_rows.clear()
        try:
            from .swap_priority import active_swaps

            swaps = active_swaps()
        except (OSError, ValueError, subprocess.SubprocessError):
            self.swap_status_row.set_subtitle(_("Could not read active swap devices."))
            self.swap_save_button.set_sensitive(False)
            return
        if not swaps:
            self.swap_status_row.set_subtitle(_("No active swap devices found."))
            self.swap_save_button.set_sensitive(False)
            return
        self.swap_status_row.set_subtitle(_("Changes apply next time swap is activated."))
        for item in swaps:
            source = item["source"]
            current = item["priority"]
            # -1 is the UI's "Automatic" value. Kernel-reported negative
            # priorities all mean no explicit priority was configured.
            selected = current if current >= 0 else -1
            row = Adw.ActionRow(title=source)
            mountpoint = item["mountpoint"] or _("Not mounted")
            row.set_subtitle(
                _("Type: {type} · Mountpoint: {mountpoint} · Used/size: {used:.2f} / {size:.2f} GB · Active priority: {priority}").format(
                    type=item["type"], mountpoint=mountpoint,
                    used=item["used"] / 1_000_000_000,
                    size=item["size"] / 1_000_000_000,
                    priority=(str(current) if current >= 0 else _("Automatic ({value})").format(value=current)),
                )
            )
            adjustment = Gtk.Adjustment.new(selected, -1, 32767, 1, 100, 0)
            spin = Gtk.SpinButton.new(adjustment, 1, 0)
            spin.set_numeric(True)
            spin.set_width_chars(6)
            # ActionRow suffixes otherwise stretch vertically to the row's
            # full height (especially with a wrapped two-line subtitle).
            spin.set_valign(Gtk.Align.CENTER)
            spin.set_vexpand(False)
            spin.set_tooltip_text(_("Use -1 for automatic priority, or 0–32767; higher values are preferred."))
            row.add_suffix(spin)
            self.swap_group.add(row)
            self._swap_rows[source] = {
                "row": row, "spin": spin, "saved": selected, "device": item,
            }
            spin.connect("value-changed", self._swap_value_changed)
        self.swap_save_button.set_sensitive(False)

    def _swap_value_changed(self, _spin):
        if not hasattr(self, "swap_save_button"):
            return
        self.swap_save_button.set_sensitive(any(
            round(data["spin"].get_value()) != data["saved"]
            for data in self._swap_rows.values()
        ))

    def _save_swap_priorities(self, *_args):
        if not self._swap_rows:
            return
        import json

        payload = [
            {"source": source, "priority": round(data["spin"].get_value())}
            for source, data in self._swap_rows.items()
        ]
        self.swap_save_button.set_sensitive(False)
        self.swap_status_row.set_subtitle(_("Authorizing and saving priorities for next activation…"))
        try:
            self._swap_process = Gio.Subprocess.new(
                admin_command("swap-priority", SWAP_HELPER, json.dumps(payload, separators=(",", ":"))),
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
            )
            self._swap_process.communicate_utf8_async(None, None, self._swap_finished, payload)
        except (GLib.Error, OSError):
            self.swap_status_row.set_subtitle(_("Could not save swap priorities; existing settings remain."))

    def _swap_finished(self, process, result, payload):
        try:
            success, stdout, stderr = process.communicate_utf8_finish(result)
        except (GLib.Error, TypeError, ValueError):
            success, stdout, stderr = False, "", ""
        if not success or not process.get_successful():
            self.swap_status_row.set_subtitle(
                (stderr or _("Could not save swap priorities; existing settings remain.")).strip()
            )
            self._swap_value_changed(None)
            return
        for item in payload:
            if item["source"] in self._swap_rows:
                self._swap_rows[item["source"]]["saved"] = item["priority"]
        self.swap_save_button.set_sensitive(False)
        self.swap_status_row.set_subtitle(
            _("Saved for next activation. Reboot to apply; active swap was left untouched.")
        )

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
            command_success, _stdout, _stderr = process.communicate_utf8_finish(result)
        except (GLib.Error, TypeError, ValueError):
            self._zram_failed()
            return
        if not command_success or not process.get_successful():
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
        if (self.tuning_group is not None and self._active in PROFILE_INDEX
                and self._tuning_selected() != self._tuning_saved):
            self._updating = True
            row.set_selected(PROFILE_INDEX[self._active])
            self._updating = False
            self.tuning_status_row.set_subtitle(_("Apply profile changes before switching modes."))
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
        self._load_tuning_profile(profile_id)
        self.status_row.set_subtitle(self._profile_status(profile_id))
        # TuneD applies the selected profile asynchronously. Read back both
        # the PPD selection and CPUFreq governor rather than implying that a
        # successful D-Bus write proves the hardware policy changed.
        GLib.timeout_add(750, self._refresh_profile_status, profile_id)

    def _refresh_profile_status(self, requested_profile):
        try:
            actual_profile = self._read_active()
        except (OSError, subprocess.SubprocessError, ValueError):
            actual_profile = requested_profile
        self._active = actual_profile
        self._updating = True
        self.mode_row.set_selected(PROFILE_INDEX[actual_profile])
        self._updating = False
        self.mode_row.set_sensitive(True)
        self._load_tuning_profile(actual_profile)
        self.status_row.set_subtitle(self._profile_status(actual_profile))
        return GLib.SOURCE_REMOVE

    def _switch_failed(self):
        self._updating = True
        if self._active in PROFILE_INDEX:
            self.mode_row.set_selected(PROFILE_INDEX[self._active])
        self._updating = False
        self.mode_row.set_sensitive(True)
        self._load_tuning_profile(self._active or "balanced")
        self.status_row.set_subtitle(_("Change cancelled or unavailable; the previous profile remains active."))
