// Prerendered OG image per essay: builds to /og/<slug>.png at build time
// (static output, no SSR adapter), so GitHub Pages serves it publicly for social
// link previews, Buffer/X/LinkedIn image fetches, and the on-page hero banner.
//
// The card is themed by the essay's primary tag (color accent + badge), carries
// the inkpen brand mark + a reading-time badge, and — when a committed component
// screenshot exists for the essay — embeds that real visual as a preview panel
// (Vercel/GitHub-style). Prose essays fall back to the centered title card.
//
// Skips drafts. Mirrors the getStaticPaths pattern in pages/blog/[...slug].astro.
import type { APIRoute } from 'astro';
import { getCollection } from 'astro:content';
import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { renderOgImage } from '../../lib/og-image';
import { readingTime } from '../../lib/readingTime';

const __dirname = dirname(fileURLToPath(import.meta.url));
// src/pages/og -> site/public/sshot (committed component screenshots)
const SSHOT_DIR = join(__dirname, '..', '..', '..', 'public', 'sshot');

/**
 * Find a committed dark-theme component screenshot for the essay, if one exists.
 * Files are named <component>-<slug>-dark.png. Returns the bytes, or undefined.
 */
async function loadPreviewImage(slug: string): Promise<Uint8Array | undefined> {
  if (!existsSync(SSHOT_DIR)) return undefined;
  const { readdir } = await import('node:fs/promises');
  const files = await readdir(SSHOT_DIR);
  // First dark-variant match wins (deterministic enough; essays rarely have 2+).
  const match = files.find((f) => f.endsWith(`-${slug}-dark.png`));
  if (!match) return undefined;
  return readFile(join(SSHOT_DIR, match));
}

export async function getStaticPaths() {
  const essays = await getCollection('essays', ({ data }) => data.draft !== true);
  return essays.map((entry) => {
    const slug = entry.id.replace(/\.(md|mdx)$/, '');
    return {
      params: { slug },
      props: {
        title: entry.data.title,
        tags: entry.data.tags ?? [],
        minutes: readingTime(entry.body ?? ''),
        slug,
      },
    };
  });
}

export const GET: APIRoute = async ({ props }) => {
  const previewImage = await loadPreviewImage(props.slug);
  const png = await renderOgImage({
    title: props.title,
    tags: props.tags,
    readingMinutes: props.minutes,
    subtitle: 'The Inkpens',
    brand: 'theinkpens',
    previewImage,
    slug: props.slug,
  });
  if (!png) {
    return new Response('OG image generation failed (font unavailable).', { status: 500 });
  }
  // Wrap the bytes in a fresh Uint8Array over its own ArrayBuffer so the TS DOM
  // lib's BodyInit accepts it (newer libs reject shared/ArrayBufferLike views).
  const body = new Uint8Array(png);
  return new Response(body, {
    headers: {
      'Content-Type': 'image/png',
      // Deterministic per title — safe to cache forever once built.
      'Cache-Control': 'public, max-age=31536000, immutable',
    },
  });
};
