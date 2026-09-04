// Post-build step: pair every hydrated React island with a <noscript> static
// screenshot so JS-less scrapers see real content.
//
// WHY: interactive components only render after hydration, so scrapers —
// Medium's "Import a story", link-preview bots, some search crawlers — see an
// empty mount point and the cross-posted copy arrives without its key
// visuals. The syndication pipeline commits a static screenshot for each
// component (/sshot/<component>-<slug>-dark.png, permanent URLs); this script
// injects it as a noscript SIBLING right before each <astro-island>, where:
//   - JS-less readers/scrapers render the <noscript> image;
//   - real browsers (JS on) never display it and hydration is untouched
//     (it's outside the island, so no hydration mismatch).
//
// Runs after every `astro build` (wired into the npm build script). Skips
// silently when a component has no committed screenshot yet — e.g. a post's
// first deploy happens before the syndication record-commit adds them.
//
// Component names come from the astro-island's component-url
// (/_astro/<Name>.<hash>.js), matching the screenshot naming convention
// (<name>-<slug>-dark.png) from scripts/lib/component-shot.mjs.
import { readFile, writeFile, readdir } from 'node:fs/promises';
import { existsSync, openSync, readSync, closeSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

// scripts/ -> site/dist
const DIST = join(fileURLToPath(new URL('.', import.meta.url)), '..', 'dist');
const MARK = 'data-static-fallback';

/** PNG IHDR dims (bytes 16–24) for width/height attrs (no layout shift). */
function pngSize(file) {
  try {
    const buf = Buffer.alloc(24);
    const fh = openSync(file, 'r');
    readSync(fh, buf, 0, 24, 0);
    closeSync(fh);
    return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
  } catch {
    return {};
  }
}

async function main() {
  const blogDir = join(DIST, 'blog');
  if (!existsSync(blogDir)) return;
  let injected = 0;

  for (const entry of await readdir(blogDir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const slug = entry.name;
    const htmlPath = join(blogDir, slug, 'index.html');
    if (!existsSync(htmlPath)) continue;
    let html = await readFile(htmlPath, 'utf8');
    if (!html.includes('<astro-island')) continue;

    html = html.replace(/<astro-island[^>]*>/g, (tag) => {
      const m = /component-url="\/_astro\/([A-Za-z0-9_]+)\.[^"/]+\.js"/.exec(tag);
      if (!m) return tag;
      const shot = `/sshot/${m[1].toLowerCase()}-${slug}-dark.png`;
      const shotFile = join(DIST, shot.slice(1));
      if (!existsSync(shotFile)) return tag;
      const { width, height } = pngSize(shotFile);
      const dims = width && height ? ` width="${Math.round(width / 2)}" height="${Math.round(height / 2)}"` : '';
      const noscript = `<noscript ${MARK}><img src="${shot}" alt="${m[1]} — static preview of this interactive demo"${dims} style="display:block;max-width:100%;height:auto;border-radius:8px;margin:1rem auto" loading="lazy" /></noscript>`;
      injected++;
      return noscript + tag;
    });

    await writeFile(htmlPath, html, 'utf8');
  }
  if (injected) console.log(`inject-fallbacks: paired ${injected} island(s) with static screenshots`);
}

main().catch((err) => {
  console.error(`inject-fallbacks failed: ${err.message}`);
  process.exit(1);
});
