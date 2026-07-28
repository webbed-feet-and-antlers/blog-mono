import { test } from 'node:test';
import assert from 'node:assert/strict';
import { tagStyle, allTags, TAG_COLORS } from './tags.ts';

test('tagStyle: known tag returns its color family classes', () => {
  const s = tagStyle('gpu');
  assert.ok(s.includes('emerald'), 'gpu -> emerald');
  assert.ok(s.includes('ring-1'));
});

test('tagStyle: unknown tag falls back to zinc', () => {
  const s = tagStyle('something-new');
  assert.ok(s.includes('zinc'), 'unknown -> zinc');
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

test('allTags: tallies counts across essays', () => {
  const essays = [
    { data: { tags: ['gpu', 'embeddings'] } },
    { data: { tags: ['gpu', 'systems'] } },
    { data: { tags: ['gpu'] } },
  ];
  const out = allTags(essays);
  assert.equal(out.find((t) => t.tag === 'gpu').count, 3);
  assert.equal(out.find((t) => t.tag === 'embeddings').count, 1);
});

test('allTags: sorts by count desc then name asc', () => {
  const essays = [
    { data: { tags: ['b', 'a'] } },
    { data: { tags: ['b'] } }, // b=2, a=1 -> b first
  ];
  const out = allTags(essays);
  assert.equal(out[0].tag, 'b');
  assert.equal(out[1].tag, 'a');
});

test('allTags: handles essays with missing/empty tags', () => {
  const out = allTags([{ data: {} }, { data: { tags: [] } }, { data: { tags: ['x'] } }]);
  assert.equal(out.length, 1);
  assert.equal(out[0].tag, 'x');
});
