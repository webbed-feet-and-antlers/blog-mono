#!/usr/bin/env node
// `task posse:confirm -- <slug> <platform> <url>` — record that a human
// published an assisted draft. Moves the platform from `draftLinks:` to
// `syndication:` (full public URL), so idempotency and the
// "Also published on" footer both pick it up.
//
// Usage:
//   task posse:confirm -- my-post substack https://theinkpens.substack.com/p/my-post
//   node --env-file-if-exists=.env scripts/confirm.mjs my-post medium https://medium.com/p/abc123
//
// Omit the URL to see the saved draft link (e.g. to copy the public URL
// after publishing from it).
import { loadBlog } from './lib/blogs.mjs';
import { writeSyndicationIds, clearDraftLink } from './lib/frontmatter.mjs';

const ASSISTED_KEYS = ['substack', 'medium', 'linkedinArticle', 'indiehackers'];

const [slug, platform, url] = process.argv.slice(2);

if (!slug || !platform) {
  console.error('Usage: node scripts/confirm.mjs <slug> <substack|medium|linkedinArticle|indiehackers> [public-url]');
  process.exit(1);
}
if (!ASSISTED_KEYS.includes(platform)) {
  console.error(`Unknown platform "${platform}". Expected one of: ${ASSISTED_KEYS.join(', ')}`);
  process.exit(1);
}

const blog = await loadBlog(slug);
if (!blog) {
  console.error(`Blog not found: ${slug}`);
  process.exit(1);
}

const draft = blog.data.draftLinks?.[platform];
const published = blog.data.syndication?.[platform];

if (!url) {
  if (published) console.log(`Already confirmed: ${platform} → ${published}`);
  if (draft) console.log(`Draft link (open it, publish, then re-run with the public URL):\n  ${draft}`);
  else console.log(`No draft link saved for ${platform} on ${slug}.`);
  process.exit(0);
}

if (!/^https?:\/\//.test(url)) {
  console.error(`The URL must be the public post URL (got: ${url})`);
  process.exit(1);
}

const wroteSyndication = await writeSyndicationIds(blog.path, { [platform]: url });
const clearedDraft = draft ? await clearDraftLink(blog.path, platform) : false;

if (wroteSyndication || clearedDraft) {
  console.log(`✓ ${slug}: ${platform} recorded as published`);
  console.log(`  ${url}`);
} else {
  console.log(`No changes needed (already recorded).`);
}
