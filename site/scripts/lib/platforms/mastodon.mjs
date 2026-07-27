// Mastodon — https://docs.joinmastodon.org/methods/statuses/
// POST /api/v1/statuses (per-instance). Free. Bearer token (write:statuses).
export const name = 'mastodon';

export function available() {
  return Boolean(process.env.MASTODON_INSTANCE && process.env.MASTODON_TOKEN);
}

function api() {
  const instance = process.env.MASTODON_INSTANCE.replace(/\/$/, '');
  return instance + '/api/v1/statuses';
}

/**
 * @param {object} opts
 * @param {string} opts.text         - socialPost blurb + canonical URL
 * @param {string} [opts.existingId] - status id to update (PUT)
 * @param {boolean} opts.dryRun
 * @returns {Promise<{id: string, url: string}>}
 */
export async function publish({ text, existingId, dryRun }) {
  if (dryRun) {
    return { id: 'dry-run', url: 'https://mastodon (would POST status)' };
  }

  const method = existingId ? 'PUT' : 'POST';
  const url = existingId ? `${api()}/${existingId}` : api();
  const res = await fetch(url, {
    method,
    headers: { Authorization: `Bearer ${process.env.MASTODON_TOKEN}` },
    body: new URLSearchParams({
      status: text.slice(0, 500),
      visibility: 'public',
    }),
  });
  if (!res.ok) {
    throw new Error(`mastodon ${method} failed: ${res.status} ${await res.text()}`);
  }
  const json = await res.json();
  return { id: String(json.id), url: json.url };
}

export function publicUrl(id) {
  if (!id) return undefined;
  // We don't have the full permalink without the account slug; return the instance.
  return process.env.MASTODON_INSTANCE;
}
