#!/usr/bin/env bash
#
# Container entrypoint. Downloads the pinned jars on first boot, then replaces
# itself with `mc start` so the JVM is a direct child of PID 1 and receives
# `docker stop` signals properly.
set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

# The image sets these, but default them so the entrypoint also works when run
# straight out of a checkout.
MC_USER="${MC_USER:-minecraft}"
MC_SERVER_DIR="${MC_SERVER_DIR:-$APP_DIR/server}"
MC_BACKUP_DIR="${MC_BACKUP_DIR:-$APP_DIR/backups}"

# `./server` and `./backups` are bind mounts, so Docker creates them on the host
# owned by root -- which the unprivileged user the server runs as cannot write.
# Fix ownership, then hand the rest of the process to that user, so the JVM
# never runs as root.
#
# MC_DROPPED guards the re-exec: if gosu ever handed back a still-privileged
# process we would otherwise loop forever chowning our own image.
if [[ "$(id -u)" == "0" && "${MC_DROPPED:-0}" != "1" ]]; then
  # gosu ships in the image. Running as root on a host means you invoked the
  # container entrypoint by hand, which is not what it is for.
  if ! command -v gosu >/dev/null 2>&1; then
    echo " err gosu is not installed -- this entrypoint is for the container image." >&2
    echo "     On a host, run ./mc directly. See README." >&2
    exit 1
  fi

  for dir in "$MC_SERVER_DIR" "$MC_BACKUP_DIR"; do
    mkdir -p "$dir"
    chown "$MC_USER":"$MC_USER" "$dir" 2>/dev/null \
      || echo " warn could not chown $dir; mount it with matching ownership" >&2
  done
  export MC_DROPPED=1
  exec gosu "$MC_USER" "$0" "$@"
fi

if [[ "${1:-start}" == "start" && $# -le 1 ]]; then
  # MC_ASSUME_YES so a fresh container cannot block on the install prompt.
  # `mc install` still refuses to continue while eula.txt is false, which is
  # the prompt that actually matters.
  if [[ ! -f "$MC_SERVER_DIR/paper.jar" ]]; then
    MC_ASSUME_YES=1 ./mc install
  fi
  exec ./mc start
fi

exec "$@"