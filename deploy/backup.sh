#!/bin/sh
# Periodic backup of the surveillance database (service "backup" of deploy/compose.yml).
#
# Every BACKUP_INTERVAL_SECONDS it writes $BACKUP_DIR/surveillance-<UTC time>.dump (pg_dump custom format,
# restore it with pg_restore) and keeps the newest BACKUP_KEEP files. A dump is written under a temporary
# name, checked by reading its table of contents back, and only then renamed, so a half-written or broken
# file never takes the place of a good one. The connection comes from the PG* environment variables.
set -eu

dir=${BACKUP_DIR:-/backups}
interval=${BACKUP_INTERVAL_SECONDS:-86400}
keep=${BACKUP_KEEP:-7}
retry=300   # seconds to wait after a failed dump (the database may be restarting)

case $interval in ''|*[!0-9]*|0) echo "backup: BACKUP_INTERVAL_SECONDS must be a positive integer, got '$interval'" >&2; exit 2;; esac
case $keep in ''|*[!0-9]*|0) echo "backup: BACKUP_KEEP must be a positive integer, got '$keep'" >&2; exit 2;; esac

mkdir -p "$dir"
[ -w "$dir" ] || { echo "backup: $dir is not writable by uid $(id -u)" >&2; exit 1; }

# a sleep that ends at once when the container is stopped
trap 'exit 0' TERM INT
nap() { sleep "$1" & wait $!; }

# leftovers of a run that was killed in the middle of a dump
rm -f "$dir"/.surveillance-*.tmp

while true; do
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    tmp="$dir/.surveillance-$stamp.tmp"
    if pg_dump -Fc -f "$tmp" && pg_restore -l "$tmp" > /dev/null; then
        mv "$tmp" "$dir/surveillance-$stamp.dump"
        echo "backup: wrote surveillance-$stamp.dump ($(wc -c < "$dir/surveillance-$stamp.dump") bytes)"
        # names sort by time, so everything after the newest $keep is old
        ls -1r "$dir"/surveillance-*.dump | tail -n +"$((keep + 1))" | while read -r old; do
            rm -f "$old" && echo "backup: removed $(basename "$old")"
        done
        nap "$interval"
    else
        rm -f "$tmp"
        echo "backup: dump failed, trying again in ${retry}s" >&2
        nap "$retry"
    fi
done
