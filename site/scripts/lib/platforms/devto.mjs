// dev.to / Forem — https://developers.forem.com/api/v1
// POST /api/articles (create) | PUT /api/articles/{id} (update)
// Free; supports canonical_url; accepts Markdown body_markdown.
const API = 'https://dev.to/api/articles';

export const name = 'dev.to';

export function available() {
  return Boolean(process.env.DEV_TO_API_KEY);
}

/**
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.bodyMarkdown   - sanitized Markdown (no MDX)
 * @param {string} opts.canonicalUrl
 * @param {string[]} opts.tags         - max 4, lowercased
 * @param {string} opts.description
 * @param {string|number|undefined} [opts.existingId] - article id for update
 * @param {boolean} opts.dryRun
 * @returns {Promise<{id: string, url: string}>}
 */
export async function publish({ title, bodyMarkdown, canonicalUrl, tags, description, existingId, dryRun }) {
  const apiKey = process.env.DEV_TO_API_KEY;
  const article = {
    title,
    body_markdown: bodyMarkdown,
    canonical_url: canonicalUrl,
    published: true,
    description: description.slice(0, 140),
    tags: tags.slice(0, 4).map((t) => t.toLowerCase().replace(/[^a-z0-9]/g, '')),
  };

  if (dryRun) {
    return { id: 'dry-run', url: `https://dev.to (would ${existingId ? 'PUT' : 'POST'})` };
  }

  const method = existingId ? 'PUT' : 'POST';
  const url = existingId ? `${API}/${existingId}` : API;
  const res = await fetch(url, {
    method,
    headers: { 'api-key': apiKey, 'Content-Type': 'application/json' },
    body: JSON.stringify({ article }),
  });
  if (!res.ok) {
    throw new Error(`dev.to ${method} failed: ${res.status} ${await res.text()}`);
  }
  const json = await res.json();
  return { id: String(json.id), url: json.url };
}

export function publicUrl(id) {
  // Best-effort: dev.to article URLs include the slug, which we don't store.
  return id ? `https://dev.to` : undefined;
}
