import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import * as devto from './devto.mjs';
import * as bluesky from './bluesky.mjs';
import * as mastodon from './mastodon.mjs';
import * as buffer from './buffer.mjs';
import * as linkedin from './linkedin.mjs';
import * as linkedinArticle from './linkedin-article.mjs';
import * as medium from './medium.mjs';
import * as substack from './substack.mjs';
import * as indiehackers from './indiehackers.mjs';
import { rmSync, mkdirSync, existsSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { SESSIONS_DIR } from '../assisted-session.mjs';

// --- available(): each API adapter reflects its env vars ---

const ADAPTER_ENV = {
  devto: { DEV_TO_API_KEY: 'k' },
  bluesky: { BLUESKY_IDENTIFIER: 'h.bsky.social', BLUESKY_APP_PASSWORD: 'p' },
  mastodon: { MASTODON_INSTANCE: 'https://m.social', MASTODON_TOKEN: 't' },
  buffer: { BUFFER_API_KEY: 'k', BUFFER_X_CHANNEL_ID: 'c' },
  linkedin: { BUFFER_API_KEY: 'k', BUFFER_LINKEDIN_CHANNEL_ID: 'c' },
};

// Snapshot of env so each test can mutate cleanly.
const ENV_SNAPSHOT = { ...process.env };

function restoreEnv() {
  for (const k of Object.keys(process.env)) if (!(k in ENV_SNAPSHOT)) delete process.env[k];
  for (const [k, v] of Object.entries(ENV_SNAPSHOT)) process.env[k] = v;
}

afterEach(restoreEnv);

for (const [name, mod, env] of [
  ['dev.to', devto, ADAPTER_ENV.devto],
  ['bluesky', bluesky, ADAPTER_ENV.bluesky],
  ['mastodon', mastodon, ADAPTER_ENV.mastodon],
  ['buffer', buffer, ADAPTER_ENV.buffer],
  ['linkedin', linkedin, ADAPTER_ENV.linkedin],
]) {
  test(`${name}.available(): false with no env, true with credentials set`, () => {
    restoreEnv();
    assert.equal(mod.available(), false, 'no creds -> unavailable');
    Object.assign(process.env, env);
    assert.equal(mod.available(), true, 'creds set -> available');
  });
}

test('assisted/manual platforms: always available (no credentials)', () => {
  restoreEnv();
  assert.equal(medium.available(), true);
  assert.equal(substack.available(), true);
  assert.equal(indiehackers.available(), true);
  assert.equal(linkedinArticle.available(), true);
});

// --- dry-run paths: each API adapter short-circuits before any HTTP/fetch ---

const DRY_RUN_INPUTS = {
  devto: { title: 'T', bodyMarkdown: 'body', canonicalUrl: 'https://x', tags: [], description: 'd', dryRun: true },
  bluesky: { posts: ['a', 'b'], imagePath: null, dryRun: true },
  mastodon: { posts: ['a'], imagePath: null, dryRun: true },
  buffer: { posts: ['a', 'b'], slug: 's', dryRun: true },
  linkedin: { posts: ['a'], canonicalUrl: 'https://x', slug: 's', dryRun: true },
};

for (const [name, mod, input] of [
  ['devto', devto, DRY_RUN_INPUTS.devto],
  ['bluesky', bluesky, DRY_RUN_INPUTS.bluesky],
  ['mastodon', mastodon, DRY_RUN_INPUTS.mastodon],
  ['buffer', buffer, DRY_RUN_INPUTS.buffer],
  ['linkedin', linkedin, DRY_RUN_INPUTS.linkedin],
]) {
  test(`${name}.publish(dryRun:true): returns dry-run id without calling fetch`, async () => {
    restoreEnv();
    // Set creds so any pre-check passes, then dry-run.
    Object.assign(process.env, ADAPTER_ENV[name] || ADAPTER_ENV.devto);
    // If fetch were called, this stub would throw.
    const origFetch = globalThis.fetch;
    globalThis.fetch = () => {
      throw new Error('fetch must not be called in dry-run');
    };
    try {
      const result = await mod.publish(input);
      assert.equal(result.id, 'dry-run');
      assert.ok(result.url, 'has a url');
    } finally {
      globalThis.fetch = origFetch;
    }
  });
}

// --- assisted/manual adapters: package writes go to a temp-ish dir ---
// (the real gitignored .syndication-output — acceptable, cleaned by re-runs)

// Stash any REAL saved browser sessions for the duration of the test run so
// the manual-path assertions below never take the assisted branch (which
// would launch real browsers against real accounts on a dev machine).
const SESSIONS_STASH = `${SESSIONS_DIR}.test-stash`;
function stashSessions() {
  if (existsSync(SESSIONS_DIR)) renameSync(SESSIONS_DIR, SESSIONS_STASH);
}
function restoreSessions() {
  if (existsSync(SESSIONS_STASH)) renameSync(SESSIONS_STASH, SESSIONS_DIR);
}

beforeEach(() => {
  stashSessions();
});
afterEach(() => {
  restoreSessions();
});

test('medium.publish (no session): returns {id:"manual"} and produces no HTTP', async () => {
  const origFetch = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('medium must not fetch'); };
  try {
    const result = await medium.publish({
      title: 'T', bodyMarkdown: 'body', canonicalUrl: 'https://x', tags: [], slug: 's', dryRun: true,
    });
    assert.equal(result.id, 'manual');
  } finally {
    globalThis.fetch = origFetch;
  }
});

test('substack.publish (no session): returns {id:"manual"}', async () => {
  const result = await substack.publish({
    title: 'T', bodyMarkdown: 'body', socialPost: 'blurb', canonicalUrl: 'https://x', slug: 's', dryRun: true,
  });
  assert.equal(result.id, 'manual');
});

test('indiehackers.publish (no session): returns {id:"manual"} and produces no HTTP', async () => {
  const origFetch = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('indiehackers must not fetch'); };
  try {
    const result = await indiehackers.publish({
      title: 'T', bodyMarkdown: 'body', socialPost: 'blurb', canonicalUrl: 'https://x', tags: [], slug: 's', dryRun: true,
    });
    assert.equal(result.id, 'manual');
  } finally {
    globalThis.fetch = origFetch;
  }
});

test('linkedinArticle.publish (no session): returns {id:"manual"} and produces no HTTP', async () => {
  const origFetch = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('linkedinArticle must not fetch'); };
  try {
    const result = await linkedinArticle.publish({
      title: 'T', bodyMarkdown: 'body', canonicalUrl: 'https://x', tags: [], slug: 's', dryRun: true,
    });
    assert.equal(result.id, 'manual');
    assert.ok(result.url.includes('syndicate-s'), 'url points at the package file');
  } finally {
    globalThis.fetch = origFetch;
  }
});

// --- assisted tier: with a session saved, dry-run still touches nothing ---

function fakeSession() {
  // Minimal valid storageState — never actually used in dry-run, it only
  // needs to make hasSession() true.
  return JSON.stringify({ cookies: [], origins: [] });
}

test('assisted adapters with a saved session + dryRun: id "draft", zero fetch, no browser', async () => {
  mkdirSync(SESSIONS_DIR, { recursive: true });
  writeFileSync(join(SESSIONS_DIR, 'substack.json'), fakeSession());
  writeFileSync(join(SESSIONS_DIR, 'medium.json'), fakeSession());
  writeFileSync(join(SESSIONS_DIR, 'linkedin.json'), fakeSession());
  writeFileSync(join(SESSIONS_DIR, 'indiehackers.json'), fakeSession());

  const origFetch = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('assisted adapters must not fetch in dry-run'); };
  try {
    for (const [mod, opts] of [
      [substack, { title: 'T', bodyMarkdown: 'b', bodyHtml: '<p>b</p>', socialPost: 's', canonicalUrl: 'https://x', slug: 's', dryRun: true }],
      [medium, { title: 'T', bodyMarkdown: 'b', bodyHtml: '<p>b</p>', canonicalUrl: 'https://x', tags: [], slug: 's', dryRun: true }],
      [linkedinArticle, { title: 'T', bodyMarkdown: 'b', bodyHtml: '<p>b</p>', canonicalUrl: 'https://x', tags: [], slug: 's', dryRun: true }],
      [indiehackers, { title: 'T', bodyMarkdown: 'b', bodyHtml: '<p>b</p>', socialPost: 's', canonicalUrl: 'https://x', tags: [], slug: 's', dryRun: true }],
    ]) {
      const result = await mod.publish(opts);
      assert.equal(result.id, 'draft', `${mod.name}: dry-run with session -> draft`);
      assert.ok(!result.url.startsWith('http'), `${mod.name}: dry-run url is telemetry, not a link`);
    }
  } finally {
    globalThis.fetch = origFetch;
    rmSync(SESSIONS_DIR, { recursive: true, force: true });
  }
});

// --- substack.buildDraftPayload: pure payload builder ---

test('substack.buildDraftPayload: teaser mode (default) is blurb + canonical link', () => {
  const payload = substack.buildDraftPayload({
    title: 'A & B <post>',
    bodyHtml: '<p>full body</p>',
    socialPost: 'Short blurb',
    canonicalUrl: 'https://inkpens.tech/blog/x/',
    mode: 'teaser',
  });
  assert.equal(payload.draft_title, 'A & B <post>');
  assert.equal(payload.type, 'newsletter');
  assert.ok(typeof payload.draft_body === 'string', 'draft_body must be a string');
  assert.ok(payload.draft_body.includes('Short blurb'));
  assert.ok(payload.draft_body.includes('https://inkpens.tech/blog/x/'));
  assert.ok(!payload.draft_body.includes('full body'), 'teaser omits the body');
  // Blurb is HTML-escaped into the body.
  const escaped = substack.buildDraftPayload({
    title: 'T', bodyHtml: '', socialPost: 'a<b & c', canonicalUrl: 'https://x', mode: 'teaser',
  });
  assert.ok(escaped.draft_body.includes('a&lt;b &amp; c'));
});

test('substack.buildDraftPayload: full mode embeds the body + provenance footer', () => {
  const payload = substack.buildDraftPayload({
    title: 'T',
    bodyHtml: '<p>full body</p>',
    socialPost: 's',
    canonicalUrl: 'https://inkpens.tech/blog/x/',
    mode: 'full',
  });
  assert.ok(payload.draft_body.includes('<p>full body</p>'));
  assert.ok(payload.draft_body.includes('Originally published at'));
  assert.ok(payload.draft_body.includes('https://inkpens.tech/blog/x/'));
});
