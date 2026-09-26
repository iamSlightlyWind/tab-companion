# Android Dualboot app (SM-X810)

This is the Android-side switcher for the Fedora/X810 Tab Companion port. It
is an X810-specific adaptation of the Android `bootswitcher` app in the local
`ubuntu-galaxy-tab-s9-ultra` source tree. The project is kept here so the Linux
and Android halves of the port have one source repository. The original
upstream source is MIT-licensed; see `bootswitcher/LICENSE` for the license.

## Build

Requirements: JDK 17, Android SDK platform/build-tools 35 and Android Gradle
Plugin 8.7.3 build dependencies (built here with Gradle 8.14.2). From
`bootswitcher/`, build and run the safety unit tests with Android Studio or
Gradle:

```sh
gradle testDebugUnitTest assembleDebug
```

The installable debug APK is written to
`bootswitcher/app/build/outputs/apk/debug/app-debug.apk`. The APK is debug
signed for testing. Build and unit tests only; this APK has not been installed
or tested on a tablet.

## Boot-set inputs

The app first discovers sets on Fedora's ext4 root at
`/var/lib/x810-boot-sets/<id>/`, mounted read-only from Android as
`/mnt/x810-linuxroot`. It also reads manually staged images from Android's
internal shared storage at `/sdcard/BootSets/<id>/`. An SD-card copy is not
read automatically: in Android, use **My Files** to copy the `BootSets`
folder from the microSD card to **Internal storage** (the top level, creating
`BootSets` there if needed). The internal path must look like:

```text
Internal storage/BootSets/android/{boot,init_boot,vendor_boot,dtbo}.img
Internal storage/BootSets/fedora/{boot,init_boot,vendor_boot,dtbo}.img
```

Copy complete directories before launching the app. The Android set must match
the current Android boot partitions byte-for-byte until it is deliberately
replaced; the app refuses an unverified/manual set as an automatic Android
backup. Fedora's installed set can instead come from `linuxroot`. `name.txt`
is optional. For switching in both directions, the Linux-side Tab Companion
also needs both verified sets under Fedora's `/var/lib/x810-boot-sets/`;
Android's `/sdcard/BootSets` is not visible from Fedora's ext4 root and is only
an Android-side fallback. Ensure the Linux installation/installer seeds the
Android set into `linuxroot` before depending on Fedora-to-Android switching.
Every image is checked against the measured X810 partition size and SHA-256
before the switcher offers it.

The separate Linuxroot `/var/lib/x810-boot-sets/recovery/` recovery support
archive is intentionally excluded from boot-set discovery; it is not an
Android/Fedora boot set and the app never flashes it.

### First launch after Magisk root

The app can bootstrap `android` on Linuxroot if the Linux installation did not
preseed that directory. First boot the current rooted Android, install the APK,
grant its Magisk `su` request, then open **Dualboot** in the foreground once.
Foreground startup checks `SM-X810`/`gts9pwifi`, all four by-name symlink
targets and sizes, then hashes the live partitions. It asks Magisk's
`magiskboot` to unpack the live `boot` image and requires its kernel version to
match Android's running `uname -r`. It then copies the live four images into a
staging directory on Linuxroot, checks each size and SHA-256 (including a
second hash of each live source), and commits the verified set as
`/var/lib/x810-boot-sets/android`. **This bootstrap only reads boot block
devices; it writes only backup files to Linuxroot's ext4 filesystem, not to
any raw boot partition, and does not reboot.** It also retains the prior
verified set under `/var/lib/x810-boot-backups/` when replacing one. A stale
`/sdcard/BootSets/android` manual copy is left untouched, but the verified
Linuxroot `android` copy takes precedence for that id. Background boot/tile
refreshes do not create this first snapshot; a failed identity, size, or
kernel-match check blocks switching and leaves all boot partitions unchanged.

## Root and recovery boundaries

The app requires Android root through Magisk `su`. It is intended for SM-X810
(`ro.product.device=gts9pwifi`) only. Before a write, it checks those identity
properties, verifies the by-name symlink destinations and exact sizes, and
checks again before every write. Its exact write allowlist is `boot`,
`init_boot`, `vendor_boot`, and `dtbo`; it writes each in sequence and checks
the raw readback hash. A failure stops the sequence and does not restart.

**This app never writes `recovery`, `vbmeta`, bootloader/BL, GPT/PIT, `super`,
`userdata`, or any other partition. It has no automatic recovery-flash action.**
TWRP persistence is not solved by modifying boot images: One UI may restore
Samsung stock recovery. If that happens, recovery must be flashed again from
Download Mode with Odin/Heimdall after validating the exact recovery image.
Do not boot One UI first after that recovery flash if the device's instructions
say to reboot directly into TWRP.

The Android-side switch requires an already rooted Android environment and
complete, verified Fedora/Android sets. The app does not install root, prepare
partitions, create boot images, repair recovery, or switch systems by itself;
a user explicitly selects a set and then chooses whether to restart.
