#!/bin/sh
# Optional: run as PUID/PGID (Unraid default 99:100) so files in appdata belong to that user.
set -e
mkdir -p "${RFP_DATA:-/data}"
if [ -n "$PUID" ] && [ -n "$PGID" ] && [ "$(id -u)" = "0" ]; then
  chown -R "$PUID:$PGID" "${RFP_DATA:-/data}" 2>/dev/null || true
  export HOME=/tmp
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi
exec "$@"
