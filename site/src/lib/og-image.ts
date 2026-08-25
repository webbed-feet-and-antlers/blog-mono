// Typed facade over the shared JS core (og-core.mjs). Used by the Astro app's
// build-time OG-image endpoint (src/pages/og/[slug].png.ts). The actual satori
// design lives in og-core.mjs so the syndication script (plain Node .mjs) and
// the app share one implementation.
import { renderOgImageBytes } from './og-core.mjs';

export interface OgImageOptions {
  title: string;
  tags?: string[];
  readingMinutes?: number;
  subtitle?: string;
  brand?: string;
  /** Optional component screenshot bytes to embed as a preview panel. */
  previewImage?: Uint8Array;
  /** Blog slug; selects a per-blog scene override (public/scenes/<slug>.svg). */
  slug?: string;
}

/**
 * Render a 1200x630 OG image for a blog. Returns PNG bytes, or null if the
 * bundled font isn't available (caller should then skip the image).
 */
export async function renderOgImage({
  title,
  tags,
  readingMinutes,
  subtitle = 'The Inkpens',
  brand = 'theinkpens',
  previewImage,
  slug,
}: OgImageOptions): Promise<Uint8Array | null> {
  return renderOgImageBytes({ title, tags, readingMinutes, subtitle, brand, previewImage, slug });
}
