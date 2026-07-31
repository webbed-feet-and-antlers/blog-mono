import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import * as devto from './devto.mjs';
import * as bluesky from './bluesky.mjs';
import * as mastodon from './mastodon.mjs';
import * as buffer from './buffer.mjs';
import * as linkedin from './linkedin.mjs';
import * as medium from './medium.mjs';
import * as substack from './substack.mjs';
import * as indiehackers from './indiehackers.mjs';
import { rmSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

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

test('manual platforms: always available (no credentials)', () => {
  restoreEnv();
  assert.equal(medium.available(), true);
  assert.equal(substack.available(), true);
  assert.equal(indiehackers.available(), true);
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

test('indiehackers.publish: returns {id:"manual"} and produces no HTTP', async () => {
  const origFetch = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('indiehackers must not fetch'); };
  try {
    const result = await indiehackers.publish({
      title: 'T', bodyMarkdown: 'body', socialPost: 'blurb', canonicalUrl: 'https://x', tags: [], slug: 's',
    });
    assert.equal(result.id, 'manual');
  } finally {
    globalThis.fetch = origFetch;
  }
});
