// Playwright helpers for the assisted-draft adapters (Medium, LinkedIn
// Article, Indie Hackers — and Substack's draft API call, which rides the
// browser session to look like a real editor request).
//
// Fail-soft by contract: withSessionBrowser resolves null on ANY failure
// (missing session, expired login redirect, selector drift, timeout), so an
// assisted adapter falls back to the manual-package path instead of crashing
// a syndication run. Worst case is exactly today's UX.
import { chromium } from 'playwright';
import { existsSync } from 'node:fs';
import { sessionPath } from './assisted-session.mjs';

// Auth-wall URL fragments across the four platforms (Substack /signin,
// Medium /m/signin, LinkedIn /login + /checkpoint, generic /session/new).
const LOGIN_URL_RE = /\/(signin|sign-in|login|checkpoint|session\/new)/i;

/**
 * Did a navigation bounce us to a login wall? Callers use this to hint
 * `task posse:login -- <platform>` before falling back to the package.
 * @param {string} url
 */
export function looksLikeLoginUrl(url) {
  return LOGIN_URL_RE.test(url);
}

/**
 * Run fn(page) in a Chromium context carrying the saved session for
 * `platform`. Headless bundled Chromium by default; opts.headed + opts.channel
 * launch the locally installed real Chrome instead (needed for Medium, whose
 * Cloudflare front challenges headless browsers with an interstitial).
 * Resolves null (never throws) if anything at all goes wrong — including fn
 * throwing an AuthError, which we surface via the returned { authFailed: true }
 * marker so callers can print the re-login hint.
 *
 * @param {string} platform
 * @param {(page: import('playwright').Page) => Promise<T>} fn
 * @param {{ headed?: boolean, channel?: string }} [opts]
 * @returns {Promise<{ value: T } | { value: null, authFailed?: boolean, reason?: string }>}
 */
export async function withSessionBrowser(platform, fn, opts = {}) {
  const statePath = sessionPath(platform);
  if (!existsSync(statePath)) return { value: null };

  let browser;
  try {
    const launchOpts = {
      headless: !opts.headed,
      // Clipboard access for pasteHtml's fallback path.
      permissions: ['clipboard-read', 'clipboard-write'],
    };
    if (opts.channel) {
      launchOpts.channel = opts.channel;
      launchOpts.ignoreDefaultArgs = ['--enable-automation'];
      launchOpts.args = ['--disable-blink-features=AutomationControlled'];
    }
    browser = await chromium.launch(launchOpts);
    const ctx = await browser.newContext({
      storageState: statePath,
      // Clipboard access for pasteHtml's fallback path.
      permissions: ['clipboard-read', 'clipboard-write'],
    });
    const page = await ctx.newPage();
    const value = await fn(page);
    return { value };
  } catch (err) {
    return {
      value: null,
      authFailed: err?.code === 'EAUTH' || looksLikeLoginUrl(String(err?.message || '')),
      reason: err?.message,
    };
  } finally {
    await browser?.close().catch(() => {});
  }
}

/**
 * Marker error for "we hit a login wall" — withSessionBrowser turns it into
 * the authFailed hint. Throw from inside fn after checking page.url().
 */
export class AuthError extends Error {
  constructor(platform) {
    super(`login required (redirected to a sign-in page) — run: task posse:login -- ${platform}`);
    this.code = 'EAUTH';
  }
}

/**
 * Paste rich content into a contenteditable editor so it lands exactly like
 * a user copying the package and pressing Cmd+V.
 *
 * Flavors: `html` (text/html — rich-text editors) and `plain` (text/plain —
 * markdown-native editors like Indie Hackers'; defaults to tag-stripped html).
 *
 * Two-step for robustness across editor implementations:
 *   1. A synthetic ClipboardEvent('paste') carrying a DataTransfer with the
 *      flavors — what ProseMirror/Quill-style editors read.
 *   2. If the editor didn't grow, the real clipboard + keyboard paste.
 *
 * @param {import('playwright').Page} page
 * @param {import('playwright').Locator} editor - the contenteditable element (already focused/clicked)
 * @param {string | null} html - rich flavor; pass null for plain-only paste
 * @param {string} [plain] - plain flavor override (e.g. markdown source)
 */
export async function pasteHtml(page, editor, html, plain) {
  const text = plain ?? (html ? html.replace(/<[^>]+>/g, ' ') : '');
  const before = await editor.evaluate((el) => el.textContent?.length ?? 0).catch(() => 0);

  await editor.evaluate(
    (el, { html, text }) => {
      const dt = new DataTransfer();
      if (html) dt.setData('text/html', html);
      dt.setData('text/plain', text);
      el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    },
    { html, text }
  );
  await page.waitForTimeout(500);
  const after = await editor.evaluate((el) => el.textContent?.length ?? 0).catch(() => 0);

  if (after > before) return true;

  if (!html) return after > before; // plain-only paste has no clipboard fallback

  // Fallback: real clipboard + keyboard paste (needs the granted permissions
  // and a focused tab — bringToFront in case the context lost focus).
  await page.bringToFront().catch(() => {});
  await page.evaluate(async (html) => {
    const blob = new Blob([html], { type: 'text/html' });
    await navigator.clipboard.write([new ClipboardItem({ 'text/html': blob })]);
  }, html);
  await editor.click();
  await page.keyboard.press('ControlOrMeta+V');
  await page.waitForTimeout(500);
  const final = await editor.evaluate((el) => el.textContent?.length ?? 0).catch(() => 0);
  return final > after;
}
