// Reads blogs directly from the repo-root /blogs folder (outside site/),
// bypassing the Astro content layer so the script can run standalone in CI.
import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { readdir } from 'node:fs/promises';
import { join, extname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { dirname } from 'node:path';
import matter from 'gray-matter';

const __dirname = dirname(fileURLToPath(import.meta.url));
// scripts/lib/ -> up to site/ -> up to repo root -> blogs/
const BLOGS_DIR = join(__dirname, '..', '..', '..', 'blogs');

/**
 * @typedef {Object} Blog
 * @property {string} slug     - filename without extension (the URL slug)
 * @property {string} path     - absolute path to the .md/.mdx file
 * @property {string} ext      - '.md' or '.mdx'
 * @property {object} data     - parsed frontmatter
 * @property {string} body     - raw markdown/mdx body (frontmatter stripped)
 */

async function* walk(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      yield* walk(full);
    } else if (entry.isFile()) {
      yield full;
    }
  }
}

/**
 * Load a single blog by slug.
 * @param {string} slug
 * @returns {Promise<Blog | null>}
 */
export async function loadBlog(slug) {
  for (const ext of ['.mdx', '.md']) {
    const candidate = join(BLOGS_DIR, slug + ext);
    if (existsSync(candidate)) {
      return readBlog(candidate);
    }
  }
  return null;
}

/**
 * @param {string} filePath
 * @returns {Promise<Blog>}
 */
async function readBlog(filePath) {
  const raw = await readFile(filePath, 'utf8');
  const parsed = matter(raw);
  const ext = extname(filePath);
  const slug = basename(filePath, ext);
  return {
    slug,
    path: filePath,
    ext,
    data: parsed.data,
    body: parsed.content,
  };
}

/**
 * Load every blog in /blogs. Drafts are included so the script can decide.
 * @returns {Promise<Blog[]>}
 */
export async function loadBlogs() {
  if (!existsSync(BLOGS_DIR)) return [];
  const out = [];
  for await (const file of walk(BLOGS_DIR)) {
    const ext = extname(file).toLowerCase();
    if (ext !== '.md' && ext !== '.mdx') continue;
    out.push(await readBlog(file));
  }
  return out;
}

export { BLOGS_DIR };
