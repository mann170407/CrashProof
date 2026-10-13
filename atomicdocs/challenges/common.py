"""
Shared helpers for the KO-04 live-challenge scripts (c1_*.py .. c5_*.py).

Every challenge script:
  - builds its own fresh store in a temp directory (never touches real data),
  - prints a readable trace of on-disk state changes (step / show_state),
  - records named checks with check(), and
  - ends with finish(), which prints a summary, writes results/<id>.json and
    exits 0 only if every check passed.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import atomicdocs as ad  # noqa: E402

TOOL = os.path.join(ROOT, "atomicdocs.py")
RESULTS_DIR = os.path.join(HERE, "results")

_checks = []
_trace = []
_tmpdirs = []


def new_root(prefix="ko04-"):
    """A fresh, empty store root path inside a new temp directory."""
    d = tempfile.mkdtemp(prefix=prefix)
    _tmpdirs.append(d)
    return d + "/store"


def step(msg):
    line = "\n== " + msg
    print(line)
    _trace.append(msg)


def note(msg):
    print("   " + msg)
    _trace.append("  " + msg)


def describe_bytes(b, width=24):
    """Short human description of a document body, e.g. 64B 'AAAA...'."""
    if b is None:
        return "<none>"
    if not b:
        return "0B"
    if len(set(b)) == 1:
        return "%dB of 0x%02X (%r)" % (len(b), b[0], chr(b[0]))
    return "%dB %r%s" % (len(b), b[:width].decode("latin-1"), "..." if len(b) > width else "")


def show_state(root, doc, label=None):
    """Print the document directory, the CURRENT pointer and the journal."""
    ddir = root + "/docs/" + doc
    if label:
        note(label)
    if not os.path.isdir(ddir):
        note("  (no directory for %r)" % doc)
        return
    for n in sorted(os.listdir(ddir)):
        p = os.path.join(ddir, n)
        extra = ""
        if n == "CURRENT":
            try:
                extra = "  -> " + open(p, "rb").read().decode("ascii", "replace").strip()
            except OSError as e:
                extra = "  (unreadable: %s)" % e
        note("  %-28s %8d B%s" % (n, os.path.getsize(p), extra))
    jp = root + "/journal.wal"
    if os.path.exists(jp):
        _, recs, _, status = ad.scan_journal(open(jp, "rb").read())
        summary = ", ".join("%s(%s)" % (r["t"], "v%d->v%d" % (r["old"], r["new"]) if r["t"] == "BEGIN"
                                         else r.get("why", "")) for r in recs) or "empty"
        note("  journal.wal: %s [%s]" % (summary, status))


def check(name, ok, detail=""):
    _checks.append({"check": name, "pass": bool(ok), "detail": detail})
    print("   %s %s%s" % ("PASS" if ok else "FAIL", name, ("  - " + detail) if detail else ""))
    return ok


def finish(challenge_id, title):
    passed = sum(c["pass"] for c in _checks)
    total = len(_checks)
    ok = total > 0 and passed == total
    print("\n%s %s: %d/%d checks passed -> %s" % (challenge_id, title, passed, total,
                                                  "PASS" if ok else "FAIL"))
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(os.path.join(RESULTS_DIR, challenge_id + ".json"), "w") as f:
        json.dump({"id": challenge_id, "title": title, "pass": ok,
                   "passed": passed, "total": total, "checks": _checks}, f, indent=1)
    for d in _tmpdirs:
        shutil.rmtree(d, ignore_errors=True)
    sys.exit(0 if ok else 1)
