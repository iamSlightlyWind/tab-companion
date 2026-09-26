// SPDX-License-Identifier: MIT
// Adds one authenticated “Restart into Android” action to GNOME's power menu.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const STATUS_HELPER = '/usr/local/libexec/tab-companion-boot-status';
const SWITCH_HELPER = '/usr/local/libexec/tab-companion-boot-switch';
const LEGACY_TITLES = new Set([
    'Dual boot', 'Arranque dual', 'Double démarrage', 'Dual-Boot',
    'Avvio doppio', 'Arranque duplo', 'Restart into Android',
]);
const CATALOGUE = {
    'Restart into Android': {
        es: 'Reiniciar en Android', fr: 'Redémarrer sur Android',
        de: 'Zu Android neu starten', it: 'Riavvia in Android',
        pt: 'Reiniciar no Android',
    },
    'Authentication was cancelled or the boot switch failed.': {
        es: 'Se canceló la autenticación o falló el cambio de arranque.',
        fr: 'Authentification annulée ou échec du changement de démarrage.',
        de: 'Authentifizierung abgebrochen oder Startwechsel fehlgeschlagen.',
        it: 'Autenticazione annullata o cambio di avvio non riuscito.',
        pt: 'Autenticação cancelada ou falha ao mudar o arranque.',
    },
    'Writing and verifying Android boot partitions…': {
        es: 'Escribiendo y verificando las particiones de arranque de Android…',
        fr: 'Écriture et vérification des partitions de démarrage Android…',
        de: 'Android-Bootpartitionen werden geschrieben und geprüft…',
        it: 'Scrittura e verifica delle partizioni di avvio Android…',
        pt: 'A escrever e verificar as partições de arranque do Android…',
    },
};
const LANG = (GLib.getenv('LANGUAGE') || GLib.getenv('LC_ALL') ||
    GLib.getenv('LC_MESSAGES') || GLib.getenv('LANG') || 'en')
    .split(':')[0].split('_')[0].split('-')[0].toLowerCase();
function _(message) {
    return CATALOGUE[message]?.[LANG] ?? message;
}

Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async');

async function runCommand(argv) {
    const proc = Gio.Subprocess.new(argv,
        Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
    const [stdout, stderr] = await proc.communicate_utf8_async(null, null);
    return {ok: proc.get_successful(), stdout: stdout ?? '', stderr: stderr ?? ''};
}

function isLegacyTile(item) {
    return item?._tabCompanionDualBoot === true || LEGACY_TITLES.has(item?.title);
}

function removeLegacyTiles(quickSettings) {
    const menu = quickSettings?.menu;
    const indicators = quickSettings?._indicators?.get_children?.() ?? [];
    const staleIndicators = indicators.filter(indicator =>
        (indicator.quickSettingsItems ?? []).some(isLegacyTile));
    const staleItems = new Set();
    for (const indicator of staleIndicators) {
        for (const item of indicator.quickSettingsItems ?? []) {
            if (isLegacyTile(item))
                staleItems.add(item);
        }
    }
    for (const item of menu?._grid?.get_children?.() ?? []) {
        if (isLegacyTile(item))
            staleItems.add(item);
    }
    for (const item of staleItems) {
        try {
            if (item?.menu?.actor?.get_parent() === menu?._overlay)
                menu._overlay.remove_child(item.menu.actor);
            if (item?.get_parent())
                item.get_parent().remove_child(item);
            item.destroy();
        } catch (error) {
            logError(error, 'dualboot: could not remove a stale Quick Settings tile');
        }
    }
    for (const indicator of staleIndicators) {
        try {
            if (indicator.get_parent())
                indicator.get_parent().remove_child(indicator);
            indicator.destroy();
        } catch (error) {
            logError(error, 'dualboot: could not remove a stale indicator');
        }
    }
}

export default class DualBootExtension extends Extension {
    enable() {
        this._quickSettings = Main.panel.statusArea.quickSettings;
        removeLegacyTiles(this._quickSettings);
        this._androidAvailable = false;
        this._busy = false;
        this._powerItem = null;
        this._powerMenu = null;
        this._attachAttempts = 0;
        this._attachPowerItem();
        this._refresh();
    }

    _attachPowerItem() {
        const menu = this._quickSettings?._system?.menu;
        if (menu?.addAction) {
            this._powerMenu = menu;
            this._powerItem = menu.addAction(_('Restart into Android'),
                () => this._restartAndroid());
            this._powerItem.visible = this._androidAvailable;
            return;
        }
        // Shell constructs its built-in indicators asynchronously during boot.
        if (++this._attachAttempts >= 40) {
            log('dualboot: GNOME power menu hook unavailable');
            return;
        }
        this._powerMenuPoll = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 150, () => {
            this._powerMenuPoll = 0;
            if (!this._quickSettings)
                return GLib.SOURCE_REMOVE;
            this._attachPowerItem();
            return GLib.SOURCE_REMOVE;
        });
    }

    async _refresh() {
        try {
            const {ok, stdout} = await runCommand(['pkexec', STATUS_HELPER]);
            if (!ok)
                throw new Error('Boot status helper failed');
            const status = JSON.parse(stdout);
            const android = (status.sets ?? []).find(set => set.id === 'android' && set.complete);
            this._androidAvailable = Boolean(android && status.current !== 'android');
            if (this._powerItem)
                this._powerItem.visible = this._androidAvailable;
        } catch (error) {
            this._androidAvailable = false;
            if (this._powerItem)
                this._powerItem.visible = false;
            logError(error, 'dualboot: could not read boot status');
        }
    }

    async _restartAndroid() {
        if (!this._androidAvailable || this._busy)
            return;
        this._busy = true;
        if (this._powerItem)
            this._powerItem.reactive = false;
        try {
            Main.notify(_('Restart into Android'), _('Writing and verifying Android boot partitions…'));
            const {ok, stderr} = await runCommand(['pkexec', SWITCH_HELPER, 'android', '--reboot']);
            if (!ok) {
                const message = stderr.trim() || _('Authentication was cancelled or the boot switch failed.');
                Main.notifyError(_('Restart into Android'), message);
            }
        } catch (error) {
            logError(error, 'dualboot: Android restart failed');
            Main.notifyError(_('Restart into Android'), _('Authentication was cancelled or the boot switch failed.'));
        } finally {
            this._busy = false;
            if (this._powerItem)
                this._powerItem.reactive = true;
        }
    }

    disable() {
        if (this._powerMenuPoll) {
            GLib.Source.remove(this._powerMenuPoll);
            this._powerMenuPoll = 0;
        }
        removeLegacyTiles(this._quickSettings);
        if (this._powerItem) {
            try {
                // Destroying the PopupMenu item removes it from its parent menu.
                this._powerItem.destroy();
            } catch (error) {
                logError(error, 'dualboot: could not remove power-menu action');
            }
            this._powerItem = null;
        }
        this._powerMenu = null;
        this._quickSettings = null;
    }
}
