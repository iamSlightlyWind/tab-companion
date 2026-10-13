package com.tabcompanion.x810.bootswitcher

import org.junit.Assert.*
import org.junit.Test

class BootSafetyTest {
    private fun set(id: String, value: String, dir: String = "/sets/$id") = BootSets.BootSet(id, "Fedora Linux",
        true, BootSets.PARTITIONS.associate { "$dir/${it.name}.img" to value }, dir)
    private val android = set("android", "a".repeat(64))
    private val linux = set("fedora", "b".repeat(64))
    private val sets = listOf(android, linux)
    private fun live(set: BootSets.BootSet) = BootSets.PARTITIONS.associate {
        it.device to set.hashes.getValue(set.file(it))
    }

    @Test fun onlyTheFourMeasuredTransitionPartitionsAreAllowed() {
        assertEquals(listOf("boot", "init_boot", "vendor_boot", "dtbo"),
            BootSets.PARTITIONS.map { it.name })
        assertEquals(listOf("/dev/block/sda21", "/dev/block/sda22", "/dev/block/sda24", "/dev/block/sda30"),
            BootSets.PARTITIONS.map { it.node })
        assertFalse(BootSets.PARTITIONS.any { it.name in setOf("recovery", "vbmeta", "super", "userdata") })
        assertTrue(BootSets.isLinux(linux))
    }

    @Test fun recoverySupportArchiveIsNeverDiscoveredAsABootSet() {
        assertFalse(BootSets.isBootSetId("recovery"))
        assertFalse(BootSets.isBootSetId("Recovery"))
        assertFalse(BootSets.isBootSetId("rejected-20261006094100"))
        assertTrue(BootSets.isBootSetId("android"))
        assertTrue(BootSets.isBootSetId("fedora"))
        assertFalse(BootSets.isBootSetId("../android"))
    }

    @Test fun firstLaunchMayCaptureCurrentAndroidWhenNoSavedSetExists() {
        assertTrue(BootSets.canBootstrapAndroid(listOf(linux), live(android)))
        assertFalse(BootSets.canBootstrapAndroid(listOf(linux), emptyMap()))
        val rootedAndroid = set("android", "a".repeat(64), "${BootSets.LINUX_SETS}/android")
        assertFalse(BootSets.canBootstrapAndroid(listOf(linux, rootedAndroid), live(rootedAndroid)))
    }

    @Test fun fixedSystemIdsCannotBeMislabelledByStaleSnapshotMetadata() {
        assertEquals("Fedora", BootSets.labelFor("fedora", "Fedora 44"))
        assertEquals("Android", BootSets.labelFor("android", "Fedora"))
        assertEquals("LineageOS", BootSets.labelFor("lineage", "Fedora"))
    }

    @Test fun staleStockAndroidSetAfterMagiskPatchIsAChangedAndroidSnapshot() {
        val stock = set("android", "a".repeat(64), "${BootSets.LINUX_SETS}/android")
        val patchedLive = BootSets.PARTITIONS.associate { part ->
            part.device to when (part.name) {
                "boot" -> "c".repeat(64)
                "init_boot" -> "d".repeat(64)
                else -> stock.hashes.getValue(stock.file(part))
            }
        }
        assertEquals(BootSets.BackupDecision.CHANGED,
            BootSets.backupDecision(listOf(stock, linux), patchedLive))
    }

    @Test fun wrongLabelsNeverMakeAndroidRunFedora() {
        assertEquals("android", BootSets.runningAndroid(sets, "One UI 8")?.id)
        assertEquals("One UI 8", BootSets.runningAndroid(sets, "One UI 8")?.label)
    }
    @Test fun unchangedBackupNeedsNoCopy() {
        assertEquals(BootSets.BackupDecision.UNCHANGED, BootSets.backupDecision(sets, live(android)))
    }
    @Test fun stagedFedoraMustNeverBecomeAndroidBackup() {
        assertEquals(BootSets.BackupDecision.LINUX_STAGED, BootSets.backupDecision(sets, live(linux)))
        assertEquals("android", BootSets.runningAndroid(sets, "One UI 8")?.id)
    }
    @Test fun interruptedSwitchIsRejectedForEveryPartition() {
        for (part in BootSets.PARTITIONS) {
            val mixed = live(android) + (part.device to linux.hashes.getValue(linux.file(part)))
            assertEquals(BootSets.BackupDecision.MIXED, BootSets.backupDecision(sets, mixed))
        }
    }
    @Test fun changedAndroidRequiresVerifiedCapture() {
        val updated = live(android) + (BootSets.PARTITIONS.first().device to "c".repeat(64))
        assertEquals(BootSets.BackupDecision.CHANGED, BootSets.backupDecision(sets, updated))
    }
    @Test fun incompleteReadAndMultipleAndroidSetsAreRejected() {
        assertEquals(BootSets.BackupDecision.AMBIGUOUS, BootSets.backupDecision(sets, emptyMap()))
        assertEquals(BootSets.BackupDecision.AMBIGUOUS,
            BootSets.backupDecision(sets + set("lineage", "c".repeat(64)), live(android)))
    }
}
