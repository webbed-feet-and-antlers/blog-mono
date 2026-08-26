// Medium — no OFFICIAL automation path in 2026:
//   - The REST API is closed to new integrations (tokens stopped Jan 2025).
//   - There is no inbound RSS feature on Medium.
//   - No post-by-email.
//
// ASSISTED-DRAFT tier: with a local session saved via
// `task posse:login -- medium`, this adapter drives the official "Import a
// story" flow (https://medium.com/p/import) in headless Chromium — exactly
// the flow the manual instructions use, so Medium scrapes the live canonical
// page itself, re-hosts its images, and sets the canonical link back to our
// site. We stop at the imported DRAFT (Medium saves it under me/stories →
// drafts); a human reviews and clicks Publish.
//
// We knowingly accept UI fragility here because the tier is fail-soft: on a
// login redirect or selector drift, withSessionBrowser resolves null and this
// adapter falls back to the manual package — never a bad publish.
//
// Requires the canonical URL to be LIVE (the site must be deployed before
// syndicating — the standard POSSE order anyway).
import { seedPackage, addPlatformNote, packagePath, writeHtmlPackage, packageHtmlPath } from '../manual-package.mjs';
import { hasSession } from '../assisted-session.mjs';
import { withSessionBrowser, AuthError } from '../browser-draft.mjs';

export const name = 'medium';

export function available() {
  return true; // assisted (with session) or manual package — no env credentials
}

// The import dialog's URL input is the only textbox on /p/import. Match
// broadly (role first, then any input) so a redesign only costs us a
// fallback, not a failure.
async function fillImportUrl(page, canonicalUrl) {
  const candidates = [
    page.getByRole('textbox').first(),
    page.locator('input[type="url"], input[type="text"]').first(),
  ];
  for (const input of candidates) {
    if (await input.isVisible().catch(() => false)) {
      await input.fill(canonicalUrl);
      return true;
    }
  }
  return false;
}

/**
 * Drive the Import-a-story flow to a saved draft. Returns the draft editor
 * URL, or null (with reason) on any failure.
 *
 * Runs in REAL headed Chrome: Medium fronts /p/import with Cloudflare, which
 * serves headless Chromium an "Attention Required!" interstitial (that's what
 * the earlier "import URL input not found" failures actually were).
 */
async function importStoryDraft(canonicalUrl) {
  const result = await withSessionBrowser(
    'medium',
    async (page) => {
      await page.goto('https://medium.com/p/import', { waitUntil: 'domcontentloaded', timeout: 45_000 });
      if (/sign-?in|\/login|attention required/i.test(page.url() + ' ' + (await page.title()))) {
        throw new AuthError('medium');
      }

      // The import dialog input + submit. Enter usually submits; some UI
      // variants need the "Import" button clicked instead.
      if (!(await fillImportUrl(page, canonicalUrl))) {
        throw new Error('import URL input not found (Medium UI changed?)');
      }
      await page.keyboard.press('Enter');
      const importBtn = page.getByRole('button', { name: /^import/i }).first();
      if (await importBtn.isVisible().catch(() => false)) await importBtn.click().catch(() => {});

      // Medium fetches + converts the story, then lands in the editor at
      // /p/<id>/edit. Generous timeout: the scrape takes 10–30s+.
      await page.waitForURL(/\/p\/[^/]+\/edit/, { timeout: 120_000 });
      // Give the autosave a beat so the draft is durably under me/stories/drafts.
      await page.waitForTimeout(3_000);
      return page.url();
    },
    { headed: true, channel: 'chrome' }
  );
  return result; // { value } | { value: null, authFailed, reason }
}

/**
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.bodyMarkdown
 * @param {string} opts.bodyHtml       - paste-ready HTML (manual fallback)
 * @param {string} opts.canonicalUrl   - must be LIVE (Medium scrapes it)
 * @param {string[]} opts.tags
 * @param {string} opts.slug
 * @param {boolean} [opts.dryRun]      - never launch a browser when true
 * @returns {Promise<{id: string, url: string}>}
 */
export async function publish({ title, bodyMarkdown, bodyHtml, canonicalUrl, tags, slug, dryRun }) {
  let assistedNote = null;
  if (hasSession('medium')) {
    if (dryRun) {
      return { id: 'draft', url: '(dry-run: would import the canonical URL as a Medium draft)' };
    }
    const { value: draftUrl, authFailed, reason } = await importStoryDraft(canonicalUrl);
    if (draftUrl) return { id: 'draft', url: draftUrl };
    assistedNote = authFailed
      ? '⚠ Assisted draft failed: session expired — re-run `task posse:login -- medium`.'
      : `⚠ Assisted draft failed: ${reason || 'unknown error'}. Falling back to manual.`;
  }

  await seedPackage({ slug, title, canonicalUrl, bodyMarkdown, tags });
  if (bodyHtml) await writeHtmlPackage({ slug, title, bodyHtml });
  await addPlatformNote({
    slug,
    platform: 'Medium',
    instructions: [
      '**Best option — Import a story (SEO-safe, sets canonical automatically):**',
      '',
      '1. Open your stories: <https://medium.com/me/stories>',
      '2. Click **Import a story** (or go straight to <https://medium.com/p/import>)',
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
      '',
      'Tip: locally, `task posse:login -- medium` (once) + `task posse:assisted`',
      'automates the import — it stops at the draft; you review + Publish.',
      ...(assistedNote ? ['', assistedNote] : []),
    ].join('\n'),
  });
  return { id: 'manual', url: packagePath(slug) };
}

export function publicUrl() {
  return undefined;
}
