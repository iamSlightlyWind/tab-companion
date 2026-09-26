package com.tabcompanion.x810.bootswitcher

/**
 * Switching systems on this tablet means replacing four partitions.
 *
 * The sets are discovered rather than hardcoded: every directory under
 * [ROOT_DIR] that holds the four images is a system the tablet can boot, so
 * adding LineageOS is dropping a folder in, not editing this file.
 *
 * `vbmeta` is deliberately not in the list.  One UI runs fine with the
 * unsigned flags=2 vbmeta that Fedora needs, so it never has to change — and
 * it must not, because rewriting it invalidates the key Android derives for
 * its metadata-encrypted /data, which costs a full wipe of Android's user data
 * every single time.
 *
 * `recovery` is also deliberately excluded: that independent X810 partition
 * carries TWRP, and One UI's stock-recovery restore behavior is outside this
 * app's scope. The switcher never attempts to preserve or flash recovery.
 *
 * What this app does NOT do is move `super`.  Two Android ROMs — One UI and
 * LineageOS — share that partition, so they cannot both be installed at once;
 * swapping their boot sets alone would boot a kernel against the other ROM's
 * system.  The pairing that works is Fedora against whichever Android is
 * installed.
 */
object BootSets {

    private const val EXPECTED_MODEL = "SM-X810"
    private const val EXPECTED_PRODUCT = "gts9pwifi"
    // Explicit transition allowlist. recovery (TWRP), vbmeta, BL, GPT/PIT,
    // super, userdata, EFS and all other partitions are intentionally absent.
    private val ALLOWED_PARTITIONS = listOf("boot", "init_boot", "vendor_boot", "dtbo")

    /** Sizes are fixed by the partition table; anything else is a wrong file. */
    val PARTITIONS: List<Partition> = listOf(
        Partition("boot", 100_663_296L, "/dev/block/sda21"),
        Partition("init_boot", 8_388_608L, "/dev/block/sda22"),
        Partition("vendor_boot", 100_663_296L, "/dev/block/sda24"),
        Partition("dtbo", 16_777_216L, "/dev/block/sda30"),
    )

    /** Enforce the X810 allowlist and measured geometry before any block write. */
    fun validateWriteTargets(): Root.Result {
        if (PARTITIONS.map { it.name } != ALLOWED_PARTITIONS) {
            return Root.Result(-1, "Boot write allowlist mismatch; refusing all writes.")
        }
        val checks = mutableListOf(
            "[ \"\$(getprop ro.product.model)\" = \"$EXPECTED_MODEL\" ]",
            "[ \"\$(getprop ro.product.device)\" = \"$EXPECTED_PRODUCT\" ]",
        )
        PARTITIONS.forEach { part ->
            checks += "[ -b \"${part.device}\" ]"
            checks += "[ \"\$(readlink -f \"${part.device}\")\" = \"${part.node}\" ]"
            checks += "[ \"\$(blockdev --getsize64 \"${part.device}\")\" = \"${part.bytes}\" ]"
        }
        return Root.run(*checks.toTypedArray())
    }

    data class Partition(val name: String, val bytes: Long, val node: String) {
        val device: String get() = "/dev/block/by-name/$name"
    }

    /** One bootable system: a directory of images plus a name to show. */
    data class BootSet(
        val id: String,
        val label: String,
        val complete: Boolean,
        val hashes: Map<String, String>,
        /** Where its images actually live; may be on the read-only Linux mount. */
        val dir: String,
    ) {
        fun file(part: Partition): String = "$dir/${part.name}.img"
    }

    /**
     * The two places a set can live.
     *
     * The installer seeds the sets inside Fedora's own root filesystem, and
     * that partition is plain ext4 that root can mount from here — the app
     * already does it to read Fedora's name.  So there is nothing to copy:
     * Android reads the same images Fedora uses. BootMaintenance also seeds or
     * refreshes the Android set there after root is granted, with a verified
     * directory transaction.
     *
     * `/sdcard/BootSets` is a manual import/addition location. The owner can
     * copy a complete BootSets folder from microSD into Android's shared
     * storage while Android is running; recovery access to encrypted shared
     * storage is not assumed.
     */
    const val ROOT_DIR = "/sdcard/BootSets"
    const val LINUX_MOUNT = "/mnt/x810-linuxroot"
    const val LINUX_SETS = "$LINUX_MOUNT/var/lib/x810-boot-sets"

    /** Reject support-asset folders and unsafe names from either set root. */
    internal fun isBootSetId(id: String): Boolean =
        id.matches(Regex("[A-Za-z0-9_-]{1,64}")) && !id.equals("recovery", ignoreCase = true)

    /**
     * Mounts Fedora's root read-only, if it is not mounted already.
     *
     * Discovery stays read-only. BootMaintenance briefly remounts it writable
     * only for a verified backup transaction. A plain `ro` mount still replays
     * the journal, which fails on a filesystem left dirty by a hard power-off,
     * so `noload` is the fallback — it skips recovery and reads what is there.
     */
    fun mountLinuxRoot(): Boolean {
        val result = Root.run(
            "[ \"\$(getprop ro.product.model)\" = \"$EXPECTED_MODEL\" ]",
            "[ \"\$(getprop ro.product.device)\" = \"$EXPECTED_PRODUCT\" ]",
            "[ -b /dev/block/by-name/linuxroot ]",
            "[ \"\$(readlink -f /dev/block/by-name/linuxroot)\" = \"/dev/block/sda35\" ]",
            "mkdir -p $LINUX_MOUNT",
            "if grep -q ' $LINUX_MOUNT ' /proc/mounts; then " +
                "source=\$(awk '\$2 == \"$LINUX_MOUNT\" {print \$1; exit}' /proc/mounts); " +
                "[ \"\$(readlink -f \"\$source\")\" = \"/dev/block/sda35\" ]; exit 0; fi",
            "mount -o ro -t ext4 /dev/block/by-name/linuxroot $LINUX_MOUNT 2>/dev/null || " +
                "mount -o ro,noload -t ext4 /dev/block/by-name/linuxroot $LINUX_MOUNT",
        )
        return result.ok
    }

    /**
     * Last-resort names, deliberately without version numbers.
     *
     * A real name comes from `name.txt`, stamped by whichever system is
     * running — see [runningSystemName].  Guessing a version here is how a
     * tablet on One UI 7 ends up being told it runs One UI 8.
     */
    private val KNOWN_LABELS = mapOf(
        "fedora" to "Fedora",
        "oneui" to "Android",
        "lineage" to "LineageOS",
        "lineageos" to "LineageOS",
    )

    /**
     * What the system running right now calls itself.
     *
     * One UI encodes its version as major*10000 + minor*100 + patch, so 80000
     * is 8.0.  LineageOS publishes its own property.  Anything else falls back
     * to the Android release number, which is always there.
     */
    fun runningSystemName(): String {
        fun prop(name: String): String =
            Root.run("getprop $name").output.trim().takeIf { it.isNotEmpty() } ?: ""

        prop("ro.lineage.display.version").takeIf { it.isNotEmpty() }?.let {
            return "LineageOS $it"
        }
        prop("ro.lineage.version").takeIf { it.isNotEmpty() }?.let {
            return "LineageOS $it"
        }

        val release = prop("ro.build.version.release").ifEmpty { "?" }
        val oneui = prop("ro.build.version.oneui").toIntOrNull()
        if (oneui != null && oneui > 0) {
            val major = oneui / 10000
            val minor = (oneui % 10000) / 100
            return if (minor == 0) "One UI $major" else "One UI $major.$minor"
        }
        return "Android $release"
    }

    // -- reading -------------------------------------------------------------

    /**
     * Whether a set is the Linux side.
     *
     * Only used to pick an icon and to know who to ask for a name: this app
     * cannot run on that system, so it is never the one running.
     */
    fun isLinux(set: BootSet): Boolean {
        val id = set.id.lowercase()
        return id.contains("fedora") || id.contains("linux") || id.contains("debian")
    }

    /** sha256 of every boot partition as the tablet holds it right now. */
    fun liveHashes(): Map<String, String> {
        if (!validateWriteTargets().ok) return emptyMap()
        val result = Root.run(*PARTITIONS.map { "sha256sum ${it.device}" }.toTypedArray())
        if (!result.ok) return emptyMap()
        return parseSums(result.output, PARTITIONS.map { it.device })
    }

    /**
     * Every set the tablet can see, from either place.
     *
     * Android shared storage is read first and Fedora's Linuxroot set second,
     * so a verified Linuxroot Android snapshot wins over a stale manual copy
     * with the same id. A manual directory can still add a set when absent
     * from Linuxroot.
     */
    fun discover(): List<BootSet> {
        mountLinuxRoot()

        val where = LinkedHashMap<String, String>()
        for (base in listOf(ROOT_DIR, LINUX_SETS)) {
            Root.run("ls -1 $base 2>/dev/null || true").output.lineSequence()
                .map { it.trim() }
                .filter(::isBootSetId)
                .forEach { id -> where[id] = preferSetBase(where[id], base) }
        }

        return where.keys.sorted().map { id ->
            val dir = "${where[id]}/$id"
            val fallback = KNOWN_LABELS[id.lowercase()] ?: id.replaceFirstChar { it.uppercase() }
            val hashes = hashesOf(dir)
            BootSet(
                id = id,
                label = readLabel(dir) ?: fallback,
                complete = hashes.size == PARTITIONS.size,
                hashes = hashes,
                dir = dir,
            )
        }
    }

    /** An optional `name.txt` lets a set say what it wants to be called. */
    private fun readLabel(dir: String): String? {
        val r = Root.run("cat \"$dir/name.txt\" 2>/dev/null || true")
        val line = r.output.lineSequence().firstOrNull()?.trim()
        return line?.takeIf { it.isNotEmpty() && it.length <= 40 }
    }

    /**
     * sha256 of a stored set, or an empty map when the set is incomplete.
     *
     * A missing or short file has to be caught here, before anything is
     * written: half a set on disk would otherwise become half a set on the
     * partitions, and the tablet would boot no system at all.
     */
    private fun hashesOf(dir: String): Map<String, String> {
        val files = PARTITIONS.map { "$dir/${it.name}.img" }
        val checks = PARTITIONS.map { part ->
            val f = "$dir/${part.name}.img"
            "[ -f \"$f\" ] || exit 1\n" +
                "[ \"\$(stat -c %s \"$f\")\" = \"${part.bytes}\" ] || exit 1"
        }
        val result = Root.run(*(checks + files.map { "sha256sum \"$it\"" }).toTypedArray())
        if (!result.ok) return emptyMap()
        return parseSums(result.output, files)
    }

    /** Which stored set the four live partitions correspond to, if any. */
    fun identify(live: Map<String, String>, sets: List<BootSet>): BootSet? {
        if (live.size != PARTITIONS.size) return null
        return sets.firstOrNull { set ->
            set.complete && PARTITIONS.all { part ->
                val here = live[part.device]
                here != null && here == set.hashes[set.file(part)]
            }
        }
    }

    enum class BackupDecision { UNCHANGED, CHANGED, LINUX_STAGED, MIXED, AMBIGUOUS }

    /**
     * Whether a running Android may be captured as the initial Linuxroot set.
     * A single optional manual `android` directory is tolerated but not
     * trusted; different extra Android sets remain ambiguous and block capture.
     */
    fun canBootstrapAndroid(sets: List<BootSet>, live: Map<String, String>): Boolean {
        val android = sets.filterNot { isLinux(it) }
        val rooted = android.filter { it.dir == "$LINUX_SETS/${it.id}" }
        return rooted.isEmpty() && android.size <= 1 &&
            android.all { it.id.equals("android", ignoreCase = true) } &&
            PARTITIONS.all { part -> live[part.device]?.matches(Regex("[0-9a-f]{64}")) == true }
    }

    /** Prefer Linuxroot's verified snapshot over a duplicate shared-storage id. */
    internal fun preferSetBase(current: String?, candidate: String): String =
        if (candidate == LINUX_SETS || current == null) candidate else current

    fun backupDecision(sets: List<BootSet>, live: Map<String, String>): BackupDecision {
        val android = sets.filterNot { isLinux(it) }.singleOrNull()
            ?: return BackupDecision.AMBIGUOUS
        if (live.size != PARTITIONS.size) return BackupDecision.AMBIGUOUS
        val next = identify(live, sets)
        if (next != null && isLinux(next)) return if (android.complete)
            BackupDecision.LINUX_STAGED else BackupDecision.AMBIGUOUS
        if (sets.filter { isLinux(it) }.any { linux ->
                PARTITIONS.any { part ->
                    live[part.device] == linux.hashes[linux.file(part)] &&
                        live[part.device] != android.hashes[android.file(part)]
                }
            }) return BackupDecision.MIXED
        return if (next?.id == android.id) BackupDecision.UNCHANGED else BackupDecision.CHANGED
    }

    fun runningAndroid(sets: List<BootSet>, name: String): BootSet? {
        val android = sets.filterNot { isLinux(it) }
        return (android.singleOrNull() ?: android.firstOrNull { it.label == name })?.copy(label = name)
    }

    // -- storage -------------------------------------------------------------

    data class Share(val total: Long, val used: Long, val known: Boolean)

    /**
     * How the internal storage is split, mirrored from the Linux side.
     *
     * Android can measure its own userdata because it is mounted here; it
     * cannot see inside linuxroot's ext4, so only that partition's size is
     * honest and the UI says so rather than drawing a guess.
     */
    fun storage(): Map<String, Share> {
        val out = mutableMapOf<String, Share>()

        // Android's df is toybox: it has no -B, and reports 1K blocks.
        val df = Root.run("df -k /data | tail -1").output.trim().split(Regex("\\s+"))
        if (df.size >= 4) {
            val total = df[1].toLongOrNull()
            val used = df[2].toLongOrNull()
            if (total != null && used != null && total > 0) {
                out["android"] = Share(total * 1024, used * 1024, known = true)
            }
        }

        val sectors = Root.run("cat /sys/class/block/sda35/size 2>/dev/null || echo 0")
            .output.trim().toLongOrNull() ?: 0L
        // /sys counts 512-byte sectors whatever the disk's logical size is.
        if (sectors > 0) out["linux"] = Share(sectors * 512, 0, known = false)

        return out
    }

    // -- writing -------------------------------------------------------------

    /**
     * Progress is reported as partitions, not as sentences.
     *
     * The set of partitions is fixed and known before anything starts, so the
     * UI can draw all four up front and light them up one by one instead of
     * growing a list of lines.  Messages carry string resource ids because
     * this object has no Context and the app is translated.
     */
    sealed interface Progress {
        data class Writing(val part: String) : Progress
        data class Verified(val part: String) : Progress
        data class Failed(val text: Int, val arg: String = "", val detail: String = "") : Progress
        data object Done : Progress
    }

    /**
     * Writes one set, verifying every partition by reading it back.
     *
     * Nothing reboots here.  A caller that has seen [Progress.Failed] must be
     * able to stop, because a tablet with three of four partitions replaced
     * still runs the system it is on — but only until it is restarted.
     */
    @Synchronized
    fun write(set: BootSet, log: (Progress) -> Unit): Boolean {
        val targets = validateWriteTargets()
        if (!targets.ok) {
            log(Progress.Failed(R.string.progress_incomplete, set.label,
                "X810 identity/partition-size check failed; no partition was written."))
            return false
        }
        if (!BootMaintenance.beforeSwitch()) {
            log(Progress.Failed(R.string.progress_incomplete, set.label,
                "Android backup refresh failed; see console."))
            return false
        }
        val fresh = discover().firstOrNull { it.id == set.id }
        if (fresh == null || !fresh.complete || fresh.hashes != set.hashes) {
            log(Progress.Failed(R.string.progress_incomplete, set.label))
            return false
        }
        if (!set.complete) {
            log(Progress.Failed(R.string.progress_incomplete, set.label))
            return false
        }

        for (part in PARTITIONS) {
            if (!validateWriteTargets().ok) {
                log(Progress.Failed(R.string.progress_write_failed, part.name,
                    "X810 partition identity changed; refusing the next write."))
                return false
            }
            val file = set.file(part)
            val expected = set.hashes[file]
            log(Progress.Writing(part.name))

            val write = Root.run("dd if=\"$file\" of=\"${part.device}\" bs=4M", "sync")
            if (!write.ok) {
                log(Progress.Failed(R.string.progress_write_failed, part.name, write.output.take(200)))
                return false
            }

            val got = parseSums(Root.run("sha256sum ${part.device}").output, listOf(part.device))[part.device]
            if (got == null || got != expected) {
                log(Progress.Failed(R.string.progress_mismatch, part.name))
                return false
            }
            log(Progress.Verified(part.name))
        }

        log(Progress.Done)
        return true
    }

    fun reboot() {
        Root.run("sync", "svc power reboot || reboot")
    }

    // -- helpers -------------------------------------------------------------

    /**
     * `sha256sum` prints "<hash>  <path>", and busybox and toybox disagree on
     * the spacing, so the path is matched rather than the column.
     */
    private fun parseSums(output: String, paths: List<String>): Map<String, String> {
        val out = mutableMapOf<String, String>()
        output.lineSequence().forEach { line ->
            val trimmed = line.trim()
            val hash = trimmed.substringBefore(' ').trim()
            if (hash.length != 64) return@forEach
            val path = paths.firstOrNull { trimmed.endsWith(it) }
            if (path != null) out[path] = hash
        }
        return out
    }
}
