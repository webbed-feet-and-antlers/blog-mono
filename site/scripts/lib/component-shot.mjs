// Screenshots an interactive React component (rendered via the
// src/pages/sshot/[component].astro harness) to a PNG, so the image can be
// inlined into cross-posted Markdown where the live component was stripped.
//
// Flow:
//   1. astro build already emitted dist/sshot/<Component>/index.html
//   2. Playwright loads that file, waits for the [data-shot] island to mount,
//      and screenshots just that element at 2x for retina crispness.
//   3. PNG is written to public/sshot/<component>-<slug>.png (committed —
//      served permanently from the canonical site origin, since dev.to/Medium
//      hotlink rather than re-host API markdown images).
import { chromium } from 'playwright';
import { mkdir, readFile, stat } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
// scripts/lib -> site/dist (built harness) and site/public/sshot (output)
const DIST_DIR = join(__dirname, '..', '..', 'dist');
const PUBLIC_SSHOT = join(__dirname, '..', '..', 'public', 'sshot');

const MIME = {
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.mjs': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
};

/**
 * Tiny static file server over HTTP — needed because Astro's built HTML uses
 * absolute asset paths (/blog-mono/_astro/...) that don't resolve via file://.
 * Serves DIST_DIR; returns the base URL and a close() fn.
 */
function startStaticServer(root) {
  return new Promise((resolve) => {
    const server = createServer(async (req, res) => {
      try {
        let urlPath = decodeURIComponent(req.url.split('?')[0]);
        // Strip the Astro base path (/blog-mono) so paths resolve to dist/.
        urlPath = urlPath.replace(/^\/blog-mono/, '');
        if (urlPath === '/') urlPath = '/index.html';
        let filePath = join(root, urlPath);
        // If it's a dir (or doesn't exist), try <path>/index.html.
        if (!existsSync(filePath) || (await stat(filePath).then((s) => s.isDirectory()).catch(() => false))) {
          const idx = join(root, urlPath, 'index.html');
          if (existsSync(idx)) filePath = idx;
        }
        if (!existsSync(filePath) || (await stat(filePath).then((s) => s.isDirectory()).catch(() => false))) {
          res.writeHead(404);
          res.end('not found');
          return;
        }
        const data = await readFile(filePath);
        const ext = filePath.slice(filePath.lastIndexOf('.'));
        res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
        res.end(data);
      } catch (err) {
        res.writeHead(500);
        res.end(String(err));
      }
    });
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({ baseURL: `http://127.0.0.1:${port}/blog-mono`, close: () => server.close() });
    });
  });
}

/**
 * @param {object} opts
 * @param {string} opts.componentName   - e.g. "BinPacker" (must match a route)
 * @param {string} opts.slug            - essay slug (namespacing the PNG)
 * @param {string} [opts.distDir]       - override built-site dir
 * @param {string} [opts.outDir]        - override output dir
 * @returns {Promise<{path: string, url: string} | null>} local path + the
 *   site-relative URL (/sshot/<file>), or null if the harness wasn't built.
 */
export async function screenshotComponent({ componentName, slug, distDir = DIST_DIR, outDir = PUBLIC_SSHOT }) {
  const htmlPath = join(distDir, 'sshot', componentName, 'index.html');
  if (!existsSync(htmlPath)) {
    return null; // harness not built — caller falls back to strip-and-note
  }

  // Serve dist/ over HTTP so absolute asset paths (/blog-mono/_astro/...) resolve.
  const server = await startStaticServer(distDir);
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({
      viewport: { width: 832, height: 900 }, // 800px wrap + 16px padding
      deviceScaleFactor: 2,
    });
    const page = await ctx.newPage();
    await page.goto(`${server.baseURL}/sshot/${componentName}/`);

    // client:only islands render NOTHING until React hydrates, so waiting for
    // the [data-shot] wrapper isn't enough (it exists in the SSR HTML empty).
    // Wait until the wrapper has real child content (the hydrated component).
    await page.waitForFunction(
      () => {
        const el = document.querySelector('[data-shot]');
        return el && el.children.length > 0 && el.offsetHeight > 40;
      },
      { timeout: 15_000 }
    );
    // Give async effects (charts, data fetches) a moment to settle.
    await page.waitForLoadState('networkidle').catch(() => {});
    await page.waitForTimeout(500);

    const el = page.locator('[data-shot]');
    await el.scrollIntoViewIfNeeded().catch(() => {});

    await mkdir(outDir, { recursive: true });
    const file = `${componentName.toLowerCase()}-${slug}.png`;
    const outPath = join(outDir, file);
    await el.screenshot({ path: outPath, omitBackground: false });

    // site-relative URL — prefixed with the origin at the call site.
    return { path: outPath, url: `/sshot/${file}` };
  } finally {
    await browser.close();
    server.close();
  }
}

/**
 * Scan MDX body for component tags used, returning unique component names.
 * Matches `<BinPacker ... />` style tags (capitalized = React component).
 * @param {string} body
 * @returns {string[]}
 */
export function componentsInBody(body) {
  const found = new Set();
  const re = /<([A-Z][A-Za-z0-9]+)\b[^>]*\/?>/g;
  let m;
  while ((m = re.exec(body)) !== null) {
    found.add(m[1]);
  }
  return [...found];
}
