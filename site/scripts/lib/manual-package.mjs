// Builds a single "syndication package" Markdown file per essay for the
// platforms that have no clean automation path (Medium, Substack). Both
// platforms share the manual-paste workflow, so one file per essay covers
// them both — with platform-specific instructions baked in.
//
// The package contains: the full sanitized body (screenshots inlined),
// the canonical URL frontmatter (for Medium), and a "How to publish" section
// guiding the SEO-correct behavior on each platform.
//
// Idempotent across the two adapters in a single run: each adapter calls
// addPlatformNote() to append its instruction; the first call also writes
// the body + canonical header. We track which essays have been seeded so
// the Medium and Substack adapters compose into one file cleanly.
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
// scripts/lib -> site/.syndication-output
const OUT_DIR = join(__dirname, '..', '..', '.syndication-output');

/**
 * Path to the package file for a given essay slug.
 * @param {string} slug
 */
export function packagePath(slug) {
  return join(OUT_DIR, `syndicate-${slug}.md`);
}

/**
 * Path to the paste-ready HTML companion of the markdown package. Open this in
 * a browser, select-all, copy, and paste into Medium/Substack's rich-text
 * editor — formatting and images survive without the markdown-in-editor dance.
 * @param {string} slug
 */
export function packageHtmlPath(slug) {
  return join(OUT_DIR, `syndicate-${slug}.html`);
}

/**
 * Seed the package with the body + canonical header if it doesn't exist yet.
 * Called by both manual adapters; safe to call repeatedly (idempotent).
 *
 * @param {object} opts
 * @param {string} opts.slug
 * @param {string} opts.title
 * @param {string} opts.canonicalUrl
 * @param {string} opts.bodyMarkdown   - sanitized Markdown (screenshots inlined)
 * @param {string[]} opts.tags
 * @returns {Promise<void>}
 */
export async function seedPackage({ slug, title, canonicalUrl, bodyMarkdown, tags = [] }) {
  await mkdir(OUT_DIR, { recursive: true });
  const file = packagePath(slug);

  // Only seed once per run — if the file already exists, the other adapter
  // already wrote the header and we just append our note.
  if (existsSync(file)) return;

  const header = [
    `# ${title}`,
    '',
    `Canonical URL: ${canonicalUrl}`,
    tags.length ? `Tags: ${tags.join(', ')}` : '',
    '',
    '---',
    '',
    bodyMarkdown.trim(),
    '',
    '---',
    '',
    '## How to publish this manually',
    '',
  ].join('\n');

  await writeFile(file, header, 'utf8');
}

/**
 * Append a platform-specific instruction to the package. Dedup: if a block for
 * the same platform already exists (from a prior run), it is replaced in place
 * rather than appended again — so re-running syndication doesn't grow the file
 * with duplicate Medium/Substack blocks each time.
 *
 * @param {object} opts
 * @param {string} opts.slug
 * @param {string} opts.platform      - e.g. "Medium", "Substack"
 * @param {string} opts.instructions  - the markdown instruction block
 * @returns {Promise<void>}
 */
export async function addPlatformNote({ slug, platform, instructions }) {
  const file = packagePath(slug);
  if (!existsSync(file)) {
    // Shouldn't happen if seedPackage was called first, but guard anyway.
    await mkdir(OUT_DIR, { recursive: true });
    await writeFile(file, '', 'utf8');
  }
  let existing = await readFile(file, 'utf8');

  // Strip any prior block for THIS platform before (re)appending, so repeated
  // syndication runs don't accumulate duplicate platform notes. A platform
  // block runs from its `**Platform**` marker up to the next `**Other**`
  // marker (or end of file). The preceding newline is consumed too, so the
  // strip is clean and re-running doesn't drift a blank line each time.
  const marker = `**${platform}**`;
  if (existing.includes(marker)) {
    const start = existing.indexOf(marker);
    // Find the next `**Platform**` marker after this one (line-anchored).
    const after = existing.slice(start + marker.length).match(/\n\*\*[A-Z][A-Za-z0-9 ]+\*\*/);
    const end = after ? start + marker.length + after.index : existing.length;
    // Eat the leading newline(s) immediately before the marker too.
    const leadStart = existing.slice(0, start).replace(/\n+$/, '');
    existing = leadStart + existing.slice(end);
  }

  const block = `\n**${platform}**\n\n${instructions}\n`;
  await writeFile(file, existing.trimEnd() + '\n' + block, 'utf8');
}

// Minimal inline styles so the HTML renders readably standalone AND pastes
// cleanly into rich-text editors (Medium/Substack). Kept deliberately small —
// elaborate CSS doesn't survive the paste into those editors.
const HTML_SHELL = (title, bodyHtml) => `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title}</title>
<style>
  body { max-width: 720px; margin: 2rem auto; padding: 0 1rem;
         font-family: Georgia, 'Times New Roman', serif; line-height: 1.6; color: #1a1a1a; }
  h1, h2, h3 { font-family: -apple-system, system-ui, sans-serif; line-height: 1.25; }
  code, pre { font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 0.9em; }
  pre { background: #f4f4f4; padding: 1rem; border-radius: 4px; overflow-x: auto; }
  table { border-collapse: collapse; }
  td, th { border: 1px solid #ccc; padding: 0.4rem 0.8rem; }
  img { max-width: 100%; }
  blockquote { border-left: 3px solid #ccc; margin: 0; padding-left: 1rem; color: #555; }
</style>
</head>
<body>
<article>
${bodyHtml.trim()}
</article>
</body>
</html>
`;

/**
 * Write the paste-ready HTML companion to the markdown package. Overwrites
 * unconditionally each run — the HTML is fully derived from the essay body,
 * so there's nothing to dedup and overwrite is the simplest idempotent path.
 *
 * @param {object} opts
 * @param {string} opts.slug
 * @param {string} opts.title
 * @param {string} opts.bodyHtml   - HTML fragment (from markdownToHtml)
 * @returns {Promise<void>}
 */
export async function writeHtmlPackage({ slug, title, bodyHtml }) {
  await mkdir(OUT_DIR, { recursive: true });
  await writeFile(packageHtmlPath(slug), HTML_SHELL(title, bodyHtml), 'utf8');
}
