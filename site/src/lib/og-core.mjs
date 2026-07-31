// Shared core OG-image renderer (satori → SVG → sharp → PNG bytes). Pure JS so
// it's importable by BOTH the app (src/lib/og-image.ts, an Astro build-time
// endpoint) and the syndication script (scripts/lib/og-image.mjs, plain Node).
//
// Returns a Uint8Array of PNG bytes — no disk I/O. Callers decide what to do
// with the bytes: the endpoint wraps them in a Response; the script writes them
// to site/.syndication-output/ for the binary-upload platforms (Bluesky,
// Mastodon) and for Buffer to fetch.
//
// Design (1200x630):
//   - Tag-driven gradient accent (accentFor), so a feed of these isn't monotone.
//   - Hand-drawn inkpen brand mark in a corner (built recognition across posts).
//   - Reading-time + tag badges under the title for visual hierarchy.
//   - When a component screenshot is provided, a two-zone card: title/badges on
//     top, the real visual in a rounded panel below (Vercel/GitHub-style).
//     Without a screenshot, the title is centered vertically (single-zone).
import satori from 'satori';
import sharp from 'sharp';
import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { accentFor } from './og-theme.ts';
import { sceneNameFor, loadSceneDataUri } from './og-scenes.ts';

const require = createRequire(import.meta.url);

// Font + brand mark resolved from their packages via createRequire (bundler-
// safe: works under Vite's SSR transform during build AND direct Node runs).
let FONT_PATH;
let BRAND_SVG_PATH;
try {
  FONT_PATH = require.resolve('@fontsource/inter/files/inter-latin-700-normal.woff');
} catch {
  FONT_PATH = null;
}
try {
  // Resolves to <site>/public/inkpen-signature.svg (the "public" alias points
  // at the file via package resolution of the Astro root). Fall back to a direct
  // relative path if resolution fails (e.g. when run from the script).
  BRAND_SVG_PATH = require.resolve('../../public/inkpen-signature.svg');
} catch {
  BRAND_SVG_PATH = null;
}

const W = 1200;
const H = 630;

let fontCache = null;
let brandCache = null;

async function loadFont() {
  if (fontCache) return fontCache;
  if (FONT_PATH && existsSync(FONT_PATH)) {
    fontCache = await readFile(FONT_PATH);
    return fontCache;
  }
  return null;
}

// Load the inkpen signature SVG and return it as a base64 data URI for satori.
// Returns null if unavailable (the card renders fine without the mark).
async function loadBrandMark() {
  if (brandCache !== undefined) return brandCache;
  const candidates = [
    BRAND_SVG_PATH,
    // Direct relative path from this file (src/lib) -> site/public.
    new URL('../../public/inkpen-signature.svg', import.meta.url),
  ].filter(Boolean);
  for (const c of candidates) {
    const p = c instanceof URL ? c : new URL(`file://${c}`);
    if (existsSync(p)) {
      const svg = await readFile(p);
      brandCache = `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`;
      return brandCache;
    }
  }
  brandCache = null;
  return null;
}

/**
 * Render a 1200x630 OG image and return the PNG bytes.
 *
 * @param {object} opts
 * @param {string} opts.title          - essay title (the main text)
 * @param {string[]} [opts.tags]       - essay tags (drive the accent color + badge)
 * @param {number} [opts.readingMinutes] - shown as a badge when provided
 * @param {string} [opts.subtitle]     - e.g. author/site name
 * @param {string} [opts.brand]        - brand text, top-left
 * @param {Uint8Array} [opts.previewImage] - optional component screenshot to embed
 * @param {string} [opts.slug]         - essay slug; selects a per-essay scene override
 * @returns {Promise<Uint8Array|null>} PNG bytes, or null if the font isn't
 *   available (caller skips the image; posting/render still succeeds).
 */
export async function renderOgImageBytes({
  title,
  tags = [],
  readingMinutes,
  brand = 'theinkpens',
  previewImage,
  slug,
}) {
  const font = await loadFont();
  if (!font) {
    console.warn('og-image: no font available — skipping image generation');
    return null;
  }

  const accent = accentFor(tags);
  const brandMark = await loadBrandMark();
  // Resolve an SVG scene: a per-essay override (public/scenes/<slug>.svg) wins,
  // else the tag library. Tinted with the accent. undefined = plain gradient.
  const sceneName = sceneNameFor(tags, slug);
  const sceneDataUri = sceneName ? await loadSceneDataUri(sceneName, accent.accent) : undefined;

  const hasVisual = previewImage instanceof Uint8Array && previewImage.length > 0;
  const visualSrc = hasVisual ? `data:image/png;base64,${Buffer.from(previewImage).toString('base64')}` : null;

  // Top zone: brand row, title, badges. When there's a visual, it sits in the
  // bottom ~40% of the card; otherwise the title block is vertically centered.
  const titleFontSize = hasVisual ? 48 : 56;
  const containerPad = hasVisual ? 56 : 64;

  const titleBlock = {
    type: 'div',
    props: {
      style: {
        display: 'flex',
        flexDirection: 'column',
        justifyContent: hasVisual ? 'flex-start' : 'center',
        ...(hasVisual ? {} : { flex: 1 }),
      },
      children: [
        {
          type: 'div',
          props: {
            style: {
              display: 'flex',
              fontSize: titleFontSize,
              fontWeight: 700,
              lineHeight: 1.15,
              maxWidth: 1010,
              color: 'white',
            },
            children: title,
          },
        },
        // Metadata badges: reading time + primary tag. Only render the row if
        // at least one badge is present.
        (readingMinutes || accent.tag) && {
          type: 'div',
          props: {
            style: { display: 'flex', marginTop: 24, gap: 12 },
            children: [
              readingMinutes && badge(`${readingMinutes} min read`, accent),
              accent.tag && badge(`#${accent.tag}`, accent),
            ].filter(Boolean),
          },
        },
      ].filter(Boolean),
    },
  };

  const visualBlock = hasVisual
    ? {
        type: 'div',
        props: {
          style: {
            display: 'flex',
            flex: 1,
            marginTop: 32,
            borderRadius: 12,
            overflow: 'hidden',
            border: `1px solid ${accent.badgeBorder}`,
          },
          children: {
            type: 'img',
            props: { src: visualSrc, style: { width: '100%', height: '100%', objectFit: 'cover' } },
          },
        },
      }
    : null;

  // Brand row: inkpen signature mark (if available) + wordmark.
  const brandRow = {
    type: 'div',
    props: {
      style: { display: 'flex', alignItems: 'center', gap: 12 },
      children: [
        brandMark && {
          type: 'img',
          props: { src: brandMark, style: { width: 44, height: 44, opacity: 0.9 } },
        },
        {
          type: 'div',
          props: {
            style: { display: 'flex', fontSize: 24, color: accent.accent, fontWeight: 600 },
            children: brand,
          },
        },
      ].filter(Boolean),
    },
  };

  // The content (brand row, title, badges, visual) lives in a relative container
  // over the scene background. When a scene is present it's an absolutely-
  // positioned full-bleed <img> at low opacity, with a translucent dark scrim on
  // top so text stays legible; without a scene the gradient fills the card.
  const contentDiv = {
    type: 'div',
    props: {
      style: {
        width: '100%',
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        padding: `${containerPad}px`,
        color: 'white',
        fontFamily: 'Inter',
      },
      children: [brandRow, titleBlock, visualBlock].filter(Boolean),
    },
  };

  // Background layer(s). With a scene: a solid dark base, the scene fully opaque
  // on top, then a gradient scrim for depth + to keep centered text legible over
  // corner/edge artwork. Without a scene: just the gradient on the dark base.
  const gradient = `radial-gradient(circle at 0% 0%, ${accent.from}40 0%, transparent 55%), radial-gradient(circle at 100% 100%, ${accent.to}40 0%, transparent 55%)`;
  const background = sceneDataUri
    ? [
        {
          type: 'div',
          props: {
            style: { position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', backgroundColor: '#0c0a16' },
          },
        },
        {
          type: 'img',
          props: {
            src: sceneDataUri,
            style: { position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', objectFit: 'cover' },
          },
        },
        {
          type: 'div',
          props: {
            style: {
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              height: '100%',
              backgroundColor: 'rgba(12,10,22,0.55)',
              backgroundImage: gradient,
            },
          },
        },
      ]
    : [
        {
          type: 'div',
          props: {
            style: {
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              height: '100%',
              backgroundColor: '#0c0a16',
              backgroundImage: gradient,
            },
          },
        },
      ];

  const svg = await satori(
    {
      type: 'div',
      props: {
        style: { width: '100%', height: '100%', display: 'flex', position: 'relative' },
        children: [...background, contentDiv],
      },
    },
    { width: W, height: H, fonts: [{ name: 'Inter', data: font, weight: 700, style: 'normal' }] }
  );

  // Rasterize at 2x then downscale with Lanczos. satori's SVG is vector, so
  // rendering it larger smooths gradient banding and anti-aliases the scene's
  // diagonal/curved strokes, then the downscale averages those extra pixels
  // into a clean 1200x630. Density 192 = 2x the default 96 DPI.
  return sharp(Buffer.from(svg), { density: 192 })
    .resize(W, H, { kernel: 'lanczos3' })
    .png()
    .toBuffer();
}

// Small pill badge (reading time / tag) using the accent color.
function badge(text, accent) {
  return {
    type: 'div',
    props: {
      style: {
        display: 'flex',
        fontSize: 20,
        color: accent.accent,
        backgroundColor: accent.badgeBg,
        border: `1px solid ${accent.badgeBorder}`,
        borderRadius: 999,
        padding: '6px 16px',
      },
      children: text,
    },
  };
}
