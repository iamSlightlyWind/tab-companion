# Updates from the latest successful Actions build

The app updater queries the configured public GitHub repository for the newest
completed, successful `push` run, then downloads that run's matching asset from
its public rolling build release. The release tag is keyed to that run ID; old
build releases are deleted after the new one is published. No login is needed
to download release assets. The app channel defaults to `build-updates.yml` and
`tab-companion-ubuntu` on Ubuntu, `tab-companion-fedora` on Fedora, or
`tab-companion-arch` on Arch. Port packages can provide their own workflow,
branch, artifact name, and build-info path in
`/usr/share/tab-companion/port.json`.

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

The Actions workflow runs independent Ubuntu, Fedora, Arch, and Android jobs on
pushes to `main`, then publishes their indexed packages as public release assets.
The build artifacts are still separate by distro for CI handoff; release assets
are named `tab-companion-ubuntu.zip`, `tab-companion-fedora.zip`, and
`tab-companion-arch.zip`.
For public repositories, run and release metadata and assets can be fetched
without embedding a personal access token in the app. This updater deliberately
requires a public repo; private-repo support would need user-managed
authentication, not a bundled credential.

The app update feed uses public release assets rather than the Actions artifact
download endpoint, which returned HTTP 401 unauthenticated in testing. Port
feeds that still use Actions artifacts retain their configured artifact expiry.

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
