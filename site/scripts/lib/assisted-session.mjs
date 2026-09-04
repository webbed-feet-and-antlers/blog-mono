// Local browser sessions for the "assisted draft" tier (Substack, Medium,
// LinkedIn Article, Indie Hackers — the platforms with no posting API).
//
// The assisted adapters drive each platform's web editor just far enough to
// create a DRAFT; a human reviews and clicks Publish. Sessions are Playwright
// storageState JSON files captured by `task posse:login -- <platform>` (a
// headed browser — the human handles the password and any 2FA, then presses
// Enter to save). Everything lives under .syndication-output/sessions/
// (gitignored, local-only): CI never sees a session, so there the adapters
// fall back to the manual-package behavior unchanged.
import { chromium } from 'playwright';
import { existsSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import readline from 'node:readline/promises';

const __dirname = dirname(fileURLToPath(import.meta.url));
// scripts/lib -> site/.syndication-output/sessions
export const SESSIONS_DIR = join(__dirname, '..', '..', '.syndication-output', 'sessions');

// Where `task posse:login` sends you for each platform. The URLs are the
// logged-in landing surfaces — if you're already signed in the page just
// renders; if not, you sign in right there. Keys match the syndication
// frontmatter keys (linkedin covers both the feed post and the Article).
export const LOGIN_URLS = {
  substack: () => `https://${process.env.SUBSTACK_PUB || 'theinkpens'}.substack.com/publish/posts`,
  medium: () => 'https://medium.com/me/stories',
  linkedin: () => 'https://www.linkedin.com/feed/',
  indiehackers: () => 'https://www.indiehackers.com/',
};

/**
 * Path to a platform's saved storageState file.
 * @param {string} platform
 */
export function sessionPath(platform) {
  return join(SESSIONS_DIR, `${platform}.json`);
}

/**
 * Whether a saved session exists for the platform. Assisted adapters gate on
 * this: no session → skip straight to the manual-package fallback.
 * @param {string} platform
 */
export function hasSession(platform) {
  return existsSync(sessionPath(platform));
}

/**
 * Load a platform's storageState (Playwright's { cookies, origins } JSON),
 * or null when missing/corrupt.
 * @param {string} platform
 * @returns {Promise<{cookies: object[], origins: object[]} | null>}
 */
export async function loadSession(platform) {
  try {
    return JSON.parse(await readFile(sessionPath(platform), 'utf8'));
  } catch {
    return null;
  }
}

/**
 * Pull a single cookie value out of a saved session (e.g. Substack's
 * `substack.sid`) for adapters that talk HTTP directly.
 * @param {string} platform
 * @param {string} cookieName
 * @returns {Promise<string | null>}
 */
export async function sessionCookie(platform, cookieName) {
  const state = await loadSession(platform);
  const cookie = (state?.cookies ?? []).find((c) => c.name === cookieName);
  return cookie ? cookie.value : null;
}

// Session cookie per platform — the reliable login route. Medium/Substack
// auth via magic-link email (which often never arrives) and Google blocks
// automation-flagged browsers outright ("This browser or app may not be
// secure"), so `task posse:login -- <platform> --cookie=<value>` copies the
// session cookie straight from your REAL browser (where you're already
// logged in) into the saved storageState.
export const SESSION_COOKIES = {
  substack: { name: 'substack.sid', domain: '.substack.com' },
  medium: { name: 'sid', domain: '.medium.com' },
  linkedin: { name: 'li_at', domain: '.linkedin.com' },
  indiehackers: { name: '__session', domain: '.indiehackers.com' },
};

/**
 * Save a session directly from a cookie value copied out of your real
 * browser (DevTools → Application → Cookies → <name> → copy the Value
 * column). Builds a storageState carrying that cookie for the platform's
 * domain — exactly what the browser-login flow would have captured.
 *
 * @param {string} platform
 * @param {string} value - the cookie's value (just the value, no "name=")
 * @returns {Promise<string>} the path the session was saved to
 */
export async function saveSessionFromCookie(platform, value) {
  const cookie = SESSION_COOKIES[platform];
  if (!cookie) throw new Error(`Unknown platform: ${platform} (expected one of ${Object.keys(SESSION_COOKIES).join(', ')})`);
  if (!value || value.includes(' ')) throw new Error('Cookie value looks wrong — paste just the Value column (no spaces, no "name=" prefix).');

  const state = {
    cookies: [
      {
        name: cookie.name,
        value,
        domain: cookie.domain,
        path: '/',
        secure: true,
        httpOnly: true,
        sameSite: 'Lax',
        expires: Math.floor(Date.now() / 1000) + 365 * 24 * 3600,
      },
    ],
    origins: [],
  };
  await mkdir(SESSIONS_DIR, { recursive: true });
  const path = sessionPath(platform);
  await writeFile(path, JSON.stringify(state, null, 2), 'utf8');
  console.log(`Session saved from cookie: ${path}`);
  return path;
}

/**
 * Interactive login capture for `task posse:login -- <platform>`. Opens a
 * HEADED browser at the platform's landing page; you sign in (2FA and all),
 * then press Enter back in the terminal to save the session. Deliberately
 * Enter-driven rather than URL/DOM auto-detection: one code path that works
 * for every platform regardless of how their login redirects behave.
 *
 * Tries the locally installed Google Chrome first (channel: 'chrome') with
 * automation flags dialed back — plain Playwright Chromium is blocked by
 * Google sign-in ("This browser or app may not be secure") and sometimes by
 * Cloudflare fronts. If email magic-links don't arrive (common for
 * Medium/Substack), use saveSessionFromCookie instead.
 *
 * @param {string} platform - substack | medium | linkedin | indiehackers
 * @returns {Promise<string>} the path the session was saved to
 */
export async function loginInteractive(platform) {
  const url = LOGIN_URLS[platform]?.();
  if (!url) throw new Error(`Unknown platform: ${platform} (expected one of ${Object.keys(LOGIN_URLS).join(', ')})`);

  let browser;
  try {
    browser = await chromium.launch({
      headless: false,
      channel: 'chrome',
      ignoreDefaultArgs: ['--enable-automation'],
      args: ['--disable-blink-features=AutomationControlled'],
    });
  } catch {
    // No local Chrome — fall back to bundled Chromium (works for the
    // email-login platforms, still blocked by Google).
    browser = await chromium.launch({ headless: false });
  }
  try {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60_000 });

    console.log(`\nBrowser opened at ${url}`);
    console.log('Sign in there (you may already be signed in).');
    console.log('If the magic-link email never arrives or Google refuses this browser,');
    console.log(`copy the ${SESSION_COOKIES[platform]?.name ?? 'session'} cookie from your real browser and re-run with --cookie=<value>.`);
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    await rl.question(`When you're logged in, press Enter here to save the ${platform} session… `);
    rl.close();

    await mkdir(SESSIONS_DIR, { recursive: true });
    const path = sessionPath(platform);
    await writeFile(path, JSON.stringify(await ctx.storageState(), null, 2), 'utf8');
    console.log(`Session saved: ${path}`);
    return path;
  } finally {
    await browser.close();
  }
}
