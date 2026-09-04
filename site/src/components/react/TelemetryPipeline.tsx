import { useMemo, useState } from 'react';

/**
 * Passive telemetry, the way it actually travels: client events accumulate
 * in a buffer, and when the batch threshold is hit (or the user leaves) they
 * flush via navigator.sendBeacon to /api/telemetry/flush, where they distil
 * into deterministic stats — rolling latency, dwell time, and a time-of-day
 * histogram. Click the event buttons and watch it happen.
 */

type EvType = 'doc.opened' | 'question.answered' | 'session.abandoned';

interface Ev {
  type: EvType;
  seconds: number;
  hour: number;
}

interface Stats {
  answeredLatency: number[];
  dwellSecs: number;
  hours: number[];
  flushed: number;
}

const THRESHOLD = 8;

// Intentionally single-mode fills (same segment color in both themes) — the
// dark: duplicate keeps the pairing audit honest.
const FILLS: Record<EvType, string> = {
  'doc.opened': 'bg-sky-500 dark:bg-sky-500',
  'question.answered': 'bg-emerald-500 dark:bg-emerald-500',
  'session.abandoned': 'bg-rose-500 dark:bg-rose-500',
};

const SEED: Ev[] = [
  { type: 'doc.opened', seconds: 130, hour: 9 },
  { type: 'question.answered', seconds: 14, hour: 9 },
  { type: 'question.answered', seconds: 31, hour: 10 },
  { type: 'doc.opened', seconds: 96, hour: 10 },
  { type: 'question.answered', seconds: 8, hour: 11 },
  { type: 'session.abandoned', seconds: 0, hour: 11 },
  { type: 'question.answered', seconds: 22, hour: 14 },
  { type: 'question.answered', seconds: 17, hour: 14 },
  { type: 'doc.opened', seconds: 210, hour: 15 },
  { type: 'question.answered', seconds: 40, hour: 15 },
  { type: 'question.answered', seconds: 26, hour: 19 },
  { type: 'doc.opened', seconds: 84, hour: 20 },
];

const EMPTY_STATS: Stats = { answeredLatency: [], dwellSecs: 0, hours: Array.from({ length: 24 }, () => 0), flushed: 0 };

/** Pure fold: merge a flushed batch into the running distilled stats. */
function distill(stats: Stats, batch: Ev[]): Stats {
  const hours = [...stats.hours];
  for (const e of batch) hours[e.hour] = (hours[e.hour] ?? 0) + 1;
  return {
    answeredLatency: [...stats.answeredLatency, ...batch.filter((e) => e.type === 'question.answered').map((e) => e.seconds)],
    dwellSecs: stats.dwellSecs + batch.filter((e) => e.type === 'doc.opened').reduce((a, e) => a + e.seconds, 0),
    hours,
    flushed: stats.flushed + batch.length,
  };
}

const SECONDARY_BTN =
  'rounded-md border border-zinc-700 px-2.5 py-1 font-mono text-xs text-zinc-300 transition-colors hover:bg-zinc-800 dark:border-zinc-300 dark:text-zinc-600 dark:hover:bg-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950 dark:focus-visible:ring-offset-white disabled:cursor-not-allowed disabled:opacity-50';

export default function TelemetryPipeline() {
  const [buffer, setBuffer] = useState<Ev[]>([]);
  const [stats, setStats] = useState<Stats>(() => distill(EMPTY_STATS, SEED));
  const [lastFlush, setLastFlush] = useState<number | null>(SEED.length);
  const [n, setN] = useState(SEED.length);

  const push = (type: EvType) => {
    const i = n;
    const ev: Ev = {
      type,
      seconds: type === 'question.answered' ? 6 + ((i * 13) % 42) : type === 'doc.opened' ? 80 + ((i * 29) % 150) : 0,
      hour: (9 + i * 5) % 24,
    };
    setN(i + 1);
    const next = [...buffer, ev];
    if (next.length >= THRESHOLD) {
      setStats((s) => distill(s, next));
      setBuffer([]);
      setLastFlush(next.length);
    } else {
      setBuffer(next);
    }
  };

  const flush = () => {
    if (buffer.length === 0) return;
    setStats((s) => distill(s, buffer));
    setLastFlush(buffer.length);
    setBuffer([]);
  };

  const view = useMemo(() => {
    const lat = stats.answeredLatency;
    const avg = lat.length ? lat.reduce((a, b) => a + b, 0) / lat.length : null;
    const maxHour = Math.max(...stats.hours, 1);
    return { avg, n: lat.length, maxHour, dwellMin: Math.round(stats.dwellSecs / 60) };
  }, [stats]);

  return (
    <div className="not-prose my-6 rounded-xl border border-zinc-800 bg-zinc-900 p-4 dark:border-zinc-200 dark:bg-zinc-50">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-zinc-100 dark:text-zinc-900">Passive telemetry pipeline</p>
          <p className="font-mono text-xs text-zinc-400 dark:text-zinc-500">
            buffer {buffer.length}/{THRESHOLD} · auto-flush at threshold
          </p>
        </div>
        <div className="flex flex-wrap gap-1">
          {(Object.keys(FILLS) as EvType[]).map((t) => (
            <button key={t} onClick={() => push(t)} className={SECONDARY_BTN}>
              + {t}
            </button>
          ))}
          <button onClick={flush} disabled={buffer.length === 0} className={SECONDARY_BTN}>
            flush
          </button>
        </div>
      </div>

      <div
        className="flex h-8 w-full overflow-hidden rounded-md ring-1 ring-inset ring-zinc-800 dark:ring-zinc-200"
        role="img"
        aria-label={`Telemetry buffer holding ${buffer.length} of ${THRESHOLD} events before flushing`}
      >
        {Array.from({ length: THRESHOLD }, (_, i) => (
          <div
            key={i}
            className={`h-full flex-1 border-r border-zinc-950 last:border-r-0 dark:border-white ${
              buffer[i] ? FILLS[buffer[i].type] : 'bg-zinc-800 dark:bg-zinc-100'
            }`}
          />
        ))}
      </div>

      <p className="mt-2 font-mono text-[11px] text-zinc-400 dark:text-zinc-500">
        POST /api/telemetry/flush · navigator.sendBeacon · last batch: {lastFlush ?? '—'} events
      </p>

      <div className="mt-3 grid grid-cols-3 gap-2 text-center">
        <div className="rounded-lg bg-zinc-950 px-2 py-2 dark:bg-white">
          <p className="font-mono text-lg font-semibold text-zinc-100 dark:text-zinc-900">{view.avg ? `${view.avg.toFixed(1)}s` : '—'}</p>
          <p className="font-mono text-[10px] uppercase tracking-wide text-zinc-400 dark:text-zinc-500">avg answer (n={view.n})</p>
        </div>
        <div className="rounded-lg bg-zinc-950 px-2 py-2 dark:bg-white">
          <p className="font-mono text-lg font-semibold text-zinc-100 dark:text-zinc-900">{view.dwellMin}m</p>
          <p className="font-mono text-[10px] uppercase tracking-wide text-zinc-400 dark:text-zinc-500">dwell (active)</p>
        </div>
        <div className="rounded-lg bg-zinc-950 px-2 py-2 dark:bg-white">
          <p className="font-mono text-lg font-semibold text-zinc-100 dark:text-zinc-900">{stats.flushed}</p>
          <p className="font-mono text-[10px] uppercase tracking-wide text-zinc-400 dark:text-zinc-500">events distilled</p>
        </div>
      </div>

      <div className="mt-3 rounded-lg bg-zinc-950 p-3 dark:bg-white">
        <p className="mb-2 font-mono text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          time-of-day matrix — study activity by hour
        </p>
        <div className="flex h-16 items-end gap-[2px]" role="img" aria-label="Histogram of study activity by hour of day">
          {stats.hours.map((c, h) => (
            <div
              key={h}
              className="flex-1 rounded-sm bg-brand-500 dark:bg-brand-600"
              style={{ height: `${Math.max(4, (c / view.maxHour) * 100)}%` }}
            />
          ))}
        </div>
        <div className="mt-1 flex justify-between font-mono text-[9px] text-zinc-500 dark:text-zinc-400">
          <span>00</span>
          <span>06</span>
          <span>12</span>
          <span>18</span>
          <span>23</span>
        </div>
      </div>

      <p className="mt-1 text-center text-[11px] text-zinc-400 dark:text-zinc-500">
        Click the event buttons — at the threshold the batch flushes and the distilled stats recompute.
      </p>
    </div>
  );
}
