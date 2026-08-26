// Substack — no OFFICIAL posting API exists anywhere (confirmed across Buffer,
// Postiz, dlvr.it, Narrareach, and Substack's own read-only 2026 Developer
// API). Also no canonical-URL support, so full-text cross-posts are an SEO
// duplicate risk — the SEO-cautious "teaser" mode is available via
// SUBSTACK_DRAFT_MODE=teaser, but the default is the FULL body (this is the
// newsletter; a blurb-only email is not what subscribers signed up for).
//
// ASSISTED-DRAFT tier: the web editor talks to an internal JSON API
// (POST /api/v1/drafts with the `substack.sid` session cookie — see the
// community-verified reference at
// https://github.com/AnthonyDavidAdams/substack-api-reference). With a local
// session saved via `task posse:login -- substack`, this adapter creates an
// unpublished DRAFT there and hands off — a human reviews and clicks Publish.
// The draft request rides the Playwright session's page (same-origin fetch
// with real browser cookies) because Substack 403s plain non-browser clients.
//
// draft_body must be a STRINGIFIED PROSEMIRROR DOC — HTML strings are stored
// as literal text (visible tags), and JSON objects render as markup. The
// converter below walks the markdown AST (remark) and emits Substack's schema.
//
// Without a session (or on any failure), this is the MANUAL platform as
// before: it contributes to the shared syndication package artifact.
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import { seedPackage, addPlatformNote, packagePath, writeHtmlPackage, packageHtmlPath } from '../manual-package.mjs';
import { markdownToHtml } from '../markdown.mjs';
import { hasSession } from '../assisted-session.mjs';
import { withSessionBrowser, AuthError } from '../browser-draft.mjs';

export const name = 'substack';

export function available() {
  return true; // assisted (with session) or manual package — no env credentials
}

// ── markdown AST → Substack ProseMirror ─────────────────────────────────────

/** @returns {object} a text node, with marks only when present */
function textNode(text, marks = []) {
  return marks.length ? { type: 'text', text, marks } : { type: 'text', text };
}

/**
 * Build a captionedImage node in the editor's REAL shape (captured from a
 * clipboard-paste insert): captionedImage wrapping an image2 child with the
 * upload's metadata. A bare {src} attr renders an EMPTY container.
 *
 * @param {string} url
 * @param {object} [meta] - the /api/v1/image response {url, bytes, imageWidth, imageHeight, contentType}
 */
function imageNode(url, imageData) {
  const meta = imageData?.get?.(url) ?? imageData;
  if (meta?.url) {
    return {
      type: 'captionedImage',
      content: [
        {
          type: 'image2',
          attrs: {
            src: meta.url,
            srcNoWatermark: null,
            fullscreen: null,
            imageSize: null,
            height: meta.imageHeight ?? null,
            width: meta.imageWidth ?? null,
            resizeWidth: null,
            bytes: meta.bytes ?? null,
            alt: null,
            title: null,
            type: meta.contentType ?? 'image/png',
            href: null,
            belowTheFold: false,
            topImage: false,
            internalRedirect: null,
            isProcessing: false,
            align: null,
            offset: false,
          },
        },
      ],
    };
  }
  // No upload metadata (upload skipped/failed) — best effort.
  return { type: 'captionedImage', content: [{ type: 'image2', attrs: { src: url } }] };
}

/** Inline mdast nodes → ProseMirror inline content. */
function inlineToPm(nodes, marks = [], imageUrlMap) {
  const out = [];
  for (const n of nodes ?? []) {
    switch (n.type) {
      case 'text':
        // Literal $…$ math stays TEXT — inline equation nodes are not in the
        // editor's schema and blank the whole post (verified by bisect).
        // Just drop the delimiters so readers don't see stray dollars.
        out.push(textNode((n.value ?? '').replace(/\$([^$\n]+)\$/g, '$1'), marks));
        break;
      case 'strong':
        out.push(...inlineToPm(n.children, [...marks, { type: 'strong' }], imageUrlMap));
        break;
      case 'emphasis':
        out.push(...inlineToPm(n.children, [...marks, { type: 'em' }], imageUrlMap));
        break;
      case 'inlineCode':
        out.push(textNode(n.value, [...marks, { type: 'code' }]));
        break;
      case 'delete':
        out.push(...inlineToPm(n.children, [...marks, { type: 'strike' }], imageUrlMap));
        break;
      case 'link':
        out.push(...inlineToPm(n.children, [...marks, { type: 'link', attrs: { href: n.url, anchorType: 'LINK' } }], imageUrlMap));
        break;
      case 'break':
        out.push({ type: 'hard_break' });
        break;
      case 'image':
        out.push(imageNode(n.url, imageUrlMap));
        break;
      case 'html': {
        // mdxToMarkdown inlines component screenshots as <picture>/<img> HTML.
        // (Kept for safety — the top-level hoisting in markdownToProseMirrorDoc
        // handles the real cases, so an inline img here is unusual.)
        const src = /<img [^>]*src="([^"]+)"/.exec(n.value)?.[1];
        if (src) out.push(imageNode(src, imageUrlMap));
        else {
          const text = n.value.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
          if (text.trim()) out.push(textNode(text.trim(), marks));
        }
        break;
      }
      default:
        if (n.value) out.push(textNode(n.value, marks));
        break;
    }
  }
  return out;
}

/**
 * Paragraph → array of blocks. CommonMark treats <picture> as a PHRASING
 * element, so component-screenshot html always parses inline inside a
 * paragraph — but captionedImage is block-level and the editor DROPS it when
 * nested. Split the paragraph around images so they stand alone.
 */
function paragraphToPm(node, imageUrlMap) {
  const blocks = [];
  let inline = [];
  const flush = () => {
    if (inline.length) blocks.push({ type: 'paragraph', content: inline });
    inline = [];
  };
  for (const n of node.children ?? []) {
    const src =
      n.type === 'html' ? /<img [^>]*src="([^"]+)"/.exec(n.value)?.[1]
      : n.type === 'image' ? n.url
      : null;
    if (src) {
      flush();
      blocks.push(imageNode(src, imageUrlMap));
    } else if (n.type === 'html') {
      const text = n.value.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
      if (text) inline.push(textNode(text));
    } else {
      inline.push(...inlineToPm([n], [], imageUrlMap));
    }
  }
  flush();
  return blocks.length ? blocks : [{ type: 'paragraph', content: [] }];
}

/** Block mdast nodes → ProseMirror block node(s); arrays flatten. */
function blockToPm(node, imageUrlMap) {
  switch (node.type) {
    case 'paragraph':
      return paragraphToPm(node, imageUrlMap);
    case 'heading':
      // Substack's editor headings are levels 2–3; clamp the markdown depth.
      return {
        type: 'heading',
        attrs: { level: Math.min(Math.max(node.depth ?? 2, 2), 3) },
        content: inlineToPm(node.children, [], imageUrlMap),
      };
    case 'code':
      return {
        type: 'code_block',
        ...(node.lang ? { attrs: { language: node.lang } } : {}),
        content: [{ type: 'text', text: node.value }],
      };
    case 'blockquote':
      return { type: 'blockquote', content: (node.children ?? []).flatMap((c) => blockToPm(c, imageUrlMap)).filter(Boolean) };
    case 'list':
      return {
        type: node.ordered ? 'ordered_list' : 'bullet_list',
        content: (node.children ?? []).map((li) => ({
          type: 'list_item',
          content: (li.children ?? []).flatMap((c) => blockToPm(c, imageUrlMap)).filter(Boolean),
        })),
      };
    case 'thematicBreak':
      return { type: 'horizontal_rule' };
    case 'table':
      // Substack's editor has NO table node — emitting one blanks the whole
      // post (verified by bisect). Normally tables are pre-rendered to PNGs
      // and swapped out in createDraftOnSubstack; this fallback keeps any
      // straggler as an aligned code block so nothing crashes.
      return {
        type: 'code_block',
        content: [{ type: 'text', text: (node.children ?? [])
          .map((row) => '| ' + (row.children ?? []).map((c) => inlineText(c)).join(' | ') + ' |')
          .join('\n') }],
      };
    case 'html': {
      const src = /<img [^>]*src="([^"]+)"/.exec(node.value)?.[1];
      return src ? imageNode(src, imageUrlMap) : null;
    }
    default:
      return null;
  }
}

/**
 * Convert sanitized markdown into a Substack ProseMirror doc object.
 * imageUrlMap (optional) swaps original image srcs for re-hosted Substack
 * URLs (foreign image URLs are stripped from posts).
 *
 * @param {string} markdown
 * @param {Map<string, string>} [imageUrlMap]
 * @returns {{ type: 'doc', attrs: object, content: object[] }}
 */
export function markdownToProseMirrorDoc(markdown, imageUrlMap) {
  // Extract display-math blocks RAW, before parsing: micromark eats
  // backslash-punctuation in text nodes (x\,dx → x,dx), which corrupts latex.
  const latexBlocks = [];
  const withSentinels = (markdown ?? '').replace(/(?:^|\n)\$\$([\s\S]+?)\$\$(?=\n|$)/g, (_m, latex) => {
    latexBlocks.push(latex.trim());
    return `\n\nLATEXBLOCK${latexBlocks.length - 1}\n\n`;
  });
  // GFM (strikethrough) needs the remark-gfm plugin; tables are normally
  // pre-rendered to PNG images before this runs (the editor has no table
  // node) and land here as ordinary markdown images.
  const tree = unified().use(remarkParse).use(remarkGfm).parse(withSentinels);
  const content = tree.children.flatMap((c) => {
    // Sentinels become Substack's real math node (verified rendering):
    // latex_block with the latex in persistentExpression plus a random id.
    const sentinel = c.type === 'paragraph' && c.children?.length === 1 && c.children[0].type === 'text'
      ? /^LATEXBLOCK(\d+)$/.exec(c.children[0].value.trim())
      : null;
    if (sentinel) {
      return [{
        type: 'latex_block',
        attrs: { persistentExpression: latexBlocks[Number(sentinel[1])], id: randomId() },
      }];
    }
    const blocks = blockToPm(c, imageUrlMap);
    return Array.isArray(blocks) ? blocks : blocks ? [blocks] : [];
  }).filter(Boolean);
  return { type: 'doc', attrs: { schemaVersion: 'v1' }, content };
}

/** Random 10-char uppercase id, matching the editor's own latex_block ids. */
function randomId() {
  return Array.from({ length: 10 }, () => String.fromCharCode(65 + Math.floor(Math.random() * 26))).join('');
}

/** Plain-text rendering of an inline mdast node (for the table fallback). */
function inlineText(node) {
  if (node.type === 'text' || node.type === 'inlineCode') return node.value ?? '';
  return (node.children ?? []).map(inlineText).join('');
}

/**
 * Build the POST /api/v1/drafts payload. Pure function (unit-tested).
 *
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.bodyMarkdown   - sanitized markdown (full mode body)
 * @param {string} opts.socialPost     - teaser blurb
 * @param {string} opts.canonicalUrl
 * @param {'teaser'|'full'} [opts.mode]
 * @param {Map<string, string>} [opts.imageUrlMap] - re-hosted image srcs
 */
export function buildDraftPayload({ title, bodyMarkdown, socialPost, canonicalUrl, mode = 'full', imageUrlMap }) {
  let doc;
  if (mode === 'teaser') {
    doc = {
      type: 'doc',
      attrs: { schemaVersion: 'v1' },
      content: [
        { type: 'paragraph', content: [textNode(socialPost || 'New post.')] },
        {
          type: 'paragraph',
          content: [
            textNode('Read the full blog → '),
            textNode(canonicalUrl, [{ type: 'link', attrs: { href: canonicalUrl, anchorType: 'LINK' } }]),
          ],
        },
      ],
    };
  } else {
    doc = markdownToProseMirrorDoc(bodyMarkdown ?? '', imageUrlMap);
    doc.content.push({
      type: 'paragraph',
      content: [
        { type: 'text', text: 'Originally published at ', marks: [{ type: 'em' }] },
        {
          type: 'text',
          text: canonicalUrl,
          marks: [
            { type: 'em' },
            { type: 'link', attrs: { href: canonicalUrl, anchorType: 'LINK' } },
          ],
        },
      ],
    });
  }
  return {
    draft_title: title,
    // MUST be a string — a JSON object here renders as visible markup.
    draft_body: JSON.stringify(doc),
    type: 'newsletter',
  };
}

/**
 * Re-host inline images for full-body mode: Substack strips foreign <img>
 * srcs, so each is uploaded via POST /api/v1/image (base64 data URI — NOT
 * multipart) and the src mapped to the returned S3 URL. Bytes are fetched
 * from Node (no CORS) and the upload runs as a same-origin page fetch.
 * Best-effort per image: failures leave the original src (image drops out).
 *
 * @param {import('playwright').Page} page - a page on the publication origin
 * @param {string} markdown
 * @returns {Promise<Map<string, string>>} original src → re-hosted URL
 */
async function uploadInlineImages(page, markdown, map = new Map()) {
  const srcs = [...markdown.matchAll(/!\[[^\]]*\]\(([^)]+)\)|<img [^>]*src="([^"]+)"/g)]
    .map((m) => m[1] || m[2])
    .slice(0, 12);
  for (const src of srcs) {
    try {
      const buf = await fetch(src).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.arrayBuffer();
      });
      const dataUri = `data:image/png;base64,${Buffer.from(buf).toString('base64')}`;
      const res = await page.evaluate(async (dataUri) => {
        try {
          const r = await fetch('/api/v1/image', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: dataUri }),
          });
          return { status: r.status, json: await r.json().catch(() => null) };
        } catch (e) {
          return { status: 0, json: null, error: String(e) };
        }
      }, dataUri);
      if (res.status === 200 && res.json?.url) {
        // Keyed by BOTH the original src and the re-hosted url, so markdown
        // refs to either resolve to full upload metadata.
        map.set(src, res.json);
        map.set(res.json.url, res.json);
        console.log(`    substack image re-hosted: ${src.split('/').pop()} → ${res.json.url.split('/').pop()}`);
      } else {
        console.warn(`    substack image upload failed (${res.status}) for ${src}: ${res.error ?? JSON.stringify(res.json)?.slice(0, 120)}`);
      }
    } catch (e) {
      // Leave the original src — Substack drops the image but the text survives.
      console.warn(`    substack image fetch failed for ${src}: ${e.message}`);
    }
  }
  return map;
}

/**
 * Render GFM pipe-tables as PNG images and swap them into the markdown as
 * image references — Substack's editor has NO table node (emitting one
 * blanks the whole post), so a rendered table image is the faithful route
 * (and the standard Substack workaround). Uses the session page to render
 * styled HTML and re-host the PNG. Failures keep the original markdown
 * (which falls back to an aligned code block in the converter).
 *
 * @param {import('playwright').Page} page
 * @param {string} markdown
 * @param {Map<string, object>} imageMeta - filled with the table uploads' metadata
 * @returns {Promise<string>} markdown with tables replaced by ![](url) refs
 */
async function renderTablesToImages(page, markdown, imageMeta) {
  const lines = (markdown ?? '').split('\n');
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const isTableRow = (l) => /^\s*\|/.test(l);
    const isSeparator = (l) => /^\s*\|[\s:|-]+\|?\s*$/.test(l);
    if (isTableRow(lines[i]) && i + 1 < lines.length && isSeparator(lines[i + 1])) {
      const block = [];
      while (i < lines.length && isTableRow(lines[i])) block.push(lines[i++]);
      try {
        const meta = await renderTablePng(page, block);
        if (meta?.url) {
          imageMeta.set(meta.url, meta);
          console.log(`    substack table rendered → ${meta.url.split('/').pop()}`);
          out.push('', `![Table](${meta.url})`, '');
          continue;
        }
      } catch (e) {
        console.warn(`    substack table render failed: ${e.message}`);
      }
      out.push(...block);
    } else {
      out.push(lines[i++]);
    }
  }
  return out.join('\n');
}

/** Render one pipe-table block to an uploaded PNG; returns the upload metadata. */
async function renderTablePng(page, blockLines) {
  // Render through the markdown→HTML pipeline so cell formatting (code
  // spans, bold, links) becomes real tags instead of literal markdown.
  const tableHtml = await markdownToHtml(blockLines.join('\n'));
  const html = `<!doctype html><html><body style="margin:0;background:#fff">
${tableHtml}
</body></html>
<style>
  table { border-collapse: collapse; font-family: -apple-system, system-ui, sans-serif; font-size: 15px; color: #1a1a1a; }
  th, td { border: 1px solid #ccc; padding: 8px 14px; text-align: left; }
  th { background: #f4f4f4; font-weight: 600; }
  code { font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 0.9em; background: #f4f4f4; padding: 1px 4px; border-radius: 3px; }
  a { color: #1a6faa; }
</style>`;
  // setContent navigates off the publication origin — remember it and go
  // back before the same-origin image upload fetch.
  const originUrl = page.url();
  await page.setContent(html);
  const el = page.locator('table');
  const buf = await el.screenshot({ scale: 'css' });
  await page.goto(originUrl, { waitUntil: 'domcontentloaded' });
  const dataUri = `data:image/png;base64,${buf.toString('base64')}`;
  const res = await page.evaluate(async (dataUri) => {
    const r = await fetch('/api/v1/image', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: dataUri }),
    });
    return { status: r.status, json: await r.json().catch(() => null) };
  }, dataUri);
  if (res.status === 200 && res.json?.url) return res.json;
  throw new Error(`image upload ${res.status}`);
}

/**
 * Create the draft via the session browser (same-origin fetch on the
 * publication — carries cookies + a real browser fingerprint, dodging the
 * 403s Substack gives plain HTTP clients).
 */
async function createDraftOnSubstack({ title, bodyMarkdown, socialPost, canonicalUrl, mode }) {
  const pub = process.env.SUBSTACK_PUB || 'theinkpens';
  const origin = `https://${pub}.substack.com`;

  const result = await withSessionBrowser('substack', async (page) => {
    await page.goto(`${origin}/publish/posts`, { waitUntil: 'domcontentloaded', timeout: 45_000 });
    if (/\/signin|\/login/i.test(page.url())) throw new AuthError('substack');

    // The drafts endpoint REQUIRES draft_bylines ([{id, publicationUserId}]) —
    // a payload without them 400s ("Invalid value"). Both IDs come from the
    // same-origin profile endpoint; pick this publication's membership.
    const profile = await page.evaluate(async () => {
      const r = await fetch('/api/v1/user/profile/self', { headers: { Accept: 'application/json' } });
      return r.ok ? await r.json() : null;
    });
    const membership = (profile?.publicationUsers ?? []).find(
      (pu) => pu.publication?.subdomain === pub || pu.publication === pub
    ) ?? profile?.publicationUsers?.[0];

    const imageMeta = new Map();
    let markdownForDoc = bodyMarkdown ?? '';
    if (mode === 'full') {
      await uploadInlineImages(page, markdownForDoc, imageMeta);
      markdownForDoc = await renderTablesToImages(page, markdownForDoc, imageMeta);
    }
    const payload = buildDraftPayload({ title, bodyMarkdown: markdownForDoc, socialPost, canonicalUrl, mode, imageUrlMap: imageMeta });
    if (profile && membership) {
      payload.draft_bylines = [{ id: profile.id, publicationUserId: membership.id }];
    }

    const res = await page.evaluate(async (payload) => {
      const r = await fetch('/api/v1/drafts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      return { status: r.status, json: await r.json().catch(() => null) };
    }, payload);

    if (res.status === 401 || res.status === 403) throw new AuthError('substack');
    if (res.status >= 400 || !res.json?.id) {
      throw new Error(`draft API ${res.status}: ${JSON.stringify(res.json).slice(0, 200)}`);
    }
    // The response carries the draft object; the editor deep link is either
    // draft_url or derived from the id. Human reviews there before Publish.
    const url = typeof res.json.draft_url === 'string' && res.json.draft_url.includes('/publish/')
      ? res.json.draft_url
      : `${origin}/publish/post/${res.json.id}`;
    return { id: res.json.id, url };
  });
  return result; // { value } | { value: null, authFailed, reason }
}

/**
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.bodyMarkdown
 * @param {string} opts.bodyHtml       - paste-ready HTML (manual fallback)
 * @param {string} opts.socialPost     - short blurb for a teaser intro
 * @param {string} opts.canonicalUrl
 * @param {string} opts.slug
 * @param {boolean} [opts.dryRun]      - never touch the network when true
 * @returns {Promise<{id: string, url: string}>}
 */
export async function publish({ title, bodyMarkdown, bodyHtml, socialPost, canonicalUrl, slug, dryRun }) {
  const mode = process.env.SUBSTACK_DRAFT_MODE === 'teaser' ? 'teaser' : 'full';

  let assistedNote = null;
  if (hasSession('substack')) {
    if (dryRun) {
      return { id: 'draft', url: `(dry-run: would create a ${mode} draft on Substack)` };
    }
    const { value: draft, authFailed, reason } = await createDraftOnSubstack({ title, bodyMarkdown, socialPost, canonicalUrl, mode });
    if (draft) return { id: 'draft', url: draft.url };
    // Session exists but the draft failed (expired login, API drift). Record
    // why on the package and fall through — the manual path keeps the run green.
    assistedNote = authFailed
      ? '⚠ Assisted draft failed: session expired — re-run `task posse:login -- substack`.'
      : `⚠ Assisted draft failed: ${reason || 'unknown error'}. Falling back to manual.`;
  }

  await seedPackage({ slug, title, canonicalUrl, bodyMarkdown });
  if (bodyHtml) await writeHtmlPackage({ slug, title, bodyHtml });
  const substackPub = process.env.SUBSTACK_PUB || 'theinkpens';
  await addPlatformNote({
    slug,
    platform: 'Substack',
    instructions: [
      'Substack has **no posting API** and **no canonical-URL support**. To avoid',
      'duplicate-content cannibalizing your own site, prefer posting only a teaser',
      'plus a "read more" link rather than the full body.',
      '',
      '**New post editor:**',
      '',
      `<https://${substackPub}.substack.com/publish/post>`,
      '',
      `Suggested teaser intro: ${socialPost || '(use the socialPost blurb)'}`,
      'Then link: "Read the full blog → " followed by:',
      '',
      `<${canonicalUrl}>`,
      '',
      'To paste formatted content (teaser or full body), open the HTML companion,',
      'select-all → copy → paste into the Substack editor:',
      '',
      `<${packageHtmlPath(slug)}>`,
      '',
      'Tip: locally, `task posse:login -- substack` (once) + `task posse:assisted`',
      'automates this — the draft is created for you; you just review + Publish.',
      ...(assistedNote ? ['', assistedNote] : []),
    ].join('\n'),
  });
  return { id: 'manual', url: packagePath(slug) };
}

export function publicUrl() {
  return undefined;
}
