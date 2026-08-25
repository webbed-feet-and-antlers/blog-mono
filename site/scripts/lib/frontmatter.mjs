// Reads frontmatter + writes syndication state back into the blog file.
//
// The write-back is SURGICAL: only the targeted block (`syndication:` or
// `draftLinks:`) in the frontmatter is touched. Every other frontmatter field
// (quotes, date format, array style, field order) and the entire body are
// preserved byte-for-byte. This keeps the auto-commit diffs minimal and never
// rewrites the author's formatting.
import { readFile, writeFile } from 'node:fs/promises';
import matter from 'gray-matter';
import { stringify as stringifyYaml } from 'yaml';

const FRONTMATTER_RE = /^---\r?\n([\s\S]*?)\r?\n---\r?\n([\s\S]*)$/;

/**
 * Parse out the three regions we care about.
 * @param {string} raw
 * @returns {{ frontmatterText: string, body: string } | null}
 */
function splitRaw(raw) {
  const m = raw.match(FRONTMATTER_RE);
  if (!m) return null;
  return { frontmatterText: m[1], body: m[2] };
}

/**
 * Merge entries into a frontmatter map block (e.g. `syndication:` or
 * `draftLinks:`) by editing the raw frontmatter text directly — not by
 * re-stringifying the whole object.
 *
 * Strategy:
 *  - Parse the frontmatter once (gray-matter) to get the current map.
 *  - Merge the new entries into it.
 *  - Re-emit ONLY that block as YAML, and:
 *      • if the block already exists -> replace it in place;
 *      • otherwise append a new block at the end of the frontmatter.
 *
 * @param {string} filePath
 * @param {string} key                 - top-level frontmatter key ("syndication", "draftLinks")
 * @param {Record<string, string|number|undefined>} entries - entries to merge in
 * @returns {Promise<boolean>} true if the file changed
 */
async function writeMapBlock(filePath, key, entries) {
  const raw = await readFile(filePath, 'utf8');
  const parts = splitRaw(raw);
  if (!parts) throw new Error(`${filePath}: could not parse frontmatter`);

  const parsed = matter(raw);
  const existing = parsed.data[key] ?? {};
  const merged = { ...existing };
  let changed = false;
  for (const [k, value] of Object.entries(entries)) {
    if (value === undefined || value === null) continue;
    if (String(existing[k]) !== String(value)) {
      merged[k] = value;
      changed = true;
    }
  }
  if (!changed) return false;

  // Emit just the block in block style.
  const block = `${key}:\n` + stringifyYaml(merged)
    .split('\n')
    .filter((l) => l.length) // drop the trailing blank line yaml emits
    .map((l) => '  ' + l)    // indent under the key:
    .join('\n');

  let fm = parts.frontmatterText;
  // Match an existing block: the key line + its indented children, where the
  // FINAL child line does not consume its trailing newline (backtracking off
  // the star group handles that) — so a following block (e.g. draftLinks:
  // after syndication:) stays cleanly separated. LF line endings assumed,
  // same as the rest of the pipeline.
  const blockRe = new RegExp(`(^|\\n)${key}:\\n(?:[ \\t]+.+\\n)*[ \\t]+.+`);
  if (blockRe.test(fm)) {
    fm = fm.replace(blockRe, `$1${block}`);
  } else {
    fm = fm.trimEnd() + '\n' + block;
  }

  const next = `---\n${fm}\n---\n${parts.body}`;
  if (next === raw) return false;
  await writeFile(filePath, next, 'utf8');
  return true;
}

/**
 * Merge new syndication IDs into the `syndication:` block. Presence of an ID
 * means "published on this platform" (idempotency + the Also-published-on
 * footer) — never write draft links here, use writeDraftLinks.
 *
 * @param {string} filePath
 * @param {Record<string, string|number|undefined>} newIds
 * @returns {Promise<boolean>} true if the file changed
 */
export async function writeSyndicationIds(filePath, newIds) {
  return writeMapBlock(filePath, 'syndication', newIds);
}

/**
 * Merge new editor links into the `draftLinks:` block. A draft link means
 * "an assisted adapter created a draft; a human still needs to hit Publish"
 * — it suppresses re-drafting but must never render on the site.
 *
 * @param {string} filePath
 * @param {Record<string, string|undefined>} newLinks
 * @returns {Promise<boolean>} true if the file changed
 */
export async function writeDraftLinks(filePath, newLinks) {
  return writeMapBlock(filePath, 'draftLinks', newLinks);
}

/**
 * Remove a single platform's draft link (after `posse:confirm` moves it into
 * `syndication:`). If the block becomes empty it is removed entirely.
 *
 * @param {string} filePath
 * @param {string} platform - the draftLinks sub-key to clear
 * @returns {Promise<boolean>} true if the file changed
 */
export async function clearDraftLink(filePath, platform) {
  const raw = await readFile(filePath, 'utf8');
  const parts = splitRaw(raw);
  if (!parts) throw new Error(`${filePath}: could not parse frontmatter`);

  const parsed = matter(raw);
  const existing = parsed.data.draftLinks ?? {};
  if (!(platform in existing)) return false;
  delete existing[platform];

  let fm = parts.frontmatterText;
  // Same non-newline-consuming shape as writeMapBlock: the final child line
  // keeps its newline when more frontmatter follows, so rewriting or
  // removing the block never glues neighboring lines together.
  const blockRe = /(^|\n)draftLinks:\n(?:[ \t]+.+\n)*[ \t]+.+/;
  if (Object.keys(existing).length === 0) {
    // Last entry removed — drop the whole block.
    fm = fm.replace(blockRe, '');
  } else {
    const block = 'draftLinks:\n' + stringifyYaml(existing)
      .split('\n')
      .filter((l) => l.length)
      .map((l) => '  ' + l)
      .join('\n');
    fm = fm.replace(blockRe, `$1${block}`);
  }

  const next = `---\n${fm}\n---\n${parts.body}`;
  if (next === raw) return false;
  await writeFile(filePath, next, 'utf8');
  return true;
}
