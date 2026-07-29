// Converts raw MDX essay body into platform-safe Markdown for long-form
// syndication targets (dev.to, Medium). Short-form targets (X/Bluesky/
// Mastodon/Substack) don't need the body — they use the `social` blurb.
//
// Transformations:
//   - ESM `import ... from '...'` lines  -> removed
//   - JSX component tags (`<BinPacker ... />`)
//     -> if an image exists for that component in `componentImages`, REPLACED
//        in place with `![<Component> demo](<url>)` (a real screenshot of the
//        interactive demo, since dev.to/Medium can't run React);
//        otherwise removed entirely.
//   - A trailing note points readers to the interactive original.
//   - Markdown headings, paragraphs, fenced code, lists, math -> preserved.
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkStringify from 'remark-stringify';
import remarkMath from 'remark-math';
import remarkRehype from 'remark-rehype';
import rehypeStringify from 'rehype-stringify';
import remarkGfm from 'remark-gfm';
import { visit } from 'unist-util-visit';

const INTERACTIVE_NOTE = (canonicalUrl) =>
  `\n\n> 🔁 *Parts of this essay are interactive on the original post — see them live: ${canonicalUrl}*`;

/**
 * @param {string} mdxBody
 * @param {string} canonicalUrl
 * @param {Record<string, string>} [componentImages] - { BinPacker: "https://.../sshot/binpacker-x.png" }
 * @returns {Promise<string>} sanitized Markdown
 */
export async function mdxToMarkdown(mdxBody, canonicalUrl, componentImages = {}) {
  // Pre-strip ESM import statements with a regex — simpler and more robust
  // than hunting mdxjsEsm nodes in the AST.
  const noImports = mdxBody.replace(
    /^\s*import\s+[^;\n]+from\s+['"][^'"]+['"];?\s*$/gm,
    ''
  );

  const processor = unified()
    .use(remarkParse)
    .use(remarkMath)
    .use(remarkStringify, { bullet: '-', fence: '`', listItemIndent: 'one' });

  const tree = processor.parse(noImports);

  // Replace each JSX component tag (raw `html` node whose tag starts with a
  // capital letter) with a Markdown image node if we have a screenshot,
  // otherwise remove it. Doing this on the AST keeps position/spacing correct.
  visit(tree, 'html', (node, index, parent) => {
    const tagMatch = node.value.match(/<([A-Z][A-Za-z0-9]*)\b/);
    if (!tagMatch) return;
    const name = tagMatch[1];
    const imageUrl = componentImages[name];
    if (imageUrl && typeof index === 'number' && parent) {
      // Replace in place with a paragraph containing a Markdown image.
      parent.children[index] = {
        type: 'paragraph',
        children: [
          {
            type: 'image',
            url: imageUrl,
            alt: `${name} demo (interactive on the original post)`,
          },
        ],
      };
    } else if (typeof index === 'number' && parent) {
      // No screenshot available — drop the tag entirely.
      parent.children[index] = { type: 'paragraph', children: [] };
    }
  });

  let result = processor.stringify(tree);

  // If the source had any component usage or imports, point readers to the
  // interactive version (covers stripped tags without screenshots too).
  const hadInteractive = /import\s+/.test(mdxBody) || /<[A-Z][A-Za-z0-9]*[\s/>]/.test(noImports);
  if (hadInteractive) {
    result = result.trimEnd() + INTERACTIVE_NOTE(canonicalUrl);
  }

  return result.trim() + '\n';
}

/**
 * Render sanitized Markdown to an HTML fragment for the manual paste path
 * (Medium/Substack rich-text editors). Reuses remark-parse so it accepts the
 * output of mdxToMarkdown directly — JSX stripping / screenshot inlining /
 * import removal all happen upstream, so this is a pure markdown→HTML step.
 *
 * GFM tables are enabled (essays use pipe tables). Math is deliberately left
 * as plain `$…$` text rather than run through rehype-katex — Medium/Substack
 * don't load KaTeX's CSS, so KaTeX HTML would render as broken markup.
 *
 * @param {string} markdown - sanitized Markdown (typically from mdxToMarkdown)
 * @returns {Promise<string>} HTML fragment (no <html>/<body> wrapper)
 */
export async function markdownToHtml(markdown) {
  const output = await unified()
    .use(remarkParse)
    .use(remarkMath)
    .use(remarkGfm) // GFM tables, strikethrough, autolinks
    .use(remarkRehype)
    .use(rehypeStringify)
    .process(markdown);
  return String(output);
}
