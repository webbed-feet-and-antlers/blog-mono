// Mastodon — https://docs.joinmastodon.org/methods/statuses/
// Threads: each status after the first sets in_reply_to_id to the prior id.
// Media: POST /api/v2/media (returns id), then media_ids[] on the status.
// Free, per-instance. Bearer token (write:statuses, write:media).
export const name = 'mastodon';

export function available() {
  return Boolean(process.env.MASTODON_INSTANCE && process.env.MASTODON_TOKEN);
}

const instance = () => process.env.MASTODON_INSTANCE.replace(/\/$/, '');

async function uploadMedia(imagePath) {
  const { readFile } = await import('node:fs/promises');
  const bytes = await readFile(imagePath);
  const form = new FormData();
  // Use a Blob so fetch sets the multipart boundary correctly.
  form.append('file', new Blob([bytes], { type: 'image/png' }), 'og.png');
  form.append('description', 'Blog preview image');
  const res = await fetch(`${instance()}/api/v2/media`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${process.env.MASTODON_TOKEN}` },
    body: form,
  });
  if (!res.ok) throw new Error(`mastodon media upload failed: ${res.status} ${await res.text()}`);
  const json = await res.json();
  return String(json.id);
}

async function postStatus({ text, inReplyToId, mediaId }) {
  const body = new URLSearchParams({ status: text.slice(0, 500), visibility: 'public' });
  if (inReplyToId) body.set('in_reply_to_id', inReplyToId);
  if (mediaId) body.append('media_ids[]', mediaId);
  const res = await fetch(`${instance()}/api/v1/statuses`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${process.env.MASTODON_TOKEN}` },
    body,
  });
  if (!res.ok) throw new Error(`mastodon status failed: ${res.status} ${await res.text()}`);
  return res.json();
}

/**
 * @param {object} opts
 * @param {string[]} opts.posts
 * @param {string|null} opts.imagePath
 * @param {boolean} opts.dryRun
 * @returns {Promise<{id: string, url: string}>}
 */
export async function publish({ posts, imagePath, dryRun }) {
  if (dryRun) {
    return { id: 'dry-run', url: `https://mastodon (would thread ${posts.length} post(s)${imagePath ? ' + image' : ''})` };
  }

  const mediaId = imagePath ? await uploadMedia(imagePath) : undefined;

  // Root status carries the media.
  let prev = await postStatus({ text: posts[0], inReplyToId: null, mediaId });
  for (const text of posts.slice(1)) {
    prev = await postStatus({ text, inReplyToId: String(prev.id) });
  }
  return { id: String(prev.id), url: prev.url };
}

export function publicUrl() {
  return process.env.MASTODON_INSTANCE;
}
