import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { writeSyndicationIds } from './frontmatter.mjs';
import { mkdtempSync, rmSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

let dir;
beforeEach(() => { dir = mkdtempSync(join(tmpdir(), 'fm-')); });
afterEach(() => rmSync(dir, { recursive: true, force: true }));

const ESSAY_NO_BLOCK = `---
title: "My Essay"
description: "desc"
pubDate: 2026-07-28
tags: ["a", "b"]
---

Body line one.

Body line two.
`;

const ESSAY_WITH_BLOCK = `---
title: "My Essay"
description: "desc"
pubDate: 2026-07-28
syndication:
  devto: 12345
tags: ["a", "b"]
---

Body here.
`;

function writeEssay(content) {
  const p = join(dir, 'essay.mdx');
  writeFileSync(p, content, 'utf8');
  return p;
}

test('writeSyndicationIds: inserts a new syndication block when none exists', async () => {
  const p = writeEssay(ESSAY_NO_BLOCK);
  const changed = await writeSyndicationIds(p, { devto: 99, bluesky: 'at://x' });
  assert.equal(changed, true);
  const after = readFileSync(p, 'utf8');
  assert.ok(after.includes('syndication:'));
  assert.ok(after.includes('devto: 99'));
  assert.ok(after.includes('bluesky: at://x'));
});

test('writeSyndicationIds: preserves the author original formatting (quotes, dates, array style)', async () => {
  const p = writeEssay(ESSAY_NO_BLOCK);
  await writeSyndicationIds(p, { devto: 99 });
  const after = readFileSync(p, 'utf8');
  // The title with quotes, the bare date, and the inline array must be untouched.
  assert.ok(after.includes('title: "My Essay"'));
  assert.ok(after.includes('pubDate: 2026-07-28'));
  assert.ok(after.includes('tags: ["a", "b"]'));
});

test('writeSyndicationIds: updates an existing block in place (no duplication)', async () => {
  const p = writeEssay(ESSAY_WITH_BLOCK);
  const changed = await writeSyndicationIds(p, { mastodon: '111' });
  assert.equal(changed, true);
  const after = readFileSync(p, 'utf8');
  // Existing devto:12345 preserved, mastodon added, exactly one syndication: key.
  assert.ok(after.includes('devto: 12345'));
  assert.ok(/mastodon:\s+['"]?111['"]?/.test(after), 'mastodon: 111 present');
  assert.equal((after.match(/^syndication:/gm) || []).length, 1, 'exactly one syndication block');
});

test('writeSyndicationIds: idempotent — same IDs return false and write nothing', async () => {
  const p = writeEssay(ESSAY_WITH_BLOCK);
  const before = readFileSync(p, 'utf8');
  const changed = await writeSyndicationIds(p, { devto: 12345 }); // same as existing
  assert.equal(changed, false);
  const after = readFileSync(p, 'utf8');
  assert.equal(after, before, 'file unchanged');
});

test('writeSyndicationIds: number and string IDs are treated as equal (idempotent across types)', async () => {
  const p = writeEssay(ESSAY_NO_BLOCK);
  await writeSyndicationIds(p, { devto: 12345 }); // number
  const changed = await writeSyndicationIds(p, { devto: '12345' }); // string, same value
  assert.equal(changed, false, 'string "12345" matches existing number 12345');
});

test('writeSyndicationIds: body preserved byte-for-byte', async () => {
  const p = writeEssay(ESSAY_NO_BLOCK);
  const bodyBefore = ESSAY_NO_BLOCK.split('---\n')[2]; // everything after the closing fence
  await writeSyndicationIds(p, { devto: 1 });
  const after = readFileSync(p, 'utf8');
  const bodyAfter = after.split('---\n').slice(2).join('---\n');
  assert.equal(bodyAfter, bodyBefore);
});

test('writeSyndicationIds: skips null/undefined values', async () => {
  const p = writeEssay(ESSAY_NO_BLOCK);
  await writeSyndicationIds(p, { devto: 1, bluesky: undefined, mastodon: null });
  const after = readFileSync(p, 'utf8');
  assert.ok(after.includes('devto: 1'));
  assert.ok(!/bluesky:/.test(after));
  assert.ok(!/mastodon:/.test(after));
});
