# Updates from the latest successful Actions build

Tab Companion does not use GitHub Releases or release tags for its shared app
and Linux-port update channels. It queries the configured public GitHub repo
for the newest completed, successful `push` run of a named workflow on a
configured branch (normally `main`), then downloads that run's named Actions
artifact. The app channel defaults to `build-updates.yml` and
`tab-companion-build`; port packages can provide their own workflow, branch,
artifact name, and build-info path in `/usr/share/tab-companion/port.json`.

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

The Actions workflow runs on pushes to `main`. It tests, builds target
packages, writes the index, and uploads the single named artifact. It does not
publish releases, create tags, or upload release assets.
For public repositories, run and artifact metadata can be queried without
embedding a personal access token in the app. This updater deliberately
requires a public repo; private-repo support would need user-managed
authentication, not a bundled credential.

Actions artifacts expire (GitHub's default retention is 90 days, subject to
repository policy), so the updater reports a missing/expired build rather than
falling back to an older release. A successful recent build is required.

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
