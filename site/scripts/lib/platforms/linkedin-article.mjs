// LinkedIn Article (the long-form 125k-char format):
//   - The /rest/posts API only creates feed posts (text/image/video, ~3-4k chars).
//   - The Article format is UI-only: there is no endpoint to create one.
//   - A feed post can't BE an Article, but it can share the Article's URL —
//     which is the flow: this adapter drafts the Article, and once it's
//     published and confirmed, linkedin.mjs posts the caption sharing it.
//
// ASSISTED-DRAFT tier: with a local session saved via
// `task posse:login -- linkedin`, this adapter opens the Article editor in
// headless Chromium, pastes the title + the paste-ready HTML body (the same
// rich-paste the manual flow uses), and waits for LinkedIn's autosave to
// persist the DRAFT. A human then reviews and clicks Publish. Articles DO
// support canonical URLs — the draft carries a reminder note to set it (the
// editor has no canonical field pre-publish UI hook we can drive reliably).
//
// LinkedIn runs the most aggressive automation detection of the four assisted
// platforms; the mitigation is human-scale volume (one article per blog post)
// and drafts-only — nothing goes public without a person clicking Publish.
// Fail-soft: any selector drift or auth bounce falls back to the manual
// package, never a bad publish.
//
// The short LinkedIn post (via Buffer) is the CAPTION for this Article: it
// posts automatically on the first syndication run AFTER `task posse:confirm
// -- <slug> linkedinArticle <url>` records the public URL (see linkedin.mjs).
import { seedPackage, addPlatformNote, packagePath, writeHtmlPackage, packageHtmlPath } from '../manual-package.mjs';
import { hasSession } from '../assisted-session.mjs';
import { withSessionBrowser, AuthError, pasteHtml } from '../browser-draft.mjs';

export const name = 'LinkedIn Article';

export function available() {
  return true; // assisted (with session) or manual package — no env credentials
}

// The Article editor's title/body. Title: a labeled textbox (or the first
// contenteditable heading); body: the large editor region. Match broadly so
// a redesign costs a fallback, not a failure.
async function findEditorFields(page) {
  const title =
    (await page.getByLabel(/title/i).first().isVisible().catch(() => false))
      ? page.getByLabel(/title/i).first()
      : (await page.getByRole('textbox').first().isVisible().catch(() => false))
        ? page.getByRole('textbox').first()
        : null;
  const body =
    (await page.getByLabel(/body|write your article/i).first().isVisible().catch(() => false))
      ? page.getByLabel(/body|write your article/i).first()
      : (await page.locator('[contenteditable="true"]').last().isVisible().catch(() => false))
        ? page.locator('[contenteditable="true"]').last()
        : null;
  return { title, body };
}

/**
 * Open the Article editor, paste title + body, wait for autosave. Returns
 * the draft editor URL, or null (with reason) on any failure.
 */
async function createArticleDraft({ title, bodyHtml }) {
  const result = await withSessionBrowser('linkedin', async (page) => {
    await page.goto('https://www.linkedin.com/article/new/', { waitUntil: 'domcontentloaded', timeout: 45_000 });
    if (/\/login|\/checkpoint/i.test(page.url())) throw new AuthError('linkedin');

    // Some accounts land the editor via /post/new → "Write article" instead.
    if (!/article|post\/new|publish/i.test(page.url())) {
      await page.goto('https://www.linkedin.com/post/new', { waitUntil: 'domcontentloaded', timeout: 45_000 });
      const writeArticle = page.getByText(/write an? article/i).first();
      if (await writeArticle.isVisible().catch(() => false)) await writeArticle.click();
    }

    // Dismiss any "welcome"-style modal that would swallow the first click.
    for (const label of [/skip/i, /dismiss/i, /not now/i, /close/i]) {
      const btn = page.getByRole('button', { name: label }).first();
      if (await btn.isVisible().catch(() => false)) await btn.click().catch(() => {});
    }

    const fields = await findEditorFields(page);
    if (!fields.title || !fields.body) {
      throw new Error('article editor fields not found (LinkedIn UI changed?)');
    }

    await fields.title.click();
    await page.keyboard.type(title, { delay: 10 });
    const pasted = await pasteHtml(page, fields.body, bodyHtml);
    if (!pasted) throw new Error('rich-text paste did not land in the editor body');

    // Autosave fires within a few seconds of typing; give it room, then
    // capture whatever URL the editor settled on as the draft link.
    await page.waitForTimeout(6_000);
    const url = page.url();
    if (!/article|post/i.test(url)) throw new Error(`unexpected editor URL: ${url}`);
    return url;
  });
  return result; // { value } | { value: null, authFailed, reason }
}

/**
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.bodyMarkdown
 * @param {string} opts.bodyHtml       - paste-ready HTML (draft body / manual paste)
 * @param {string} opts.canonicalUrl
 * @param {string[]} opts.tags
 * @param {string} opts.slug
 * @param {boolean} [opts.dryRun]      - never launch a browser when true
 * @returns {Promise<{id: string, url: string}>}
 */
export async function publish({ title, bodyMarkdown, bodyHtml, canonicalUrl, tags, slug, dryRun }) {
  let assistedNote = null;
  if (hasSession('linkedin')) {
    if (dryRun) {
      return { id: 'draft', url: '(dry-run: would paste the article into LinkedIn as a draft)' };
    }
    const { value: draftUrl, authFailed, reason } = await createArticleDraft({ title, bodyHtml });
    if (draftUrl) return { id: 'draft', url: draftUrl };
    assistedNote = authFailed
      ? '⚠ Assisted draft failed: session expired — re-run `task posse:login -- linkedin`.'
      : `⚠ Assisted draft failed: ${reason || 'unknown error'}. Falling back to manual.`;
  }

  await seedPackage({ slug, title, canonicalUrl, bodyMarkdown, tags });
  if (bodyHtml) await writeHtmlPackage({ slug, title, bodyHtml });
  await addPlatformNote({
    slug,
    platform: 'LinkedIn Article',
    instructions: [
      'LinkedIn Articles (the long-form format) cannot be created via the API —',
      'paste into the "Write an article" UI (~2 min; a local session can',
      'automate this draft — see the tip below).',
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
      '4. Publish, then record the public article URL:',
      '',
      `   task posse:confirm -- ${slug} linkedinArticle <article-url>`,
      '',
      '   The short LinkedIn post (via Buffer) is the caption for this article:',
      '   once confirmed, the next syndication run posts it sharing this URL.',
      '',
      'Tip: locally, `task posse:login -- linkedin` (once) + `task posse:assisted`',
      'creates the article draft for you; you review + Publish.',
      ...(assistedNote ? ['', assistedNote] : []),
    ].join('\n'),
  });
  return { id: 'manual', url: packagePath(slug) };
}

export function publicUrl() {
  return undefined;
}
