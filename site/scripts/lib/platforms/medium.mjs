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
import { seedPackage, addPlatformNote } from '../manual-package.mjs';

export const name = 'medium';

export function available() {
  return true; // manual — no credentials needed
}

/**
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.bodyMarkdown
 * @param {string} opts.canonicalUrl
 * @param {string[]} opts.tags
 * @param {string} opts.slug
 * @returns {Promise<{id: string, url: string}>}
 */
export async function publish({ title, bodyMarkdown, canonicalUrl, tags, slug }) {
  await seedPackage({ slug, title, canonicalUrl, bodyMarkdown, tags });
  await addPlatformNote({
    slug,
    platform: 'Medium',
    instructions: [
      'Best option: use Medium → **Import a story** and paste the canonical URL',
      `(\`${canonicalUrl}\`). Medium scrapes your post and **automatically sets the`,
      'canonical link back to your site** — SEO-safe.',
      '',
      'Alternative: paste the body above into a new Medium story, then manually',
      `set the canonical link (⋯ → Story settings → Canonical link) to \`${canonicalUrl}\`.`,
    ].join('\n'),
  });
  return { id: 'manual', url: '(manual paste — see package artifact)' };
}

export function publicUrl() {
  return undefined;
}
