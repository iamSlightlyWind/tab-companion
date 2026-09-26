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

Install `app/build/outputs/apk/debug/app-debug.apk` for testing. GitHub Actions
also builds it independently and uploads `tab-companion-android-apk`.

The app needs Magisk root and verified Android/Fedora boot-image sets. It only
writes `boot`, `init_boot`, `vendor_boot`, and `dtbo`, verifies device identity,
partition sizes and image hashes, then checks readback. It never writes
recovery, vbmeta, bootloader, GPT/PIT, or user data; it does not install root,
prepare partitions, or repair recovery. Review the target device and image set
before switching.
