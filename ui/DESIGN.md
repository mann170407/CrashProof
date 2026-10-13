# AtomicDocs Crash Lab: design notes

A keyboard-driven, full-viewport presentation for a dark projector. Each scene is a live
experiment on the real store code, not a slide.

## References and what we took from each

| Reference | What we took |
|---|---|
| Bartosz Ciechanowski, *Mechanical Watch*: https://ciechanow.ski/mechanical-watch/ | Show the mechanism one part at a time. Each interactive figure has one control (scrubber or step), a text caption tied to the current moment of the animation, and the same colour for a part in the text and in the figure. This gave us the timeline scrubber plus caption, and a fixed colour per lane and per version letter. |
| Josh W. Comeau, *A Friendly Introduction to Spring Physics*: https://www.joshwcomeau.com/animation/a-friendly-introduction-to-spring-physics/ | Use springs, not easing curves, for anything that physically moves (the CURRENT pointer swinging, cards entering, the flip of a test card). Use a little bounce for meaning, never for decoration. Do not use springs for opacity or colour. |
| Dan Luu, *Files are hard* / file consistency: https://danluu.com/file-consistency/ | The story arc of the problem scene: an in-place overwrite tears, write ordering is not guaranteed without fsync, and the directory itself must be fsynced. |
| Pillai et al., *All File Systems Are Not Created Equal* (ALICE, OSDI '14): https://www.usenix.org/conference/osdi14/technical-sessions/presentation/pillai | The power-loss proof is drawn the way ALICE reasons: one dot per legal post-crash disk state, grouped per system call, so the commit point is visible as the moment the colour flips from A to B. |
| Jepsen timelines (`jepsen.checker.timeline`): https://cljdoc.org/d/jepsen/jepsen/0.1.17/api/jepsen.checker.timeline | One column per process (swimlanes), time flowing downwards, each operation drawn as a block in its lane, and anomalies (crash, fault) spanning every lane. |
| Linear marketing-site design system (third-party breakdowns, e.g. https://www.shadcn.io/design/linear/raw) | A near-black canvas with surfaces stepped by white opacity rather than hue, hairline borders at 5 to 9% white, Inter with tight negative tracking at display sizes, and one restrained accent (indigo) used only for the thing that matters: here, the commit point. |
| Vercel Geist colours: https://vercel.com/geist/colors | Give every scale step a job: 100 to 300 backgrounds, 400 to 600 borders, 900 secondary text, 1000 primary text. Our `ink-*` ladder and the `fg / fg-2 / fg-3` text tokens follow that rule. |
| Stripe, *Designing accessible color systems*: https://stripe.com/blog/accessible-color-systems | Pick categorical colours by measured perceptual contrast. We validated the A/B/corrupt palette with a CVD and contrast script (see below), and identity never relies on colour alone. |
| Motion (Framer Motion) layout animations: https://motion.dev/docs/react-layout-animations | `layoutId` for the CURRENT pointer (a single element that moves between version cards), `AnimatePresence` with `popLayout` for files created and unlinked, and `MotionConfig reducedMotion="user"`. |

## Design system

**Scale.** The root font size is `min(1.0417vw, 1.852vh)`, so `1rem = 20px` at 1920x1080 and
everything (type, spacing, cards) scales as one with the viewport. Body text is at least 20px at
1080p, and the deck still fits a 1366x768 laptop.

**Palette (CSS tokens in `src/index.css` `@theme`)**

| Role | Token | Value |
|---|---|---|
| canvas / surfaces | `ink-950 … ink-600` | `#07080b` `#0b0d12` `#10131a` `#151924` `#1d2230` `#2a3142` |
| hairlines | `line`, `line-strong` | white at 9% and 18% |
| text | `fg`, `fg-2`, `fg-3` | `#f3f5f9`, `#b4bccd`, `#7a8398` |
| version A (old) | `va` / `va-text` | `#3b93f5` / `#8cc2ff` |
| version B (new) | `vb` / `vb-text` | `#cc7e14` / `#ffc266` |
| corrupt | `vx` / `vx-text` | `#f0436a` / `#ff8fa8` plus a cross glyph and 45° hatching |
| writer / brand | `brand` | `#8b7bff` |
| commit point | `commit` | `#b9a8ff`, with a glow |
| recovery | `recover` | `#2dd4bf` |
| reader | `reader` | `#f472b6` |
| status | `pass` / `fail` / `warn` | `#3ddc97` / `#ff5d73` / `#ffb547`, always shown with ✓ / ✗ / text |

The A/B/X mark colours pass the dataviz validator in dark mode (lightness band, chroma, contrast
≥ 3:1). The CVD separation between rose and amber is in the 6 to 8 ΔE "floor" band, which is
allowed only with secondary encoding, so corrupt is always drawn as a cross with hatching.

**Type.** Inter Variable (UI) and JetBrains Mono (syscalls, file names, native calls). Both are
bundled through `@fontsource`, so there are no network fonts. Sizes at 1080p:

| Use | Size |
|---|---|
| hero | 7.2rem (144px), tracking -0.045em |
| scene title | 2.6rem (52px) |
| card numbers | 2.6 to 3.4rem |
| lead | 1.1 to 1.45rem |
| body | 1rem (20px) |
| labels | 0.7 to 0.85rem (14 to 17px), mono or uppercase |

**Spacing.** Tailwind's 0.25rem scale. Scene gutter is 2.4rem, panel padding 1.25 to 1.5rem, grid gaps 1 to 1.25rem.

**Motion.**

| Element | Motion |
|---|---|
| entrances | 0.5 to 0.7s, `cubic-bezier(0.16, 1, 0.3, 1)` (expo out), staggered 40 to 120ms |
| scene change | 0.55s slide (±90px) and fade |
| layout and cards | spring, stiffness 420, damping 34 |
| CURRENT pointer swing | spring, stiffness 140, damping 15 (slight overshoot, so the commit is felt) |
| power cut | 120ms white flash, then about 1s of blackout with flicker and a "POWER CUT" stamp |
| recovery | 1.6s teal sweep |
| timeline playback (at 1x) | 850ms per syscall, 2.3s on a crash, 2s on the commit point, 0.5 to 4x speed |

With `prefers-reduced-motion`, motion is reduced through `MotionConfig`, the CSS animations are
disabled, and the power cut becomes a plain dim.

**Encodings.** A file that is not durable is drawn translucent with a dashed border and tagged
"page cache" or "dir entry". It turns solid with a green pulse on fsync. CURRENT is a pointer
chip that physically moves between version cards. A crash shows a red CRASH stamp, a fault
shows an amber EIO stamp, and recovery shows a teal border and sweep.

## Components

`App` (shell: progress rail, scene index, overview `G`, fullscreen `F`, hash routing),
`BackendSwitch` (Windows | Linux with LIVE / REPLAY / OFFLINE per backend), `TimelinePlayer`
(shared playback, single or synchronized Compare, result banner), `Swimlanes`, `DiskView`,
`DotGrid`, `PowerCutOverlay`, `SectorField` (canvas hero background), `CountUp`, `SceneKit`
(`ChallengeScene`, `useRunner`, `CompareToggle`, `LoadingVeil`), `ui` (Button, Segmented, Chip,
StatusDot, SourceBadge, OsMark, SceneTitle, Panel).
