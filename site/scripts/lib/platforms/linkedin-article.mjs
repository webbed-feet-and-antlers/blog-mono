// LinkedIn Article (the long-form 125k-char format) — MANUAL in 2026:
//   - The /rest/posts API only creates feed posts (text/image/video, ~3-4k chars).
//   - The Article format is UI-only: there is no endpoint to create one.
//   - And a feed post cannot deep-link into an Article as its expanded view —
//     they are independent surfaces.
//
// So the LinkedIn Article is a MANUAL platform, exactly like Medium/Substack:
// this adapter contributes to the shared syndication package artifact with the
// UI steps for LinkedIn's "Write an article" flow. The short LinkedIn post
// (the headline + canonical link, posted via Buffer) is a SEPARATE surface and
// stays fully automated in linkedin.mjs — this adapter does not replace it.
//
// LinkedIn Articles DO support canonical URLs (unlike Substack), so the
// instructions emphasize setting it to avoid duplicate-content cannibalization
// of our own site.
import { seedPackage, addPlatformNote, packagePath, writeHtmlPackage, packageHtmlPath } from '../manual-package.mjs';

export const name = 'LinkedIn Article';

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
    platform: 'LinkedIn Article',
    instructions: [
      'LinkedIn Articles (the long-form format) cannot be created via the API —',
      'this is a manual paste into the "Write an article" UI (~2 min).',
      '',
      '1. Open LinkedIn → **Write article**: <https://www.linkedin.com/post/new>',
      '   (or Home → "Write article" under the post composer).',
      '2. Paste the title and the body. Best path: open the HTML companion file,',
      '   select-all → copy → paste into the article editor (rich text survives):',
      '',
      `<${packageHtmlPath(slug)}>`,
      '',
      '3. **Set the canonical URL** to avoid duplicate-content cannibalizing your',
      '   own site (⋯ → Settings → Canonical URL):',
      '',
      `<${canonicalUrl}>`,
      '',
      '4. Publish. The short headline post (posted automatically via Buffer) and',
      '   this Article are independent surfaces — there is no deep-link between',
      '   them, so readers find the Article from your profile or the feed.',
    ].join('\n'),
  });
  return { id: 'manual', url: packagePath(slug) };
}

export function publicUrl() {
  return undefined;
}
