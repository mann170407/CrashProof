#!/usr/bin/env python3
"""
crash_explorer.py - simulated POWER-LOSS testing for AtomicDocs.

For every point in a put() (after each system call), enumerate the legal
disk states a power cut could leave (unsynced writes lost, sectors torn,
directory entries not yet durable), materialise each one as a real directory,
run the REAL recovery code on it, and check:

    atomicity   : the document reads as exactly OLD or exactly NEW
    durability  : if put() had returned before the crash, it reads as NEW
    liveness    : the store accepts a new write afterwards

Also runs the naive writer through the same explorer, and a mutation test that
deletes each fsync in turn to show which ones the guarantees depend on.

    python3 crash_explorer.py            # full run, writes explorer_results.json
    python3 crash_explorer.py --quick
"""
import json, os, shutil, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import atomicdocs as ad
from simfs import SimFS

WORK = tempfile.mkdtemp(prefix="explorer-")
QUICK = "--quick" in sys.argv
DOC = "report"


def payload(tag, n):
    unit = ("%s|" % tag).encode()
    return (unit * (n // len(unit) + 1))[:n]


def fresh_store(prior):
    """A durable starting store holding the given committed versions."""
    d = tempfile.mkdtemp(dir=WORK) + "/store"
    s = ad.AtomicDocs(d)
    for body in prior:
        s.put(DOC, body)
    ad.AtomicDocs(d)                         # reopen = recovery + checkpoint
    return d


def classify(path, allowed):
    """allowed: list of acceptable byte strings (None = document absent)."""
    try:
        s = ad.AtomicDocs(path)
        got = s.get(DOC)
    except Exception as e:                   # recovery or read failed
        return "ERROR:" + type(e).__name__, None, None
    idx = next((i for i, a in enumerate(allowed) if got == a), None)
    try:                                     # liveness: store still usable
        s.put(DOC, b"after-recovery")
        alive = ad.AtomicDocs(path).get(DOC) == b"after-recovery"
    except Exception:
        alive = False
    return ("CORRUPT" if idx is None else "OK"), idx, alive


def explore(seq, prior=(), model="W1", skip=(), samples=150, keep_trace=False):
    """Explore power loss during a sequence of put() calls.
    Atomicity: the document is one of prior[-1] / seq[0] / seq[1] ...
    Durability: once seq[i]'s put() has returned, the result is seq[i] or later."""
    prior = list(prior)
    old = prior[-1] if prior else None
    allowed = [old] + list(seq)
    base = fresh_store(prior)
    sim = SimFS(base, rename_model=model)
    store = ad.AtomicDocs(base, fs=sim, auto_recover=False, skip=skip)
    for body in seq:
        store.put(DOC, body)
        sim.mark_ack()
    points = [("start", 0)] + sim.syscalls
    seen, res = {}, {"OLD": 0, "NEW": 0, "CORRUPT": 0, "ERROR": 0,
                     "DURABILITY_VIOLATION": 0, "DEAD": 0, "states": 0, "checks": 0}
    trace, modes = [], set()
    for label, k in points:
        must = max([i + 1 for i, a in enumerate(sim.acks) if a <= k], default=0)
        row = {"after": label, "k": k, "acked": must > 0, "OLD": 0, "NEW": 0, "BAD": 0, "states": 0}
        for mode, nopt, st in sim.crash_states(k, samples=samples):
            modes.add(mode)
            fp = st.fingerprint(sim.root)
            if fp not in seen:
                dest = tempfile.mkdtemp(dir=WORK) + "/store"
                sim.materialize(st, dest)
                seen[fp] = classify(dest, allowed)
                shutil.rmtree(os.path.dirname(dest))
                outcome, idx, alive = seen[fp]
                res["states"] += 1
                if outcome == "OK":
                    res["OLD" if idx == 0 else "NEW"] += 1
                elif outcome == "CORRUPT":
                    res["CORRUPT"] += 1
                else:
                    res["ERROR"] += 1
                if outcome == "OK" and not alive:
                    res["DEAD"] += 1
            outcome, idx, alive = seen[fp]
            res["checks"] += 1
            if outcome == "OK" and idx < must and (fp, must) not in seen:
                seen[(fp, must)] = True
                res["DURABILITY_VIOLATION"] += 1
            row["states"] += 1
            row["optional"] = nopt
            if outcome == "OK":
                row["OLD" if idx == 0 else "NEW"] += 1
            else:
                row["BAD"] += 1
        trace.append(row)
    shutil.rmtree(os.path.dirname(base))
    res["crash_points"] = len(points)
    res["micro_ops"] = len(sim.ops)
    res["mode"] = "/".join(sorted(modes))
    return (res, trace) if keep_trace else res


def explore_crash_then_recovery(point, old, new, samples=150):
    """A process dies at a crash point (its writes sit in the page cache, not
    yet durable), the store is reopened and recovery serves a value, then the
    power fails. Checks: during recovery -> OLD or NEW; once recovery has
    returned and served a value, every power-loss state must give that value."""
    base = fresh_store([payload("V0", 300), old])
    sim = SimFS(base)
    ad._CRASH_AT = point
    try:
        ad.AtomicDocs(base, fs=sim, auto_recover=False).put(DOC, new)
    except Exception:
        pass
    finally:
        ad._CRASH_AT = None
    r0 = len(sim.ops)
    served = ad.AtomicDocs(base, fs=sim).get(DOC)       # recovery runs inside the simulator
    r1 = len(sim.ops)
    res = {"point": point, "served": "OLD" if served == old else "NEW" if served == new else "CORRUPT",
           "states": 0, "bad": 0, "flipped_after_served": 0}
    seen = {}
    for label, k in [(l, k) for l, k in sim.syscalls if k >= r0] + [("end", r1)]:
        for mode, nopt, st in sim.crash_states(k, samples=samples):
            fp = st.fingerprint(sim.root)
            if fp not in seen:
                dest = tempfile.mkdtemp(dir=WORK) + "/store"
                sim.materialize(st, dest)
                try:
                    got = ad.AtomicDocs(dest).get(DOC)
                except Exception:
                    got = b"<error>"
                shutil.rmtree(os.path.dirname(dest))
                seen[fp] = got
                res["states"] += 1
            got = seen[fp]
            if got not in (old, new):
                res["bad"] += 1
            elif k >= r1 and got != served and (fp, "f") not in seen:
                seen[(fp, "f")] = True
                res["flipped_after_served"] += 1
    shutil.rmtree(os.path.dirname(base))
    return res


def explore_naive(old, new, do_fsync, samples=150, keep_trace=False):
    d = tempfile.mkdtemp(dir=WORK) + "/naive"
    os.makedirs(d)
    with open(d + "/report.txt", "wb") as f:
        f.write(old); f.flush(); os.fsync(f.fileno())
    sim = SimFS(d)
    ad.naive_put(sim, d + "/report.txt", new, do_fsync=do_fsync)
    sim.mark_ack()
    ack_at = sim.acks[0]
    res = {"OLD": 0, "NEW": 0, "CORRUPT": 0, "DURABILITY_VIOLATION": 0, "states": 0}
    trace, seen = [], set()
    for label, k in [("start", 0)] + sim.syscalls:
        row = {"after": label, "k": k, "acked": k >= ack_at, "OLD": 0, "NEW": 0, "BAD": 0, "states": 0}
        for mode, nopt, st in sim.crash_states(k, samples=samples):
            got = st.reachable_files(d).get("report.txt", b"")
            o = "OLD" if got == old else "NEW" if got == new else "CORRUPT"
            row["states"] += 1; row["optional"] = nopt
            row[o if o != "CORRUPT" else "BAD"] += 1
            fp = st.fingerprint(d)
            if fp not in seen:
                seen.add(fp)
                res[o] += 1; res["states"] += 1
            if k >= ack_at and o != "NEW" and (fp, "dv") not in seen:
                seen.add((fp, "dv"))
                res["DURABILITY_VIOLATION"] += 1
        trace.append(row)
    shutil.rmtree(d)
    return (res, trace) if keep_trace else res


def main():
    t0 = time.time()
    out = {}
    v0 = payload("V0", 300)
    small_old, small_new = payload("OLD", 700), payload("NEW", 1300)   # few sectors: exhaustive
    out["ours_W1_small"], out["trace_ours"] = explore([small_new], [v0, small_old], keep_trace=True)
    out["ours_W0_small"] = explore([small_new], [v0, small_old], model="W0")
    out["ours_first_put"] = explore([small_new], [])                       # document did not exist
    out["ours_gc"] = explore([small_new], [v0, payload("V1", 400), small_old])   # triggers GC
    out["ours_two_puts"] = explore([payload("A", 600), payload("B", 900)], [v0, small_old], samples=60)
    if not QUICK:
        big_old, big_new = payload("OLD", 9000), payload("NEW", 21000)
        out["ours_W1_large"] = explore([big_new], [v0, big_old], samples=120)
        out["ours_W0_large"] = explore([big_new], [v0, big_old], model="W0", samples=120)
    out["naive_nofsync"], out["trace_naive"] = explore_naive(small_old, small_new, False, keep_trace=True)
    out["naive_fsync"] = explore_naive(small_old, small_new, True)
    out["crash_then_recovery"] = [explore_crash_then_recovery(p, small_old, small_new)
                                  for p in ad.CRASH_POINTS]
    out["mutations"] = {}
    for m in ("data_fsync", "data_dirsync", "ptr_fsync", "ptr_dirsync", "journal_fsync"):
        out["mutations"][m] = explore([small_new], [v0, small_old], skip=(m,))
    ad.VERIFY = False                        # negative control: checksums off
    out["mutations"]["data_fsync+no_checksums"] = explore([small_new], [v0, small_old], skip=("data_fsync",))
    ad.VERIFY = True
    out["seconds"] = round(time.time() - t0, 1)
    json.dump(out, open(os.path.join(HERE, "explorer_results.json"), "w"), indent=1)

    def line(name, r):
        print("%-26s states=%-5d OLD=%-5d NEW=%-5d CORRUPT=%-4d ERR=%-3d acked-lost=%-3d dead=%d"
              % (name, r["states"], r["OLD"], r["NEW"], r["CORRUPT"], r.get("ERROR", 0),
                 r["DURABILITY_VIOLATION"], r.get("DEAD", 0)))
    print("\nSimulated power loss (distinct post-crash disk states; NEW = any newer version)")
    for k in out:
        if k.startswith("ours_") or k.startswith("naive_"):
            line(k, out[k])
    print("\nProcess crash, then recovery, then power loss")
    for r in out["crash_then_recovery"]:
        print("  %-22s served=%-4s states=%-4d bad=%d changed-after-served=%d"
              % (r["point"], r["served"], r["states"], r["bad"], r["flipped_after_served"]))
    print("\nMutation test: remove one fsync, re-explore")
    for m, r in out["mutations"].items():
        line("  without " + m, r)
    ours = [out[k] for k in out if k.startswith("ours_")]
    bad = sum(r["CORRUPT"] + r["ERROR"] + r["DURABILITY_VIOLATION"] + r["DEAD"] for r in ours)
    bad += sum(r["bad"] + r["flipped_after_served"] for r in out["crash_then_recovery"])
    total = sum(r["states"] for r in ours) + sum(r["states"] for r in out["crash_then_recovery"])
    out["total_states"] = total
    json.dump(out, open(os.path.join(HERE, "explorer_results.json"), "w"), indent=1)
    print("\nRESULT:", "PASS" if bad == 0 else "FAIL", "- %d distinct states checked in %.0fs" % (total, out["seconds"]))
    shutil.rmtree(WORK, ignore_errors=True)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
