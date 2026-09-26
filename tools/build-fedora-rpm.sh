#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
app_root=$(cd "$(dirname "$0")/.." && pwd)
port_root="$app_root/ports/fedora-x810"
builder="$port_root/tools/build-tab-companion-x810-bundle.sh"
spec="$app_root/packaging/fedora/tab-companion.spec"
out="$port_root/out/rpms"

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required to synchronize canonical app source; no package has been changed." >&2
    exit 2
fi
bash "$builder"
bundle="$port_root/out/tab-companion-x810.tar.gz"
dist=${RPM_DIST:-$(rpm --eval '%{?dist}')}
run_number=${APP_BUILD_RUN_NUMBER:-0}
base_version=$(sed -n 's/^Version: //p' "$app_root/ports/ubuntu-x910/DEBIAN/control" | head -n1)
if [[ ! "$run_number" =~ ^[0-9]+$ || -z "$base_version" ]]; then
    echo "APP_BUILD_RUN_NUMBER must be a non-negative integer and app base version must be configured." >&2
    exit 2
fi
build_release=$((1000000 + run_number))
if [[ -z "$dist" ]]; then
    echo "RPM_DIST must identify the target Fedora release (for example .fc44)." >&2
    exit 2
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$out"

if command -v rpmbuild >/dev/null 2>&1; then
    mkdir -p "$tmp/BUILD" "$tmp/BUILDROOT" "$tmp/RPMS" "$tmp/SOURCES" "$tmp/SPECS" "$tmp/SRPMS"
    install -m 0644 "$bundle" "$tmp/SOURCES/tab-companion-x810.tar.gz"
    rpmbuild -bb "$spec" --define "_topdir $tmp" --define "dist $dist" \
        --define "app_version $base_version" --define "app_release $build_release.$run_number"
    mapfile -t rpms < <(find "$tmp/RPMS" -type f -name 'tab-companion-*.rpm' -print)
    if [[ ${#rpms[@]} -ne 1 ]]; then
        echo "Expected exactly one noarch app RPM; got ${#rpms[@]}." >&2
        exit 1
    fi
    install -m 0644 "${rpms[0]}" "$out/"
elif command -v podman >/dev/null 2>&1; then
    # Keep this package build reproducible on Linux workstations without
    # rpm-build installed locally. The package is noarch; Fedora 44 supplies
    # the build toolchain in a disposable container.
    podman run --rm -v "$app_root:/src:Z" \
        -e RPM_DIST="$dist" -e APP_BUILD_RUN_NUMBER="$run_number" \
        -e APP_BASE_VERSION="$base_version" -e APP_BUILD_RELEASE="$build_release.$run_number" \
        registry.fedoraproject.org/fedora:44 \
        bash -lc '
            set -euo pipefail
            dnf -y install rpm-build tar findutils python3 >/dev/null
            tmp=$(mktemp -d)
            trap '\''rm -rf "$tmp"'\'' EXIT
            mkdir -p "$tmp/BUILD" "$tmp/BUILDROOT" "$tmp/RPMS" "$tmp/SOURCES" "$tmp/SPECS" "$tmp/SRPMS"
            cp /src/ports/fedora-x810/out/tab-companion-x810.tar.gz "$tmp/SOURCES/"
            rpmbuild -bb /src/packaging/fedora/tab-companion.spec \
                --define "_topdir $tmp" --define "dist $RPM_DIST" \
                --define "app_version $APP_BASE_VERSION" --define "app_release $APP_BUILD_RELEASE"
            mapfile -t rpms < <(find "$tmp/RPMS" -type f -name "tab-companion-*.rpm" -print)
            [[ ${#rpms[@]} -eq 1 ]]
            install -m 0644 "${rpms[0]}" /src/ports/fedora-x810/out/rpms/
        '
else
    echo "Install rpm-build or Podman to build the Fedora package; no package was changed." >&2
    exit 2
fi

rpm_name=$(find "$out" -maxdepth 1 -type f -name "tab-companion-*.${dist#*.}.noarch.rpm" \
    -printf '%f\n' | sort -V | tail -n1)
rpm_file="$out/$rpm_name"
if [[ -z "$rpm_name" ]]; then
    echo "Built RPM could not be found in $out" >&2
    exit 1
fi
(cd "$out" && sha256sum "$(basename "$rpm_file")" > "$(basename "$rpm_file").sha256")
echo "Built $rpm_file"
