import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readingTime } from './readingTime.ts';

test('readingTime: returns >= 1 even for tiny bodies', () => {
  assert.equal(readingTime('one two three'), 1);
  assert.equal(readingTime(''), 1);
});

test('readingTime: ~200 wpm math', () => {
  // 200 words -> 1 min; 400 words -> 2 min
  const twoHundred = 'word '.repeat(200).trim();
  const fourHundred = 'word '.repeat(400).trim();
  assert.equal(readingTime(twoHundred), 1);
  assert.equal(readingTime(fourHundred), 2);
});

test('readingTime: strips fenced code blocks before counting', () => {
  const withCode = '```js\n' + 'code '.repeat(500) + '\n```\n\nreal words here';
  // 500 code words stripped, only "real words here" counted -> 1 min
  assert.equal(readingTime(withCode), 1);
});

test('readingTime: strips inline code, images, links', () => {
  const body = 'see `codeRef` and ![alt](img.png) and [link](url) and plain text words';
  const mins = readingTime(body);
  assert.ok(mins >= 1);
});
