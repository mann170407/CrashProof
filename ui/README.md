# AtomicDocs Crash Lab: presentation UI

A keyboard-driven React deck (13 scenes) that runs the real AtomicDocs store live on **Windows**
and **Linux (WSL2)** and animates every system call it makes.

## Run it for the presentation

```powershell
python ui/server.py      # Windows backend + the built UI on :8765
# optional, second terminal: Linux backend in WSL2 on :8766
wsl -d Ubuntu -- bash /mnt/c/Users/Mann/OneDrive/Desktop/CrashProof/ui/start_linux_backend.sh
```

Then open **http://127.0.0.1:8765** and press **F** for fullscreen.

- `ui/server.py` serves the prebuilt `ui/web/dist`. If you changed the frontend, rebuild first:
  `cd ui/web && npm run build`.
- Nothing is loaded from the internet: fonts are bundled.
- If a backend is not running, its data comes from recorded fixtures
  (`ui/web/public/fixtures/<platform>/`) and is labelled **REPLAY**. If neither the backend nor a
  fixture is available, a built-in simulation is used and labelled **SIM** (or **OFFLINE** in the
  header). **LIVE** always means the real store just ran.

## Keyboard

| Key | Action |
|---|---|
| `→` / `PageDown`, `←` / `PageUp` | next / previous scene |
| `1` … `9`, `0`, `-`, `=` | jump to scene 1 … 9, 10, 11, 12 (scene 13: `End`) |
| `Home` / `End` | first / last scene |
| `G` (or `Esc` to close) | overview of all scenes |
| `F` | fullscreen |
| `C` | Compare mode: run on Linux **and** Windows side by side |
| `R` | run the scene's live experiment (on Power-loss proof: re-run the enumeration) |
| `Space` | timeline play / pause (Problem scene: save, cut power, again) |
| `,` / `.` | step the timeline back / forward |
| `I` | challenge scenes: switch between the timeline and the insight view |
| `B` | hide / show the result banner |
| `T` | Power-loss proof: switch the per-syscall chart between AtomicDocs and naive |

The Windows | Linux switch in the header picks the backend; the choice is remembered. Each
backend shows its own status dot: LIVE, REPLAY or OFFLINE.

## Scenes

1. **Pull the plug. Lose nothing.** Headline numbers from `/api/results`.
2. **The problem.** Naive `O_TRUNC` save vs AtomicDocs. Hit **CUT POWER**; the outcome comes from a real run at the matching crash point.
3. **Try it yourself.** An editor that autosaves through the same five-step write path (in-browser model, `src/lib/editorDisk.ts`). **Power Off** is simulated: the UI collapses, the screen stays black for exactly 5 s, then the Crash Proof logo and **Restart** appear; recovery restores the last committed text and cursor. *Cut power at* 01–05 makes the next save die at that step (04 → old text, 05 → new text).
4. **How it works.** The 11-step write path. Click any step to pull the plug there and replay the trace, recovery included.
5. **Two kernels, one protocol.** op → native call on Linux vs Windows, caveats, live timing.
6. **Power-loss proof.** `/api/powerloss`: dot grids of every crash state, and states per syscall with the commit point marked.
7. **C1 to C5 (scenes 7 to 11).** The live KO-04 challenges: timeline player (swimlanes + disk view) and an insight view: the commit-point matrix, the N-process race, retention and the paused reader, outcome unknown, and idempotence fingerprints.
8. **Test dashboard (scene 12).** Streams `/api/tests/stream` (all 8 suites, or C1–C5 only) on Linux, Windows or both.
9. **What it takes (scene 13).** OS concepts and limitations.

## Develop

Node 20+ is required. On this machine, put it on `PATH` first:
`export PATH="/c/Users/Mann/AppData/Local/nodejs/node-v24.21.0-win-x64:$PATH"`

```bash
cd ui/web
npm install
npm run dev        # http://localhost:5173 ; proxies /api -> :8765 and /api-linux -> :8766
npm run build      # type-check + production build into ui/web/dist
npm run preview    # serve dist on :4173 (same proxies)
```

Backend URLs are set in one place, `src/config.ts`. You can also override them per page with
`?windows=<url>&linux=<url>`, for example
`http://127.0.0.1:8765/?linux=http://192.168.1.20:8766/api`.

Code map: `src/api.ts` (typed client and LIVE → REPLAY → SIM fallback), `src/types.ts`
(contract types), `src/scenes/*`, `src/components/*` (TimelinePlayer, Swimlanes, DiskView,
DotGrid, PowerCut, …), `src/mocks/*` (offline simulation). See `ui/DESIGN.md` for the design
references and the design system.
