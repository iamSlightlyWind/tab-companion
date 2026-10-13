package com.tabcompanion.x810.bootswitcher

import android.content.Context
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Serialized with discovery/writes; keeps OTA images accessible from Fedora. */
object BootMaintenance {
    private var context: Context? = null
    private fun quote(value: String) = "'" + value.replace("'", "'\\''") + "'"

    fun initialize(ctx: Context) {
        context = ctx.applicationContext
    }

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
        return captureAndroidIfRunning(ctx)
    }

    private fun captureAndroidIfRunning(ctx: Context): Boolean {
        if (!BootSets.mountLinuxRoot()) {
            record(ctx, "Android: shared Linuxroot boot-set storage is unavailable; switch cancelled.")
            return false
        }

        // If Fedora is already staged, the live boot partitions describe the
        // next boot, not the Android session currently running. Never replace
        // Android's saved image set with those staged Fedora images.
        val sets = BootSets.discover()
        val live = BootSets.liveHashes()
        val next = BootSets.identify(live, sets)
        if (next != null && BootSets.isLinux(next)) {
            record(ctx, "Android: Fedora is already staged; kept the Android snapshot unchanged.")
            return true
        }

        // Save the exact boot partition contents currently in use by Android.
        // Do not inspect kernel signatures or compare against stale hashes:
        // this intentionally preserves Magisk/KernelSU-patched boot images.
        val script = ctx.assets.open("refresh-android.sh").bufferedReader().use { it.readText() }
        val result = Root.run("SET_ID='android'", "SYSTEM_NAME=${quote(BootSets.runningSystemName())}", script)
        record(ctx, (if (result.ok) "Android: current boot files saved to shared Linuxroot storage.\n"
                     else "Android: current boot snapshot failed (${result.code}).\n") + result.output.takeLast(2000))
        if (!result.ok) return false
        Prefs(ctx).clearTileCache()
        return true
    }

    fun refresh(
        ctx: Context,
        sets: List<BootSets.BootSet>,
        live: Map<String, String>,
        allowBootstrap: Boolean = false,
    ): Boolean {
        initialize(ctx)
        if (sets.any { it.id.equals("android", ignoreCase = true) }) return true
        if (!allowBootstrap) return true
        // First-run seed only: make an Android snapshot so both systems are
        // selectable. Never mislabel Fedora's already-staged set as Android.
        if (BootSets.identify(live, sets)?.let(BootSets::isLinux) == true) return true
        return captureAndroidIfRunning(ctx)
    }

}
