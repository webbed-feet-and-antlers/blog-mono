// Normalizes per-platform social copy from frontmatter into a uniform shape:
//   { posts: string[], image: boolean }
//
// Resolution order for a platform's `posts`:
//   1. blog.social.<platform>   (string | string[])
//   2. blog.socialPost          (string — backward compat)
//   3. blog.description         (string — last-resort fallback)
//
// The canonical URL is appended to the LAST post for thread-capable platforms
// (twitter/bluesky/mastodon). For LinkedIn it is NOT appended here — that
// adapter posts the link as a separate comment (LinkedIn demotes in-body links).

const THREAD_PLATFORMS = new Set(['twitter', 'bluesky', 'mastodon']);

/**
 * @param {object} data           - blog frontmatter
 * @param {string} canonicalUrl
 * @returns {{ [platform: string]: { posts: string[], image: boolean } }}
 */
export function normalizeSocial(data, canonicalUrl) {
  const social = data.social ?? {};
  const wantImage = social.image !== false; // default true unless explicitly false
  const platforms = ['twitter', 'linkedin', 'bluesky', 'mastodon'];
  const out = {};

  for (const p of platforms) {
    const raw = social[p] ?? data.socialPost ?? data.description ?? '';
    const posts = Array.isArray(raw) ? [...raw] : [raw];
    const cleaned = posts.map((s) => String(s).trim()).filter(Boolean);

    if (cleaned.length === 0) {
      out[p] = { posts: [], image: wantImage };
      continue;
    }

    // Append canonical URL to the last post for thread platforms.
    if (THREAD_PLATFORMS.has(p)) {
      cleaned[cleaned.length - 1] = `${cleaned[cleaned.length - 1]}\n\n${canonicalUrl}`.trim();
    }
    out[p] = { posts: cleaned, image: wantImage };
  }

  return out;
}

/**
 * Short single-line blurb for the Substack teaser (no URL appended — the
 * teaser template adds the canonical link separately).
 * @param {object} data
 */
export function teaserBlurb(data) {
  const social = data.social ?? {};
  return (
    social.linkedin ??
    social.twitter ??
    data.socialPost ??
    data.description ??
    ''
  );
}
