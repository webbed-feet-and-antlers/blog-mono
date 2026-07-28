import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { seedPackage, addPlatformNote, packagePath } from './manual-package.mjs';
import { mkdtempSync, rmSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// manual-package.mjs computes OUT_DIR from __dirname at module load, so to
// redirect writes we override the OUT_DIR the module reads. The module exports
// packagePath() which uses that OUT_DIR; we test packagePath's shape directly
// and exercise seedPackage/addPlatformNote against the real (gitignored) output
// dir, cleaning up after.

let dir;
beforeEach(() => { dir = mkdtempSync(join(tmpdir(), 'pkg-')); });
afterEach(() => rmSync(dir, { recursive: true, force: true }));

test('packagePath: returns a path ending in syndicate-<slug>.md', () => {
  const p = packagePath('my-essay');
  assert.ok(p.endsWith('syndicate-my-essay.md'));
  assert.ok(p.includes('.syndication-output'));
});

test('seedPackage: writes the header (title, canonical, body, How-to section)', async () => {
  const slug = 'pkg-header';
  await seedPackage({
    slug, title: 'My Title', canonicalUrl: 'https://x/y',
    bodyMarkdown: 'the body text', tags: ['a', 'b'],
  });
  const file = packagePath(slug);
  assert.ok(existsSync(file), 'package file created');
  const content = readFileSync(file, 'utf8');
  assert.ok(content.includes('# My Title'));
  assert.ok(content.includes('https://x/y'));
  assert.ok(content.includes('the body text'));
  assert.ok(content.includes('How to publish this manually'));
});

test('seedPackage: idempotent — second call does not overwrite the header', async () => {
  const slug = 'pkg-idem';
  await seedPackage({ slug, title: 'First', canonicalUrl: 'u1', bodyMarkdown: 'b1', tags: [] });
  // Second seed with different values should be a no-op (file already exists).
  await seedPackage({ slug, title: 'Second', canonicalUrl: 'u2', bodyMarkdown: 'b2', tags: [] });
  const content = readFileSync(packagePath(slug), 'utf8');
  assert.ok(content.includes('First'), 'first title preserved');
  assert.ok(!content.includes('Second'), 'second seed did not overwrite');
});

test('addPlatformNote: appends a platform instruction block', async () => {
  const slug = 'pkg-note';
  await seedPackage({ slug, title: 'T', canonicalUrl: 'u', bodyMarkdown: 'b', tags: [] });
  await addPlatformNote({ slug, platform: 'Medium', instructions: 'Use Import a story' });
  const content = readFileSync(packagePath(slug), 'utf8');
  assert.ok(content.includes('**Medium**'));
  assert.ok(content.includes('Use Import a story'));
});

test('seedPackage + addPlatformNote: Medium and Substack compose into one file', async () => {
  const slug = 'pkg-compose';
  await seedPackage({ slug, title: 'T', canonicalUrl: 'u', bodyMarkdown: 'b', tags: [] });
  await addPlatformNote({ slug, platform: 'Medium', instructions: 'medium instructions' });
  await addPlatformNote({ slug, platform: 'Substack', instructions: 'substack instructions' });
  const content = readFileSync(packagePath(slug), 'utf8');
  assert.ok(content.includes('**Medium**'));
  assert.ok(content.includes('**Substack**'));
  // Order: Medium note before Substack note (appended in call order)
  assert.ok(content.indexOf('**Medium**') < content.indexOf('**Substack**'));
});

test('seedPackage: omits the Tags line when tags empty', async () => {
  const slug = 'pkg-notags';
  await seedPackage({ slug, title: 'T', canonicalUrl: 'u', bodyMarkdown: 'b', tags: [] });
  const content = readFileSync(packagePath(slug), 'utf8');
  assert.ok(!/^Tags:/m.test(content), 'no Tags line when tags empty');
});
