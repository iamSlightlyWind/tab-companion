# Runs as root immediately before the Android dual-boot switch.
# Captures the live boot partitions to Linuxroot, shared with Fedora.
set -eu
umask 022
mountpoint=/mnt/x810-linuxroot
base=$mountpoint/var/lib/x810-boot-sets
backup=$mountpoint/var/lib/x810-boot-backups
case "$SET_ID" in android) ;; *) echo 'Only the Android set may be captured here.'; exit 1;; esac
test "$(getprop ro.product.device)" = gts9pwifi
test "$(getprop ro.product.model)" = SM-X810
test -b /dev/block/by-name/linuxroot
test "$(readlink -f /dev/block/by-name/linuxroot)" = /dev/block/sda35
mounted_source=$(awk '$2 == "/mnt/x810-linuxroot" {print $1; exit}' /proc/mounts)
test "$(readlink -f "$mounted_source")" = /dev/block/sda35
if grep " $mountpoint " /proc/mounts | grep -q noload; then
    echo 'Linuxroot needs filesystem recovery; snapshot was not written.'
    exit 1
fi
old=$base/android
previous=$backup/android.previous
stage=$backup/android.pending
writable=0
cleanup() {
    result=$?
    set +e
    if [ "$writable" = 1 ] && [ ! -d "$old" ] && [ -d "$previous" ]; then mv "$previous" "$old"; fi
    rm -rf "$stage"
    sync
    if [ "$writable" = 1 ]; then mount -o remount,ro "$mountpoint" || result=1; fi
    exit "$result"
}
trap cleanup EXIT
mount -o remount,rw "$mountpoint"
writable=1
mkdir -p "$base" "$backup"
if [ ! -d "$old" ] && [ -d "$previous" ]; then mv "$previous" "$old"; fi
if [ -d "$old" ] && [ -d "$previous" ]; then rm -rf "$previous"; fi
rm -rf "$stage"
mkdir "$stage"
chmod 0700 "$stage"
for part in boot init_boot vendor_boot dtbo; do
    case "$part" in
        boot) bytes=100663296; node=/dev/block/sda21;;
        init_boot) bytes=8388608; node=/dev/block/sda22;;
        vendor_boot) bytes=100663296; node=/dev/block/sda24;;
        dtbo) bytes=16777216; node=/dev/block/sda30;;
        *) exit 1;;
    esac
    test "$(readlink -f /dev/block/by-name/$part)" = "$node"
    test "$(blockdev --getsize64 /dev/block/by-name/$part)" = "$bytes"
    dd if=/dev/block/by-name/$part of="$stage/$part.img" bs=4M 2>/dev/null
    # Geometry/short-read guard only. Do not validate signatures, kernel
    # versions, or hashes: Magisk and KernelSU intentionally alter these.
    test "$(stat -c %s "$stage/$part.img")" = "$bytes"
    chmod 0644 "$stage/$part.img"
done
printf '%s\n' "$SYSTEM_NAME" > "$stage/name.txt"
chmod 0644 "$stage/name.txt"
sync
if [ -d "$old" ]; then mv "$old" "$previous"; fi
mv "$stage" "$old"
sync
rm -rf "$previous"
sync
echo 'Android: current boot files saved to shared Linuxroot storage.'
