// Renders GFM tables and LaTeX math in the cross-post markdown into inline
// PNG images (data URIs), for the paste-ready HTML package. Medium and
// LinkedIn have no table/LaTeX support and strip formatting from
// API-submitted HTML — but their editors DO ingest images from pasted rich
// content, so embedding the visuals as images makes them survive the paste.
// Same visual treatment as the Substack adapter (which uploads to Substack's
// CDN instead); here images stay embedded as data URIs for portability.
//
// Tables reuse the markdown→HTML pipeline for styled cells; math is rendered
// by KaTeX in Node and rasterized in a browser page with the real KaTeX CSS
// (served locally so the font files resolve).
import { chromium } from 'playwright';
import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'node:http';
import katex from 'katex';
import { markdownToHtml } from './markdown.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const KATEX_DIST = join(__dirname, '..', '..', 'node_modules', 'katex', 'dist');

/** Table CSS shared with the Substack table renderer, for a consistent look. */
const TABLE_CSS = `
  table { border-collapse: collapse; font-family: -apple-system, system-ui, sans-serif; font-size: 15px; color: #1a1a1a; }
  th, td { border: 1px solid #ccc; padding: 8px 14px; text-align: left; }
  th { background: #f4f4f4; font-weight: 600; }
  code { font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 0.9em; background: #f4f4f4; padding: 1px 4px; border-radius: 3px; }
  a { color: #1a6faa; }
`;

/**
 * Find GFM pipe-table blocks. A block starts where a pipe-row line is
 * followed by a |---| separator line and runs through consecutive pipe rows.
 * Pure function (unit-tested).
 *
 * @param {string} markdown
 * @returns {{ start: number, end: number, lines: string[] }[]} line-index ranges
 */
export function scanTableBlocks(markdown) {
  const lines = (markdown ?? '').split('\n');
  const isRow = (l) => /^\s*\|/.test(l);
  const isSeparator = (l) => /^\s*\|[\s:|-]+\|?\s*$/.test(l);
  const out = [];
  let i = 0;
  while (i < lines.length) {
    if (isRow(lines[i]) && i + 1 < lines.length && isSeparator(lines[i + 1])) {
      const start = i;
      while (i < lines.length && isRow(lines[i])) i++;
      out.push({ start, end: i - 1, lines: lines.slice(start, i) });
    } else {
      i++;
    }
  }
  return out;
}

/**
 * Find LaTeX math — display ($$…$$) and inline ($…$) — outside fenced code
 * blocks (which legitimately contain dollar signs). Pure (unit-tested).
 *
 * @param {string} markdown
 * @returns {{ latex: string, display: boolean }[]} unique formulas
 */
export function scanMath(markdown) {
  const seen = new Set();
  const found = [];
  // Fence-aware scan: only regex within non-code segments.
  const segments = [];
  let inFence = false;
  let seg = [];
  for (const line of (markdown ?? '').split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) {
      segments.push({ code: inFence, text: seg.join('\n') });
      seg = [];
      inFence = !inFence;
      continue;
    }
    if (!inFence) seg.push(line);
    else segments.push({ code: true, text: line });
  }
  segments.push({ code: inFence, text: seg.join('\n') });
  const re = /\$\$([\s\S]+?)\$\$|\$([^\s$](?:[^$\n]*[^\s$])?)\$/g;
  for (const s of segments) {
    if (s.code || !s.text.includes('$')) continue;
    for (const m of s.text.matchAll(re)) {
      const display = Boolean(m[1]);
      const latex = (display ? m[1] : m[2]).trim();
      const key = (display ? 'D:' : 'I:') + latex;
      if (!seen.has(key)) {
        seen.add(key);
        found.push({ latex, display });
      }
    }
  }
  return found;
}

/** Tiny static server over a directory (fonts/CSS for the KaTeX page). */
function serveDir(root) {
  return new Promise((resolve) => {
    const server = createServer(async (req, res) => {
      try {
        const file = join(root, decodeURIComponent(req.url.split('?')[0]));
        if (!existsSync(file)) {
          res.writeHead(404);
          return res.end();
        }
        const ext = file.slice(file.lastIndexOf('.'));
        const mime = { '.css': 'text/css', '.js': 'text/javascript', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf' }[ext] ?? 'application/octet-stream';
        res.writeHead(200, { 'Content-Type': mime });
        res.end(await readFile(file));
      } catch {
        res.writeHead(500);
        res.end();
      }
    });
    server.listen(0, '127.0.0.1', () => resolve({ url: `http://127.0.0.1:${server.address().port}`, close: () => server.close() }));
  });
}

const toDataUri = (pngBuf) => `data:image/png;base64,${pngBuf.toString('base64')}`;

/**
 * Render tables + math to data-URI images and swap them into the markdown
 * (`![Table](data:…)` / `![formula](data:…)`). Returns the ORIGINAL markdown
 * unchanged when there is nothing to render or the browser isn't available —
 * callers treat this as best-effort enrichment.
 *
 * @param {string} markdown
 * @returns {Promise<string>}
 */
export async function enrichMarkdownVisuals(markdown) {
  const tables = scanTableBlocks(markdown);
  const maths = scanMath(markdown);
  if (!tables.length && !maths.length) return markdown;

  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: 1200, height: 800 }, deviceScaleFactor: 2 });
    const page = await ctx.newPage();

    // ── tables: markdown→HTML→styled PNG ──
    const tableImages = new Map(); // start-line → dataUri
    for (const t of tables) {
      try {
        const html = await markdownToHtml(t.lines.join('\n'));
        await page.setContent(`<!doctype html><html><body style="margin:0;background:#fff">${html}</body></html><style>${TABLE_CSS}</style>`);
        const buf = await page.locator('table').screenshot();
        tableImages.set(t.start, toDataUri(buf));
      } catch {
        // Leave the table as markdown — the HTML package keeps the <table>.
      }
    }

    // ── math: KaTeX (rendered in Node) rasterized with the real CSS ──
    const mathImages = new Map(); // D:/I: latex → dataUri
    if (maths.length && existsSync(join(KATEX_DIST, 'katex.min.css'))) {
      const server = await serveDir(KATEX_DIST);
      try {
        await page.setContent(
          `<!doctype html><html><head><link rel="stylesheet" href="${server.url}/katex.min.css"></head><body style="background:#fff;margin:0;padding:8px"></body></html>`
        );
        for (const { latex, display } of maths) {
          try {
            const rendered = katex.renderToString(latex, { displayMode: display, throwOnError: false });
            const dataUri = await page.evaluate(async ({ rendered, display }) => {
              const span = document.createElement('span');
              span.style.cssText = display
                ? 'display:block;text-align:center;padding:6px 10px;font-size:22px;color:#1a1a1a'
                : 'display:inline-block;padding:1px 3px;font-size:20px;color:#1a1a1a';
              span.innerHTML = rendered;
              document.body.appendChild(span);
              return true;
            }, { rendered, display }).then(async () => {
              const buf = await page.locator('body > span:last-child').screenshot();
              await page.evaluate(() => document.body.lastChild.remove());
              return toDataUri(buf);
            });
            mathImages.set((display ? 'D:' : 'I:') + latex, dataUri);
          } catch {
            // Skip — the raw latex text stays in place.
          }
        }
      } finally {
        server.close();
      }
    }

    // ── compose: tables by line range, math by fence-aware regex ──
    let out = [];
    const lines = (markdown ?? '').split('\n');
    let i = 0;
    while (i < lines.length) {
      const img = tableImages.get(i);
      if (img) {
        out.push('', `![Table](${img})`, '');
        const t = tables.find((x) => x.start === i);
        i = t.end + 1;
      } else {
        out.push(lines[i]);
        i++;
      }
    }
    let composed = out.join('\n');

    if (mathImages.size) {
      const re = /\$\$([\s\S]+?)\$\$|\$([^\s$](?:[^$\n]*[^\s$])?)\$/g;
      const pieces = [];
      let cursor = 0;
      let inFence = false;
      // Re-scan fences on the composed text (image lines contain no fences).
      for (const m of composed.matchAll(re)) {
        const before = composed.slice(cursor, m.index);
        const fencesBefore = (before.match(/^\s*(```|~~~)/gm) ?? []).length;
        inFence = fencesBefore % 2 === 1;
        if (!inFence) {
          const display = Boolean(m[1]);
          const latex = (display ? m[1] : m[2]).trim();
          const img = mathImages.get((display ? 'D:' : 'I:') + latex);
          if (img) {
            pieces.push(before, display ? `\n\n![formula](${img})\n\n` : `![formula](${img})`);
            cursor = m.index + m[0].length;
          }
        }
      }
      pieces.push(composed.slice(cursor));
      composed = pieces.join('');
    }

    const tableCount = tableImages.size;
    const mathCount = mathImages.size;
    if (tableCount || mathCount) {
      console.log(`    rich package: ${tableCount} table(s) + ${mathCount} formula(s) rendered as images`);
    }
    return composed;
  } finally {
    await browser.close();
  }
}
