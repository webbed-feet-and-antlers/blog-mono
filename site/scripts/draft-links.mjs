#!/usr/bin/env node
// Print one draft-editor URL per line for a blog's saved draftLinks — used
// by the `posse:open-manual` task to deep-link straight into each platform's
// editor instead of the platform homepage. Exit 0 with no output when there
// are no drafts (the task falls back to the generic editor tabs).
//
// Usage: node scripts/draft-links.mjs <slug>
import { loadBlog } from './lib/blogs.mjs';

const [slug] = process.argv.slice(2);
if (!slug) {
  console.error('Usage: node scripts/draft-links.mjs <slug>');
  process.exit(1);
}

const blog = await loadBlog(slug);
if (!blog) {
  console.error(`Blog not found: ${slug}`);
  process.exit(1);
}

for (const url of Object.values(blog.data.draftLinks ?? {})) {
  if (url) console.log(url);
}
