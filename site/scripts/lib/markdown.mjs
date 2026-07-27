// Converts raw MDX essay body into platform-safe Markdown for long-form
// syndication targets (dev.to, Medium). Short-form targets (X/Bluesky/
// Mastodon/Substack) don't need the body — they use the `socialPost` blurb.
//
// What gets stripped/transformed:
//   - ESM `import ... from '...'` lines  -> removed
//   - JSX component tags (`<BinPacker ... />`, `<Chart>...</Chart>`)
//     -> replaced with a short note linking to the interactive original
//   - Markdown headings, paragraphs, fenced code, lists, math -> preserved
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkStringify from 'remark-stringify';
import remarkMath from 'remark-math';
import { remove } from 'unist-util-remove';

const INTERACTIVE_NOTE = (canonicalUrl) =>
  `\n\n> 🔁 *Parts of this essay are interactive on the original post — see them live: ${canonicalUrl}*`;

/**
 * Strip MDX imports and JSX component tags, returning plain Markdown safe for
 * dev.to and Medium (which accept Markdown, not MDX).
 *
 * @param {string} mdxBody
 * @param {string} canonicalUrl
 * @returns {Promise<string>} sanitized Markdown
 */
export async function mdxToMarkdown(mdxBody, canonicalUrl) {
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

  // Drop JSX-looking raw HTML blocks (e.g. <BinPacker client:visible />).
  // remark-parse alone treats these as raw `html` nodes; we remove any whose
  // tag starts with a capital letter (our component convention).
  remove(tree, (node) => {
    if (node.type === 'html' && /<[A-Z][A-Za-z0-9]*/.test(node.value)) {
      return true;
    }
    return false;
  });

  let result = processor.stringify(tree);

  // If the source had any component usage or imports, point readers to the
  // interactive version (the stripped components don't render elsewhere).
  const hadInteractive = /import\s+/.test(mdxBody) || /<[A-Z][A-Za-z0-9]*[\s/>]/.test(noImports);
  if (hadInteractive) {
    result = result.trimEnd() + INTERACTIVE_NOTE(canonicalUrl);
  }

  return result.trim() + '\n';
}
