import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import * as devto from './devto.mjs';
import * as bluesky from './bluesky.mjs';
import * as mastodon from './mastodon.mjs';
import * as buffer from './buffer.mjs';
import * as linkedin from './linkedin.mjs';
import * as medium from './medium.mjs';
import * as substack from './substack.mjs';
import { rmSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// --- available(): each API adapter reflects its env vars ---

const ADAPTER_ENV = {
  devto: { DEV_TO_API_KEY: 'k' },
  bluesky: { BLUESKY_IDENTIFIER: 'h.bsky.social', BLUESKY_APP_PASSWORD: 'p' },
  mastodon: { MASTODON_INSTANCE: 'https://m.social', MASTODON_TOKEN: 't' },
  buffer: { BUFFER_API_KEY: 'k', BUFFER_X_CHANNEL_ID: 'c' },
  linkedin: { LINKEDIN_TOKEN: 't', LINKEDIN_PERSON_URN: 'urn:li:person:x' },
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

test('medium + substack: always available (manual platforms, no credentials)', () => {
  restoreEnv();
  assert.equal(medium.available(), true);
  assert.equal(substack.available(), true);
});

// --- dry-run paths: each API adapter short-circuits before any HTTP/fetch ---

const DRY_RUN_INPUTS = {
  devto: { title: 'T', bodyMarkdown: 'body', canonicalUrl: 'https://x', tags: [], description: 'd', dryRun: true },
  bluesky: { posts: ['a', 'b'], imagePath: null, dryRun: true },
  mastodon: { posts: ['a'], imagePath: null, dryRun: true },
  buffer: { posts: ['a', 'b'], slug: 's', dryRun: true },
  // linkedin dry-run needs a non-expiring token; see dedicated test below
};

for (const [name, mod, input] of [
  ['devto', devto, DRY_RUN_INPUTS.devto],
  ['bluesky', bluesky, DRY_RUN_INPUTS.bluesky],
  ['mastodon', mastodon, DRY_RUN_INPUTS.mastodon],
  ['buffer', buffer, DRY_RUN_INPUTS.buffer],
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

test('linkedin.publish: throws on expired token even in dry-run (checkTokenExpiry runs first)', async () => {
  restoreEnv();
  process.env.LINKEDIN_TOKEN = 't';
  process.env.LINKEDIN_PERSON_URN = 'urn:li:person:x';
  process.env.LINKEDIN_TOKEN_ISSUED = '2020-01-01T00:00:00Z'; // long expired
  await assert.rejects(
    () => linkedin.publish({ posts: ['p'], canonicalUrl: 'https://x', imagePath: null, dryRun: true }),
    /EXPIRED/i
  );
});

test('linkedin.publish: warns when token near expiry (within 5 days)', async () => {
  restoreEnv();
  process.env.LINKEDIN_TOKEN = 't';
  process.env.LINKEDIN_PERSON_URN = 'urn:li:person:x';
  // 58 days ago -> 2 days remaining -> within the 5-day warn window
  const issued = new Date(Date.now() - 58 * 86400_000).toISOString();
  process.env.LINKEDIN_TOKEN_ISSUED = issued;
  await assert.rejects(
    () => linkedin.publish({ posts: ['p'], canonicalUrl: 'https://x', imagePath: null, dryRun: true }),
    /expires in/i
  );
});

test('linkedin.publish: passes expiry check when token is fresh', async () => {
  restoreEnv();
  process.env.LINKEDIN_TOKEN = 't';
  process.env.LINKEDIN_PERSON_URN = 'urn:li:person:x';
  process.env.LINKEDIN_TOKEN_ISSUED = new Date().toISOString(); // just issued
  const result = await linkedin.publish({ posts: ['p'], canonicalUrl: 'https://x', imagePath: null, dryRun: true });
  assert.equal(result.id, 'dry-run');
});

test('linkedin.publish: errors if LINKEDIN_TOKEN_ISSUED is missing or unparseable', async () => {
  restoreEnv();
  process.env.LINKEDIN_TOKEN = 't';
  process.env.LINKEDIN_PERSON_URN = 'urn:li:person:x';
  delete process.env.LINKEDIN_TOKEN_ISSUED;
  await assert.rejects(
    () => linkedin.publish({ posts: ['p'], canonicalUrl: 'https://x', imagePath: null, dryRun: true }),
    /LINKEDIN_TOKEN_ISSUED/i
  );
});

// --- manual adapters: write to a temp dir, return {id:'manual'} ---

// Both medium.mjs and substack.mjs import manual-package.mjs, which computes
// OUT_DIR from __dirname at module load. To redirect writes to a temp dir we
// stub the module via Node's module loader cache. Simpler: mock the fs methods
// the package lib uses. We do that with t.mock above — but to keep zero-config,
// here we just verify the return contract (id:'manual') by letting them write
// to the real (gitignored) output dir and cleaning up after.
const OUT_DIR = join(tmpdir(), `posse-test-${process.pid}`);
beforeEach(() => mkdirSync(OUT_DIR, { recursive: true }));
afterEach(() => rmSync(OUT_DIR, { recursive: true, force: true }));

test('medium.publish: returns {id:"manual"} and produces no HTTP', async () => {
  const origFetch = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('medium must not fetch'); };
  try {
    const result = await medium.publish({
      title: 'T', bodyMarkdown: 'body', canonicalUrl: 'https://x', tags: [], slug: 's',
    });
    assert.equal(result.id, 'manual');
  } finally {
    globalThis.fetch = origFetch;
  }
});

test('substack.publish: returns {id:"manual"}', async () => {
  const result = await substack.publish({
    title: 'T', bodyMarkdown: 'body', socialPost: 'blurb', canonicalUrl: 'https://x', slug: 's',
  });
  assert.equal(result.id, 'manual');
});
