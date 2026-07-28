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

/**
 * @param {object} opts
 * @param {string[]} opts.posts        - thread body; canonical URL already in last post
 * @param {string|null} opts.imagePath - local path to OG png, or null
 * @param {boolean} opts.dryRun
 * @returns {Promise<{id: string, url: string}>} id is the root post at:// uri
 */
export async function publish({ posts, imagePath, dryRun }) {
  if (dryRun) {
    return { id: 'dry-run', url: `https://bsky.app (would thread ${posts.length} post(s)${imagePath ? ' + image' : ''})` };
  }

  const { accessJwt } = await createSession();
  const blob = imagePath ? await uploadBlob(accessJwt, imagePath) : null;

  // Root post carries the image (if any). No reply ref on the first post.
  const rootRecord = {
    $type: 'app.bsky.feed.post',
    text: posts[0].slice(0, 300),
    createdAt: new Date().toISOString(),
    ...(blob
      ? { embed: { $type: 'app.bsky.embed.images', images: [{ alt: 'Essay preview', image: blob, aspectRatio: { width: 1200, height: 630 } }] } }
      : {}),
  };
  const root = await createRecord(accessJwt, rootRecord);

  // Chain the remaining posts as replies.
  let parent = root;
  for (const text of posts.slice(1)) {
    const replyRecord = {
      $type: 'app.bsky.feed.post',
      text: text.slice(0, 300),
      createdAt: new Date().toISOString(),
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
