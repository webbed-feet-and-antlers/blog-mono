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
  linkedin: { posts: ['a'], articleUrl: 'https://www.linkedin.com/pulse/x', dryRun: true },
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

// --- linkedin: the caption post depends on its Article being confirmed ---

test('linkedin.publish without an article URL: refuses (caption needs its Article)', async () => {
  restoreEnv();
  Object.assign(process.env, ADAPTER_ENV.linkedin);
  await assert.rejects(
    linkedin.publish({ posts: ['a'], articleUrl: undefined, dryRun: true }),
    /LinkedIn Article/,
  );
});

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

// --- bluesky.linkFacets: URLs get clickable link facets (byte offsets) ---

test('bluesky.linkFacets: bare URL gets a link facet with correct byte range', () => {
  const text = 'First post, more at https://inkpens.tech/blog/x/';
  const facets = bluesky.linkFacets(text);
  assert.equal(facets.length, 1);
  assert.deepEqual(facets[0].index, { byteStart: 20, byteEnd: 48 });
  assert.equal(facets[0].features[0].$type, 'app.bsky.richtext.facet#link');
  assert.equal(facets[0].features[0].uri, 'https://inkpens.tech/blog/x/');
});

test('bluesky.linkFacets: no URL -> no facets', () => {
  assert.deepEqual(bluesky.linkFacets('just words, no links here'), []);
});

test('bluesky.linkFacets: multibyte chars before the URL shift byte offsets', () => {
  // ’ is 3 UTF-8 bytes but 1 UTF-16 unit — byte offsets must exceed .index.
  const text = 'We’ve shipped https://example.com';
  const facets = bluesky.linkFacets(text);
  assert.equal(facets.length, 1);
  assert.equal(facets[0].index.byteStart, 16); // 14 UTF-16 units + 2 extra bytes
  assert.equal(facets[0].features[0].uri, 'https://example.com');
});

test('bluesky.linkFacets: trailing punctuation is excluded from the link', () => {
  const text = 'Read it: https://example.com/post.).';
  const facets = bluesky.linkFacets(text);
  assert.equal(facets.length, 1);
  assert.equal(facets[0].features[0].uri, 'https://example.com/post');
  assert.deepEqual(facets[0].index, { byteStart: 9, byteEnd: 33 });
});

test('bluesky.linkFacets: multiple URLs each get a facet', () => {
  const facets = bluesky.linkFacets('a https://one.com b https://two.com c');
  assert.equal(facets.length, 2);
  assert.deepEqual(facets.map((f) => f.features[0].uri), ['https://one.com', 'https://two.com']);
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

// --- substack.buildDraftPayload: pure payload builder (ProseMirror docs) ---

test('substack.buildDraftPayload: teaser mode is blurb + canonical link paragraphs', () => {
  const payload = substack.buildDraftPayload({
    title: 'A & B <post>',
    bodyMarkdown: 'full body',
    socialPost: 'Short blurb',
    canonicalUrl: 'https://inkpens.tech/blog/x/',
    mode: 'teaser',
  });
  assert.equal(payload.draft_title, 'A & B <post>');
  assert.equal(payload.type, 'newsletter');
  assert.ok(typeof payload.draft_body === 'string', 'draft_body must be a string');
  const doc = JSON.parse(payload.draft_body);
  assert.equal(doc.type, 'doc');
  assert.equal(doc.attrs.schemaVersion, 'v1');
  assert.equal(doc.content.length, 2);
  assert.equal(doc.content[0].type, 'paragraph');
  assert.equal(doc.content[0].content[0].text, 'Short blurb');
  const link = doc.content[1].content.find((n) => n.marks?.some((m) => m.type === 'link'));
  assert.ok(link, 'canonical link present');
  assert.equal(link.marks.find((m) => m.type === 'link').attrs.href, 'https://inkpens.tech/blog/x/');
  assert.ok(!payload.draft_body.includes('full body'), 'teaser omits the body');
  assert.ok(!payload.draft_body.includes('<p>'), 'no literal HTML anywhere');
});

test('substack.buildDraftPayload: full mode converts markdown + provenance footer', () => {
  const payload = substack.buildDraftPayload({
    title: 'T',
    bodyMarkdown: ['## Heading', '', 'A **bold** paragraph with `code` and a [link](https://example.com).', '', '```js', 'const x = 1;', '```'].join('\n'),
    socialPost: 's',
    canonicalUrl: 'https://inkpens.tech/blog/x/',
    mode: 'full',
  });
  const doc = JSON.parse(payload.draft_body);
  const types = doc.content.map((n) => n.type);
  assert.ok(types.includes('heading'), `headings converted (${types})`);
  assert.ok(types.includes('code_block'), 'code fences converted');
  const para = doc.content.find((n) => n.type === 'paragraph' && n.content?.some((c) => c.marks?.some((m) => m.type === 'strong')));
  assert.ok(para, 'bold marks converted');
  assert.ok(JSON.stringify(doc).includes('"code"'), 'inline code marks converted');
  const footer = doc.content[doc.content.length - 1];
  assert.ok(footer.content.some((n) => n.text === 'Originally published at '), 'provenance footer present');
  assert.ok(payload.draft_body.includes('https://inkpens.tech/blog/x/'), 'canonical link present');
  assert.ok(!payload.draft_body.includes('<p>'), 'no literal HTML');
});

test('substack.markdownToProseMirrorDoc: GFM tables convert to table/table_row/table_cell', () => {
  const doc = substack.markdownToProseMirrorDoc('| A | B |\n| --- | --- |\n| 1 | 2 |');
  const table = doc.content.find((n) => n.type === 'table');
  assert.ok(table, 'table node present');
  assert.equal(table.content[0].type, 'table_row');
  assert.equal(table.content[0].content.length, 2);
  assert.equal(table.content[0].content[0].type, 'table_cell');
  assert.equal(table.content[0].content[1].content[0].text, 'B');
});

test('substack.markdownToProseMirrorDoc: literal $math$ becomes inline equations, $$..$$ becomes block', () => {
  const doc = substack.markdownToProseMirrorDoc(
    'The energy is $E=mc^2$ in text.\n\n$$\\int_0^1 x\\,dx$$\n\nplain after'
  );
  const para = doc.content.find((n) => n.type === 'paragraph');
  const eq = para.content.find((n) => n.type === 'equation');
  assert.ok(eq, 'inline equation present');
  assert.equal(eq.attrs.latex, 'E=mc^2');
  const blockEq = doc.content.find((n) => n.type === 'equation');
  assert.ok(blockEq, 'block equation present');
  assert.ok(JSON.stringify(doc).includes('int_0^1'));
  // No stray dollar signs left behind in text nodes.
  assert.ok(!JSON.stringify(doc).includes('$E'), 'math text stripped of $ delimiters');
});

test('substack.markdownToProseMirrorDoc: inline <picture> blocks are hoisted to top level', () => {
  const doc = substack.markdownToProseMirrorDoc(
    'text before\n<picture><source srcset="l"><img src="https://inkpens.tech/sshot/x.png"></picture>\ntext after'
  );
  const img = doc.content.find((n) => n.type === 'captionedImage');
  assert.ok(img, 'captionedImage at top level');
  assert.ok(!JSON.stringify(doc.content.filter((n) => n.type === 'paragraph')).includes('captionedImage'),
    'not nested inside a paragraph');
});

test('substack.markdownToProseMirrorDoc: re-hosted image map swaps srcs', () => {
  const map = new Map([['https://inkpens.tech/sshot/x.png', 'https://substack-post-media.s3.amazonaws.com/public/abc.png']]);
  // Top-level html img AND inline img inside a paragraph (mdxToMarkdown emits
  // the picture block without surrounding blank lines — it lands INLINE).
  const doc = substack.markdownToProseMirrorDoc(
    'before\n\n<img src="https://inkpens.tech/sshot/x.png" />\n\nafter\n\ntext with inline <img src="https://inkpens.tech/sshot/x.png" /> image',
    map
  );
  const srcs = JSON.stringify(doc).match(/"src":"([^"]+)"/g) ?? [];
  assert.ok(srcs.length >= 2, `found ${srcs.length} image srcs`);
  for (const s of srcs) {
    assert.ok(s.includes('substack-post-media'), `image re-hosted, got ${s}`);
  }
});

