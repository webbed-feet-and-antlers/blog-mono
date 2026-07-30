import { useMemo, useState } from 'react';

/**
 * A dependency-free learning-rate scheduler visualizer.
 * Plots warmup + decay schedules as an inline SVG so the essay ships no
 * charting library — the whole island is React state + a polyline.
 */

type ScheduleId = 'cosine' | 'linear' | 'step' | 'constant';

interface Schedule {
  id: ScheduleId;
  label: string;
  blurb: string;
}

const SCHEDULES: Schedule[] = [
  { id: 'cosine', label: 'Cosine anneal', blurb: 'Smooth decay to min LR — the modern default for transformers.' },
  { id: 'linear', label: 'Linear decay', blurb: 'Straight-line ramp-down; simple and predictable.' },
  { id: 'step', label: 'Step decay', blurb: 'Hold flat, then drop by a factor at fixed milestones.' },
  { id: 'constant', label: 'Constant', blurb: 'Warmup then flat — no decay at all.' },
];

/** Learning rate at step `t`, normalized 0..1 over the training run. */
function lrAt(t: number, schedule: ScheduleId, warmup: number, minLr: number): number {
  if (t < warmup) return t / warmup; // linear warmup from 0 → 1
  const progress = warmup >= 1 ? (t - warmup) / (1 - warmup) : 1; // 0..1 across the decay region
  switch (schedule) {
    case 'cosine':
      return minLr + (1 - minLr) * (1 + Math.cos(Math.PI * progress)) / 2;
    case 'linear':
      return minLr + (1 - minLr) * (1 - progress);
    case 'step': {
      // Drop by half at 33% and 66% of the decay region.
      const factor = progress < 1 / 3 ? 1 : progress < 2 / 3 ? 0.5 : 0.25;
      return minLr + (1 - minLr) * (factor - minLr) / (1 - minLr) < minLr
        ? minLr
        : minLr + (1 - minLr) * factor;
    }
    case 'constant':
    default:
      return 1;
  }
}

const STEPS = 200;
const W = 560;
const H = 200;
const PAD = 28;

export default function LearningRateScheduler() {
  const [schedule, setSchedule] = useState<ScheduleId>('cosine');
  const [warmupPct, setWarmupPct] = useState(5); // % of total steps
  const [minLrPct, setMinLrPct] = useState(10); // % of peak LR

  const warmup = warmupPct / 100;
  const minLr = minLrPct / 100;

  const points = useMemo(() => {
    const coords: string[] = [];
    for (let i = 0; i <= STEPS; i++) {
      const t = i / STEPS;
      const lr = lrAt(t, schedule, warmup, minLr);
      const x = PAD + t * (W - 2 * PAD);
      const y = H - PAD - lr * (H - 2 * PAD);
      coords.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    }
    return coords.join(' ');
  }, [schedule, warmup, minLr]);

  const active = SCHEDULES.find((s) => s.id === schedule)!;

  return (
    <div className="not-prose my-6 rounded-xl border border-zinc-800 bg-zinc-900 p-4 dark:border-zinc-200 dark:bg-zinc-50">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-zinc-100 dark:text-zinc-900">
            Learning-rate scheduler
          </p>
          <p className="font-mono text-xs text-zinc-400 dark:text-zinc-500">
            warmup {warmupPct}% · min LR {minLrPct}% of peak
          </p>
        </div>
        <div className="flex flex-wrap gap-1">
          {SCHEDULES.map((s) => (
            <button
              key={s.id}
              onClick={() => setSchedule(s.id)}
              className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                schedule === s.id
                  ? 'bg-brand-600 text-white'
                  : 'border border-zinc-700 text-zinc-300 hover:bg-zinc-800 dark:border-zinc-300 dark:text-zinc-600 dark:hover:bg-zinc-100'
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      <div className="overflow-hidden rounded-lg bg-zinc-950 p-2 dark:bg-white">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={`Learning rate over training steps using the ${active.label} schedule`}>
          {/* gridlines */}
          {[0, 0.25, 0.5, 0.75, 1].map((g) => {
            const y = H - PAD - g * (H - 2 * PAD);
            return (
              <g key={g}>
                <line x1={PAD} y1={y} x2={W - PAD} y2={y} className="stroke-zinc-800 dark:stroke-zinc-200" strokeWidth={1} />
                <text x={PAD - 6} y={y + 3} textAnchor="end" className="fill-zinc-400 dark:fill-zinc-500" fontSize={9} fontFamily="ui-monospace, monospace">
                  {Math.round(g * 100)}
                </text>
              </g>
            );
          })}
          {/* warmup marker */}
          {warmup > 0 && (
            <line
              x1={PAD + warmup * (W - 2 * PAD)}
              y1={PAD}
              x2={PAD + warmup * (W - 2 * PAD)}
              y2={H - PAD}
              className="stroke-brand-600/40 dark:stroke-brand-400/40"
              strokeWidth={1}
              strokeDasharray="3 3"
            />
          )}
          {/* the LR curve */}
          <polyline points={points} fill="none" className="stroke-brand-500" strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
          {/* axis labels */}
          <text x={(W) / 2} y={H - 4} textAnchor="middle" className="fill-zinc-400 dark:fill-zinc-500" fontSize={9} fontFamily="ui-monospace, monospace">
            training step →
          </text>
        </svg>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <label className="block">
          <span className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
            warmup ({warmupPct}%)
          </span>
          <input
            type="range"
            min={0}
            max={50}
            value={warmupPct}
            onChange={(e) => setWarmupPct(Number(e.target.value))}
            className="w-full accent-brand-600"
          />
        </label>
        <label className="block">
          <span className="mb-1 block font-mono text-[10px] uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
            min LR ({minLrPct}%)
          </span>
          <input
            type="range"
            min={0}
            max={50}
            value={minLrPct}
            onChange={(e) => setMinLrPct(Number(e.target.value))}
            className="w-full accent-brand-600"
          />
        </label>
      </div>

      <p className="mt-3 text-xs text-zinc-400 dark:text-zinc-500">{active.blurb}</p>
      <p className="mt-1 text-center text-[11px] text-zinc-400 dark:text-zinc-500">
        Switch schedules and drag the sliders — the curve recomputes over {STEPS} steps on every change.
      </p>
    </div>
  );
}
