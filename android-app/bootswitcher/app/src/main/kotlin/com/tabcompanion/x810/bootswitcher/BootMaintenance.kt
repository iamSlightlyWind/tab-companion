package com.tabcompanion.x810.bootswitcher

import android.content.Context
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Serialized with discovery/writes; keeps OTA images accessible from Fedora. */
object BootMaintenance {
    private var context: Context? = null
    private fun quote(value: String) = "'" + value.replace("'", "'\\''") + "'"

    fun log(context: Context): String = context.getSharedPreferences("maintenance", 0)
        .getString("log", "") ?: ""

    private fun record(context: Context, message: String) {
        val time = SimpleDateFormat("MM-dd HH:mm:ss", Locale.ROOT).format(Date())
        context.getSharedPreferences("maintenance", 0).edit()
            .putString("log", (log(context) + "\n$time $message").takeLast(6000)).commit()
    }

    fun recoverInterrupted() {
        if (!BootSets.mountLinuxRoot()) return
        Root.run("""
            m=${BootSets.LINUX_MOUNT}
            b=${BootSets.LINUX_MOUNT}/var/lib/x810-boot-backups
            for previous in "${'$'}b"/*.previous; do
                [ -d "${'$'}previous" ] || continue
                id=${'$'}{previous##*/}; id=${'$'}{id%.previous}
                case "${'$'}id" in ''|*[!a-zA-Z0-9_-]*) continue;; esac
                target=${BootSets.LINUX_SETS}/${'$'}id
                [ ! -e "${'$'}target" ] || continue
                if grep " ${'$'}m " /proc/mounts | grep -q noload; then exit 1; fi
                mount -o remount,rw "${'$'}m"
                trap 'sync; mount -o remount,ro "${'$'}m"' EXIT
                mv "${'$'}previous" "${'$'}target"
                sync
            done
        """.trimIndent())
    }

    fun beforeSwitch(): Boolean {
        val ctx = context ?: return false
        return refresh(ctx, BootSets.discover(), BootSets.liveHashes(), allowBootstrap = true)
    }

    fun refresh(
        ctx: Context,
        sets: List<BootSets.BootSet>,
        live: Map<String, String>,
        allowBootstrap: Boolean = false,
    ): Boolean {
        context = ctx.applicationContext
        val androidSets = sets.filterNot { BootSets.isLinux(it) }
        val rootedAndroid = androidSets.filter { it.dir == "${BootSets.LINUX_SETS}/${it.id}" }
        if (rootedAndroid.isEmpty()) {
            if (!allowBootstrap) {
                record(ctx, "Android: no Linuxroot Android backup yet; open Dualboot to capture it.")
                return false
            }
            if (!BootSets.canBootstrapAndroid(sets, live)) {
                record(ctx, "Android: initial backup blocked; X810 identity, hashes or set layout is ambiguous.")
                return false
            }
            return bootstrapAndroid(ctx, live)
        }

        val android = rootedAndroid.singleOrNull()
        if (android == null || androidSets.any { it.id != android.id } ||
            !android.id.matches(Regex("[a-zA-Z0-9_-]+")) ||
            BootSets.PARTITIONS.any { live[it.device]?.matches(Regex("[0-9a-f]{64}")) != true }) {
            record(ctx, "Android: cannot identify one verified Android set or read all X810 partitions.")
            return false
        }
        val decision = BootSets.backupDecision(sets, live)
        if (decision == BootSets.BackupDecision.AMBIGUOUS) {
            record(ctx, "Android: cannot identify a unique backup or read all X810 partitions.")
            return false
        }
        val next = BootSets.identify(live, sets)
        if (decision == BootSets.BackupDecision.LINUX_STAGED) {
            record(ctx, "Android: Linux is staged; Android backup preserved.")
            return true
        }
        if (decision == BootSets.BackupDecision.MIXED) {
            record(ctx, "Android: mixed boot partitions; backup preserved, switching blocked.")
            return false
        }
        val name = BootSets.runningSystemName()
        val changed = next?.id != android.id
        if (!changed && android.label == name) {
            record(ctx, "Android: all four backup hashes match; no copy needed.")
            return true
        }
        val expected = BootSets.PARTITIONS.joinToString("\n") {
            "${live.getValue(it.device)}  ${it.name}.img"
        }
        val script = ctx.assets.open("refresh-android.sh").bufferedReader().use { it.readText() }
        val result = Root.run("SET_ID=${quote(android.id)}", "SYSTEM_NAME=${quote(name)}",
            "EXPECTED=${quote(expected)}", "CHANGED=${if (changed) 1 else 0}", script)
        record(ctx, (if (result.ok) "" else "Android: backup refresh failed (${result.code}).\n") +
            result.output.takeLast(2000))
        if (!result.ok) return false
        Prefs(ctx).clearTileCache()
        return true
    }

    /** Read-only from the boot partitions; all new files land on Linuxroot. */
    private fun bootstrapAndroid(ctx: Context, live: Map<String, String>): Boolean {
        if (!BootSets.mountLinuxRoot()) {
            record(ctx, "Android: initial backup blocked; linuxroot is unavailable.")
            return false
        }
        val expected = BootSets.PARTITIONS.joinToString("\n") {
            "${live.getValue(it.device)}  ${it.name}.img"
        }
        val script = ctx.assets.open("refresh-android.sh").bufferedReader().use { it.readText() }
        record(ctx, "Android: capturing the running X810 image set; no boot partition is written.")
        val name = BootSets.runningSystemName()
        val result = Root.run("SET_ID='android'", "SYSTEM_NAME=${quote(name)}",
            "EXPECTED=${quote(expected)}", "CHANGED=1", script)
        record(ctx, (if (result.ok) "" else "Android: initial snapshot failed (${result.code}).\n") +
            result.output.takeLast(2000))
        if (!result.ok) return false

        val saved = BootSets.discover().singleOrNull {
            it.id == "android" && it.dir == "${BootSets.LINUX_SETS}/android" && it.complete
        }
        if (saved == null || BootSets.identify(live, listOf(saved))?.id != "android") {
            record(ctx, "Android: snapshot completed but verification did not match live images.")
            return false
        }
        Prefs(ctx).clearTileCache()
        return true
    }
}
