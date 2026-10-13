#!/usr/bin/env python3
"""
run_all.py - run every AtomicDocs test suite and the five KO-04 live challenges,
then print one pass/fail table.

    python run_all.py                 # everything (crash_test at 40 trials/point)
    python run_all.py --quick         # crash_test at 5 trials/point
    python run_all.py --challenges    # only the five KO-04 challenges
    python run_all.py c3 c5           # only the named challenges

Each entry runs as its own process; full output goes to logs/<name>.log.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")

SUITES = [
    ("crash_explorer", ["crash_explorer.py"], "simulated power loss at every syscall"),
    ("fault_test", ["fault_test.py"], "injected I/O faults, corruption, concurrency"),
    ("crash_test", ["crash_test.py"], "real process kill at every crash point"),
]
CHALLENGES = [
    ("c1", ["challenges/c1_commit_point.py"], "KO-04-C1 commit-point crash matrix"),
    ("c2", ["challenges/c2_lost_update.py"], "KO-04-C2 lost-update rejection"),
    ("c3", ["challenges/c3_retention_read_pressure.py"], "KO-04-C3 retention under read pressure"),
    ("c4", ["challenges/c4_post_commit_fsync.py"], "KO-04-C4 failure after commit before flush"),
    ("c5", ["challenges/c5_interrupted_recovery.py"], "KO-04-C5 interrupted recovery"),
]


def run(name, script, env):
    os.makedirs(LOGS, exist_ok=True)
    log = os.path.join(LOGS, name + ".log")
    t0 = time.time()
    with open(log, "wb") as f:
        rc = subprocess.run([sys.executable] + script, cwd=HERE, env=env,
                            stdout=f, stderr=subprocess.STDOUT).returncode
    return rc, time.time() - t0, log


def last_line(log):
    with open(log, "rb") as f:
        lines = [l.strip() for l in f.read().decode("utf-8", "replace").splitlines() if l.strip()]
    return lines[-1][:90] if lines else ""


def main(args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if "--quick" in args:
        env["TRIALS"] = "5"
    names = [a for a in args if not a.startswith("--")]
    if names:
        todo = [c for c in CHALLENGES + SUITES if c[0] in names]
    elif "--challenges" in args:
        todo = CHALLENGES
    else:
        todo = SUITES + CHALLENGES

    rows, failed = [], 0
    for name, script, what in todo:
        print("running %-15s %s ..." % (name, what), flush=True)
        rc, secs, log = run(name, script, env)
        failed += rc != 0
        rows.append((name, "PASS" if rc == 0 else "FAIL (rc=%d)" % rc, secs, last_line(log)))

    print("\n%-15s %-12s %7s  %s" % ("test", "result", "secs", "last line of output"))
    print("-" * 100)
    for name, res, secs, tail in rows:
        print("%-15s %-12s %7.1f  %s" % (name, res, secs, tail))
    print("\n%d/%d passed. Full logs in %s" % (len(rows) - failed, len(rows), LOGS))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
