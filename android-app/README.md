# Android switcher (SM-X810)

Experimental X810 adaptation of the Android BootSwitcher in
[`agcarbajo/ubuntu-galaxy-tab-s9-ultra`](https://github.com/agcarbajo/ubuntu-galaxy-tab-s9-ultra)
(MIT; see `bootswitcher/LICENSE`). Produced with substantial AI assistance;
unofficial—review and validate before use.

Build with JDK 17, Android SDK 35, and Gradle 8.14.2:

```sh
cd android-app/bootswitcher
gradle testDebugUnitTest assembleDebug
```

Install `app/build/outputs/apk/debug/app-debug.apk` for testing. The dedicated
`Build Tab Companion Android switcher` workflow runs only when `android-app/`
changes (or when manually dispatched). It tests/assembles the APK, uploads a
90-day per-run Actions artifact, and updates the durable
[latest Android APK release](https://github.com/iamSlightlyWind/tab-companion/releases/tag/tab-companion-android-latest).
The APK is separate from the Linux app-update release so Android-only commits
cannot invalidate the Linux updater's same-run provenance checks. Its JSON
sidecar records the source commit and APK SHA-256.

CI currently publishes a debug APK. GitHub-hosted runners may not share the
same debug signing key between runs; if Android rejects an in-place install
with a signature mismatch, uninstall the old switcher and install the new APK.
For seamless replacement upgrades, configure a persistent private signing key
as GitHub Actions secrets and use a release signing config; do not commit the
keystore or passwords.

The app needs Magisk root and verified Android/Fedora boot-image sets. It only
writes `boot`, `init_boot`, `vendor_boot`, and `dtbo`, verifies device identity,
partition sizes and image hashes, then checks readback. It never writes
recovery, vbmeta, bootloader, GPT/PIT, or user data; it does not install root,
prepare partitions, or repair recovery. Review the target device and image set
before switching.
