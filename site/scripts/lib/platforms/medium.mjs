// Medium — no robust free automation path in 2026:
//   - The REST API is closed to new integrations (tokens stopped Jan 2025).
//   - There is no inbound RSS feature on Medium.
//   - No post-by-email.
//   - The only "Import a story" flow is browser-only, not API-accessible, and
//     driving it via Playwright would be the most fragile piece of the whole
//     pipeline (third-party UI automation in a flow that posts publicly).
//
// So Medium is a MANUAL platform: this adapter contributes to the shared
// syndication package artifact with the SEO-correct instructions (use Medium's
// "Import a story" so the canonical link points back to our own site). The
// package file is uploaded by the workflow for a ~30-second manual paste.
import { seedPackage, addPlatformNote, packagePath, writeHtmlPackage, packageHtmlPath } from '../manual-package.mjs';

export const name = 'medium';

export function available() {
  return true; // manual — no credentials needed
}

/**
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.bodyMarkdown
 * @param {string} opts.bodyHtml       - paste-ready HTML (for rich-text paste)
 * @param {string} opts.canonicalUrl
 * @param {string[]} opts.tags
 * @param {string} opts.slug
 * @returns {Promise<{id: string, url: string}>} url is the package file path
 */
export async function publish({ title, bodyMarkdown, bodyHtml, canonicalUrl, tags, slug }) {
  await seedPackage({ slug, title, canonicalUrl, bodyMarkdown, tags });
  if (bodyHtml) await writeHtmlPackage({ slug, title, bodyHtml });
  await addPlatformNote({
    slug,
    platform: 'Medium',
    instructions: [
      '**Best option — Import a story (SEO-safe, sets canonical automatically):**',
      '',
      '1. Open your stories: <https://medium.com/me/stories>',
      '2. Click **Import a story**',
      '3. Paste the canonical URL (Medium scrapes your post and sets the canonical',
      '   link back to your site automatically):',
      '',
      `<${canonicalUrl}>`,
      '',
      '**Alternative — paste as HTML (skip the scraper, e.g. if it fails):**',
      '',
      `Open the HTML companion file (select-all → copy → paste into a new story):`,
      '',
      `<${packageHtmlPath(slug)}>`,
      'Then manually set the canonical link (⋯ → Story settings → Canonical link)',
      'to the URL above.',
    ].join('\n'),
  });
  return { id: 'manual', url: packagePath(slug) };
}

export function publicUrl() {
  return undefined;
}
