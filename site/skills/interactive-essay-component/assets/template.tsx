/**
 * TEMPLATE for an interactive essay component (React island).
 *
 * Copy this file to src/components/react/<Name>.tsx and fill in the logic.
 *
 * Hard rules (see skills/interactive-essay-component/references/component-spec.md):
 *  - Filename = component tag = harness KNOWN entry. Default-export the component.
 *  - Dependency-free: import only React hooks. No recharts/d3. Hand-roll SVG/flexbox.
 *  - Root div MUST start with `not-prose my-6 rounded-xl border border-zinc-800 bg-zinc-900 p-4 dark:border-zinc-200 dark:bg-zinc-50`.
 *  - DARK-FIRST: bare classes = DARK (default); `dark:` prefix = LIGHT. Every color needs a partner.
 *  - Accessibility: role="img" + aria-label on SVGs; aria-label on icon buttons; label inputs; focus-visible rings.
 *  - font-mono for labels/stats; Tailwind colors as full literals (never bg-${x}-500).
 *
 * After building: register in the screenshot harness (4 edits) and run
 * scripts/verify.sh <Name> from site/.
 */
import { useMemo, useState } from 'react';

// Keep pure helpers OUTSIDE the component — testable, no hook access.
function compute(value: number): number {
  return value * 2;
}

export default function Template() {
  // Plain primitives for inputs; useMemo for derived data.
  const [count, setCount] = useState(1);
  const doubled = useMemo(() => compute(count), [count]);

  return (
    <div className="not-prose my-6 rounded-xl border border-zinc-800 bg-zinc-900 p-4 dark:border-zinc-200 dark:bg-zinc-50">
      {/* Header: card title (sans) + mono parameter readout. */}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-zinc-100 dark:text-zinc-900">
            Component title
          </p>
          <p className="font-mono text-xs text-zinc-400 dark:text-zinc-500">
            count = {count} · doubled = {doubled}
          </p>
        </div>
        <button
          onClick={() => setCount((c) => c + 1)}
          className="rounded-md bg-brand-600 px-3 py-1 text-sm font-medium text-white transition-colors hover:bg-brand-700 dark:bg-brand-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950 dark:focus-visible:ring-offset-white"
        >
          + count
        </button>
      </div>

      {/* Inner panel: chart well / data area (darker than the card). */}
      <div className="overflow-hidden rounded-lg bg-zinc-950 p-2 dark:bg-white">
        {/* Hand-rolled SVG with role="img" + aria-label (NOT a charting lib). */}
        <svg
          viewBox="0 0 100 20"
          className="w-full"
          role="img"
          aria-label={`Bar showing count ${count} and doubled value ${doubled}`}
        >
          {/* Set fontSize + fontFamily as attributes inside SVG, not classes. */}
          <rect x="2" y="6" width={count * 10} height="8" className="fill-brand-500 dark:fill-brand-600" />
          <text x="2" y="3" fontSize="4" fontFamily="ui-monospace, monospace" className="fill-zinc-400 dark:fill-zinc-500">
            {count}
          </text>
        </svg>
      </div>

      {/* Mono caption / helper text (the "instrument panel" look). */}
      <p className="mt-3 text-center text-[11px] text-zinc-400 dark:text-zinc-500">
        Click the button to increment — the bar and stat update live.
      </p>
    </div>
  );
}
