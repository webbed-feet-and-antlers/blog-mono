// Script-side OG image renderer. Thin wrapper around the shared satori core
// (src/lib/og-core.mjs) that adds the disk write the syndication script needs:
// it renders the PNG bytes once and writes them to .syndication-output/og-<slug>.png
// so the binary-upload platforms (Bluesky, Mastodon) can attach a local file,
// and so the OG image lands in a stable path. The app serves the same image
// publicly via the build-time /og/<slug>.png endpoint; both share one design.
//
// Signature preserved verbatim for scripts/syndicate.mjs:
//   renderOgImage({ title, subtitle, brand, slug, outDir }) -> path string | null
import { mkdir, writeFile } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { renderOgImageBytes } from '../../src/lib/og-core.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
// scripts/lib -> site/.syndication-output
const OUT_DIR = join(__dirname, '..', '..', '.syndication-output');

/**
 * Render an OG image and write it to disk.
 *
 * @param {object} opts
 * @param {string} opts.title
 * @param {string[]} [opts.tags]     - blog tags (drive the accent color + badge)
 * @param {number} [opts.readingMinutes] - shown as a badge when provided
 * @param {string} [opts.subtitle]   - e.g. author/site name
 * @param {string} [opts.brand]      - brand text, top-left
 * @param {string} [opts.slug]       - for the output filename
 * @param {string} [opts.outDir]     - override output dir
 * @returns {Promise<string|null>}   - absolute path to the PNG, or null on failure
 */
export async function renderOgImage({ title, tags, readingMinutes, subtitle = 'The Inkpens', brand = 'blog-mono', slug, outDir = OUT_DIR }) {
  // The script's OG image is for Bluesky/Mastodon (a standalone post image), so
  // it gets color theming + badges + the scene background but does NOT embed a
  // component screenshot (those are inlined into the cross-posted body separately).
  // `slug` both names the file and selects a per-blog scene override.
  const bytes = await renderOgImageBytes({ title, tags, readingMinutes, subtitle, brand, slug });
  if (!bytes) return null;

  await mkdir(outDir, { recursive: true });
  const file = join(outDir, `og-${slug}.png`);
  await writeFile(file, bytes);
  return file;
}
