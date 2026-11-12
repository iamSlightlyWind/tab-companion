# Updates from the latest successful Actions build

The app updater queries the configured public GitHub repository for the newest
completed, successful `push` run, then downloads that run's matching asset from
its public run-keyed build release. The release tag is keyed to that run ID;
the Linux publish job removes superseded build releases and its temporary
Ubuntu/Fedora/Arch Actions package artifacts after publication. No login is
needed to download release assets. The app channel defaults to
`build-updates.yml` and `tab-companion-ubuntu` on Ubuntu,
`tab-companion-fedora` on Fedora, or `tab-companion-arch` on Arch. Port packages
can provide their own workflow, branch, release asset name, build-info path,
release-tag prefix, and `public_release: true` setting in
`/usr/share/tab-companion/port.json`; this lets a port use the same public
run-keyed release path instead of the unauthenticated Actions-artifact API.
For example, a port's updater routing fields can be:

```json
{
  "workflow_file": "port-updates.yml",
  "branch": "main",
  "artifact_name": "x810-fedora-port",
  "public_release": true,
  "release_tag_prefix": "x810-fedora-port-build",
  "build_info_path": "/usr/share/tab-companion/port-build.json"
}
```

## Artifact contract

The artifact contains `tab-companion-update.json` and one or more native
packages. The index is scoped to the project and contains run identity plus
target-matched assets:

```json
{
  "schema_version": 1,
  "project": "tab-companion",
  "version": "1.4.2",
  "run_id": 123456789,
  "run_number": 42,
  "commit": "<full commit SHA>",
  "branch": "main",
  "assets": [{
    "name": "tab-companion-1.4.2.42-1.fc44.noarch.rpm",
    "sha256": "<64 lowercase hex characters>",
    "size": 123456,
    "format": "rpm",
    "package_name": "tab-companion",
    "package_version": "1.4.2.42-1.fc44",
    "target": {"os_id": "fedora", "os_version": "44", "arch": "aarch64", "device": "SM-X810"}
  }]
}
```

The updater matches OS/version/architecture/device, extracts only the named
asset from the downloaded ZIP, verifies its declared size and SHA-256, then
asks the platform package manager to install it. `app-build.json` records the
installed build's run ID and commit so repeated checks do not offer the same
build again. Port packages should include a corresponding port build-info
file. `aur-source` assets are built with `makepkg` as the logged-in user, not
as root; pacman installs the resulting package.

The Linux updater workflow (`build-updates.yml`) runs only for changes that are
not Android-only, documentation-only, or README-only. It builds the Ubuntu,
Fedora, and Arch packages together and publishes the indexed packages as
run-keyed public release assets. They are deliberately kept in one Linux
provenance run: each package contains the current run ID, and the updater
requires its release manifest to match the latest successful run exactly.
Therefore it does not copy an old package into a new run or restamp an
unchanged package. If no Linux package input changed, the workflow does not
run and the last successful Linux release remains the current package set.

The Android switcher uses the independent `build-android.yml` workflow. It runs
only for `android-app/` changes (or manual dispatch), tests and assembles the
APK, keeps the per-run Actions artifact for 90 days, and updates the durable
`tab-companion-android-latest` release with the APK and a provenance/hash JSON
sidecar. Keeping Android-only runs out of the Linux updater workflow is needed
for compatibility: installed Linux versions select the newest successful
`build-updates.yml` run and reject assets whose run ID/commit do not match it.
The Android stable-tag release does not replace the repository's Linux
`latest` release. If Android source does not change, the last published APK
remains available at that tag; it is not rebuilt or repackaged under a newer
Linux run.

The Android build caches the pinned Android SDK packages and Gradle user-home
dependencies/build cache. Gradle's project build cache is enabled in
`gradle.properties`. Fedora's RPM build caches downloaded Fedora 44 DNF tool
packages/metadata; this is only a dependency-download cache, never a cached RPM
or built app package. The three Linux packages are staged as short-lived
Actions artifacts for CI handoff; public release ZIPs are named
`tab-companion-ubuntu.zip`, `tab-companion-fedora.zip`, and
`tab-companion-arch.zip`.
For public repositories, run and release metadata and assets can be fetched
without embedding a personal access token in the app. This updater deliberately
requires a public repo; private-repo support would need user-managed
authentication, not a bundled credential.

The app update feed uses public release assets rather than the Actions artifact
download endpoint, which returned HTTP 401 unauthenticated in testing. Release
cleanup is part of the successful Linux publish job, so failed builds preserve
the last good updater package. Android stays separate because the Linux updater
selects the newest successful Linux workflow run and requires its exact-run
artifact; an Android-only run in that feed would break existing clients.

## Package-manager behavior and boundaries

- **Ubuntu/Debian:** the shared updater can install a compatible DEB through
  APT/dpkg and polkit. The original X910 system updater remains a separate,
  legacy path; it is not the feed used for Tab Companion self-updates.
- **Fedora:** the app validates RPM metadata and uses DNF/DNF5 through the
  fixed polkit installer helper.
- **Arch:** local pacman packages use the system package manager; AUR source
  assets are safely extracted and built as the logged-in user.

The user confirms installation. No updater writes boot partitions. Kernel and
boot images remain manual TWRP packages. Detached signatures are not part of
this design: HTTPS to the configured public repo plus the index's package
hash is the selected trust model. This detects corruption/mismatch, not a
compromised repository owner or workflow.

## Arch SM-X810 device-support package

The generic port channel also supports an Arch SM-X810 device-support
package; it does not require a separate kernel updater. The Arch support
package can publish `x810-device-support` as a normal indexed `pacman-local`
asset. The installed support package provides `/usr/share/tab-companion/port.json`
with routing metadata like this (replace the repository URL and workflow
filename with the actual public build source):

```json
{
  "schema_version": 1,
  "port_id": "x810-device-support",
  "name": "X810 Arch device support",
  "repo_url": "https://github.com/OWNER/REPOSITORY",
  "version": "0.1.0",
  "device_id": "SM-X810",
  "os_id": "arch",
  "arch": "aarch64",
  "workflow_file": "build-updates.yml",
  "branch": "main",
  "artifact_name": "x810-device-support",
  "public_release": true,
  "release_tag_prefix": "x810-device-support-build",
  "build_info_path": "/usr/share/tab-companion/port-build.json"
}
```

The workflow publishes `x810-device-support-build-<run_id>` with the public
asset `x810-device-support.zip`. That ZIP contains the package and the regular
`tab-companion-update.json` build index. Its relevant fields should be:

```json
{
  "schema_version": 1,
  "project": "x810-device-support",
  "version": "0.1.0",
  "run_id": 123456789,
  "run_number": 42,
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "branch": "main",
  "assets": [{
    "name": "x810-device-support-0.1.0-1-any.pkg.tar.zst",
    "sha256": "<64 lowercase hex characters>",
    "size": 123456,
    "format": "pacman-local",
    "package_name": "x810-device-support",
    "package_version": "0.1.0-1",
    "target": {
      "os_id": "arch",
      "arch": "aarch64",
      "device": "SM-X810"
    }
  }]
}
```

The index `project` must equal the installed `port_id`; its run ID, run number,
commit, and branch must match the latest successful push run. In the manifest,
`target.device` is compared with the host's device ID; `device_id` is the
separate key used by the installed `port.json`. The package itself is
`arch=('any')`, while the manifest target stays `aarch64` so the feed is only
offered to the intended ARM64 device. The root installer independently checks
the package's name, version, and Pacman architecture (`any`) before installing.

This is a package update channel only: the support package must not contain or
install a kernel, modules, boot images, or partition-writing logic. Tab
Companion's guarded X810 kernel/boot updater remains Fedora-only. Arch local
package installation still performs the required `pacman -Syu` before
`pacman -U`, so other repository-managed system packages may update as part of
that transaction; this channel does not itself provide or flash a kernel/boot
update.

## Full build reset

For an intentional clean-build sanity check, run **Actions → Reset Tab
Companion builds** on `main` and enter `RESET TAB COMPANION BUILDS`. It waits
for active app builds, rebuilds Ubuntu, Fedora, Arch, and the Android switcher,
then removes superseded run-keyed Linux releases and older package artifacts
after both build pipelines succeed. It does not delete GitHub Actions caches:
dependency downloads remain cached, while packages/APKs are rebuilt from the
current source. It retains the latest push-keyed Linux release because
installed updaters use that exact run identity; the reset run is a sanity
build, not a replacement for the push update feed.
