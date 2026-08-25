// Indie Hackers — no automation path exists in 2026:
//   - No public API (official or otherwise) for creating content.
//   - No inbound RSS auto-import (unlike Medium/dev.to/Hashnode).
//   - No post-by-email.
//   - Cookie-based Firebase auth only; no OAuth or API keys.
// The only supported way to publish is the markdown editor at /new-post,
// which goes live instantly with no approval queue.
//
// Manual platform: contributes to the shared syndication package artifact
// with SEO-safe guidance (IH has no canonical-URL field, so prefer posting a
// teaser + "read more" link rather than the full body to avoid duplicate content
// cannibalizing your own site).
import { seedPackage, addPlatformNote, packagePath, writeHtmlPackage, packageHtmlPath } from '../manual-package.mjs';

export const name = 'Indie Hackers';

export function available() {
  return true; // manual — no credentials needed
}

/**
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.bodyMarkdown
 * @param {string} opts.bodyHtml       - paste-ready HTML (for the full-body paste)
 * @param {string} opts.socialPost     - short blurb for a teaser intro
 * @param {string} opts.canonicalUrl
 * @param {string[]} opts.tags
 * @param {string} opts.slug
 * @returns {Promise<{id: string, url: string}>} url is the package file path
 */
export async function publish({ title, bodyMarkdown, bodyHtml, socialPost, canonicalUrl, tags = [], slug }) {
  await seedPackage({ slug, title, canonicalUrl, bodyMarkdown, tags });
  if (bodyHtml) await writeHtmlPackage({ slug, title, bodyHtml });
  const topics = tags.length ? tags.join(', ') : '(none — add 1–3 relevant topics)';
  await addPlatformNote({
    slug,
    platform: 'Indie Hackers',
    instructions: [
      'Indie Hackers has **no API, no RSS import, and no canonical-URL field**.',
      'Posts go live instantly (no approval queue) at the markdown editor below.',
      'Suggested topics: ' + topics,
      '',
      '**New post editor:**',
      '',
      '<https://www.indiehackers.com/new-post>',
      '',
      '**Option A — teaser + link (SEO-safe, recommended):**',
      '  Indie Hackers will be treated as canonical by Google if you post the full',
      '  body, so prefer a teaser that drives readers to your own site.',
      `  Teaser intro: ${socialPost || '(use the socialPost blurb)'}`,
      '  Then link: "Read the full blog →" followed by:',
      `  <${canonicalUrl}>`,
      '',
      '**Option B — full text (accept the SEO trade-off):**',
      '  Open the HTML companion, select-all → copy → paste into the editor',
      '  (Indie Hackers\' editor is markdown-native, so the markdown body above',
      '  pastes cleanly too):',
      `  <${packageHtmlPath(slug)}>`,
      '  Lead with: "Originally published at " + the canonical URL so readers',
      '  can find the canonical version.',
    ].join('\n'),
  });
  return { id: 'manual', url: packagePath(slug) };
}

export function publicUrl() {
  return undefined;
}
