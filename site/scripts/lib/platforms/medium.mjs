// Medium — https://github.com/Medium/medium-api-docs (legacy)
// POST /v1/users/{userId}/posts. Requires a pre-2025 integration token; if
// MEDIUM_TOKEN is unset, the adapter reports unavailable and is skipped.
// Supports canonicalUrl. Publish-only (no official update endpoint).
const API = 'https://api.medium.com/v1';

export const name = 'medium';

export function available() {
  return Boolean(process.env.MEDIUM_TOKEN && process.env.MEDIUM_USER_ID);
}

/**
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.bodyMarkdown   - sanitized Markdown
 * @param {string} opts.canonicalUrl
 * @param {string[]} opts.tags         - max 3, ≤25 chars each
 * @param {boolean} opts.dryRun
 * @returns {Promise<{id: string, url: string}>}
 */
export async function publish({ title, bodyMarkdown, canonicalUrl, tags, dryRun }) {
  if (dryRun) {
    return { id: 'dry-run', url: 'https://medium.com (would POST)' };
  }

  const userId = process.env.MEDIUM_USER_ID;
  const res = await fetch(`${API}/users/${userId}/posts`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${process.env.MEDIUM_TOKEN}`,
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify({
      title: title.slice(0, 100),
      contentFormat: 'markdown',
      content: bodyMarkdown,
      canonicalUrl,
      tags: tags.slice(0, 3).map((t) => t.toLowerCase().replace(/\s+/g, '-')).slice(0, 25),
      publishStatus: 'public',
    }),
  });
  if (!res.ok) {
    throw new Error(`medium POST failed: ${res.status} ${await res.text()}`);
  }
  const json = await res.json();
  return { id: json.data.id, url: json.data.url };
}

export function publicUrl(id) {
  return id && id !== 'dry-run' ? `https://medium.com/p/${id}` : undefined;
}
