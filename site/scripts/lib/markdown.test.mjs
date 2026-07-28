import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mdxToMarkdown } from './markdown.mjs';

const URL = 'https://theinkpens.com/blog/x/';

test('mdxToMarkdown: strips ESM import statements', async () => {
  const out = await mdxToMarkdown("import Foo from './Foo';\n\nbody text", URL);
  assert.ok(!/import\s+Foo/.test(out), 'import line removed');
  assert.ok(out.includes('body text'));
});

test('mdxToMarkdown: strips JSX component tags', async () => {
  const out = await mdxToMarkdown('intro\n\n<BinPacker client:visible />\n\noutro', URL);
  assert.ok(!/<BinPacker/.test(out), 'JSX tag removed');
  assert.ok(out.includes('intro'));
  assert.ok(out.includes('outro'));
});

test('mdxToMarkdown: inlines a screenshot image where the component was', async () => {
  const imgUrl = 'https://theinkpens.com/sshot/binpacker-x.png';
  const out = await mdxToMarkdown(
    'before\n\n<BinPacker client:visible />\n\nafter',
    URL,
    { BinPacker: imgUrl }
  );
  assert.ok(out.includes(`![BinPacker demo (interactive on the original post)](${imgUrl})`));
  // image lands between before/after (position preserved)
  const beforeIdx = out.indexOf('before');
  const imgIdx = out.indexOf(imgUrl);
  const afterIdx = out.indexOf('after');
  assert.ok(beforeIdx < imgIdx && imgIdx < afterIdx, 'image is positioned between before and after');
});

test('mdxToMarkdown: component without a screenshot is stripped (no image, note still appended)', async () => {
  const out = await mdxToMarkdown('text\n\n<BinPacker />\nmore', URL, {});
  assert.ok(!/!\[BinPacker/.test(out), 'no image inlined');
  assert.ok(!/<BinPacker/.test(out), 'tag removed');
});

test('mdxToMarkdown: preserves fenced code blocks and $$math$$', async () => {
  const out = await mdxToMarkdown('```js\nconst x = 1;\n```\n\n$$\na^2 + b^2\n$$', URL);
  assert.ok(out.includes('```js'));
  assert.ok(out.includes('const x = 1;'));
  assert.ok(out.includes('$$'));
});

test('mdxToMarkdown: appends interactive note iff imports or capitalized JSX present', async () => {
  const withComponent = await mdxToMarkdown('text\n\n<BinPacker />\n', URL);
  assert.ok(withComponent.includes('interactive on the original post'));
  const withImport = await mdxToMarkdown("import X from 'y';\n\ntext", URL);
  assert.ok(withImport.includes('interactive on the original post'));
  const plain = await mdxToMarkdown('just plain markdown, no components', URL);
  assert.ok(!plain.includes('interactive on the original post'), 'no note for plain markdown');
});

test('mdxToMarkdown: the interactive note contains the canonical URL', async () => {
  const out = await mdxToMarkdown('<BinPacker />', URL);
  assert.ok(out.includes(URL), 'canonical URL appears in the note');
});
