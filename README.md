# Tab Companion

GTK app and package updater for Linux on Samsung Galaxy tablets. This
experimental project derives from
[`agcarbajo/ubuntu-galaxy-tab-s9-ultra`](https://github.com/agcarbajo/ubuntu-galaxy-tab-s9-ultra)
and was produced with substantial AI assistance. It is unofficial; review and
validate it before use.

## Install once

1. Wait for a successful [build](https://github.com/iamSlightlyWind/tab-companion/actions/workflows/build-updates.yml)
   on `main`.
2. Download the matching ZIP from the [latest build](https://github.com/iamSlightlyWind/tab-companion/releases/latest):
   `tab-companion-ubuntu.zip`, `tab-companion-fedora.zip`, or `tab-companion-arch.zip`.
   Extract it; it contains the package and its build index.
3. Install the package matching your Linux distribution and tablet:

   **Fedora (SM-X810):**
   ```sh
   sudo dnf install ./tab-companion-*.rpm
   ```

   **Ubuntu/Debian (SM-X910):**
   ```sh
   sudo apt install ./ubuntu-gts9u-companion_*.deb
   ```

   **Arch:** extract `tab-companion-arch-source.tar.gz`, then run `makepkg -si`
   as your normal user from the extracted directory (not as root).

Launch **Tab Companion** from the app grid or run `tab-companion`.
For a text-only updater (for example from a terminal without a usable desktop), run
`tab-companion --tui`. It exposes only the app update, Linux port package update,
and on X810 the kernel/boot update plus cached fallback restore. It uses the same
checksum checks and polkit helpers as the GUI; it does not reboot automatically.

## Update

In Tab Companion, open **Updates**, select **Tab Companion**, then choose
**Check** and **Install update**. It downloads the package from the latest
successful `main` build and verifies its checksum. New commits pushed to `main`
run the Linux package workflow only when its Linux package/build inputs changed;
that workflow builds Ubuntu, Fedora, and Arch packages together because each
release manifest is bound to that workflow run. Android switcher source changes
use a separate workflow, whose latest APK is available from the
[Android switcher release](https://github.com/iamSlightlyWind/tab-companion/releases/tag/tab-companion-android-latest).
This separation keeps Android-only successful runs from breaking Linux updater
provenance checks. If a component has no relevant source change, its last
successful public package remains the current one; old outputs are not
re-stamped as a new build.

On Fedora SM-X810, **Updates** also includes **Kernel and boot images**. Choose
a writable fallback directory before installing. Prefer a microSD or USB-OTG
drive; Tab Companion saves the current `boot`, `init_boot`, `vendor_boot`,
`dtbo` images and matching kernel modules under
`<chosen-folder>/<build-id>/files/`, verifies
the snapshot, then installs the matching kernel RPM and writes/read-back-checks
those four partitions. It does not reboot automatically and does not touch
`vbmeta`, recovery, firmware, GPT, or user data. After a successful update it
keeps the five newest verified pre-update snapshots in the selected folder.
The update is one privileged operation: if DNF or a raw image write fails, it
attempts to restore the previous module tree and, when partition writes have
begun, all four old images. It never reboots automatically. Each backup contains
`READ-ME-TWRP.txt` and a module-restore script. Choose microSD/USB-OTG if
possible, or copy the entire numbered folder somewhere TWRP can mount;
TWRP may not be able to read Fedora's internal `linuxroot`.
The GUI's **List and restore…** action (or the TUI's cached-fallback action)
lists only numbered snapshots whose metadata and all recorded files pass
integrity checks. Select a build number to restore its four boot images and
matching kernel modules; the privileged helper revalidates the snapshot,
requires authentication, reads back every partition write, and never reboots.

## Reinstall or roll back

To repair/update an existing install, repeat **Install once** and install the
package over the current one; don't uninstall first. The rolling release keeps
only the newest build. To roll back, use a previously saved package:

- Fedora: `sudo dnf install ./tab-companion-<older>.rpm`
- Ubuntu: `sudo apt install --allow-downgrades ./ubuntu-gts9u-companion_<older>_all.deb`
- Arch: `sudo pacman -U ./tab-companion-<older>-any.pkg.tar.zst`

Linux-port packages can use a separate update channel when their port provides
its own compatible Actions build and metadata. The X810 kernel/boot channel is
specific to Fedora SM-X810 and is not shown on other devices or distributions.
