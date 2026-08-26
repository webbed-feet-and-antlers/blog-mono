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
// contenteditable heading); body: the large editor region. These exact
// selectors are the pair verified end-to-end (paste → autosave with content
// → persisted draft): the FIRST textbox is the title, the LAST
// contenteditable is the body. The earlier label-based lookup pasted into an
// element LinkedIn's editor model doesn't track — the DOM grew but saves
// carried empty content.
async function findEditorFields(page) {
  const title = (await page.getByRole('textbox').first().isVisible().catch(() => false))
    ? page.getByRole('textbox').first()
    : null;
  const body = (await page.locator('[contenteditable="true"]').last().isVisible().catch(() => false))
    ? page.locator('[contenteditable="true"]').last()
    : null;
  return { title, body };
}

/**
 * Open the Article editor, paste title + body, wait for autosave. Returns
 * the draft editor URL, or null (with reason) on any failure.
 */
async function createArticleDraft({ title, bodyHtml }) {
  // Create the draft via LinkedIn's own voyager REST API (same-origin fetch
  // with the session cookie + JSESSIONID csrf token), bypassing the editor
  // entirely. The editor-paste approach proved unfixable: the DOM accepts the
  // paste but LinkedIn's editor model silently drops it, autosaving an empty
  // draft — while POSTing contentHtml directly works (verified 201 + rendered
  // in the editor afterwards).
  const result = await withSessionBrowser(
    'linkedin',
    async (page) => {
      await page.goto('https://www.linkedin.com/article/new/', { waitUntil: 'domcontentloaded', timeout: 45_000 });
      if (/\/login|\/checkpoint/i.test(page.url())) throw new AuthError('linkedin');

      // contentHtml may only reference LinkedIn-hosted media — foreign <img>
      // srcs make the create 400 ("error adding your image"). Strip images;
      // the mdxToMarkdown "Try this demo live" caption links survive and
      // point readers at the live interactive versions on the canonical site.
      const safeHtml = bodyHtml
        .replace(/<picture[^>]*>[\s\S]*?<\/picture>/g, '')
        .replace(/<img[^>]*>/g, '');
      const res = await page.evaluate(async ({ title, bodyHtml, profileUrn }) => {
        const csrf = decodeURIComponent(
          (document.cookie.split('; ').find((c) => c.startsWith('JSESSIONID=')) ?? '').replace(/^JSESSIONID=/, '').replace(/^"|"$/g, '')
        );
        const r = await fetch('/voyager/api/voyagerPublishingDashFirstPartyArticles/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'csrf-token': csrf, 'X-Restli-Method': 'create' },
          body: JSON.stringify({
            authors: [{ profileUrn }],
            title,
            contentHtml: bodyHtml,
            state: 'AUTOSAVED',
          }),
        });
        return { status: r.status, json: await r.json().catch(() => null) };
      }, {
        title,
        bodyHtml: safeHtml,
        profileUrn: process.env.LINKEDIN_PROFILE_URN || 'urn:li:fsd_profile:ACoAACWJuRoBEsIwpKARLD4NyKRu9tsdIndFr9g',
      });

      if (res.status !== 201 || !res.json?.entityUrn) {
        throw new Error(`article create API ${res.status}: ${JSON.stringify(res.json).slice(0, 200)}`);
      }
      const articleId = res.json.entityUrn.split(':').pop();

      // Verify the draft renders with content before calling it a success.
      const editUrl = `https://www.linkedin.com/article/edit/${articleId}/`;
      await page.goto(editUrl, { waitUntil: 'domcontentloaded', timeout: 45_000 });
      await page.waitForTimeout(6_000);
      const len = await page.evaluate(() => {
        const editables = [...document.querySelectorAll('[contenteditable="true"]')];
        return Math.max(0, ...editables.map((e) => e.textContent.length));
      });
      if (len < 200) throw new Error(`draft created but renders empty (${len} chars)`);
      console.log(`    linkedin draft created via API: article ${articleId}, body renders ${len} chars`);
      return editUrl;
    },
    { headed: true, channel: 'chrome' }
  );
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
      'LinkedIn Articles: a local session creates a draft with the FULL TEXT',
      'via the voyager API (linked in draftLinks above) — but LinkedIn\'s',
      'server-side conversion strips formatting, so the draft is plain text.',
      'A HUMAN paste preserves headings/bold/code/lists (the editor\'s own',
      'clipboard pipeline); automation of that step is blocked by their',
      'anti-automation throttling.',
      '',
      '1. Open the API-created draft (the draftLinks URL above).',
      '2. Select-all of the body, then paste over it from the HTML companion:',
      '',
      `<${packageHtmlPath(slug)}>`,
      '',
      '3. Optionally drag in the component screenshots from',
      '   inkpens.tech/sshot/… at the "Try this demo live" captions.',
      '4. **Set the canonical URL** (⋯ → Settings → Canonical URL):',
      '',
      `<${canonicalUrl}>`,
      '',
      '5. Publish, then record the public article URL:',
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
