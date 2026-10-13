# Crash Proof

**Pull the plug. Lose nothing.**

AtomicDocs is a crash-consistent document store. Cut the power at any instant during a save and every document is exactly its old version or its new one: never a mixture, and never lost once a save is acknowledged.

Team Crashproof: Mann Sharma · Adarsh Pillai · Purumalla Shreeyans. Kernel Ops · KO-04 · OSH021.

## How it works

A save never overwrites the only copy of a document:

1. Journal the intent (`BEGIN` → `journal.wal`, fsync).
2. Write the new version to a temp file, fsync it, rename it to `vNNNNNN.dat`, fsync the directory.
3. Write a new `CURRENT` pointer (with the version's SHA-256) to a temp file and fsync it.
4. **Rename it over `CURRENT`. This rename is the single commit point.**
5. Fsync the directory, then write `COMMIT` and garbage-collect (keep the newest 3 versions).

On open, recovery trusts only bytes that verify. A crash before the commit point recovers the old version; a crash after it recovers the new one. Recovery is idempotent, so interrupting it and re-running it gives the same disk.

It runs on Linux and on Windows, with each system call mapped to its equivalent:

| Linux | Windows |
|---|---|
| `flock` | `LockFileEx` |
| `rename(2)` | `MoveFileExW` with write-through |
| `fsync` on a directory | `FlushFileBuffers` on a directory handle |

## Results

| Test | Result |
|---|---|
| Simulated power loss at every system call (SimFS, ALICE-style) | 417 distinct disk states, 0 corrupt |
| Real process kills at every crash point | 320 kills, 0 corrupt |
| Injected I/O faults, corruption, concurrency | 21 / 21 pass |
| KO-04 challenges C1–C5 | 5 / 5 pass, 238 checks |

Tested on Windows 11 (NTFS) and on Linux under WSL2 (ext4). Logs are in [`atomicdocs/logs/`](atomicdocs/logs/).

The cost of durability is speed: a save takes about 19.3 ms vs 2.6 ms for a naive overwrite on Linux ext4, and 26 ms vs 1.6 ms on Windows NTFS.

## Repository layout

| Path | What it is |
|---|---|
| [`atomicdocs/`](atomicdocs/) | The store, the power-loss explorer, the fault and crash tests, and the challenges C1–C5 |
| [`ui/server.py`](ui/server.py) | Backend that runs the real store live and records every system call it makes |
| [`ui/web/`](ui/web/) | The Crash Lab presentation: a React deck with 13 scenes |
| [`start_demo.bat`](start_demo.bat) | Starts both backends and opens the deck |
| [`DEMO_GUIDE.md`](DEMO_GUIDE.md) | Presenter run-of-show |
| [`demo_screenshots/`](demo_screenshots/) | Screenshots of every scene |

## Run the tests

Python 3 only, no dependencies:

```bash
cd atomicdocs
python run_all.py
```

## Run the presentation

The built deck isn't checked in, so build it once (Node 20+):

```bash
cd ui/web
npm install
npm run build
```

Then double-click **`start_demo.bat`**. It starts the Windows backend on `:8765` and the Linux backend inside WSL2 on `:8766`, then opens `http://127.0.0.1:8765`. Press **F** for fullscreen and **→** to move between scenes.

Without WSL, the Windows backend alone works: `python ui/server.py`. Scenes that need a backend which isn't running replay recorded results and are labelled **REPLAY**.

### Try it yourself (scene 3)

An editor that autosaves through the same write path as you type. **Power Off** is simulated: it closes the app, not the computer. The screen stays black for 5 seconds, then the Crash Proof logo appears with a **Restart** button. Restart brings back the last committed text and the cursor position. Use **Cut power at** to make the next save die at a chosen step: before the commit point you get the old text back, after it the new text.

The editor is an in-browser model of the protocol. Scenes 6–12 run the real store code.

## Limitations

- Transactions cover a single document only.
- Local file systems only, not NFS or SMB shares.
- Assumes the drive honours flush commands. The power-loss proof is simulated (SimFS), not a real cord pull; the process-kill tests are real.
