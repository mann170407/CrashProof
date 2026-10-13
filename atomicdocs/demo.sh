#!/usr/bin/env bash
# Live demo: kill a write at the worst moment, then show recovery.
# Runs on Linux, macOS and Windows (Git Bash). PYTHON=... overrides the interpreter.
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
"$PY" -c "import sys" 2>/dev/null || PY=python
WORK=$(mktemp -d)
ROOT=$WORK/store
V2=$WORK/v2.txt
OUT=$WORK/out.txt
echo "== 1. Commit version 1"
echo "Quarterly report v1: revenue 100" | "$PY" atomicdocs.py "$ROOT" put report -
"$PY" atomicdocs.py "$ROOT" get report

echo; echo "== 2. Write version 2 but CRASH halfway through the data file (torn write)"
# binary stdout: identical bytes on every OS (no \r\n translation on Windows)
"$PY" -c "import sys; sys.stdout.buffer.write(b'Quarterly report v2: revenue 250 '*2000 + b'\n')" > "$V2"
ATOMICDOCS_CRASH_AT=torn_data "$PY" atomicdocs.py "$ROOT" put report "$V2" || echo "   process died with exit code $?"
echo "   files on disk right after the crash:"; ls -la "$ROOT/docs/report" | sed 's/^/   /'

echo; echo "== 3. Reopen -> recovery rolls the torn write back"
"$PY" atomicdocs.py "$ROOT" recover
"$PY" atomicdocs.py "$ROOT" get report

echo; echo "== 4. Write version 2 and CRASH right after the commit point (before COMMIT is logged)"
ATOMICDOCS_CRASH_AT=after_pointer_rename "$PY" atomicdocs.py "$ROOT" put report "$V2" || echo "   process died with exit code $?"
echo; echo "== 5. Reopen -> recovery rolls forward to the new version"
"$PY" atomicdocs.py "$ROOT" recover
# read into a file first: piping straight into head can kill the writer with EPIPE
"$PY" atomicdocs.py "$ROOT" get report > "$OUT"
head -c 70 "$OUT"; echo " ..."
if cmp -s "$OUT" "$V2"; then echo "   (byte-for-byte identical to v2)"; else echo "   MISMATCH with v2"; exit 1; fi
"$PY" atomicdocs.py "$ROOT" history report

echo; echo "== 6. Same torn write against a naive in-place writer"
printf 'Quarterly report v1: revenue 100\n' > "$WORK/naive.txt"
ATOMICDOCS_CRASH_AT=torn_data "$PY" atomicdocs.py "$ROOT" naive-put "$WORK/naive.txt" "$V2" || true
echo "   naive file is now $(wc -c < "$WORK/naive.txt" | tr -d ' ') bytes of a $(wc -c < "$V2" | tr -d ' ')-byte document: neither v1 nor v2"
rm -rf "$WORK"
