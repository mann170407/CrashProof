# Crash Proof: presenter run-of-show

About 11 minutes. Linux is the main stage, and Windows gets one short portability segment.

## Before you go on stage (5 min)

1. Double-click **`start_demo.bat`**. Two console windows open, one per backend. Leave them open.
2. The browser opens `http://127.0.0.1:8765`. In the header, both **Linux** and **Windows** should show a green dot. If either is missing, wait 10 s and refresh.
3. Click **Linux** in the header so the live runs use ext4.
4. Press **F** for fullscreen.
5. Warm-up: go to scene 12 (`=`), choose **C1–C5 only** and **Both**, then press **R**. It should finish 5/5 on both kernels in about 35 s.
6. Clear the editor: go to scene 3, select all the text in the editor and type a fresh first line. Leave **Cut power at** on **Manual**. Then press **Home**.

If a backend dies mid-talk, its scenes switch to recorded data labelled **REPLAY**, so keep talking. **LIVE** always means the real store just ran. The editor (scene 3) runs in the browser and never needs a backend.

## Keys

| Key | Action |
|---|---|
| `→` / `←` | next / previous scene |
| `1`…`9`, `0`, `-`, `=` | jump to scene 1…12 (scene 13: `End` or `→`) |
| `G` | overview grid of all scenes |
| `Space` | Problem scene: save → cut power → again. Elsewhere: play / pause the timeline |
| `R` | run the scene's live experiment |
| `I` | challenge scenes: timeline ↔ insight view |
| `C` | Compare mode: Linux and Windows side by side |
| `,` / `.` | step the timeline back / forward |
| `Enter` | on the black power-off screen: Restart |

While you type in the editor, the keys above are ignored. Click outside the text box to use them again.

## Run-of-show

| # | Scene | Do | Say |
|---|---|---|---|
| 1 | **Pull the plug. Lose nothing.** | Let the numbers count up. | "417 power-loss states, 320 real process kills, 21 fault scenarios, 238 challenge checks. Zero corrupted documents." |
| 2 | **The problem** | `Space` to start the save, `Space` again at about 30–50% to cut power. | "The naive save truncates first, so A is gone instantly and the document is corrupt. AtomicDocs never touched v1, so recovery rolls back to exactly A." |
| 3 | **Try it yourself** (editor) | Type a sentence, pause, and watch the save path tick through 01–05. Type again and press **Power Off** mid-save. Wait out the 5 s black, then press **Restart**. | "Power Off is simulated: it closes this app, not the laptop. That save was cut before the commit point, so you get the last committed text back, with the cursor where it was." Then choose **04** under *Cut power at*, type, restart: old text. Choose **05**, type, restart: new text. "Before the CURRENT rename, old. After it, new. Never a mixture." |
| 4 | **How it works** | Click a step, e.g. *rename CURRENT*, to crash there. | "One rename is the commit point. Before it the document recovers to A; after it, to B." |
| 5 | **Two kernels, one protocol** | Point at the mapping table. | "`flock` → `LockFileEx`, `rename(2)` → `MoveFileExW` with write-through, `fsync(dir)` → `FlushFileBuffers` on a directory handle. We listed Windows as future work, then built it." |
| 6 | **Power-loss proof** | `R` to re-run. `T` toggles the per-syscall chart. | "We enumerate every disk state a power cut could leave: lost unsynced writes, torn 512 B sectors, renames that aren't durable yet. Naive: 9 states, 7 corrupt. AtomicDocs: 27 states, 0 corrupt." |
| 7 | **C1 commit-point crash matrix** | `R`, then `I` for the matrix. | "We crash before and after the CURRENT rename, with a real child process." |
| 8 | **C2 lost-update rejection** | `R`. | "N processes race to write; compare-and-swap lets exactly one win, and the losers get a conflict instead of silently overwriting." |
| 9 | **C3 retention under read pressure** | `R`, then `I`. | "GC keeps 3 versions; a paused reader retries a bounded number of times and never reads a half-deleted file." |
| 10 | **C4 failure after commit, before flush** | `R`. | "`fsync(dir)` fails with EIO after the rename. We report *outcome unknown* rather than lie, and recovery settles it." |
| 11 | **C5 interrupted recovery** | `R`, and press `C` to show both kernels. | "We kill recovery itself half-way, re-run it twice, and get the identical disk." |
| 12 | **Test dashboard** | **C1–C5 only**, **Both**, `R`. | "Every suite, live, on two kernels, about 35 seconds." Use **All 8 suites** only if you have several minutes. |
| 13 | **What it takes** | — | OS concepts and limitations. |

## The editor in one breath

Each autosave runs the same five steps as the real store: write a temp copy → fsync it → rename it into place → **rename CURRENT (the commit point)** → fsync the directory. Power Off kills the save wherever it is. On Restart, recovery keeps the new version only if the CURRENT rename had already happened; otherwise it discards the half-done save and restores the previous one, with its cursor. Anything typed but not yet saved was only in memory, and the banner says how many characters were lost.

**If a judge asks:** the editor is an in-browser model of the protocol, built to make the idea visible. The proof that the real store behaves this way is scenes 6–12, which run the actual store code.

## Say these before a judge asks

- **Speed:** AtomicDocs is about 7× slower than a naive save: 19.3 ms vs 2.6 ms on Linux ext4, and 26 ms vs 1.6 ms on Windows NTFS. That's the price of the fsyncs; correctness is the point.
- **WSL2 `/tmp` is a RAM disk (tmpfs).** All Linux stores and test runs use the real ext4 disk instead, so the flushes are real.
- **The power-loss proof is a simulator (SimFS, ALICE-style).** You can't physically cut power hundreds of times on stage. The process-kill matrix (320 kills) is real.
- **Real Windows bugs we hit:** text mode turned `\n` into `\r\n` and broke the checksums, and Windows refuses to rename over a file a reader has open (fixed with `FILE_SHARE_DELETE`).

## If something goes wrong

- **A badge says REPLAY or OFFLINE:** the backend for that kernel stopped. Re-run `start_demo.bat` (running backends are left alone), or carry on, because the replay data is from real runs.
- **The page looks stuck:** press `F5`. Scene state resets; the editor's last committed save is kept.
- **Changed the frontend?** Rebuild before presenting: `cd ui/web && npm run build`.
