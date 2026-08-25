// Reads frontmatter + writes syndication IDs back into the blog file.
//
// The write-back is SURGICAL: only the `syndication:` block in the frontmatter
// is touched. Every other frontmatter field (quotes, date format, array style,
// field order) and the entire body are preserved byte-for-byte. This keeps the
// auto-commit diffs minimal and never rewrites the author's formatting.
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
 * Merge new syndication IDs into the existing frontmatter block by editing the
 * raw frontmatter text directly (not by re-stringifying the whole object).
 *
 * Strategy:
 *  - Parse the frontmatter once (gray-matter) to get the current syndication map.
 *  - Merge new IDs into it.
 *  - Re-emit ONLY the syndication block as YAML, and:
 *      • if a `syndication:` block already exists -> replace it in place;
 *      • otherwise append a new block at the end of the frontmatter.
 *
 * @param {string} filePath
 * @param {Record<string, string|number|undefined>} newIds
 * @returns {Promise<boolean>} true if the file changed
 */
export async function writeSyndicationIds(filePath, newIds) {
  const raw = await readFile(filePath, 'utf8');
  const parts = splitRaw(raw);
  if (!parts) throw new Error(`${filePath}: could not parse frontmatter`);

  const parsed = matter(raw);
  const existing = parsed.data.syndication ?? {};
  const merged = { ...existing };
  let changed = false;
  for (const [key, value] of Object.entries(newIds)) {
    if (value === undefined || value === null) continue;
    if (String(existing[key]) !== String(value)) {
      merged[key] = value;
      changed = true;
    }
  }
  if (!changed) return false;

  // Emit just the syndication block in block style.
  const syndBlock = 'syndication:\n' + stringifyYaml(merged)
    .split('\n')
    .filter((l) => l.length) // drop the trailing blank line yaml emits
    .map((l) => '  ' + l)    // indent under syndication:
    .join('\n');

  let fm = parts.frontmatterText;
  // Match an existing syndication: block (key + its indented children).
  const syndRe = /(^|\n)syndication:\n(?:[ \t]+.+\n?)*/;
  if (syndRe.test(fm)) {
    fm = fm.replace(syndRe, `$1${syndBlock}`);
  } else {
    fm = fm.trimEnd() + '\n' + syndBlock;
  }

  const next = `---\n${fm}\n---\n${parts.body}`;
  if (next === raw) return false;
  await writeFile(filePath, next, 'utf8');
  return true;
}
