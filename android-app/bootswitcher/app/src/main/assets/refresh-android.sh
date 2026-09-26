# Executed by BootMaintenance under the same lock as partition switching.
# Only backup files are written. No dd destination is a block device.
set -eu
mountpoint=/mnt/x810-linuxroot
base=$mountpoint/var/lib/x810-boot-sets
backup=$mountpoint/var/lib/x810-boot-backups
# SET_ID, SYSTEM_NAME, EXPECTED and CHANGED are assigned by the caller.
case "$SET_ID" in ''|*[!a-zA-Z0-9_-]*) exit 1;; esac
test "$(getprop ro.product.device)" = gts9pwifi
test "$(getprop ro.product.model)" = SM-X810
test -b /dev/block/by-name/linuxroot
test "$(readlink -f /dev/block/by-name/linuxroot)" = /dev/block/sda35
mounted_source=$(awk '$2 == "/mnt/x810-linuxroot" {print $1; exit}' /proc/mounts)
test "$(readlink -f "$mounted_source")" = /dev/block/sda35
if grep " $mountpoint " /proc/mounts | grep -q noload; then
    echo "Android: Linux filesystem needs recovery; backup unchanged."
    exit 1
fi
old=$base/$SET_ID
previous=$backup/$SET_ID.previous
stage=$backup/$SET_ID.pending
scratch=/data/local/tmp/x810-boot-check
writable=0
cleanup() {
    result=$?
    set +e
    if [ "$writable" = 1 ] && [ ! -d "$old" ] && [ -d "$previous" ]; then mv "$previous" "$old"; fi
    sync
    if [ "$writable" = 1 ]; then mount -o remount,ro "$mountpoint" || result=1; fi
    rm -rf "$scratch"
    exit "$result"
}
trap cleanup EXIT
# Verify a changed kernel belongs to this running Android before trusting any
# unknown image, including images left by an interrupted external switch.
if [ "$CHANGED" = 1 ]; then
    mkdir -p "$scratch"
    (cd "$scratch"; /data/adb/magisk/magiskboot unpack /dev/block/by-name/boot >/dev/null 2>&1)
    grep -aFq "Linux version $(uname -r) " "$scratch/kernel"
fi
mount -o remount,rw "$mountpoint"
writable=1
mkdir -p "$backup"
# Recover a power loss between the two directory renames.
if [ ! -d "$old" ] && [ -d "$previous" ]; then mv "$previous" "$old"; fi
if [ "$CHANGED" = 1 ]; then
    rm -rf "$stage"
    mkdir "$stage"
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
        test "$(stat -c %s "$stage/$part.img")" = "$bytes"
    done
    printf '%s\n' "$EXPECTED" > "$stage/expected"
    (cd "$stage"; sha256sum -c expected)
    # A concurrent OTA/root tool must not turn the snapshot into a mixed set.
    for part in boot init_boot vendor_boot dtbo; do
        test "$(sha256sum /dev/block/by-name/$part | cut -d ' ' -f1)" = "$(sha256sum "$stage/$part.img" | cut -d ' ' -f1)"
    done
    printf '%s\n' "$SYSTEM_NAME" > "$stage/name.txt"
    rm "$stage/expected"
    sync
    # Retain any prior rollback generation; the current `$old` will become the
    # new immediate `.previous` only after this captured set passed readback.
    if [ -d "$previous" ]; then
        stamp=$(date +%s)
        archive=$backup/$SET_ID.previous.$stamp
        suffix=0
        while [ -e "$archive" ]; do
            suffix=$((suffix + 1))
            archive=$backup/$SET_ID.previous.$stamp.$suffix
        done
        mv "$previous" "$archive"
        sync
    fi
    if [ -d "$old" ]; then mv "$old" "$previous"; fi
    mv "$stage" "$old"
    sync
    echo 'Android: backup updated and verified; previous copy retained.'
else
    printf '%s\n' "$SYSTEM_NAME" > "$old/name.txt.new"
    mv "$old/name.txt.new" "$old/name.txt"
    echo 'Android: backup label corrected.'
fi
