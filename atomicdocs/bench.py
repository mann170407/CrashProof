#!/usr/bin/env python3
"""Cost of crash safety: AtomicDocs.put vs naive write+fsync vs naive write (no fsync)."""
import os, sys, tempfile, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atomicdocs as ad
HERE = os.path.dirname(os.path.abspath(__file__))
N = 200
res = {}
for size in (1_000, 100_000):
    data = os.urandom(size)
    root = tempfile.mkdtemp() + "/s"; s = ad.AtomicDocs(root)
    t = time.perf_counter()
    for _ in range(N): s.put("d", data)
    res["atomicdocs_%dB_ms" % size] = round((time.perf_counter() - t) / N * 1000, 3)
    p = os.path.join(os.path.dirname(root), "naive")
    t = time.perf_counter()
    for _ in range(N):
        with open(p, "wb") as f: f.write(data); f.flush(); os.fsync(f.fileno())
    res["naive_fsync_%dB_ms" % size] = round((time.perf_counter() - t) / N * 1000, 3)
    t = time.perf_counter()
    for _ in range(N):
        with open(p, "wb") as f: f.write(data)
    res["naive_nofsync_%dB_ms" % size] = round((time.perf_counter() - t) / N * 1000, 3)
print(json.dumps(res, indent=1))
json.dump(res, open(os.path.join(HERE, "bench.json"), "w"), indent=1)
