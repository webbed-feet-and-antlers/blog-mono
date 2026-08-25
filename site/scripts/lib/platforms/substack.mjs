// Substack — no OFFICIAL posting API exists anywhere (confirmed across Buffer,
// Postiz, dlvr.it, Narrareach, and Substack's own read-only 2026 Developer
// API). Also no canonical-URL support, so full-text cross-posts are an SEO
// duplicate risk.
//
// ASSISTED-DRAFT tier: the web editor talks to an internal JSON API
// (POST /api/v1/drafts with the `substack.sid` session cookie — see the
// community-verified reference at
// https://github.com/AnthonyDavidAdams/substack-api-reference). With a local
// session saved via `task posse:login -- substack`, this adapter creates an
// unpublished DRAFT there and hands off — a human reviews and clicks Publish.
// The draft request rides the Playwright session's page (same-origin fetch
// with real browser cookies) because Substack 403s plain non-browser clients.
//
// Without a session (or on any failure), this is the MANUAL platform as
// before: it contributes to the shared syndication package artifact with the
// SEO-safe guidance (post a teaser + link rather than the full body).
import { seedPackage, addPlatformNote, packagePath, writeHtmlPackage, packageHtmlPath } from '../manual-package.mjs';
import { hasSession } from '../assisted-session.mjs';
import { withSessionBrowser, AuthError } from '../browser-draft.mjs';

export const name = 'substack';

export function available() {
  return true; // assisted (with session) or manual package — no env credentials
}

/** Minimal HTML escaping for the title/blurb we interpolate into draft_body. */
function escapeHtml(s) {
  return String(s ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

/**
 * Build the POST /api/v1/drafts payload. Pure function (unit-tested).
 *
 * Modes (SUBSTACK_DRAFT_MODE, default "teaser"):
 *   teaser — blurb + "Read the full blog →" canonical link. SEO-safe default:
 *            Substack has no canonical-URL field, so a full-text cross-post
 *            would be treated as canonical by Google and cannibalize our site.
 *   full   — the whole body (paste-ready HTML) + an "Originally published at"
 *            footer link. Accepts the duplicate-content trade-off. Foreign
 *            <img> URLs are stripped by Substack, so callers must re-host
 *            images via /api/v1/image before sending (uploadInlineImages).
 *
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.bodyHtml        - full sanitized body as HTML
 * @param {string} opts.socialPost      - teaser blurb
 * @param {string} opts.canonicalUrl
 * @param {'teaser'|'full'} [opts.mode]
 */
export function buildDraftPayload({ title, bodyHtml, socialPost, canonicalUrl, mode = 'teaser' }) {
  let draftBody;
  if (mode === 'full') {
    draftBody = [
      bodyHtml.trim(),
      '<hr>',
      `<p><em>Originally published at <a href="${canonicalUrl}">${canonicalUrl}</a></em>.</p>`,
    ].join('\n');
  } else {
    const blurb = escapeHtml(socialPost || 'New post.');
    draftBody = [
      `<p>${blurb}</p>`,
      `<p><a href="${canonicalUrl}">Read the full blog →</a></p>`,
    ].join('\n');
  }
  // draft_body must be a STRING (HTML), never a JSON object — objects render
  // as visible markup in the editor.
  return { draft_title: title, draft_body: draftBody, type: 'newsletter' };
}

/**
 * Re-host inline images for full-body mode: Substack strips foreign <img>
 * srcs, so each is uploaded via POST /api/v1/image (base64 data URI — NOT
 * multipart) and the src rewritten to the returned S3 URL. Bytes are fetched
 * from Node (no CORS) and the upload runs as a same-origin page fetch.
 * Best-effort per image: failures leave the original src (image drops out).
 *
 * @param {import('playwright').Page} page - a page on the publication origin
 * @param {string} bodyHtml
 * @returns {Promise<string>} bodyHtml with re-hosted srcs
 */
async function uploadInlineImages(page, bodyHtml) {
  const srcs = [...bodyHtml.matchAll(/<img [^>]*src="([^"]+)"/g)].map((m) => m[1]).slice(0, 12);
  let next = bodyHtml;
  for (const src of srcs) {
    try {
      const buf = await fetch(src).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.arrayBuffer();
      });
      const dataUri = `data:image/png;base64,${Buffer.from(buf).toString('base64')}`;
      const res = await page.evaluate(async (dataUri) => {
        const r = await fetch('/api/v1/image', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image: dataUri }),
        });
        return { status: r.status, json: await r.json().catch(() => null) };
      }, dataUri);
      if (res.status === 200 && res.json?.url) {
        next = next.split(`src="${src}"`).join(`src="${res.json.url}"`);
      }
    } catch {
      // Leave the original src — Substack drops the image but the text survives.
    }
  }
  return next;
}

/**
 * Create the draft via the session browser (same-origin fetch on the
 * publication — carries cookies + a real browser fingerprint, dodging the
 * 403s Substack gives plain HTTP clients). Full mode re-hosts inline images
 * first, on the same page.
 */
async function createDraftOnSubstack({ title, bodyHtml, socialPost, canonicalUrl, mode }) {
  const pub = process.env.SUBSTACK_PUB || 'theinkpens';
  const origin = `https://${pub}.substack.com`;

  const result = await withSessionBrowser('substack', async (page) => {
    await page.goto(`${origin}/publish/posts`, { waitUntil: 'domcontentloaded', timeout: 45_000 });
    if (/\/signin|\/login/i.test(page.url())) throw new AuthError('substack');

    let payload = buildDraftPayload({ title, bodyHtml, socialPost, canonicalUrl, mode });
    if (mode === 'full' && bodyHtml) {
      payload = buildDraftPayload({
        title,
        bodyHtml: await uploadInlineImages(page, bodyHtml),
        socialPost,
        canonicalUrl,
        mode,
      });
    }

    const res = await page.evaluate(async (payload) => {
      const r = await fetch('/api/v1/drafts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      return { status: r.status, json: await r.json().catch(() => null) };
    }, payload);

    if (res.status === 401 || res.status === 403) throw new AuthError('substack');
    if (res.status >= 400 || !res.json?.id) {
      throw new Error(`draft API ${res.status}: ${JSON.stringify(res.json).slice(0, 200)}`);
    }
    // The response carries the draft object; the editor deep link is either
    // draft_url or derived from the id. Human reviews there before Publish.
    const url = typeof res.json.draft_url === 'string' && res.json.draft_url.includes('/publish/')
      ? res.json.draft_url
      : `${origin}/publish/post/${res.json.id}`;
    return { id: res.json.id, url };
  });
  // { value } on success; { value: null, authFailed, reason } on any failure.
  return result;
}

/**
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.bodyMarkdown
 * @param {string} opts.bodyHtml       - paste-ready HTML (teaser paste / full draft body)
 * @param {string} opts.socialPost     - short blurb for a teaser intro
 * @param {string} opts.canonicalUrl
 * @param {string} opts.slug
 * @param {boolean} [opts.dryRun]      - never touch the network when true
 * @returns {Promise<{id: string, url: string}>} id is 'draft' (assisted),
 *   'manual' (package fallback), or 'dry-run' telemetry comes via the url.
 */
export async function publish({ title, bodyMarkdown, bodyHtml, socialPost, canonicalUrl, slug, dryRun }) {
  const mode = process.env.SUBSTACK_DRAFT_MODE === 'full' ? 'full' : 'teaser';

  let assistedNote = null;
  if (hasSession('substack')) {
    if (dryRun) {
      return { id: 'draft', url: `(dry-run: would create a ${mode} draft on Substack)` };
    }
    const { value: draft, authFailed, reason } = await createDraftOnSubstack({ title, bodyHtml, socialPost, canonicalUrl, mode });
    if (draft) return { id: 'draft', url: draft.url };
    // Session exists but the draft failed (expired login, API drift). Record
    // why on the package and fall through — the manual path keeps the run green.
    assistedNote = authFailed
      ? '⚠ Assisted draft failed: session expired — re-run `task posse:login -- substack`.'
      : `⚠ Assisted draft failed: ${reason || 'unknown error'}. Falling back to manual.`;
  }

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
      'Then link: "Read the full blog → " followed by:',
      '',
      `<${canonicalUrl}>`,
      '',
      'To paste formatted content (teaser or full body), open the HTML companion,',
      'select-all → copy → paste into the Substack editor:',
      '',
      `<${packageHtmlPath(slug)}>`,
      '',
      'Tip: locally, `task posse:login -- substack` (once) + `task posse:assisted`',
      'automates this — the draft is created for you; you just review + Publish.',
      ...(assistedNote ? ['', assistedNote] : []),
    ].join('\n'),
  });
  return { id: 'manual', url: packagePath(slug) };
}

export function publicUrl() {
  return undefined;
}
