#!/usr/bin/env python3
"""
Crash-injection test harness for AtomicDocs.

For every crash point, repeatedly:
  1. commit a known OLD version,
  2. start a child process that writes a NEW version and is killed
     (os._exit) at that crash point,
  3. reopen the store (runs recovery),
  4. check the document equals OLD or NEW byte-for-byte.
Then runs the same crash against a naive in-place writer for comparison.
NOTE: a process kill keeps the kernel page cache, so this tests process
crashes. Power loss is tested by crash_explorer.py; faults by fault_test.py.
"""
import hashlib, json, os, random, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import atomicdocs as ad

TOOL = os.path.join(HERE, "atomicdocs.py")
TRIALS = int(os.environ.get("TRIALS", "40"))
rng = random.Random(2026)


def payload(tag, n):
    # structured content so a mixture is detectable: every line names its version
    line = ("%s|" % tag).encode()
    return (line * (n // len(line) + 1))[:n]


def child_put(root, doc, data, crash):
    env = dict(os.environ, ATOMICDOCS_CRASH_AT=crash or "")
    return subprocess.run([sys.executable, TOOL, root, "put", doc, "-"],
                          input=data, env=env, capture_output=True).returncode


def crash_matrix():
    rows = []
    for point in ad.CRASH_POINTS:
        res = {"point": point, "old": 0, "new": 0, "corrupt": 0, "trials": TRIALS}
        for i in range(TRIALS):
            root = tempfile.mkdtemp(prefix="adocs-") + "/s"
            s = ad.AtomicDocs(root)
            old = payload("OLD-%d" % i, rng.randint(10, 200_000))
            new = payload("NEW-%d" % i, rng.randint(10, 200_000))
            s.put("report", old)
            rc = child_put(root, "report", new, point)
            assert rc == 137, (point, rc)
            got = ad.AtomicDocs(root).get("report")       # recovery runs here
            if got == old: res["old"] += 1
            elif got == new: res["new"] += 1
            else: res["corrupt"] += 1
            shutil.rmtree(os.path.dirname(root))
        rows.append(res)
    return rows


def naive_matrix():
    res = {"old": 0, "new": 0, "corrupt": 0, "trials": TRIALS}
    for i in range(TRIALS):
        d = tempfile.mkdtemp(prefix="naive-"); path = os.path.join(d, "report.txt")
        old = payload("OLD-%d" % i, rng.randint(10, 200_000))
        new = payload("NEW-%d" % i, rng.randint(10, 200_000))
        open(path, "wb").write(old)
        env = dict(os.environ, ATOMICDOCS_CRASH_AT="torn_data")
        subprocess.run([sys.executable, TOOL, d, "naive-put", path, "-"], input=new, env=env)
        got = open(path, "rb").read()
        if got == old: res["old"] += 1
        elif got == new: res["new"] += 1
        else: res["corrupt"] += 1
        shutil.rmtree(d)
    return res


if __name__ == "__main__":
    out = {"crash_matrix": crash_matrix(), "naive": naive_matrix()}
    print("\nAtomicDocs crash matrix (%d trials per point)" % TRIALS)
    print("%-22s %6s %6s %8s" % ("crash point", "old", "new", "corrupt"))
    for r in out["crash_matrix"]:
        print("%-22s %6d %6d %8d" % (r["point"], r["old"], r["new"], r["corrupt"]))
    n = out["naive"]
    print("%-22s %6d %6d %8d   <- naive in-place write" % ("naive torn write", n["old"], n["new"], n["corrupt"]))
    json.dump(out, open(os.path.join(HERE, "results.json"), "w"), indent=1)
    bad = sum(r["corrupt"] for r in out["crash_matrix"])
    # A process kill keeps the page cache, so the outcome is deterministic:
    # crash before the CURRENT rename -> OLD, from the rename on -> NEW.
    committed = set(ad.CRASH_POINTS[ad.CRASH_POINTS.index("after_pointer_rename"):])
    covered = [r["point"] for r in out["crash_matrix"]]
    wrong = ["%s: %d old / %d new" % (r["point"], r["old"], r["new"]) for r in out["crash_matrix"]
             if r["new" if r["point"] in committed else "old"] != r["trials"]]
    if covered != ad.CRASH_POINTS:
        wrong.append("crash points not all covered: %s" % covered)
    for w in wrong:
        print("UNEXPECTED OUTCOME", w)
    print("\nRESULT:", "PASS - 0 corrupted documents; all %d crash points x %d trials recovered to the expected version"
          % (len(covered), TRIALS) if not bad and not wrong else "FAIL - %d corrupted, %d unexpected" % (bad, len(wrong)))
    sys.exit(1 if bad or wrong else 0)
