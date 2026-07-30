import { test } from 'node:test';
import assert from 'node:assert/strict';
import { componentsInBody } from './component-shot.mjs';

test('componentsInBody: detects capitalized component tags', () => {
  assert.deepEqual(componentsInBody('text <BinPacker client:visible /> more'), ['BinPacker']);
  assert.deepEqual(componentsInBody('<Chart />'), ['Chart']);
});

test('componentsInBody: dedupes repeated components', () => {
  const out = componentsInBody('<BinPacker />\n\n<BinPacker />');
  assert.deepEqual(out, ['BinPacker']);
});

test('componentsInBody: returns multiple unique components in order of first appearance', () => {
  const out = componentsInBody('<BinPacker />\n<Chart>\n<BinPacker />');
  assert.deepEqual(out, ['BinPacker', 'Chart']);
});

test('componentsInBody: ignores lowercase html tags', () => {
  assert.deepEqual(componentsInBody('<div>\n<span>hi</span>\n</div>'), []);
});

test('componentsInBody: requires at least 2 chars in the component name (regex [A-Z][A-Za-z0-9]+)', () => {
  // single-letter capitalized tags are NOT matched by this regex — pin the edge case
  assert.deepEqual(componentsInBody('<X />'), []);
});

test('componentsInBody: returns empty for plain markdown with no JSX', () => {
  assert.deepEqual(componentsInBody('just # text\n\n- a\n- b'), []);
});
