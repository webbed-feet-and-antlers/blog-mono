// Bluesky / AT Protocol — https://docs.bsky.app
// Threads: create the root post, then createRecord with `reply:{root,parent}`
// referencing each prior {uri,cid}. Images via app.bsky.embed.images after
// uploadBlob (<=1MB). Free, open.
const PDS = process.env.BLUESKY_PDS || 'https://bsky.social';

export const name = 'bluesky';

export function available() {
  return Boolean(process.env.BLUESKY_IDENTIFIER && process.env.BLUESKY_APP_PASSWORD);
}

async function createSession() {
  const res = await fetch(`${PDS}/xrpc/com.atproto.server.createSession`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      identifier: process.env.BLUESKY_IDENTIFIER,
      password: process.env.BLUESKY_APP_PASSWORD,
    }),
  });
  if (!res.ok) throw new Error(`bluesky createSession failed: ${res.status} ${await res.text()}`);
  const json = await res.json();
  return { accessJwt: json.accessJwt, did: json.did };
}

async function uploadBlob(accessJwt, imagePath) {
  const { readFile } = await import('node:fs/promises');
  const bytes = await readFile(imagePath);
  const res = await fetch(`${PDS}/xrpc/com.atproto.repo.uploadBlob`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${accessJwt}`, 'Content-Type': 'image/png' },
    body: bytes,
  });
  if (!res.ok) throw new Error(`bluesky uploadBlob failed: ${res.status} ${await res.text()}`);
  const json = await res.json();
  return json.blob; // {$type:'blob', ref:{$link}, mimeType, size}
}

async function createRecord(accessJwt, record) {
  const res = await fetch(`${PDS}/xrpc/com.atproto.repo.createRecord`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${accessJwt}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      repo: process.env.BLUESKY_IDENTIFIER,
      collection: 'app.bsky.feed.post',
      record,
    }),
  });
  if (!res.ok) throw new Error(`bluesky createRecord failed: ${res.status} ${await res.text()}`);
  return res.json(); // {uri, cid}
}

// URL detection for facets: match greedily, then back off trailing punctuation
// so "https://x.com/blog/)." links to the URL and not the period.
const URL_RE = /https?:\/\/[^\s<>"']+/g;
const TRAILING_PUNCT = /[.,;:!?)\]}'"]+$/;
const encoder = new TextEncoder();

/**
 * Build Bluesky richtext facets for every bare URL in `text`. Bluesky does NOT
 * linkify plain-text URLs from API posts — without a link facet the URL renders
 * as inert text. Facet indices are UTF-8 BYTE offsets (not JS string indices),
 * so multi-byte characters before a URL shift its range.
 *
 * @param {string} text
 * @returns {object[]} facets (empty when the text has no URLs)
 */
export function linkFacets(text) {
  const facets = [];
  for (const m of text.matchAll(URL_RE)) {
    const url = m[0].replace(TRAILING_PUNCT, '');
    if (!url) continue;
    const byteStart = encoder.encode(text.slice(0, m.index)).length;
    facets.push({
      index: { byteStart, byteEnd: byteStart + encoder.encode(url).length },
      features: [{ $type: 'app.bsky.richtext.facet#link', uri: url }],
    });
  }
  return facets;
}

/**
 * @param {object} opts
 * @param {string[]} opts.posts        - thread body; canonical URL already in last post
 * @param {string|null} opts.imagePath - local path to OG png, or null
 * @param {string} [opts.title]        - blog title (link card)
 * @param {string} [opts.description]  - blog description (link card)
 * @param {string} [opts.canonicalUrl] - renders as a link CARD on the post carrying the URL
 * @param {boolean} opts.dryRun
 * @returns {Promise<{id: string, url: string}>} id is the root post at:// uri
 */
export async function publish({ posts, imagePath, title, description, canonicalUrl, dryRun }) {
  if (dryRun) {
    return {
      id: 'dry-run',
      url: `https://bsky.app (would thread ${posts.length} post(s)${imagePath ? ' + image' : ''}${canonicalUrl ? ' + link card' : ''})`,
    };
  }

  const { accessJwt } = await createSession();
  const blob = imagePath ? await uploadBlob(accessJwt, imagePath) : null;

  // The post carrying the canonical URL (the thread's last) gets a link card
  // via embed.external — clickable facets alone don't render a card, and an
  // explicit embed with an uploaded thumb is immediate and deterministic.
  // A post has ONE embed: when the root is also the link post (single-post
  // thread) the card replaces the bare image (the card carries the thumb).
  const external = canonicalUrl
    ? {
        $type: 'app.bsky.embed.external',
        external: {
          uri: canonicalUrl,
          title: title ?? '',
          description: description ?? '',
          ...(blob ? { thumb: blob } : {}),
        },
      }
    : null;
  const rootIsLinkPost = posts.length === 1;

  // Root post: link card when it's the link post, else the image (if any).
  const rootText = posts[0].slice(0, 300);
  const rootFacets = linkFacets(rootText);
  const rootRecord = {
    $type: 'app.bsky.feed.post',
    text: rootText,
    createdAt: new Date().toISOString(),
    ...(rootFacets.length ? { facets: rootFacets } : {}),
    ...(rootIsLinkPost && external
      ? { embed: external }
      : blob
        ? { embed: { $type: 'app.bsky.embed.images', images: [{ alt: 'Blog preview', image: blob, aspectRatio: { width: 1200, height: 630 } }] } }
        : {}),
  };
  const root = await createRecord(accessJwt, rootRecord);

  // Chain the remaining posts as replies; the last one carries the link card.
  let parent = root;
  for (let i = 1; i < posts.length; i++) {
    const clipped = posts[i].slice(0, 300);
    const facets = linkFacets(clipped);
    const isLinkPost = i === posts.length - 1;
    const replyRecord = {
      $type: 'app.bsky.feed.post',
      text: clipped,
      createdAt: new Date().toISOString(),
      ...(facets.length ? { facets } : {}),
      ...(isLinkPost && external ? { embed: external } : {}),
      reply: { root: { uri: root.uri, cid: root.cid }, parent: { uri: parent.uri, cid: parent.cid } },
    };
    parent = await createRecord(accessJwt, replyRecord);
  }

  const handle = process.env.BLUESKY_IDENTIFIER.replace(/^[^@]*@/, '');
  const rkey = root.uri.split('/').pop();
  return { id: root.uri, url: `https://bsky.app/profile/${handle}/post/${rkey}` };
}

export function publicUrl(uri) {
  if (!uri) return undefined;
  const rkey = uri.split('/').pop();
  const handle = process.env.BLUESKY_IDENTIFIER.replace(/^[^@]*@/, '');
  return `https://bsky.app/profile/${handle}/post/${rkey}`;
}
