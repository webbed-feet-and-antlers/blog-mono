import { useEffect, useMemo, useRef, useState } from 'react';

/**
 * A dependency-free visualizer comparing four rendering strategies by their
 * request → paint timeline: Static (SSG), SSR, Client-Side (CSR), and Islands
 * (Astro's approach). The point: Islands gets SSG-fast first paint and only
 * pays a small extra cost to hydrate interactivity — the best of both.
 *
 * The durations are illustrative/relative (not measured), chosen so the
 * differences read at a glance. Hand-rolled with flexbox + divs — no charting
 * library ships to the reader.
 */

type StrategyId = 'static' | 'ssr' | 'csr' | 'islands';

interface Phase {
  /** Relative duration (arbitrary units); widths are proportional to this. */
  dur: number;
  label: string;
  /** Tailwind bg class for the segment fill. */
  fill: string;
}

interface Strategy {
  id: StrategyId;
  label: string;
  tag: string;
  /** Where on the timeline (0..1) first contentful paint happens. */
  fcp: number;
  /** Where on the timeline (0..1) the page becomes interactive. */
  tti: number;
  phases: Phase[];
  blurb: string;
}

// Phase fills — stable class strings so Tailwind's compiler detects them.
const SERVER = 'bg-emerald-500';
const NETWORK = 'bg-sky-500';
const JSEXEC = 'bg-fuchsia-500';

const STRATEGIES: Strategy[] = [
  {
    id: 'static',
    label: 'Static (SSG)',
    tag: 'precomputed',
    fcp: 0.22,
    tti: 0.22,
    phases: [
      { dur: 1, label: 'serve file', fill: SERVER },
      { dur: 4, label: 'send HTML', fill: NETWORK },
      { dur: 0, label: 'no JS', fill: JSEXEC },
    ],
    blurb:
      'Pages are pre-built to HTML at compile time. The server just serves a file — no compute per request, and there’s no JavaScript to run. Content is visible and interactive the instant it arrives.',
  },
  {
    id: 'ssr',
    label: 'SSR',
    tag: 'render on request',
    fcp: 0.62,
    tti: 0.72,
    phases: [
      { dur: 6, label: 'render on server', fill: SERVER },
      { dur: 4, label: 'send HTML', fill: NETWORK },
      { dur: 2, label: 'hydrate', fill: JSEXEC },
    ],
    blurb:
      'The server renders fresh HTML on every request, so first paint waits for that work. Content arrives ready to display, but a small hydration step may follow. Per-request compute is the cost.',
  },
  {
    id: 'csr',
    label: 'Client-Side (CSR)',
    tag: 'SPA',
    fcp: 0.92,
    tti: 0.92,
    phases: [
      { dur: 1, label: 'serve shell', fill: SERVER },
      { dur: 7, label: 'send JS bundle', fill: NETWORK },
      { dur: 8, label: 'parse + render', fill: JSEXEC },
    ],
    blurb:
      'The server sends a near-empty shell; the browser downloads the whole app as JavaScript, parses it, and renders everything client-side. The page looks blank until all of that finishes — the slowest path to content.',
  },
  {
    id: 'islands',
    label: 'Islands',
    tag: 'ours',
    fcp: 0.22,
    tti: 0.4,
    phases: [
      { dur: 1, label: 'serve precomputed file', fill: SERVER },
      { dur: 4, label: 'send HTML', fill: NETWORK },
      { dur: 3, label: 'hydrate only islands', fill: JSEXEC },
    ],
    blurb:
      'Static-first like SSG — the page paints immediately. But the specific components marked interactive (client:visible, client:load) hydrate on their own schedule, without blocking the rest of the page. Best of both: fast paint, real interactivity, tiny JS.',
  },
];

const ANIM_MS = 2600;

// Common time axis for the "compare all" view: the longest strategy's total
// duration. Bars in that view are scaled against this so CSR genuinely extends
// further right than SSG — making the differences read at a glance.
const MAX_DUR = Math.max(
  ...STRATEGIES.map((s) => s.phases.reduce((n, p) => n + p.dur, 0)),
);

/** Phase segment boundaries as fractions of a given total duration. */
function phaseBounds(strategy: Strategy, total: number) {
  let acc = 0;
  return strategy.phases
    .filter((p) => p.dur > 0)
    .map((p) => {
      const start = acc / total;
      acc += p.dur;
      const end = acc / total;
      return { ...p, start, end, width: (p.dur / total) * 100 };
    });
}

export default function RenderingStrategies() {
  const [activeId, setActiveId] = useState<StrategyId>('islands');
  const [compare, setCompare] = useState(true); // default: all four on one axis
  const [progress, setProgress] = useState(1); // 0..1 of timeline revealed; 1 = fully shown
  const [playing, setPlaying] = useState(false);
  const rafRef = useRef<number | null>(null);

  const strategy = useMemo(
    () => STRATEGIES.find((s) => s.id === activeId)!,
    [activeId],
  );

  // Play animation: sweep progress 0 → 1, revealing phases in sequence.
  useEffect(() => {
    if (!playing) return;
    const start = performance.now() - progress * ANIM_MS;
    const tick = (now: number) => {
      const next = Math.min(1, (now - start) / ANIM_MS);
      setProgress(next);
      if (next < 1) {
        rafRef.current = requestAnimationFrame(tick);
      } else {
        setPlaying(false);
      }
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing]);

  const play = () => {
    if (progress >= 1) setProgress(0);
    setPlaying(true);
  };

  // Selecting a tab drops out of compare mode and shows that one strategy.
  const select = (id: StrategyId) => {
    setPlaying(false);
    setCompare(false);
    setActiveId(id);
    setProgress(1);
  };

  const toggleCompare = () => {
    setPlaying(false);
    setProgress(1);
    setCompare((c) => !c);
  };

  // In compare mode all bars share the common MAX_DUR axis; in single mode a
  // bar is scaled to its own total (so its labels fill the width).
  const singleTotal = strategy.phases.reduce((n, p) => n + p.dur, 0);

  const ours = strategy.id === 'islands';

  return (
    <div className="not-prose my-6 rounded-xl border border-zinc-800 bg-zinc-900 p-4 dark:border-zinc-200 dark:bg-zinc-50">
      {/* Header + controls */}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm font-semibold text-zinc-100 dark:text-zinc-900">
          Rendering strategies: who paints first?
        </p>
        <p className="font-mono text-xs text-zinc-400 dark:text-zinc-500">
          relative time → first contentful paint (FCP) / time to interactive
          (TTI)
        </p>
      </div>

      {/* Timeline(s) */}
      <div className="overflow-hidden rounded-lg bg-zinc-950 p-3 dark:bg-white">
        {compare ? (
          <div className="space-y-2.5">
            {STRATEGIES.map((s) => (
              <StrategyRow
                key={s.id}
                strategy={s}
                total={MAX_DUR}
                progress={progress}
                playing={playing}
                labelled
              />
            ))}
          </div>
        ) : (
          <StrategyRow
            strategy={strategy}
            total={singleTotal}
            progress={progress}
            playing={playing}
          />
        )}

        {/* Axis (shared by both modes). */}
        <div className="mt-1 flex justify-between font-mono text-[10px] text-zinc-500 dark:text-zinc-400">
          <span>request</span>
          <span>page usable →</span>
        </div>

        {/* Play control + legend */}
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <button
            onClick={play}
            disabled={playing}
            className="rounded-md bg-brand-600 px-3 py-1 text-xs font-medium text-white transition-colors hover:bg-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950 disabled:cursor-not-allowed disabled:opacity-50 dark:focus-visible:ring-offset-white"
          >
            {playing ? 'playing…' : progress >= 1 ? '▶ replay' : '▶ play'}
          </button>
          <div className="flex flex-wrap items-center gap-3 font-mono text-[10px] text-zinc-400 dark:text-zinc-500">
            <LegendDot fill={SERVER} label="server" />
            <LegendDot fill={NETWORK} label="network" />
            <LegendDot fill={JSEXEC} label="client JS" />
          </div>
        </div>
      </div>

      {/* Explanation */}
      {compare ? (
        <p className="mt-3 text-center text-[11px] text-zinc-400 dark:text-zinc-500">
          All four on one axis — notice how CSR's bar runs far longer, while SSG
          and Islands reach FCP almost immediately. ★ Islands is how this site
          is built.
        </p>
      ) : (
        <>
          <p className="mt-3 text-xs leading-relaxed text-zinc-400 dark:text-zinc-500">
            <span
              className={`font-semibold ${ours ? 'text-brand-400 dark:text-brand-600' : 'text-zinc-200 dark:text-zinc-700'}`}
            >
              {strategy.label}
            </span>{' '}
            — {strategy.blurb}
          </p>
          <p className="mt-1 text-center text-[11px] text-zinc-400 dark:text-zinc-500">
            Switch strategies, hit play, or{' '}
            {
              <button
                onClick={toggleCompare}
                className="underline decoration-dotted hover:text-zinc-200 dark:hover:text-zinc-700"
              >
                compare all at once
              </button>
            }
            . {ours && '★ Islands is how this site is built.'}
          </p>
        </>
      )}
    </div>
  );
}

/** One strategy's timeline: an optional label row, the phase bar, and the
 *  FCP/TTI markers over it. Phases are scaled against `total` (so a stack of
 *  rows can share a common axis) and revealed proportionally to playback. */
function StrategyRow({
  strategy,
  total,
  progress,
  playing,
  labelled = false,
}: {
  strategy: Strategy;
  total: number;
  progress: number;
  playing?: boolean;
  labelled?: boolean;
}) {
  const bounds = phaseBounds(strategy, total);
  const isOurs = strategy.id === 'islands';
  return (
    <div className="relative">
      {labelled && (
        <div className="mb-1 flex items-center gap-1.5 font-mono text-[11px] text-zinc-300 dark:text-zinc-600">
          <span
            className={
              isOurs
                ? 'font-semibold text-brand-400 dark:text-brand-600'
                : 'font-medium'
            }
          >
            {strategy.label}
          </span>
          {isOurs && (
            <span className="text-brand-400 dark:text-brand-600">★</span>
          )}
          <span className="text-zinc-500 dark:text-zinc-400">
            · {strategy.tag}
          </span>
        </div>
      )}
      <div className="relative">
        <div
          className="flex h-9 w-full overflow-hidden rounded-md ring-1 ring-inset ring-zinc-800 dark:ring-zinc-200"
          role="img"
          aria-label={`Load timeline for ${strategy.label}: ${strategy.phases
            .filter((p) => p.dur > 0)
            .map((p) => `${p.label}`)
            .join(', then ')}`}
        >
          {bounds.map((p, i) => (
            <div
              key={i}
              className={`${p.fill} relative flex items-center justify-center overflow-hidden text-[10px] font-medium text-white`}
              style={{
                width: `${p.width}%`,
                opacity: progress >= p.start ? 1 : 0.12,
                transition: 'opacity 120ms',
              }}
            >
              {p.width > 14 ? p.label : ''}
            </div>
          ))}
        </div>
        <Marker
          frac={strategy.fcp}
          progress={progress}
          label="FCP"
          tone="amber"
        />
        <Marker
          frac={strategy.tti}
          progress={progress}
          label="TTI"
          tone="brand"
        />
        <div
          className="pointer-events-none absolute top-0 h-full w-0.5 bg-white/80 dark:bg-zinc-900/80"
          style={{ left: `${progress * 100}%`, opacity: playing ? 1 : 0 }}
        />
      </div>
    </div>
  );
}

/** A vertical marker line + label sitting at a fraction of the timeline. */
function Marker({
  frac,
  progress,
  label,
  tone,
}: {
  frac: number;
  progress: number;
  label: string;
  tone: 'amber' | 'brand';
}) {
  const reached = progress >= frac;
  const lineColor =
    tone === 'amber' ? 'bg-amber-300' : 'bg-brand-300 dark:bg-brand-400';
  const textColor =
    tone === 'amber'
      ? 'text-amber-300 dark:text-amber-600'
      : 'text-brand-300 dark:text-brand-600';
  return (
    <div
      className="absolute top-0 flex h-10 flex-col items-center"
      style={{ left: `${frac * 100}%`, transform: 'translateX(-50%)' }}
    >
      {/* Thicker dashed line + a faint dark halo so the marker reads against any
          bright phase fill (the 1px solid line vanished into the coloured bar). */}
      <div
        className={`h-full w-0.5 ${lineColor} ${reached ? 'opacity-100' : 'opacity-30'} transition-opacity`}
        style={{
          boxShadow: '0 0 0 1px rgba(0,0,0,0.35), 0 0 2px rgba(0,0,0,0.5)',
        }}
      />
      <span
        className={`mt-0.5 -translate-x-1/2 whitespace-nowrap rounded-sm bg-zinc-950/80 px-1 font-mono text-[9px] font-bold ${textColor} ${reached ? 'opacity-100' : 'opacity-40'} dark:bg-white/80`}
      >
        {label}
      </span>
    </div>
  );
}

function LegendDot({ fill, label }: { fill: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span
        className={`inline-block h-2.5 w-2.5 rounded-sm ${fill}`}
        aria-hidden="true"
      />
      {label}
    </span>
  );
}
