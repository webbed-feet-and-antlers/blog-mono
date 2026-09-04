import { useEffect, useState } from 'react';

/**
 * The ReAct (Reason + Act) loop behind interactive coding agents like Claude
 * Code or Cursor: prompt → reason → tool → output, back to reason. Step or
 * play through the cycle; the iteration counter shows the loop re-entering
 * reasoning after every tool result. Hand-rolled SVG — no charting library.
 */

interface RunState {
  stage: number; // 0 prompt · 1 reason · 2 tool · 3 output (the return edge)
  iteration: number;
}

const STAGES = [
  { label: 'User prompt', caption: 'The user states a goal in natural language — “fix the broken auth route”.' },
  { label: 'Model reasons', caption: 'The model thinks about the task and picks the next tool to call.' },
  { label: 'Executes tool', caption: 'The tool runs — read a file, edit code, execute the shell.' },
  { label: 'Tool returns output', caption: 'The result feeds back into reasoning; the loop continues until the task is done.' },
];

const W = 560;
const H = 180;
const BOXES = [
  { x: 16, y: 34, w: 150, h: 44, label: 'User prompt' },
  { x: 216, y: 34, w: 150, h: 44, label: 'Model reasons' },
  { x: 416, y: 34, w: 128, h: 44, label: 'Executes tool' },
];

/** Advance one stage; after the tool output the loop re-enters reasoning. */
function advance(r: RunState): RunState {
  return r.stage === 3
    ? { stage: 1, iteration: r.iteration + 1 }
    : { stage: r.stage + 1, iteration: r.iteration };
}

const PRIMARY_BTN =
  'rounded-md bg-brand-600 px-3 py-1 text-sm font-medium text-white transition-colors hover:bg-brand-700 dark:bg-brand-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950 dark:focus-visible:ring-offset-white';
const SECONDARY_BTN =
  'rounded-md border border-zinc-700 px-3 py-1 text-sm text-zinc-300 transition-colors hover:bg-zinc-800 dark:border-zinc-300 dark:text-zinc-600 dark:hover:bg-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950 dark:focus-visible:ring-offset-white';

export default function ReActLoop() {
  const [run, setRun] = useState<RunState>({ stage: 0, iteration: 1 });
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => setRun(advance), 1500);
    return () => clearInterval(t);
  }, [playing]);

  const active = STAGES[run.stage];
  const edgeActive = run.stage === 3;

  return (
    <div className="not-prose my-6 rounded-xl border border-zinc-800 bg-zinc-900 p-4 dark:border-zinc-200 dark:bg-zinc-50">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-zinc-100 dark:text-zinc-900">The ReAct loop</p>
          <p className="font-mono text-xs text-zinc-400 dark:text-zinc-500">
            iteration {run.iteration} · {active.label.toLowerCase()}
          </p>
        </div>
        <div className="flex gap-1">
          <button onClick={() => setPlaying((p) => !p)} className={PRIMARY_BTN}>
            {playing ? 'Pause' : 'Play'}
          </button>
          <button onClick={() => setRun(advance)} className={SECONDARY_BTN}>
            Step
          </button>
        </div>
      </div>

      <div className="overflow-hidden rounded-lg bg-zinc-950 p-2 dark:bg-white">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full"
          role="img"
          aria-label={`ReAct loop diagram: user prompt, model reasons, executes tool, and the tool output returning to reasoning. Currently at: ${active.label}`}
        >
          {BOXES.map((b, i) => (
            <g key={b.label}>
              <rect
                x={b.x}
                y={b.y}
                width={b.w}
                height={b.h}
                rx={8}
                strokeWidth={1.5}
                className={
                  run.stage === i
                    ? 'fill-brand-600 stroke-brand-400 dark:fill-brand-600 dark:stroke-brand-400'
                    : 'fill-zinc-900 stroke-zinc-700 dark:fill-zinc-50 dark:stroke-zinc-300'
                }
              />
              <text
                x={b.x + b.w / 2}
                y={b.y + b.h / 2 + 4}
                textAnchor="middle"
                fontSize={11}
                fontFamily="ui-monospace, monospace"
                className={run.stage === i ? 'fill-white' : 'fill-zinc-300 dark:fill-zinc-600'}
              >
                {b.label}
              </text>
            </g>
          ))}

          {/* forward arrows — highlighted when the target stage is active */}
          <line x1={166} y1={56} x2={204} y2={56} strokeWidth={1.5} className={run.stage === 1 ? 'stroke-brand-500 dark:stroke-brand-600' : 'stroke-zinc-600 dark:stroke-zinc-400'} />
          <polygon points="212,56 203,51 203,61" className={run.stage === 1 ? 'fill-brand-500 dark:fill-brand-600' : 'fill-zinc-600 dark:fill-zinc-400'} />
          <line x1={366} y1={56} x2={404} y2={56} strokeWidth={1.5} className={run.stage === 2 ? 'stroke-brand-500 dark:stroke-brand-600' : 'stroke-zinc-600 dark:stroke-zinc-400'} />
          <polygon points="412,56 403,51 403,61" className={run.stage === 2 ? 'fill-brand-500 dark:fill-brand-600' : 'fill-zinc-600 dark:fill-zinc-400'} />

          {/* the return edge: tool output flows back into reasoning */}
          <path
            d="M 480 78 L 480 132 L 291 132 L 291 84"
            fill="none"
            strokeWidth={1.5}
            strokeDasharray={edgeActive ? undefined : '5 4'}
            className={edgeActive ? 'stroke-brand-500 dark:stroke-brand-600' : 'stroke-zinc-600 dark:stroke-zinc-400'}
          />
          <polygon points="291,78 286,88 296,88" className={edgeActive ? 'fill-brand-500 dark:fill-brand-600' : 'fill-zinc-600 dark:fill-zinc-400'} />
          <text
            x={386}
            y={126}
            textAnchor="middle"
            fontSize={10}
            fontFamily="ui-monospace, monospace"
            className={edgeActive ? 'fill-brand-300 dark:fill-brand-600' : 'fill-zinc-500 dark:fill-zinc-400'}
          >
            tool returns output
          </text>
        </svg>
      </div>

      <p className="mt-3 text-xs text-zinc-300 dark:text-zinc-600">{active.caption}</p>
      <p className="mt-2 font-mono text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        assumes: explicit goals · clear feedback · short sessions
      </p>
      <p className="mt-1 text-center text-[11px] text-zinc-400 dark:text-zinc-500">
        Play the loop — every tool result re-enters reasoning until the task is done.
      </p>
    </div>
  );
}
