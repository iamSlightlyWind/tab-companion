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

## Update

In Tab Companion, open **Updates**, select **Tab Companion**, then choose
**Check** and **Install update**. It downloads the package from the latest
successful `main` build and verifies its checksum. New commits pushed to `main`
run independent Ubuntu, Fedora, Arch, and Android jobs. A rolling public GitHub
Release is updated after a successful run; only the newest build release is kept.
The Android switcher remains a separate Actions artifact.

## Reinstall or roll back

To repair/update an existing install, repeat **Install once** and install the
package over the current one; don't uninstall first. The rolling release keeps
only the newest build. To roll back, use a previously saved package:

- Fedora: `sudo dnf install ./tab-companion-<older>.rpm`
- Ubuntu: `sudo apt install --allow-downgrades ./ubuntu-gts9u-companion_<older>_all.deb`
- Arch: `sudo pacman -U ./tab-companion-<older>-any.pkg.tar.zst`

Linux-port packages can use a separate update channel when their port provides
its own compatible Actions build and metadata. Kernel and boot-image updates
remain manual TWRP installs.
