// OG-image color theming: maps an essay's tags to a gradient accent. Mirrors
// the Tailwind color families in tags.ts but as raw hex (satori can't read
// Tailwind classes). Single source of truth for OG coloring.
//
// accentFor(tags) scans ALL tags and returns the first mapped one's Accent, so
// an essay whose primary tag isn't mapped (e.g. "astro") still gets color from
// a later tag. Falls back to the neutral default.

export interface Accent {
  /** Display name of the source tag, for the badge. */
  tag: string;
  /** Radial-gradient stop colors (satori backgroundImage). */
  from: string;
  to: string;
  /** Accent text/border color for title highlight + badges. */
  accent: string;
  /** Badge background (translucent accent). */
  badgeBg: string;
  /** Badge border. */
  badgeBorder: string;
}

// Each family matches the Tailwind -500 used in tags.ts pill styling.
const ACCENTS: Record<string, Omit<Accent, 'tag'>> = {
  emerald: { from: '#059669', to: '#10b981', accent: '#34d399', badgeBg: 'rgba(16,185,129,0.15)', badgeBorder: 'rgba(52,211,153,0.35)' },
  violet: { from: '#7c3aed', to: '#8b5cf6', accent: '#a78bfa', badgeBg: 'rgba(139,92,246,0.15)', badgeBorder: 'rgba(167,139,250,0.35)' },
  amber: { from: '#d97706', to: '#f59e0b', accent: '#fbbf24', badgeBg: 'rgba(245,158,11,0.15)', badgeBorder: 'rgba(251,191,36,0.35)' },
  rose: { from: '#e11d48', to: '#f43f5e', accent: '#fb7185', badgeBg: 'rgba(244,63,94,0.15)', badgeBorder: 'rgba(251,113,133,0.35)' },
  indigo: { from: '#4338ca', to: '#6366f1', accent: '#818cf8', badgeBg: 'rgba(99,102,241,0.15)', badgeBorder: 'rgba(129,140,248,0.35)' },
  sky: { from: '#0284c7', to: '#0ea5e9', accent: '#38bdf8', badgeBg: 'rgba(14,165,233,0.15)', badgeBorder: 'rgba(56,189,248,0.35)' },
  cyan: { from: '#0891b2', to: '#06b6d4', accent: '#22d3ee', badgeBg: 'rgba(6,182,212,0.15)', badgeBorder: 'rgba(34,211,238,0.35)' },
  fuchsia: { from: '#c026d3', to: '#d946ef', accent: '#e879f9', badgeBg: 'rgba(217,70,239,0.15)', badgeBorder: 'rgba(232,121,249,0.35)' },
  // Neutral default (slate/zinc) for unmapped tags.
  zinc: { from: '#334155', to: '#475569', accent: '#94a3b8', badgeBg: 'rgba(148,163,184,0.15)', badgeBorder: 'rgba(148,163,184,0.35)' },
};

// Tag → color family. Extended beyond tags.ts to cover the actual essay tags in
// this repo (astro, mdx, web, github-pages) so every post gets a distinct color.
// Keep this aligned with TAG_COLORS in tags.ts.
const TAG_FAMILY: Record<string, string> = {
  // from tags.ts
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
  // extended for essays in this repo
  astro: 'fuchsia',
  mdx: 'violet',
  web: 'sky',
  'github-pages': 'indigo',
  blog: 'amber',
};

/**
 * Resolve the first mapped tag's accent, else the neutral default.
 * @param tags essay tags, in declared order
 */
export function accentFor(tags: string[]): Accent {
  for (const t of tags) {
    const family = TAG_FAMILY[t.toLowerCase()];
    if (family && ACCENTS[family]) {
      return { tag: t, ...ACCENTS[family] };
    }
  }
  return { tag: '', ...ACCENTS.zinc };
}
