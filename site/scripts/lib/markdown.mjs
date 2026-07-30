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

// Per-image "try it live" caption — emitted under each static screenshot so a
// reader on a platform that can't run React knows the demo is interactive on the
// canonical site. Raw HTML so it survives remark-rehype for the Medium/Substack
// paste path; renders as a small centred link. Dev.to shows it inline too.
const IMAGE_CAPTION = (canonicalUrl) =>
  `<p style="text-align:center;font-size:0.9em;"><a href="${canonicalUrl}">↗ Try this demo live on inkpens.tech</a></p>`;

/**
 * @param {string} mdxBody
 * @param {string} canonicalUrl
 * @param {Record<string, string | {dark?: string, light?: string}>} [componentImages]
 *   - A string value is a single image URL (legacy / single-shot).
 *   - An object value { dark, light } is a themed pair → emitted as a <picture>
 *     that switches on prefers-color-scheme, with the dark image as the <img>
 *     fallback (dev.to/Medium strip <picture> and show the <img> only, so dark
 *     — the site's default — is what most readers see).
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

  // Build the replacement NODES for a component tag: the image (a <picture>
  // for a themed pair, a plain image for a single string) followed by a
  // "try it live" caption linking to the canonical site. Returns an array so
  // the visitor can splice both in. An empty image node array means no
  // screenshot — we still emit nothing (the trailing INTERACTIVE_NOTE covers it).
  const buildImageNodes = (name) => {
    const entry = componentImages[name];
    if (!entry) return []; // no screenshot — drop tag, end note still appears
    const alt = `${name} demo (interactive on the original post)`;

    if (typeof entry === 'string') {
      // Single image (back-compat).
      return [{ type: 'paragraph', children: [{ type: 'image', url: entry, alt }] }];
    }

    // Themed pair. Prefer both; degrade to whichever we have.
    const { dark, light } = entry;
    if (dark && light) {
      // Raw HTML node: <picture> switches on theme; <img> is the dark fallback.
      const html = `<picture><source srcset="${light}" media="(prefers-color-scheme: light)"><img src="${dark}" alt="${alt}"></picture>`;
      return [{ type: 'html', value: html }];
    }
    // Only one variant captured — emit it as a plain image.
    const url = dark || light;
    return [{ type: 'paragraph', children: [{ type: 'image', url, alt }] }];
  };

  // Replace each JSX component tag (raw `html` node whose tag starts with a
  // capital letter) with its image + caption, otherwise remove it. Splicing on
  // the AST keeps position/spacing correct.
  visit(tree, 'html', (node, index, parent) => {
    const tagMatch = node.value.match(/<([A-Z][A-Za-z0-9]*)\b/);
    if (!tagMatch) return;
    if (typeof index !== 'number' || !parent) return;
    const imageNodes = buildImageNodes(tagMatch[1]);
    if (imageNodes.length === 0) {
      // No screenshot — drop the tag entirely.
      parent.children[index] = { type: 'paragraph', children: [] };
      return;
    }
    // Image node(s) + the "try it live" caption beneath it.
    const replacement = [...imageNodes, { type: 'html', value: IMAGE_CAPTION(canonicalUrl) }];
    parent.children.splice(index, 1, ...replacement);
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
    // allowDangerousHtml: mdxToMarkdown may emit a raw <picture> node for themed
    // screenshot pairs. Without this, remark-rehype drops raw HTML to nothing,
    // losing the image entirely on the Medium/Substack paste path.
    .use(remarkRehype, { allowDangerousHtml: true })
    .use(rehypeStringify, { allowDangerousHtml: true })
    .process(markdown);
  return String(output);
}
