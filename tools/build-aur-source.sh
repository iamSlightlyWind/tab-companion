#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
app_root=$(cd "$(dirname "$0")/.." && pwd)
version=${1:-$(sed -n 's/^Version: //p' "$app_root/ports/ubuntu-x910/DEBIAN/control" | head -n1)}
run_number=${APP_BUILD_RUN_NUMBER:-0}
if [[ ! "$version" =~ ^[0-9]+(\.[0-9]+)*$ || ! "$run_number" =~ ^[0-9]+$ ]]; then
    echo "VERSION must be dotted numeric and APP_BUILD_RUN_NUMBER a non-negative integer." >&2
    exit 2
fi
pkgrel=${2:-$((1000000 + run_number))}
if [[ ! "$pkgrel" =~ ^[1-9][0-9]*$ ]]; then
    echo "PKGREL must be a positive integer." >&2
    exit 2
fi
bash "$app_root/ports/fedora-x810/tools/build-tab-companion-x810-bundle.sh"
bundle="$app_root/ports/fedora-x810/out/tab-companion-x810.tar.gz"
bundle_hash=$(sha256sum "$bundle" | awk '{print $1}')
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
sed -e "s/@VERSION@/$version/g" -e "s/@PKGREL@/$pkgrel/g" \
    -e "s/@BUNDLE_SHA256@/$bundle_hash/g" \
    "$app_root/packaging/arch/PKGBUILD.in" > "$tmp/PKGBUILD"
out="$app_root/ports/fedora-x810/out/arch"
mkdir -p "$out"
archive="$out/tab-companion-aur-source.tar.gz"
cp "$bundle" "$tmp/tab-companion-x810.tar.gz"
cp "$app_root/packaging/arch/tab-companion.install" "$tmp/tab-companion.install"
tar -C "$tmp" -czf "$archive" PKGBUILD tab-companion.install tab-companion-x810.tar.gz
sha256sum "$archive" > "$archive.sha256"
echo "Built AUR source archive $archive (PKGBUILD builds as the user)."
