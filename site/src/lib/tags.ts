/**
 * Colored semantic tags. Each known tag maps to a Tailwind color family; the
 * `tagStyle()` helper returns the pill classes (dark-first). Unknown tags fall
 * back to neutral zinc.
 *
 * To color a new tag, add it to TAG_COLORS. The pill class strings below are
 * written out in full per color (not interpolated) so Tailwind's compiler can
 * detect them — do not build them dynamically.
 */
import type { CollectionEntry } from 'astro:content';

export const TAG_COLORS: Record<string, string> = {
  gpu: 'emerald',
  embeddings: 'violet',
  systems: 'amber',
  fp8: 'rose',
  posse: 'indigo',
  automation: 'sky',
  'github-actions': 'cyan',
  indieweb: 'fuchsia',
  'machine-learning': 'sky',
  'data-science': 'emerald',
};

// Full literal class strings per color so Tailwind can detect them.
// Dark-first: bare = dark mode, `dark:` = light mode.
const PILL_CLASSES: Record<string, string> = {
  emerald:
    'bg-emerald-500/10 text-emerald-400 ring-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-600 dark:ring-emerald-500/20',
  violet:
    'bg-violet-500/10 text-violet-400 ring-violet-500/20 dark:bg-violet-500/10 dark:text-violet-600 dark:ring-violet-500/20',
  amber:
    'bg-amber-500/10 text-amber-400 ring-amber-500/20 dark:bg-amber-500/10 dark:text-amber-600 dark:ring-amber-500/20',
  rose:
    'bg-rose-500/10 text-rose-400 ring-rose-500/20 dark:bg-rose-500/10 dark:text-rose-600 dark:ring-rose-500/20',
  indigo:
    'bg-indigo-500/10 text-indigo-400 ring-indigo-500/20 dark:bg-indigo-500/10 dark:text-indigo-600 dark:ring-indigo-500/20',
  sky:
    'bg-sky-500/10 text-sky-400 ring-sky-500/20 dark:bg-sky-500/10 dark:text-sky-600 dark:ring-sky-500/20',
  cyan:
    'bg-cyan-500/10 text-cyan-400 ring-cyan-500/20 dark:bg-cyan-500/10 dark:text-cyan-600 dark:ring-cyan-500/20',
  fuchsia:
    'bg-fuchsia-500/10 text-fuchsia-400 ring-fuchsia-500/20 dark:bg-fuchsia-500/10 dark:text-fuchsia-600 dark:ring-fuchsia-500/20',
  zinc:
    'bg-zinc-500/10 text-zinc-400 ring-zinc-500/20 dark:bg-zinc-500/10 dark:text-zinc-600 dark:ring-zinc-500/20',
};

const PILL_BASE =
  'inline-flex items-center rounded-full px-2 py-0.5 font-mono text-[11px] font-medium ring-1 ring-inset';

/** Returns the full pill class string for a tag (colored by TAG_COLORS, zinc fallback). */
export function tagStyle(tag: string): string {
  const color = TAG_COLORS[tag.toLowerCase()] ?? 'zinc';
  return `${PILL_BASE} ${PILL_CLASSES[color]}`;
}

export interface TagTally {
  tag: string;
  count: number;
}

/** Tally all tags across the essays collection, sorted by count desc then name. */
export function allTags(essays: CollectionEntry<'essays'>[]): TagTally[] {
  const counts = new Map<string, number>();
  for (const e of essays) {
    for (const t of e.data.tags ?? []) {
      counts.set(t, (counts.get(t) ?? 0) + 1);
    }
  }
  return [...counts.entries()]
    .map(([tag, count]) => ({ tag, count }))
    .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag));
}
