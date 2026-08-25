import { test } from 'node:test';
import assert from 'node:assert/strict';
import { tagStyle, allTags, TAG_COLORS } from './tags.ts';

test('tagStyle: known tag returns its color family classes', () => {
  const s = tagStyle('gpu');
  assert.ok(s.includes('emerald'), 'gpu -> emerald');
  assert.ok(s.includes('ring-1'));
});

test('tagStyle: unknown tag gets a (stable) color, never bare grey zinc', () => {
  // Unknown tags are auto-colored by a hash, so they should NOT fall back to
  // the grey zinc pill. Same input must always map to the same color.
  const a = tagStyle('something-new');
  const b = tagStyle('something-new');
  assert.ok(!a.includes('text-zinc-400'), 'unknown tag should not be grey');
  assert.equal(a, b, 'same tag must always get the same color');
});

test('tagStyle: different unknown tags can map to different colors', () => {
  // Across enough distinct tags, the hash should produce more than one color.
  const colors = new Set(
    ['astro', 'mdx', 'web', 'github-pages', 'rust', 'sql', 'react', 'docker'].map(
      (t) => tagStyle(t).match(/text-([a-z]+)-\d/)?.[1],
    ),
  );
  assert.ok(colors.size > 1, 'expected more than one color across distinct tags');
});

test('tagStyle: lookup is case-insensitive', () => {
  assert.equal(tagStyle('GPU'), tagStyle('gpu'));
  assert.equal(tagStyle('Embeddings'), tagStyle('embeddings'));
});

test('tagStyle: always includes the base pill classes', () => {
  const s = tagStyle('whatever');
  assert.ok(s.includes('rounded-full'));
  assert.ok(s.includes('font-mono'));
});

test('TAG_COLORS: maps the expected known tags', () => {
  assert.equal(TAG_COLORS['gpu'], 'emerald');
  assert.equal(TAG_COLORS['posse'], 'indigo');
  assert.equal(TAG_COLORS['embeddings'], 'violet');
});

test('allTags: tallies counts across blogs', () => {
  const blogs = [
    { data: { tags: ['gpu', 'embeddings'] } },
    { data: { tags: ['gpu', 'systems'] } },
    { data: { tags: ['gpu'] } },
  ];
  const out = allTags(blogs);
  assert.equal(out.find((t) => t.tag === 'gpu').count, 3);
  assert.equal(out.find((t) => t.tag === 'embeddings').count, 1);
});

test('allTags: sorts by count desc then name asc', () => {
  const blogs = [
    { data: { tags: ['b', 'a'] } },
    { data: { tags: ['b'] } }, // b=2, a=1 -> b first
  ];
  const out = allTags(blogs);
  assert.equal(out[0].tag, 'b');
  assert.equal(out[1].tag, 'a');
});

test('allTags: handles blogs with missing/empty tags', () => {
  const out = allTags([{ data: {} }, { data: { tags: [] } }, { data: { tags: ['x'] } }]);
  assert.equal(out.length, 1);
  assert.equal(out[0].tag, 'x');
});
