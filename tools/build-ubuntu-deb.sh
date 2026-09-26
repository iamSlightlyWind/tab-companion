#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
app_root=$(cd "$(dirname "$0")/.." && pwd)
stage="$app_root/ports/ubuntu-x910"
out="$app_root/ports/ubuntu-x910/out"

if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "dpkg-deb is required; no package has been changed." >&2
    exit 2
fi
python3 "$app_root/tools/sync-package-stages.py"
mkdir -p "$out"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
build_stage="$tmp/package"
cp -a "$stage" "$build_stage"
python3 "$app_root/tools/write-app-build-metadata.py" "$build_stage"

package=$(sed -n 's/^Package: //p' "$build_stage/DEBIAN/control" | head -n1)
base_version=$(sed -n 's/^Version: //p' "$build_stage/DEBIAN/control" | head -n1)
arch=$(sed -n 's/^Architecture: //p' "$build_stage/DEBIAN/control" | head -n1)
run_number=${APP_BUILD_RUN_NUMBER:-0}
if [[ ! "$run_number" =~ ^[0-9]+$ ]]; then
    echo "APP_BUILD_RUN_NUMBER must be a non-negative integer." >&2
    exit 2
fi
build_release=$((1000000 + run_number))
version="${base_version}+build.${build_release}"
if [[ -z "$package" || -z "$base_version" || -z "$arch" ]]; then
    echo "Package, Version, and Architecture are required in DEBIAN/control." >&2
    exit 1
fi
sed -i "s/^Version: .*/Version: $version/" "$build_stage/DEBIAN/control"
artifact="$out/${package}_${version}_${arch}.deb"
dpkg-deb --build --root-owner-group "$build_stage" "$artifact"
sha256sum "$artifact" > "$artifact.sha256"
echo "Built $artifact"
