#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
script_dir=$(cd "$(dirname "$0")" && pwd)
cd "$script_dir/.."
app_root=$(cd "$script_dir/../../.." && pwd)
python3 "$app_root/tools/sync-package-stages.py"

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/usr" "$stage/usr/local/libexec" \
    "$stage/etc/xdg/autostart" "$stage/usr/share/licenses/tab-companion"
cp -a packaging/tab-companion-x810/usr/. "$stage/usr/"
install -m 0644 packaging/tab-companion-x810/LICENSE-upstream \
    "$stage/usr/share/licenses/tab-companion/LICENSE"
install -m 0755 tools/x810-boot-switch-core \
    "$stage/usr/local/libexec/x810-boot-switch-core"
for helper in tab-companion-boot-status tab-companion-boot-switch; do
    install -m 0755 "tools/$helper" "$stage/usr/local/libexec/$helper"
done
install -m 0755 tools/x810-kernel-update-core \
    "$stage/usr/local/libexec/x810-kernel-update-core"
install -m 0755 tools/tab-companion-kernel-update \
    "$stage/usr/local/libexec/tab-companion-kernel-update"
install -m 0755 usr/libexec/tab-companion-keyboard-recover \
    "$stage/usr/libexec/tab-companion-keyboard-recover"
install -m 0755 tools/tab-companion-power-profile \
    "$stage/usr/libexec/tab-companion-power-profile"
install -m 0755 usr/libexec/tab-companion-zram-size \
    "$stage/usr/libexec/tab-companion-zram-size"
install -m 0755 usr/libexec/tab-companion-swap-priority \
    "$stage/usr/libexec/tab-companion-swap-priority"
install -m 0755 usr/libexec/tab-companion-thermal-setting \
    "$stage/usr/libexec/tab-companion-thermal-setting"
install -m 0755 usr/libexec/tab-companion-browser-memory-setting \
    "$stage/usr/libexec/tab-companion-browser-memory-setting"
install -m 0755 usr/libexec/tab-companion-browser-memory-agent \
    "$stage/usr/libexec/tab-companion-browser-memory-agent"
install -D -m 0644 usr/lib/systemd/user/tab-companion-browser-memory.service \
    "$stage/usr/lib/systemd/user/tab-companion-browser-memory.service"
install -m 0644 packaging/io.github.agcarbajo.TabCompanion.X810.policy \
    "$stage/usr/share/polkit-1/actions/io.github.agcarbajo.TabCompanion.X810.policy"
install -m 0644 packaging/tab-companion-x810/usr/share/polkit-1/actions/io.github.agcarbajo.TabCompanion.PowerProfile.policy \
    "$stage/usr/share/polkit-1/actions/io.github.agcarbajo.TabCompanion.PowerProfile.policy"
install -m 0644 packaging/tab-companion-polkit-agent.desktop \
    "$stage/etc/xdg/autostart/tab-companion-polkit-agent.desktop"
python3 "$app_root/tools/write-app-build-metadata.py" "$stage"
find "$stage" -type d -name __pycache__ -prune -exec rm -rf {} +
mkdir -p out
tar -C "$stage" -czf out/tab-companion-x810.tar.gz .
sha256sum out/tab-companion-x810.tar.gz > out/tab-companion-x810.tar.gz.sha256
echo "built out/tab-companion-x810.tar.gz"
