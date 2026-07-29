import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mdxToMarkdown, markdownToHtml } from './markdown.mjs';

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

// --- markdownToHtml: paste-ready HTML for the Medium/Substack manual path ---

test('markdownToHtml: renders headings as <h1>/<h2>', async () => {
  const html = await markdownToHtml('# Title\n\n## Section');
  assert.ok(html.includes('<h1>'), 'h1 rendered');
  assert.ok(html.includes('<h2>'), 'h2 rendered');
});

test('markdownToHtml: renders fenced code as <pre><code>', async () => {
  const html = await markdownToHtml('```js\nconst x = 1;\n```');
  assert.ok(html.includes('<pre>'), 'pre rendered');
  assert.ok(html.includes('<code'), 'code tag rendered (may carry a language- class)');
  assert.ok(html.includes('const x = 1;'), 'code content preserved');
});

test('markdownToHtml: renders GFM pipe tables as <table>', async () => {
  const md = '| A | B |\n| --- | --- |\n| 1 | 2 |';
  const html = await markdownToHtml(md);
  assert.ok(html.includes('<table>'), 'GFM table rendered (requires remark-gfm)');
  assert.ok(html.includes('<th>'), 'header row rendered');
  assert.ok(html.includes('<td>'), 'cell rendered');
});

test('markdownToHtml: leaves math as plain text (no KaTeX spans)', async () => {
  // KaTeX HTML would need platform CSS Medium/Substack don't load, so math
  // should survive as plain $...$ text rather than rendered markup.
  const html = await markdownToHtml('Inline $E=mc^2$ here.');
  assert.ok(html.includes('E=mc^2'), 'math content preserved as text');
  assert.ok(!html.includes('katex'), 'no KaTeX spans injected');
});

test('markdownToHtml: blockquote survives (interactive note round-trips)', async () => {
  const md = '> 🔁 *Parts of this essay are interactive on the original post — see them live: https://x/*';
  const html = await markdownToHtml(md);
  assert.ok(html.includes('<blockquote>'), 'blockquote rendered');
  assert.ok(html.includes('interactive on the original post'), 'note text preserved');
});

test('markdownToHtml: accepts mdxToMarkdown output (integration)', async () => {
  // The real pipeline: MDX → sanitized markdown → HTML. Verify a stripped
  // JSX component's screenshot image survives into the HTML as an <img>.
  const md = await mdxToMarkdown(
    'intro\n\n<BinPacker client:visible />\n\noutro',
    URL,
    { BinPacker: 'https://x/sshot.png' }
  );
  const html = await markdownToHtml(md);
  assert.ok(html.includes('<img'), 'inlined screenshot becomes an <img>');
  assert.ok(html.includes('https://x/sshot.png'), 'image URL preserved');
});
