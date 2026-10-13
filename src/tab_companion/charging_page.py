# SPDX-License-Identifier: MIT
"""Battery charging settings and live charging telemetry for X810."""

import os

from gi.repository import Adw, Gio, GLib, Gtk

from .admin_auth import command as admin_command
from .charge_limit import (
    DEFAULT_LIMIT_PERCENT,
    HELPER as CHARGE_LIMIT_HELPER,
    MAX_LIMIT_PERCENT,
    MIN_LIMIT_PERCENT,
    read_status as read_charge_limit_status,
)
from .charging_status import read_charging_status
from .fast_charge import (
    HELPER as FAST_CHARGE_HELPER,
    helper_command as fast_charge_command,
    read_enabled as read_fast_charge_enabled,
)
from .i18n import _


class ChargingPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__(title=_("Charging"), icon_name="battery-level-90-symbolic")
        telemetry = Adw.PreferencesGroup(
            title=_("Charging status"),
            description=_("Battery current and power are fuel-gauge readings; they are not USB input draw or charger wattage."),
        )
        self.technology_row = Adw.ActionRow(title=_("Charging technology"))
        self.battery_current_row = Adw.ActionRow(title=_("Battery current"))
        self.battery_voltage_row = Adw.ActionRow(title=_("Battery voltage"))
        self.battery_power_row = Adw.ActionRow(title=_("Battery power"))
        self.contract_voltage_row = Adw.ActionRow(title=_("USB-PD contract voltage"))
        for row in (self.technology_row, self.battery_current_row, self.battery_voltage_row,
                    self.battery_power_row, self.contract_voltage_row):
            telemetry.add(row)
        self.add(telemetry)
        self._refresh_charging_status()
        self._charging_timer = GLib.timeout_add_seconds(2, self._refresh_charging_status)

        self.charge_limit_group = None
        self._charge_limit_saved = None
        if os.path.isfile(CHARGE_LIMIT_HELPER):
            status = read_charge_limit_status()
            initial = (
                status["end_percent"] if status and status["supported"]
                else DEFAULT_LIMIT_PERCENT
            )
            self._charge_limit_saved = initial
            self.charge_limit_group = Adw.PreferencesGroup(
                title=_("Battery charge limit"),
                description=_(
                    "Stop charging at the selected level and resume after the battery falls by 10 percentage points. "
                    "100% disables the limit. Kernel thermal and battery protections remain active."
                ),
            )
            self.charge_limit_row = Adw.ActionRow(title=_("Maximum charge"))
            self.charge_limit_adjustment = Gtk.Adjustment.new(
                initial, MIN_LIMIT_PERCENT, MAX_LIMIT_PERCENT, 1, 5, 0
            )
            self.charge_limit_adjustment.connect("value-changed", self._charge_limit_value_changed)
            self.charge_limit_scale = Gtk.Scale.new(
                Gtk.Orientation.HORIZONTAL, self.charge_limit_adjustment
            )
            self.charge_limit_scale.set_draw_value(False)
            self.charge_limit_scale.set_digits(0)
            self.charge_limit_scale.set_hexpand(True)
            self.charge_limit_scale.set_valign(Gtk.Align.CENTER)
            self.charge_limit_scale.set_vexpand(False)
            self.charge_limit_editor = Gtk.SpinButton.new(self.charge_limit_adjustment, 1, 0)
            self.charge_limit_editor.set_numeric(True)
            self.charge_limit_editor.set_width_chars(3)
            self.charge_limit_editor.set_valign(Gtk.Align.CENTER)
            self.charge_limit_editor.set_vexpand(False)
            self.charge_limit_units = Gtk.Label(label=_("%"))
            charge_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            charge_controls.set_hexpand(True)
            charge_controls.set_valign(Gtk.Align.CENTER)
            charge_controls.set_vexpand(False)
            charge_controls.append(self.charge_limit_scale)
            charge_controls.append(self.charge_limit_editor)
            charge_controls.append(self.charge_limit_units)
            self.charge_limit_row.add_suffix(charge_controls)
            self.charge_limit_group.add(self.charge_limit_row)
            self.charge_limit_status_row = Adw.ActionRow(title=_("Charge limit status"))
            self.charge_limit_apply_button = Gtk.Button(label=_("Apply"), valign=Gtk.Align.CENTER)
            self.charge_limit_apply_button.connect("clicked", self._apply_charge_limit)
            self.charge_limit_status_row.add_suffix(self.charge_limit_apply_button)
            self.charge_limit_group.add(self.charge_limit_status_row)
            self.add(self.charge_limit_group)
            self._refresh_charge_limit_status()
            self._charge_limit_timer = GLib.timeout_add_seconds(5, self._refresh_charge_limit_status)

        # This is the existing kernel policy toggle; the UI makes no manual PD requests.
        self.fast_charge_group = None
        self._fast_charge_updating = False
        self._fast_charge_pending = False
        if os.path.isfile(FAST_CHARGE_HELPER):
            self.fast_charge_group = Adw.PreferencesGroup(
                title=_("Fast charging and USB-PD PPS"),
                description=_(
                    "Allows the X810 kernel's normal fast-charge path. PPS is negotiated only "
                    "when the charger and cable support it and battery, temperature, and "
                    "charge-limit policy allow it. This does not send manual PD requests or "
                    "override the charge limit; the default is off."
                ),
            )
            self.fast_charge_switch = Adw.SwitchRow(
                title=_("Allow kernel-managed fast charging"),
                subtitle=_("Enabling this does not force a PPS contract."),
            )
            enabled = read_fast_charge_enabled()
            self._fast_charge_updating = True
            self.fast_charge_switch.set_active(enabled is True)
            self._fast_charge_updating = False
            self.fast_charge_switch.set_sensitive(enabled is not None)
            self.fast_charge_switch.connect("notify::active", self._fast_charge_value_changed)
            self.fast_charge_group.add(self.fast_charge_switch)
            self.fast_charge_status_row = Adw.ActionRow(title=_("Fast-charge policy"))
            self.fast_charge_group.add(self.fast_charge_status_row)
            self.add(self.fast_charge_group)
            self._refresh_fast_charge_status()
            self._fast_charge_timer = GLib.timeout_add_seconds(3, self._refresh_fast_charge_status)

    def _refresh_charging_status(self):
        status = read_charging_status()
        self.technology_row.set_subtitle(status["technology"])
        def formatted(value, unit, digits=2):
            return _("Unavailable") if value is None else f"{value:.{digits}f} {unit}"
        current = status["battery_current_a"]
        if current is not None:
            self.battery_current_row.set_subtitle(
                _("{value} A into battery").format(value=f"{current:+.2f}")
                if current >= 0 else _("{value} A (battery discharging)").format(value=f"{current:.2f}")
            )
        else:
            self.battery_current_row.set_subtitle(_("Unavailable"))
        self.battery_voltage_row.set_subtitle(formatted(status["battery_voltage_v"], "V"))
        power = status["battery_power_w"]
        if power is None:
            self.battery_power_row.set_subtitle(_("Unavailable"))
        elif power < 0:
            self.battery_power_row.set_subtitle(
                _("{value:.2f} W · battery supplying the system").format(value=abs(power))
            )
        else:
            self.battery_power_row.set_subtitle(
                _("{value:.2f} W into battery").format(value=power)
            )
        self.contract_voltage_row.set_subtitle(formatted(status["contract_voltage_v"], "V"))
        return GLib.SOURCE_CONTINUE

    def _refresh_charge_limit_status(self):
        if self.charge_limit_group is None:
            return GLib.SOURCE_REMOVE
        status = read_charge_limit_status()
        if status is None:
            self.charge_limit_status_row.set_subtitle(
                _("Charge-limit support is unavailable; install the X810 kernel/support update.")
            )
            self.charge_limit_scale.set_sensitive(False)
            self.charge_limit_editor.set_sensitive(False)
            self.charge_limit_apply_button.set_sensitive(False)
            return GLib.SOURCE_CONTINUE
        if not status["supported"]:
            self.charge_limit_status_row.set_subtitle(
                _("This kernel does not expose charge-limit controls.")
            )
            self.charge_limit_scale.set_sensitive(False)
            self.charge_limit_editor.set_sensitive(False)
            self.charge_limit_apply_button.set_sensitive(False)
            return GLib.SOURCE_CONTINUE
        end = status["end_percent"]
        if self._charge_limit_saved == getattr(self, "_charge_limit_applied", None):
            self._charge_limit_saved = end
        self.charge_limit_status_row.set_subtitle(
            _("Active: {end}% · resume at {start}%").format(
                end=end, start=status["start_percent"]
            ) if end < MAX_LIMIT_PERCENT else _("Unlimited · charging stops at 100% normally")
        )
        self.charge_limit_scale.set_sensitive(True)
        self.charge_limit_editor.set_sensitive(True)
        self._charge_limit_value_changed()
        return GLib.SOURCE_CONTINUE

    def _charge_limit_value_changed(self, *_args):
        if self.charge_limit_group is None:
            return
        selected = round(self.charge_limit_adjustment.get_value())
        self.charge_limit_apply_button.set_sensitive(
            selected != self._charge_limit_saved
        )

    def _apply_charge_limit(self, *_args):
        selected = round(self.charge_limit_adjustment.get_value())
        if selected == self._charge_limit_saved:
            return
        self.charge_limit_apply_button.set_sensitive(False)
        self.charge_limit_status_row.set_subtitle(_("Authorizing and applying charge limit…"))
        try:
            self._charge_limit_process = Gio.Subprocess.new(
                admin_command("charge-limit", CHARGE_LIMIT_HELPER, "set", str(selected)),
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
            )
            self._charge_limit_process.communicate_utf8_async(
                None, None, self._charge_limit_finished, selected
            )
        except (GLib.Error, OSError):
            self.charge_limit_status_row.set_subtitle(
                _("Could not apply charge limit; previous setting remains.")
            )
            self.charge_limit_apply_button.set_sensitive(True)

    def _charge_limit_finished(self, process, result, selected):
        try:
            success, _stdout, stderr = process.communicate_utf8_finish(result)
        except (GLib.Error, TypeError, ValueError):
            success, stderr = False, ""
        if not success or not process.get_successful():
            self.charge_limit_status_row.set_subtitle(
                (stderr or _("Could not apply charge limit; previous setting remains.")).strip()
            )
            self._charge_limit_value_changed()
            return
        self._charge_limit_saved = selected
        self._charge_limit_applied = selected
        self.charge_limit_apply_button.set_sensitive(False)
        self._refresh_charge_limit_status()

    def _refresh_fast_charge_status(self):
        if self.fast_charge_group is None:
            return GLib.SOURCE_REMOVE
        enabled = read_fast_charge_enabled()
        status = read_charging_status()
        if enabled is None:
            self.fast_charge_switch.set_sensitive(False)
            summary = _("Fast-charge switch unavailable")
        else:
            self.fast_charge_switch.set_sensitive(not self._fast_charge_pending)
            if not self._fast_charge_pending:
                self._fast_charge_updating = True
                self.fast_charge_switch.set_active(enabled)
                self._fast_charge_updating = False
            summary = _("Enabled · kernel may use PPS when eligible") if enabled else _("Disabled")
        summary += " · " + status["technology"]
        self.fast_charge_status_row.set_subtitle(summary)
        return GLib.SOURCE_CONTINUE

    def _fast_charge_value_changed(self, *_args):
        if self._fast_charge_updating or self._fast_charge_pending:
            return
        selected = self.fast_charge_switch.get_active()
        self._fast_charge_pending = True
        self.fast_charge_switch.set_sensitive(False)
        self.fast_charge_status_row.set_subtitle(_("Applying the kernel fast-charge setting…"))
        try:
            self._fast_charge_process = Gio.Subprocess.new(
                fast_charge_command(selected),
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
            )
            self._fast_charge_process.communicate_utf8_async(
                None, None, self._fast_charge_finished, selected
            )
        except (GLib.Error, OSError):
            self._fast_charge_pending = False
            self._refresh_fast_charge_status()
            self.fast_charge_status_row.set_subtitle(
                _("Could not set fast charging; the previous setting remains.")
            )

    def _fast_charge_finished(self, process, result, selected):
        try:
            success, _stdout, stderr = process.communicate_utf8_finish(result)
        except (GLib.Error, TypeError, ValueError):
            success, stderr = False, ""
        self._fast_charge_pending = False
        actual = read_fast_charge_enabled()
        if not success or not process.get_successful() or actual is not selected:
            self._fast_charge_updating = True
            self.fast_charge_switch.set_active(actual is True)
            self._fast_charge_updating = False
            self.fast_charge_switch.set_sensitive(actual is not None)
            self.fast_charge_status_row.set_subtitle(
                (stderr or _("Could not set fast charging; the previous setting remains.")).strip()
            )
            return
        self._refresh_fast_charge_status()


def available() -> bool:
    """Show Charging on devices with either supported X810 control."""
    return (
        os.path.isfile(CHARGE_LIMIT_HELPER)
        or os.path.isfile(FAST_CHARGE_HELPER)
        or os.path.isdir("/sys/class/power_supply/sm5714-battery")
    )
