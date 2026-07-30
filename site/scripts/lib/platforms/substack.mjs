// Substack — no posting API exists anywhere (confirmed across Buffer, Postiz,
// dlvr.it, Narrareach, and Substack's own read-only 2026 Developer API). Also
// no canonical-URL support, so full-text cross-posts are an SEO duplicate risk.
//
// Manual platform: contributes to the shared syndication package artifact with
// the SEO-safe guidance (post a teaser + link rather than the full body).
import { seedPackage, addPlatformNote, packagePath, writeHtmlPackage, packageHtmlPath } from '../manual-package.mjs';

export const name = 'substack';

export function available() {
  return true; // manual — no credentials needed
}

/**
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.bodyMarkdown
 * @param {string} opts.bodyHtml       - paste-ready HTML (for the teaser paste)
 * @param {string} opts.socialPost     - short blurb for a teaser intro
 * @param {string} opts.canonicalUrl
 * @param {string} opts.slug
 * @returns {Promise<{id: string, url: string}>} url is the package file path
 */
export async function publish({ title, bodyMarkdown, bodyHtml, socialPost, canonicalUrl, slug }) {
  await seedPackage({ slug, title, canonicalUrl, bodyMarkdown });
  if (bodyHtml) await writeHtmlPackage({ slug, title, bodyHtml });
  const substackPub = process.env.SUBSTACK_PUB || 'theinkpens';
  await addPlatformNote({
    slug,
    platform: 'Substack',
    instructions: [
      'Substack has **no posting API** and **no canonical-URL support**. To avoid',
      'duplicate-content cannibalizing your own site, prefer posting only a teaser',
      'plus a "read more" link rather than the full body.',
      '',
      '**New post editor:**',
      '',
      `<https://${substackPub}.substack.com/publish/post>`,
      '',
      `Suggested teaser intro: ${socialPost || '(use the socialPost blurb)'}`,
      'Then link: "Read the full essay → " followed by:',
      '',
      `<${canonicalUrl}>`,
      '',
      'To paste formatted content (teaser or full body), open the HTML companion,',
      'select-all → copy → paste into the Substack editor:',
      '',
      `<${packageHtmlPath(slug)}>`,
      '',
      'If you do paste the full body, accept the SEO trade-off (Substack will be',
      'treated as canonical by Google).',
    ].join('\n'),
  });
  return { id: 'manual', url: packagePath(slug) };
}

export function publicUrl() {
  return undefined;
}
