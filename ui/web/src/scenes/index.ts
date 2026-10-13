import type { ComponentType } from "react";
import Hero from "./Hero";
import Problem from "./Problem";
import HowItWorks from "./HowItWorks";
import LiveEditor from "./LiveEditor";
import TwoKernels from "./TwoKernels";
import PowerLossProof from "./PowerLossProof";
import { C1, C2, C3, C4, C5 } from "./Challenges";
import TestDashboard from "./TestDashboard";
import Closing from "./Closing";
import type { SceneProps } from "./types";

export interface SceneDef {
  id: string;
  title: string;
  blurb: string;
  component: ComponentType<SceneProps>;
}

export const SCENES: SceneDef[] = [
  { id: "hero", title: "Pull the plug. Lose nothing.", blurb: "AtomicDocs · headline numbers", component: Hero },
  { id: "problem", title: "The problem", blurb: "Cut power during a naive save vs AtomicDocs", component: Problem },
  { id: "editor", title: "Try it yourself", blurb: "Autosaving editor · simulated Power Off · Restart", component: LiveEditor },
  { id: "how", title: "How it works", blurb: "The write path; pull the plug at any syscall", component: HowItWorks },
  { id: "kernels", title: "Two kernels, one protocol", blurb: "Linux vs Windows native calls, caveats, timing", component: TwoKernels },
  { id: "powerloss", title: "Power-loss proof", blurb: "Every legal post-crash disk state", component: PowerLossProof },
  { id: "c1", title: "KO-04-C1 · Commit-point crash matrix", blurb: "Crash before / after the CURRENT rename", component: C1 },
  { id: "c2", title: "KO-04-C2 · Lost-update rejection", blurb: "Compare-and-swap, N-process race", component: C2 },
  { id: "c3", title: "KO-04-C3 · Retention under read pressure", blurb: "GC keeps 3, bounded reader retry", component: C3 },
  { id: "c4", title: "KO-04-C4 · Failure after commit before flush", blurb: "fsync(dir) EIO, outcome unknown", component: C4 },
  { id: "c5", title: "KO-04-C5 · Interrupted recovery", blurb: "Kill recovery, rerun twice, same disk", component: C5 },
  { id: "tests", title: "Test dashboard", blurb: "Run every suite live", component: TestDashboard },
  { id: "closing", title: "What it takes", blurb: "OS concepts & limitations", component: Closing },
];
