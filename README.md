# Tab Companion

GTK app and package updater for Linux on Samsung Galaxy tablets. This
experimental project derives from
[`agcarbajo/ubuntu-galaxy-tab-s9-ultra`](https://github.com/agcarbajo/ubuntu-galaxy-tab-s9-ultra)
and was produced with substantial AI assistance. It is unofficial; review and
validate it before use.

## Install once

1. Open the [Build Tab Companion workflow](https://github.com/iamSlightlyWind/tab-companion/actions/workflows/build-updates.yml)
   and wait for a successful run on `main`.
2. Download the matching artifact and extract it: `tab-companion-ubuntu` for Ubuntu,
   `tab-companion-fedora` for Fedora, or `tab-companion-arch` for Arch.
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

## Update

In Tab Companion, open **Updates**, select **Tab Companion**, then choose
**Check** and **Install update**. It downloads the package from the latest
successful `main` build and verifies its checksum. New commits pushed to `main`
run independent Ubuntu, Fedora, Arch, and Android jobs automatically; no tags or
GitHub releases are used. Artifacts expire after 90 days. The Android switcher
is uploaded separately as `tab-companion-android-apk`.

Linux-port packages can use a separate update channel when their port provides
its own compatible Actions build and metadata. Kernel and boot-image updates
remain manual TWRP installs.
