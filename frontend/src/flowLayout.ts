import type { CandidateOption, CandidateQuestion } from "./candidateView";

export type Point = { x: number; y: number };
export type BranchOption = CandidateOption & Point & { hidden?: number };
export const NODE_WIDTH = 176;
export const NODE_HALF_HEIGHT = 28;
export const OPTION_HALF_WIDTH = 82;

/** Horizontal routing with an independent transcript rail above it.
 * Main-node coordinates depend ONLY on viewport width, never candidate count.
 * Reserve a normal-size pool; dense requests grow downward without moving the loop. */
export function flowLayout(questions: CandidateQuestion[], availableWidth = 0) {
  // More columns when the panel has room, without reducing readable type.
  const columns = availableWidth > 0
    ? Math.min(4, Math.max(2, Math.floor((availableWidth / .9 - 822) / 230))) : 3;
  const poolLeft = 306;
  const columnWidth = 230;
  let rowY = 178;
  const groups = questions.map((question, index) => {
    if (index % columns === 0 && index > 0) {
      const previous = questions.slice(index - columns, index);
      rowY += Math.max(...previous.map((item) => Math.max(1, item.options.length))) * 30 + 32;
    }
    const baseX = poolLeft + (index % columns) * columnWidth;
    const midY = rowY + 22 + (Math.max(1, question.options.length) - 1) * 15;
    return { question,
      start: { x: baseX + 4, y: midY }, end: { x: baseX + 224, y: midY },
      options: question.options.map((option, i) => ({ ...option, x: baseX + 114, y: rowY + 22 + i * 30 })),
      label: { x: baseX + 114, y: rowY },
    };
  });
  const poolBottom = Math.max(470, ...groups.flatMap((g) => [g.label.y + 14, g.start.y + 4, g.end.y + 4, ...g.options.map((o) => o.y + 18)]));
  const poolRight = poolLeft + columns * columnWidth;
  const pickX = poolRight + 110;
  const commitX = pickX + 148;
  const kernelX = commitX + 146;
  const recordY = 520;
  return {
    width: kernelX + 112, height: Math.max(recordY + 52, poolBottom + 70),
    groups, poolLeft, poolRight, poolBottom, requestBusY: 152, selectionBusY: poolBottom + 28,
    // These are ports as well as visual centers; activation never moves them.
    transcript: { x: 660, y: 46 },
    jevProjection: { x: 116, y: 240 },
    jev: { x: 256, y: 240 },
    llmProjection: { x: pickX, y: 132 },
    exit: { x: pickX, y: 240 },
    llm: { x: pickX, y: 368 },
    commit: { x: commitX, y: 240 },
    kernel: { x: kernelX, y: 240 },
    record: { x: kernelX, y: recordY },
  };
}

export function curve(from: Point, to: Point): string {
  const middle = (from.y + to.y) / 2;
  return `M${from.x} ${from.y} C${from.x} ${middle} ${to.x} ${middle} ${to.x} ${to.y}`;
}

export function horizontalCurve(from: Point, to: Point): string {
  const middle = (from.x + to.x) / 2;
  return `M${from.x} ${from.y} C${middle} ${from.y} ${middle} ${to.y} ${to.x} ${to.y}`;
}

/** Context enters the top of the LLM node; authored progress leaves its right.
 * Keep those routes separate, including empty/single-head request geometry. */
export function contextWires(graph: ReturnType<typeof flowLayout>) {
  const { transcript: t, llmProjection: lp, llm: l, commit: c } = graph;
  return {
    transcriptToLlm: `M${t.x + 80} ${t.y + NODE_HALF_HEIGHT} V92 H${lp.x} V${lp.y - NODE_HALF_HEIGHT}`,
    contextToLlm: `M${lp.x + NODE_WIDTH / 2} ${lp.y} H${lp.x + 118} V${l.y - 48} H${l.x + 45} V${l.y - NODE_HALF_HEIGHT}`,
    acceptedProgress: `M${c.x} ${c.y} H${Math.max(c.x, t.x + 180)} V12 H${t.x} V${t.y - NODE_HALF_HEIGHT}`,
  };
}
