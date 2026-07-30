/**
 * Colored semantic tags. Known tags map to a hand-picked Tailwind color family;
 * any other tag is assigned a *stable* color via a hash of its name, so every
 * tag gets a color (no grey fallback) and a given tag is always the same color.
 *
 * The pill class strings in PILL_CLASSES are written out in full per color (not
 * interpolated) so Tailwind's compiler can detect them — do not build them
 * dynamically. New colors must be added there too.
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

// Color families available for automatic assignment. Order is stable so the
// hash distribution doesn't shift when the map is edited. Kept in sync with the
// keys of PILL_CLASSES (minus zinc, which is reserved — see tagStyle).
export const AUTO_COLORS = [
  'emerald',
  'violet',
  'amber',
  'rose',
  'indigo',
  'sky',
  'cyan',
  'fuchsia',
  'lime',
  'teal',
  'orange',
  'pink',
] as const;

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
  lime:
    'bg-lime-500/10 text-lime-400 ring-lime-500/20 dark:bg-lime-500/10 dark:text-lime-600 dark:ring-lime-500/20',
  teal:
    'bg-teal-500/10 text-teal-400 ring-teal-500/20 dark:bg-teal-500/10 dark:text-teal-600 dark:ring-teal-500/20',
  orange:
    'bg-orange-500/10 text-orange-400 ring-orange-500/20 dark:bg-orange-500/10 dark:text-orange-600 dark:ring-orange-500/20',
  pink:
    'bg-pink-500/10 text-pink-400 ring-pink-500/20 dark:bg-pink-500/10 dark:text-pink-600 dark:ring-pink-500/20',
  zinc:
    'bg-zinc-500/10 text-zinc-400 ring-zinc-500/20 dark:bg-zinc-500/10 dark:text-zinc-600 dark:ring-zinc-500/20',
};

const PILL_BASE =
  'inline-flex items-center rounded-full px-2 py-0.5 font-mono text-[11px] font-medium ring-1 ring-inset';

/**
 * Deterministically map an arbitrary string to one of AUTO_COLORS. Same input
 * always yields the same color; inputs spread roughly evenly across the palette.
 * Uses a simple FNV-1a hash — no dependencies, stable across runs/builds.
 */
function hashColor(tag: string): string {
  const key = tag.toLowerCase();
  let h = 0x811c9dc5;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  // Coerce to unsigned and index into the palette.
  return AUTO_COLORS[(h >>> 0) % AUTO_COLORS.length];
}

/** Returns the full pill class string for a tag (known mapping, else auto-colored). */
export function tagStyle(tag: string): string {
  const color = TAG_COLORS[tag.toLowerCase()] ?? hashColor(tag);
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
