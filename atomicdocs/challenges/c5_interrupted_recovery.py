#!/usr/bin/env python3
"""
KO-04-C5  Interrupted recovery (Hard)

  "Interrupt recovery at a chosen state-changing operation and rerun recovery
   twice. Preserve a whole accepted version and consistent cleanup."
  Setup: v1=A plus uncommitted v2=B and leftover temp; interrupt
         cleanup/repair; rerun twice.  Focus: prove recovery idempotence.

What this script does
  Part A  (process kill, real files, real child processes)
     For every crash point inside recovery (ad.RECOVERY_CRASH_POINTS) that the
     scenario reaches: build the store, run
        ATOMICDOCS_CRASH_AT=<point> python atomicdocs.py <root> recover
     which dies with os._exit(137) right after that state-changing step;
     interrupt it AGAIN at the same point; then rerun recovery twice with no
     fault. Checks: the accepted version is byte-exact after every rerun,
     uncommitted versions and temp files are gone, CURRENT names the accepted
     version and verifies, the journal was reset, rerun 2 changes nothing
     (identical on-disk fingerprint, empty report) and the result equals an
     uninterrupted recovery of the same starting state.
       S1  the organisers' setup: v1=A committed, uncommitted v2=B, leftover
           temp files                                   -> expected A (v1)
       S2  S1 + CURRENT torn + v2 torn (simulated media faults), so recovery
           must REWRITE CURRENT                         -> expected A (v1)
       S3  S1 + CURRENT torn, v2=B intact               -> expected B (v2)
     Part A2: two documents, so the same crash point really fires twice in a
     row (first in one document, then in the other) before the clean reruns.
  Part B  (simulated power loss DURING recovery, SimFS)
     Record recovery's micro-ops, enumerate every legal post-power-loss disk
     state at every syscall boundary, materialise each one and run the real
     recovery twice on it.

Run:  python challenges/c5_interrupted_recovery.py   (or from inside challenges/)
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from common import (ad, TOOL, new_root, step, note, show_state, describe_bytes,
                    check, finish)
from simfs import SimFS, SimCrash

ID, TITLE = "KO-04-C5", "Interrupted recovery"
DOC = "report"
A = b"\x41" * 64
B = b"\x42" * 64
WORK = os.path.dirname(new_root("ko04-c5-"))        # cleaned up by finish()

os.environ.pop("ATOMICDOCS_CRASH_AT", None)          # never crash this process
ad._CRASH_AT = None

P_TMP, P_VER, P_PTR, P_CKPT = ad.RECOVERY_CRASH_POINTS
SHORT = {P_TMP: "after tmp unlink", P_VER: "after version unlink",
         P_PTR: "after CURRENT rewrite", P_CKPT: "before checkpoint"}

# Scenario table: what is on disk before recovery, the accepted truth recovery
# must converge to, and which recovery crash points the scenario reaches.
SCENARIOS = {
    "S1": dict(
        title="v1=A committed + uncommitted v2=B + leftover temps (organisers' setup)",
        tear_current=False, tear_v2=False, truth=1, body=A,
        reach={P_TMP: True, P_VER: True, P_PTR: False, P_CKPT: True},
        why="CURRENT is readable and names v1, whose SHA-256 verifies, so v1=A is the "
            "committed truth. v2 is above what CURRENT names: it was never committed "
            "(the crashed put died before its CURRENT rename), so it is rolled back. "
            "CURRENT is already right, so the pointer-rewrite step is never needed."),
    "S2": dict(
        title="S1 + CURRENT torn + v2 torn (simulated media faults)",
        tear_current=True, tear_v2=True, truth=1, body=A,
        reach={P_TMP: True, P_VER: True, P_PTR: True, P_CKPT: True},
        why="CURRENT does not parse, so the rule is 'newest version whose SHA-256 "
            "verifies'. v2 is torn (fails length/SHA-256) and is not a whole version; "
            "v1=A verifies, so A is the truth: v2 is deleted and CURRENT is rewritten -> v1."),
    "S3": dict(
        title="S1 + CURRENT torn, v2=B intact",
        tear_current=True, tear_v2=False, truth=2, body=B,
        reach={P_TMP: True, P_VER: False, P_PTR: True, P_CKPT: True},
        why="CURRENT does not parse, so the commit record is lost and the store cannot "
            "prove v2 was NOT committed (a crash just after the CURRENT rename followed "
            "by a damaged CURRENT looks the same). The rule picks the newest version "
            "that fully verifies: v2=B, a whole version written and fsynced by put() and "
            "declared in the journal BEGIN v1->v2. Accepting B is atomic (never a mix); "
            "CURRENT is rewritten -> v2 and B is reported as rolled forward. Nothing is "
            "above v2, so the version-unlink step is never needed."),
}

STRAY_DATA_TMP = ".v000003.dat.4242.0badf00d.tmp"     # torn temp of an older crashed writer
STRAY_JNL_TMP = "journal.wal.4242.0badf00d.tmp"        # crash inside _new_journal


# ----------------------------------------------------------------- helpers
def fresh_root():
    return tempfile.mkdtemp(dir=WORK) + "/store"


def cli(root, cmd, point=None, stdin=None):
    env = dict(os.environ)
    env.pop("ATOMICDOCS_CRASH_AT", None)
    if point:
        env["ATOMICDOCS_CRASH_AT"] = point
    return subprocess.run([sys.executable, TOOL, root] + cmd, input=stdin, env=env,
                          capture_output=True)


def recover_cli(root, point=None):
    """Returns (exit code, report dict or None)."""
    r = cli(root, ["recover"], point)
    rep = None
    if r.returncode == 0:
        try:
            rep = json.loads(r.stdout.decode().strip().splitlines()[-1])
        except (ValueError, IndexError):
            rep = None
    return r.returncode, rep


def build(scn, docs=(DOC,)):
    """Create the pre-recovery disk state for scenario scn. Returns root."""
    cfg = SCENARIOS[scn]
    root = fresh_root()
    s = ad.AtomicDocs(root)
    for d in docs:
        s.put(d, A)                                              # v1 = A, committed
    for i, d in enumerate(docs):
        # v2 = B, uncommitted: a real child put killed just before the CURRENT
        # rename. The CLI runs recovery on open, which would clean up the
        # documents crashed before; so further docs use a child that opens the
        # store WITHOUT recovery and is killed at the same point.
        if i == 0:
            rc = cli(root, ["put", d, "-"], "before_pointer_rename", B).returncode
        else:
            env = dict(os.environ, ATOMICDOCS_CRASH_AT="before_pointer_rename")
            code = ("import sys; sys.path.insert(0, %r); import atomicdocs as ad; "
                    "ad.AtomicDocs(%r, auto_recover=False).put(%r, sys.stdin.buffer.read())"
                    % (os.path.dirname(TOOL), root, d))
            rc = subprocess.run([sys.executable, "-c", code], input=B, env=env,
                                capture_output=True).returncode
        assert rc == 137, "setup: crashed put exited %r" % rc
    for d in docs:
        dd = root + "/docs/" + d
        frame, _ = ad.encode_version(d, 3, b"\x43" * 64)
        with open(dd + "/" + STRAY_DATA_TMP, "wb") as f:         # extra leftover temp
            f.write(frame[: len(frame) // 2])
        if cfg["tear_v2"]:                                       # fault: v2 loses its tail
            p = dd + "/" + ad._vname(2)
            raw = open(p, "rb").read()
            with open(p, "wb") as f:
                f.write(raw[: len(raw) // 2])
        if cfg["tear_current"]:                                  # fault: CURRENT torn
            p = dd + "/CURRENT"
            raw = open(p, "rb").read()
            with open(p, "wb") as f:
                f.write(raw[: len(raw) // 2])
    with open(root + "/" + STRAY_JNL_TMP, "wb") as f:
        f.write(ad.JHDR.pack(ad.JMAGIC, ad.FORMAT, 7))
    return root


def fingerprint(root):
    """SHA-256 over (relative name, bytes) of every file under root, excluding
    journal.wal (its salt is fresh at every checkpoint) and *.lock files."""
    h, names = hashlib.sha256(), []
    for dp, dns, fns in os.walk(root):
        dns.sort()
        for fn in sorted(fns):
            rel = os.path.relpath(os.path.join(dp, fn), root).replace("\\", "/")
            if rel == "journal.wal" or fn.endswith(".lock"):
                continue
            names.append(rel)
            h.update(rel.encode() + b"\0" + open(os.path.join(dp, fn), "rb").read() + b"\1")
    return h.hexdigest()[:16], names


def tmp_files(root):
    return [os.path.relpath(os.path.join(dp, f), root).replace("\\", "/")
            for dp, _, fns in os.walk(root) for f in fns if f.endswith(".tmp")]


def versions(root, doc=DOC):
    return sorted(int(n[1:7]) for n in os.listdir(root + "/docs/" + doc) if ad._is_vname(n))


def read_doc(root, doc=DOC):
    """Read without running recovery (auto_recover=False changes nothing)."""
    try:
        return ad.AtomicDocs(root, auto_recover=False).get(doc)
    except Exception as e:                              # noqa: BLE001
        return "<%s: %s>" % (type(e).__name__, e)


def pointer_ok(root, truth, body, doc=DOC):
    """CURRENT parses, names `truth`, and its SHA-256 matches the version bytes."""
    try:
        dd = root + "/docs/" + doc
        v, sha = ad.decode_pointer(open(dd + "/CURRENT", "rb").read())
        got, _ = ad.decode_version(doc, v, open(dd + "/" + ad._vname(v), "rb").read(), sha)
        return v == truth and got == body, "CURRENT -> v%d sha %s..." % (v, sha[:12])
    except Exception as e:                              # noqa: BLE001
        return False, "CURRENT bad: %s" % e


def journal_reset(root):
    _, recs, _, status = ad.scan_journal(open(root + "/journal.wal", "rb").read())
    return status == "ok" and not recs, "status=%s records=%d" % (status, len(recs))


def truth_present(root, truth, body, doc=DOC):
    """The accepted version file still exists and verifies by its own SHA-256."""
    try:
        got, _ = ad.decode_version(doc, truth, open(root + "/docs/" + doc + "/" + ad._vname(truth), "rb").read())
        return got == body
    except Exception:                                   # noqa: BLE001
        return False


def fmt_report(rep):
    if rep is None:
        return "(no report: process died)"
    return ("rolled_forward=%s rolled_back=%s tmp_removed=%d fell_back=%s degraded=%s journal=%s"
            % (rep["rolled_forward"], rep["rolled_back"], rep["tmp_removed"],
               [("v%s" % f["to"]) for f in rep["fell_back"]], len(rep["degraded"]), rep["journal"]))


def empty_report(rep):
    return (rep is not None and rep["tmp_removed"] == 0 and not rep["rolled_back"]
            and not rep["rolled_forward"] and not rep["fell_back"] and not rep["degraded"])


def reference(scn_root):
    """Fingerprint of an UNINTERRUPTED recovery of a copy of the same state."""
    ref = fresh_root()
    shutil.copytree(scn_root, ref)
    rc, _ = recover_cli(ref)
    assert rc == 0
    return fingerprint(ref)[0]


# ----------------------------------------------------------------- Part A
def part_a_run(scn, point):
    cfg = SCENARIOS[scn]
    truth, body, reach = cfg["truth"], cfg["body"], cfg["reach"][point]
    tag = "%s/%s" % (scn, SHORT[point])
    step("%s: interrupt recovery at %s" % (scn, point))
    root = build(scn)
    ref_fp = reference(root)
    show_state(root, DOC, "before recovery (root also holds %s):" % STRAY_JNL_TMP)

    rc1, _ = recover_cli(root, point)
    note("recover #1 with ATOMICDOCS_CRASH_AT=%s -> exit %d" % (point, rc1))
    check("%s: recovery killed at the crash point (exit 137)" % tag, rc1 == 137, "exit %d" % rc1)
    show_state(root, DOC, "after the kill (partial cleanup; root tmp %s):"
               % ("removed" if not os.path.exists(root + "/" + STRAY_JNL_TMP) else "still there"))
    ok1 = truth_present(root, truth, body)

    # interrupt again at the SAME point
    again = point == P_CKPT                     # the only point every run reaches
    if again:
        rc2, _ = recover_cli(root, point)
        note("recover #2 with the SAME crash point -> exit %d (killed again before the checkpoint)" % rc2)
        show_state(root, DOC, "after the second kill (journal still holds the old BEGIN):")
        check("%s: killed again at the same point (exit 137)" % tag, rc2 == 137, "exit %d" % rc2)
    else:
        # In a one-document store this point cannot fire twice: the step it
        # follows has nothing left to do. Prove that on a COPY (so the main
        # path below still shows rerun 1 finishing the job); A2 shows a real
        # double kill with two documents.
        cp = fresh_root()
        shutil.copytree(root, cp)
        rc2, rep2 = recover_cli(cp, point)
        note("recover #2 with the SAME crash point (on a copy) -> exit %d: not reached again, the "
             "step before it is already done and is not redone; that run completed: %s"
             % (rc2, fmt_report(rep2)))
        check("%s: second attempt at the same point finds nothing to redo and converges" % tag,
              rc2 == 0 and fingerprint(cp)[0] == ref_fp and read_doc(cp) == body, "exit %d" % rc2)
    ok2 = truth_present(root, truth, body)
    check("%s: accepted v%d (whole, SHA-256 ok) survived the interruptions" % (tag, truth), ok1 and ok2)

    # two clean reruns
    rcA, repA = recover_cli(root)
    note("rerun 1 (no fault) -> exit %d" % rcA)
    note("  report: " + fmt_report(repA))
    show_state(root, DOC, "after rerun 1:")
    gotA, fpA = read_doc(root), fingerprint(root)
    check("%s: rerun 1 -> get() == %s exactly" % (tag, "A" if body == A else "B"), rcA == 0 and gotA == body,
          describe_bytes(gotA) if isinstance(gotA, bytes) else str(gotA))

    rcB, repB = recover_cli(root)
    note("rerun 2 (no fault) -> exit %d" % rcB)
    note("  report: " + fmt_report(repB))
    show_state(root, DOC, "after rerun 2:")
    gotB, fpB = read_doc(root), fingerprint(root)
    check("%s: rerun 2 -> get() == %s exactly" % (tag, "A" if body == A else "B"), rcB == 0 and gotB == body,
          describe_bytes(gotB) if isinstance(gotB, bytes) else str(gotB))
    vs = versions(root)
    expect_vs = [1] if truth == 1 else [1, 2]
    check("%s: only committed versions remain %s (nothing above v%d)" % (tag, ["v%d" % v for v in expect_vs], truth),
          vs == expect_vs, "on disk: %s" % ["v%d" % v for v in vs])
    check("%s: no .tmp file anywhere" % tag, not tmp_files(root), str(tmp_files(root)))
    pok, pdet = pointer_ok(root, truth, body)
    check("%s: CURRENT names v%d and verifies" % (tag, truth), pok, pdet)
    jok, jdet = journal_reset(root)
    check("%s: journal reset (no open BEGIN)" % tag, jok, jdet)
    check("%s: rerun 2 changed nothing (fingerprint rerun1 == rerun2)" % tag, fpA == fpB,
          "%s vs %s, files %s" % (fpA[0], fpB[0], fpB[1]))
    check("%s: rerun 2 report empty (0 tmp, nothing rolled back/forward)" % tag, empty_report(repB))
    check("%s: converged to the same disk as an uninterrupted recovery" % tag, fpB[0] == ref_fp,
          "%s vs reference %s" % (fpB[0], ref_fp))


def compact(root, docs):
    parts = []
    for d in docs:
        dd = root + "/docs/" + d
        files = [n for n in sorted(os.listdir(dd)) if not n.endswith(".lock")]
        parts.append("%s{%s}" % (d, ", ".join(files)))
    return "  ".join(parts)


def part_a2_run(scn, point):
    """Two documents: the same crash point fires in 'notes', then (on the
    rerun) again in 'report'; then two clean reruns."""
    cfg, docs = SCENARIOS[scn], ("notes", DOC)
    truth, body = cfg["truth"], cfg["body"]
    tag = "A2 %s/%s" % (scn, SHORT[point])
    root = build(scn, docs)
    ref_fp = reference(root)
    note("[%s @ %s] before:  %s" % (scn, point, compact(root, docs)))
    rcs = []
    for i in (1, 2):
        rc, _ = recover_cli(root, point)
        rcs.append(rc)
        note("  kill #%d -> exit %d   %s" % (i, rc, compact(root, docs)))
    check("%s: killed at the SAME point twice in a row" % tag, rcs == [137, 137], "exits %s" % rcs)
    fps, reps = [], []
    for i in (1, 2):
        rc, rep = recover_cli(root)
        reps.append(rep)
        fps.append(fingerprint(root)[0])
        note("  rerun %d -> exit %d   %s" % (i, rc, compact(root, docs)))
        note("     report: " + fmt_report(rep))
    good = all(read_doc(root, d) == body and pointer_ok(root, truth, body, d)[0] for d in docs)
    check("%s: both documents == %s, CURRENT -> v%d verifies, no tmp" % (tag, "A" if body == A else "B", truth),
          good and not tmp_files(root))
    check("%s: rerun 2 changed nothing and converged to the uninterrupted result" % tag,
          fps[0] == fps[1] == ref_fp and empty_report(reps[1]) and journal_reset(root)[0],
          "%s / %s / ref %s" % (fps[0], fps[1], ref_fp))


# ----------------------------------------------------------------- Part B

def sim_recovery(root, crash_at=None):
    sim = SimFS(root)
    st = ad.AtomicDocs(root, fs=sim, auto_recover=False)
    st.crash_at = crash_at
    try:
        rep = st.recover()
    except SimCrash:
        rep = None
    return sim, rep


def part_b(scn, verbose):
    cfg = SCENARIOS[scn]
    truth, body = cfg["truth"], cfg["body"]
    root = build(scn)
    sim, rep = sim_recovery(root)
    marks = {}
    for p in ad.RECOVERY_CRASH_POINTS:                 # where each kill point sits
        s2, r2 = sim_recovery(root, p)
        if r2 is None:
            marks.setdefault(len(s2.syscalls), []).append(p)
    if verbose:
        note("recovery's recorded syscalls (SimFS), with the process-kill points of Part A:")
        for i, (label, k) in enumerate(sim.syscalls, 1):
            note("  %2d. %-46s micro-ops so far: %d" % (i, label, k))
            for p in marks.get(i, []):
                note("      <-- %s fires here" % p)
    t0 = time.time()
    seen, states, distinct, bad, modes, rows = {}, 0, 0, [], set(), []
    for label, k in [("start (nothing persisted)", 0)] + list(sim.syscalls):
        row = [label, k, 0, 0, 0, None]
        for mode, nopt, st in sim.crash_states(k, samples=60):
            modes.add(mode)
            states += 1
            row[2] += 1
            row[5] = "%s/%d opt" % (mode, nopt)
            fp = st.fingerprint(sim.root)
            if fp not in seen:
                distinct += 1
                row[3] += 1
                dest = tempfile.mkdtemp(dir=WORK) + "/store"
                sim.materialize(st, dest)
                why = None
                try:
                    s1 = ad.AtomicDocs(dest)
                    g1, f1 = s1.get(DOC), fingerprint(dest)[0]
                    s2 = ad.AtomicDocs(dest)
                    g2, f2, r2 = s2.get(DOC), fingerprint(dest)[0], s2.last_recovery
                    if g1 != body or g2 != body:
                        why = "document %r / %r" % (describe_bytes(g1), describe_bytes(g2))
                    elif tmp_files(dest):
                        why = "tmp left: %s" % tmp_files(dest)
                    elif f1 != f2 or not empty_report(r2):
                        why = "second run changed something: %s" % fmt_report(r2)
                    elif not pointer_ok(dest, truth, body)[0] or versions(dest) != ([1] if truth == 1 else [1, 2]):
                        why = "CURRENT/versions wrong"
                except Exception as e:                  # noqa: BLE001
                    why = "%s: %s" % (type(e).__name__, e)
                seen[fp] = why
                shutil.rmtree(os.path.dirname(dest), ignore_errors=True)
                if why:
                    bad.append((label, why))
            if seen[fp]:
                row[4] += 1
        rows.append(row)
    secs = time.time() - t0
    if verbose:
        note("%-48s %5s %6s %8s %4s  %s" % ("power lost after", "k", "states", "distinct", "bad", "enumeration"))
        for label, k, n, d, b, m in rows:
            note("%-48s %5d %6d %8d %4d  %s" % (label[:48], k, n, d, b, m))
    note("%s: %d syscalls, %d micro-ops, %d crash states explored, %d distinct, %d corrupted/non-idempotent (%s, %.1fs)"
         % (scn, len(sim.syscalls), len(sim.ops), states, distinct, len(bad), "/".join(sorted(modes)), secs))
    for label, why in bad[:5]:
        note("   BAD after %s: %s" % (label, why))
    tagb = "B %s" % scn
    check("%s: every power-loss state during recovery -> %s exactly, no tmp, rerun changes nothing"
          % (tagb, "A" if body == A else "B"), not bad and distinct > 0,
          "%d distinct states, %d bad" % (distinct, len(bad)))
    return len(sim.syscalls), states, distinct, len(bad), modes


# ----------------------------------------------------------------- main
def main():
    t0 = time.time()
    print("KO-04-C5 Interrupted recovery - prove that recovery is idempotent")
    print("""
OS mechanism (what makes rerunning converge)
  * Truth comes only from verified bytes: CURRENT (own CRC) naming a version whose
    SHA-256 verifies; if CURRENT is missing/torn, the newest version that verifies.
    The journal is used only for cleanup and reporting, never as the truth.
  * Every step is a deterministic function of what is on disk: delete *.tmp (never
    committed by construction), delete versions ABOVE the truth (provably never
    committed), rewrite CURRENT only if it does not already name the truth.
    A step whose work is done finds nothing to do on the next run.
  * The decision is flushed (fsync version file, fsync CURRENT, fsync the doc
    directory, fsync docs/) BEFORE the checkpoint replaces the journal with a fresh
    salt via temp+fsync+rename+dir-fsync. A kill or power cut before the checkpoint
    leaves the old journal (rerun reports the same txs again); after it, nothing is
    left to do. So run N+1 starts from a state run N would also have produced.
  * Reports describe what THAT run did: rolled_back/rolled_forward come from the
    journal and repeat until the checkpoint resets it; fell_back is reported only
    by the run that actually repaired CURRENT. The on-disk result is what must
    (and does) converge.
Assumptions: local FS with atomic rename and honoured flushes; process kills keep
the page cache (Part A); power loss is simulated by SimFS (ALICE-style, rename model
W1) in Part B; faults are injected test data, A = 64 x 0x41, B = 64 x 0x42.""")

    step("Scenarios and the expected accepted version")
    for scn, cfg in SCENARIOS.items():
        note("%s  %s" % (scn, cfg["title"]))
        note("    expected: v%d = %s.  %s" % (cfg["truth"], describe_bytes(cfg["body"]), cfg["why"]))
        note("    reaches: %s" % ", ".join("%s=%s" % (p, "yes" if r else "no") for p, r in cfg["reach"].items()))

    # verify reachability claims (cheap, in the simulator) before the live runs
    step("Which recovery crash points each scenario reaches (checked, not assumed)")
    for scn, cfg in SCENARIOS.items():
        root = build(scn)
        got = {p: sim_recovery(root, p)[1] is None for p in ad.RECOVERY_CRASH_POINTS}
        check("%s reaches exactly %s" % (scn, [SHORT[p] for p in got if got[p]]), got == cfg["reach"],
              "observed %s" % got)
    covered = {p for cfg in SCENARIOS.values() for p, r in cfg["reach"].items() if r}
    check("every recovery crash point is exercised by some scenario", covered == set(ad.RECOVERY_CRASH_POINTS))

    print("\n######## Part A: process kill during recovery (real child processes, exit 137)")
    for scn, cfg in SCENARIOS.items():
        for p in ad.RECOVERY_CRASH_POINTS:
            if cfg["reach"][p]:
                part_a_run(scn, p)

    print("\n######## Part A2: the same crash point fires twice in a row (two documents)")
    step("two docs ('notes', 'report'), each in the scenario's state; kill, kill again, rerun x2")
    for scn, p in (("S1", P_TMP), ("S1", P_VER), ("S2", P_PTR), ("S1", P_CKPT), ("S3", P_PTR)):
        part_a2_run(scn, p)

    print("\n######## Part B: power loss DURING recovery (SimFS), then real recovery x2")
    note("A process kill keeps the page cache; a power cut does not. Here every legal")
    note("post-power-loss disk (unsynced micro-ops lost in any combination) at every syscall")
    note("boundary of recovery is materialised and recovered twice with the real code.")
    note("(Kill points are prefixes of this same deterministic op sequence, so 'killed,")
    note("then power lost' states are included.)")
    tot = [0, 0, 0, 0]
    for scn in SCENARIOS:
        step("Part B, scenario %s (%s)" % (scn, SCENARIOS[scn]["title"]))
        r = part_b(scn, verbose=(scn == "S1"))
        for i in range(4):
            tot[i] += r[i]
    step("Part B totals")
    note("syscalls in recovery: %d, crash states explored: %d, distinct: %d, corrupted/non-idempotent: %d"
         % tuple(tot))
    note("(crash_states(): exhaustive when <= 10 optional micro-ops, else targeted + 60 random subsets)")
    note("total runtime %.0fs" % (time.time() - t0))
    finish(ID, TITLE)


if __name__ == "__main__":
    main()
