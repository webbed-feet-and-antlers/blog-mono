// Per-essay SVG "scenes" rendered behind the OG card content — abstract
// geometric tech art that adds visual uniqueness without a flat solid gradient.
//
// Sourcing (in priority order):
//   1. Per-essay override: public/scenes/<slug>.svg (committed bespoke scene).
//   2. Tag library: TAG_SCENES maps a tag → a named scene in public/scenes/.
//   3. None → undefined (the renderer falls back to its plain gradient).
//
// Scenes are authored with a {{ACCENT}} placeholder so the renderer can tint
// them with the essay's accent color at compositing time (satori renders each
// <img> as a self-contained SVG, so CSS currentColor can't cross that boundary).
import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
// src/lib -> site/public/scenes
const SCENES_DIR = join(__dirname, '..', '..', 'public', 'scenes');

// Tag → scene name (the file stem in public/scenes/). Mirrors the families in
// og-theme.ts — same scan-all-tags, first-match-wins resolution.
const TAG_SCENES: Record<string, string> = {
  gpu: 'circuits',
  embeddings: 'vectors',
  systems: 'circuits',
  fp8: 'circuits',
  posse: 'network',
  automation: 'network',
  'github-actions': 'network',
  indieweb: 'network',
  'machine-learning': 'vectors',
  'data-science': 'vectors',
  // extended for essays in this repo
  astro: 'constellation',
  mdx: 'grid',
  web: 'grid',
  'github-pages': 'network',
  blog: 'constellation',
};

/**
 * Resolve the scene name for an essay (override > tag library > none).
 * @param tags essay tags, in declared order
 * @param slug essay slug; if public/scenes/<slug>.svg exists it wins
 */
export function sceneNameFor(tags: string[], slug?: string): string | undefined {
  if (slug && existsSync(join(SCENES_DIR, `${slug}.svg`))) return slug;
  for (const t of tags) {
    const scene = TAG_SCENES[t.toLowerCase()];
    if (scene) return scene;
  }
  return undefined;
}

/**
 * Load a scene SVG, tint it with the accent color, and return a data URI ready
 * for satori <img>. Returns undefined if the scene file is missing (caller
 * falls back to the plain gradient).
 *
 * @param name scene name (file stem in public/scenes/)
 * @param accent hex color injected into the {{ACCENT}} placeholder
 */
export async function loadSceneDataUri(name: string, accent: string): Promise<string | undefined> {
  const file = join(SCENES_DIR, `${name}.svg`);
  if (!existsSync(file)) return undefined;
  let svg = await readFile(file, 'utf8');
  svg = svg.replace(/\{\{ACCENT\}\}/g, accent);
  return `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`;
}
