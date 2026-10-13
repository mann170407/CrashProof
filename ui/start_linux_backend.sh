#!/usr/bin/env bash
# start_linux_backend.sh - run the Crash Lab backend on the Linux kernel (WSL2).
#
#   wsl -d Ubuntu -- bash /mnt/c/Users/Mann/OneDrive/Desktop/CrashProof/ui/start_linux_backend.sh
#
# 1. syncs the CODE (atomicdocs/ + ui/*.py) from the Windows checkout into
#    ~/crashproof on ext4. The backend must never run from /mnt/c: drvfs is a
#    9p network share, not a Linux file system, and fsync/rename there say
#    nothing about Linux. Results/logs written by Linux runs stay in ~/crashproof.
# 2. starts ui/server.py there on port 8766, API only (the Windows backend
#    serves the UI; CORS is *). Bound to 127.0.0.1: WSL2 localhost forwarding
#    makes it reachable from the Windows browser at http://127.0.0.1:8766
#    (verified; HOST=0.0.0.0 also works if forwarding is ever disabled).
#
# Overrides: CRASHPROOF_SRC, CRASHPROOF_DST, PORT, HOST.
set -euo pipefail

SRC="${CRASHPROOF_SRC:-/mnt/c/Users/Mann/OneDrive/Desktop/CrashProof}"
DST="${CRASHPROOF_DST:-$HOME/crashproof}"
PORT="${PORT:-8766}"
HOST="${HOST:-127.0.0.1}"

case "$DST" in
  /mnt/*) echo "refusing to run from $DST: use an ext4 path (e.g. \$HOME/crashproof)" >&2; exit 2 ;;
esac

echo "syncing code  $SRC  ->  $DST"
mkdir -p "$DST/atomicdocs/challenges" "$DST/ui"
# code only: *.py / *.sh / *.md / *.html; never results, logs or caches from Windows
( cd "$SRC/atomicdocs" && find . -type d \( -name __pycache__ -o -name logs -o -name results \) -prune -o \
    -type f \( -name '*.py' -o -name '*.sh' -o -name '*.md' -o -name '*.html' \) -print0 ) |
  while IFS= read -r -d '' f; do
    mkdir -p "$DST/atomicdocs/$(dirname "$f")"
    cp -p "$SRC/atomicdocs/$f" "$DST/atomicdocs/$f"
  done
cp -p "$SRC"/ui/*.py "$DST/ui/"
cp -p "$SRC"/ui/*.sh "$DST/ui/" 2>/dev/null || true

# stop an older copy of this backend still holding the port
pkill -f "ui/server.py --host .* --port $PORT" 2>/dev/null || true
sleep 0.3

cd "$DST"
echo "kernel: $(uname -r)   fs of $DST: $(stat -f -c %T "$DST")   python: $(python3 --version 2>&1)"
exec python3 -u ui/server.py --host "$HOST" --port "$PORT" --no-static
