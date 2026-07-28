// Renders a 1200x630 OG image per essay (title + author + brand accent) using
// satori (JSX -> SVG) + sharp (SVG -> PNG). Best-effort: if satori/sharp fail
// to load, returns null and the caller skips the image attachment (posting
// still succeeds, just without native media).
import satori from 'satori';
import sharp from 'sharp';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
// scripts/lib -> site/.syndication-output
const OUT_DIR = join(__dirname, '..', '..', '.syndication-output');

const W = 1200;
const H = 630;

// Font: bundled via @fontsource/inter (woff, supported by satori). Deterministic —
// no network fetch, works identically in local dev and CI.
const FONT_PATH = join(__dirname, '..', '..', 'node_modules', '@fontsource', 'inter', 'files', 'inter-latin-700-normal.woff');

let fontCache = null;

async function loadFont() {
  if (fontCache) return fontCache;
  if (existsSync(FONT_PATH)) {
    fontCache = await readFile(FONT_PATH);
    return fontCache;
  }
  return null;
}

/**
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} [opts.subtitle]   - e.g. author name
 * @param {string} [opts.brand]      - site/brand name
 * @param {string} [opts.slug]       - for the output filename
 * @param {string} [opts.outDir]     - override output dir
 * @returns {Promise<string|null>}   - absolute path to the PNG, or null on failure
 */
export async function renderOgImage({ title, subtitle = 'The Inkpens', brand = 'blog-mono', slug, outDir = OUT_DIR }) {
  const font = await loadFont();
  if (!font) {
    console.warn('og-image: no font available — skipping image generation');
    return null;
  }

  const svg = await satori(
    {
      type: 'div',
      props: {
        style: {
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
          padding: '64px',
          backgroundColor: '#0c0a16',
          backgroundImage:
            'radial-gradient(circle at 0% 0%, #312e81 0%, transparent 50%), radial-gradient(circle at 100% 100%, #4338ca 0%, transparent 50%)',
          color: 'white',
          fontFamily: 'Inter',
        },
        children: [
          {
            type: 'div',
            props: {
              style: { display: 'flex', alignItems: 'center', fontSize: 24 },
              children: brand,
            },
          },
          {
            type: 'div',
            props: {
              style: {
                display: 'flex',
                fontSize: 56,
                fontWeight: 700,
                lineHeight: 1.15,
                maxWidth: 980,
              },
              children: title,
            },
          },
          {
            type: 'div',
            props: {
              style: { display: 'flex', fontSize: 28, opacity: 0.8 },
              children: subtitle,
            },
          },
        ],
      },
    },
    { width: W, height: H, fonts: [{ name: 'Inter', data: font, weight: 700, style: 'normal' }] }
  );

  const png = await sharp(Buffer.from(svg)).png().toBuffer();

  await mkdir(outDir, { recursive: true });
  const file = join(outDir, `og-${slug}.png`);
  await writeFile(file, png);
  return file;
}
