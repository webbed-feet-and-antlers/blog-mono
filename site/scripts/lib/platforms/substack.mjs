// Substack — no posting API exists anywhere (confirmed across Buffer, Postiz,
// dlvr.it, Narrareach, and Substack's own read-only 2026 Developer API). Also
// no canonical-URL support, so full-text cross-posts are an SEO duplicate risk.
//
// Manual platform: contributes to the shared syndication package artifact with
// the SEO-safe guidance (post a teaser + link rather than the full body).
import { seedPackage, addPlatformNote } from '../manual-package.mjs';

export const name = 'substack';

export function available() {
  return true; // manual — no credentials needed
}

/**
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.bodyMarkdown
 * @param {string} opts.socialPost     - short blurb for a teaser intro
 * @param {string} opts.canonicalUrl
 * @param {string} opts.slug
 * @returns {Promise<{id: string, url: string}>}
 */
export async function publish({ title, bodyMarkdown, socialPost, canonicalUrl, slug }) {
  await seedPackage({ slug, title, canonicalUrl, bodyMarkdown });
  await addPlatformNote({
    slug,
    platform: 'Substack',
    instructions: [
      'Substack has **no posting API** and **no canonical-URL support**. To avoid',
      'duplicate-content cannibalizing your own site, prefer posting only a teaser',
      'plus a "read more" link rather than the full body.',
      '',
      `Suggested teaser intro: ${socialPost || '(use the socialPost blurb)'}`,
      `Then link: "Read the full essay → ${canonicalUrl}"`,
      '',
      'If you do paste the full body, accept the SEO trade-off (Substack will be',
      'treated as canonical by Google).',
    ].join('\n'),
  });
  return { id: 'manual', url: '(manual paste — see package artifact)' };
}

export function publicUrl() {
  return undefined;
}
