// Indie Hackers — no automation path exists in 2026:
//   - No public API (official or otherwise) for creating content.
//   - No inbound RSS auto-import (unlike Medium/dev.to/Hashnode).
//   - No post-by-email.
//   - Cookie-based Firebase auth only; no OAuth or API keys.
// The only supported way to publish is the markdown editor at /new-post —
// and posts go LIVE instantly there: no approval queue AND no server-side
// draft we could create and walk away from.
//
// ASSISTED tier (headed, human-in-the-loop by necessity): with a local
// session saved via `task posse:login -- indiehackers`, this adapter opens
// the editor in a VISIBLE browser, fills the title and pastes the markdown
// body (IH's editor is markdown-native), then waits while you review and
// click Post yourself. When the page navigates to the new post, the public
// URL is captured for you. Close the window instead to cancel.
//
// Because IH treats full-text posts as canonical (no canonical-URL field),
// the default paste is the SEO-safe teaser + link; the full markdown body
// sits in the package for the opt-in full paste.
import { seedPackage, addPlatformNote, packagePath, writeHtmlPackage, packageHtmlPath } from '../manual-package.mjs';
import { hasSession } from '../assisted-session.mjs';
import { withSessionBrowser, AuthError, pasteHtml } from '../browser-draft.mjs';

export const name = 'Indie Hackers';

export function available() {
  return true; // assisted (with session) or manual package — no env credentials
}

/** SEO-safe teaser body (markdown) for the default assisted paste. */
function teaserBody(socialPost, canonicalUrl) {
  return [
    socialPost || 'New post.',
    '',
    `**Read the full blog → ${canonicalUrl}**`,
  ].join('\n');
}

/**
 * Open /new-post in a HEADED browser with the saved session, fill title +
 * body, and hold the window open for the human to review and Post. Resolves
 * with the public post URL once the page navigates there, or null (reason)
 * if the window was closed/cancelled or anything failed before the handoff.
 */
async function assistedIhPost({ title, bodyMarkdown, socialPost, canonicalUrl, mode }) {
  const result = await withSessionBrowser(
    'indiehackers',
    async (page) => {
      await page.goto('https://www.indiehackers.com/new-post', { waitUntil: 'domcontentloaded', timeout: 45_000 });
      if (/\/signin|\/login|\/auth/i.test(page.url())) throw new AuthError('indiehackers');

      const titleField =
        (await page.getByLabel(/title/i).first().isVisible().catch(() => false))
          ? page.getByLabel(/title/i).first()
          : (await page.locator('input[type="text"]').first().isVisible().catch(() => false))
            ? page.locator('input[type="text"]').first()
            : null;
      const bodyField = await page.locator('[contenteditable="true"]').first().isVisible().catch(() => false)
        ? page.locator('[contenteditable="true"]').first()
        : null;
      if (!titleField || !bodyField) {
        throw new Error('editor fields not found (Indie Hackers UI changed?)');
      }

      await titleField.fill(title);
      // Markdown-native editor: the text/plain flavor carries the markdown.
      const body = mode === 'full' ? bodyMarkdown : teaserBody(socialPost, canonicalUrl);
      const pasted = await pasteHtml(page, bodyField, null, body);
      if (!pasted) throw new Error('markdown paste did not land in the editor body');

      // IH has no draft state — the human clicks Post in this visible window.
      // Resolve when the page lands on the published post, or bail if the
      // window closes first (that's a cancel). 15-minute patience cap.
      console.log('\n┌─ Indie Hackers ────────────────────────────────────');
      console.log('│ Editor opened with the post pre-filled.');
      console.log('│ Review it, then click Post (or close the window to cancel).');
      console.log('└────────────────────────────────────────────────────');
      const posted = page.waitForURL(/indiehackers\.com\/post\//, { timeout: 15 * 60_000 }).then(() => page.url());
      const closed = page.waitForEvent('close', { timeout: 15 * 60_000 }).then(() => null);
      const url = await Promise.race([posted, closed]);
      if (!url) throw new Error('cancelled — editor window closed before posting');
      return url;
    },
    { headed: true }
  );
  return result; // { value } | { value: null, authFailed, reason }
}

/**
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.bodyMarkdown
 * @param {string} opts.bodyHtml       - paste-ready HTML (manual fallback)
 * @param {string} opts.socialPost     - short blurb for a teaser intro
 * @param {string} opts.canonicalUrl
 * @param {string[]} opts.tags
 * @param {string} opts.slug
 * @param {boolean} [opts.dryRun]      - never launch a browser when true
 * @returns {Promise<{id: string, url: string}>}
 */
export async function publish({ title, bodyMarkdown, bodyHtml, socialPost, canonicalUrl, tags = [], slug, dryRun }) {
  const mode = process.env.IH_DRAFT_MODE === 'full' ? 'full' : 'teaser';
  let assistedNote = null;
  if (hasSession('indiehackers')) {
    if (dryRun) {
      return { id: 'draft', url: `(dry-run: would open the IH editor pre-filled with a ${mode} post)` };
    }
    const { value: postUrl, authFailed, reason } = await assistedIhPost({ title, bodyMarkdown, socialPost, canonicalUrl, mode });
    // A resolved URL here means the human already clicked Post — record it
    // directly as published (id = the URL), not as a draft link.
    if (postUrl) return { id: postUrl, url: postUrl };
    assistedNote = authFailed
      ? '⚠ Assisted flow failed: session expired — re-run `task posse:login -- indiehackers`.'
      : `⚠ Assisted flow not completed: ${reason || 'unknown error'}. Falling back to manual.`;
  }

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
      '',
      'Tip: locally, `task posse:login -- indiehackers` (once) + `task posse:assisted`',
      'opens the editor pre-filled; you review + click Post.',
      ...(assistedNote ? ['', assistedNote] : []),
    ].join('\n'),
  });
  return { id: 'manual', url: packagePath(slug) };
}

export function publicUrl() {
  return undefined;
}
