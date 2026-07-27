// Bluesky / AT Protocol — https://docs.bsky.app
// createSession (app password -> accessJwt) then createRecord with an
// "external" embed so the post renders as a link card to the canonical URL.
// Free, open. Updates via putRecord (we keep it create-only for simplicity).
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
  return { accessJwt: json.accessJwt };
}

async function resolveExternalCard(accessJwt, url) {
  // Get a link-card preview via the app.bsky.embed.external generation endpoint.
  try {
    const res = await fetch(`${PDS}/xrpc/app.bsky.embed.external.getExternal?${new URLSearchParams({ url })}`, {
      headers: { Authorization: `Bearer ${accessJwt}` },
    });
    if (res.ok) {
      const { external } = await res.json();
      return external; // { uri, title, description, thumb: { ref, mimeType } }
    }
  } catch {
    /* card preview is best-effort */
  }
  return undefined;
}

/**
 * @param {object} opts
 * @param {string} opts.text          - the socialPost blurb + canonical URL
 * @param {string} opts.canonicalUrl
 * @param {string} [opts.title]       - for the link card
 * @param {string} [opts.description] - for the link card
 * @param {boolean} opts.dryRun
 * @returns {Promise<{id: string, url: string}>} id is the at:// record uri
 */
export async function publish({ text, canonicalUrl, title, description, dryRun }) {
  if (dryRun) {
    return { id: 'dry-run', url: 'https://bsky.app (would createRecord)' };
  }

  const { accessJwt } = await createSession();
  const external = await resolveExternalCard(accessJwt, canonicalUrl);
  const record = {
    $type: 'app.bsky.feed.post',
    text: text.slice(0, 300),
    createdAt: new Date().toISOString(),
    embed: external
      ? {
          $type: 'app.bsky.embed.external',
          external: {
            uri: external.uri ?? canonicalUrl,
            title: external.title ?? title ?? '',
            description: external.description ?? description ?? '',
            ...(external.thumb ? { thumb: external.thumb } : {}),
          },
        }
      : undefined,
  };

  const res = await fetch(`${PDS}/xrpc/com.atproto.repo.createRecord`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${accessJwt}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      repo: process.env.BLUESKY_IDENTIFIER,
      collection: 'app.bsky.feed.post',
      record,
    }),
  });
  if (!res.ok) {
    throw new Error(`bluesky createRecord failed: ${res.status} ${await res.text()}`);
  }
  const json = await res.json();
  const uri = json.uri; // at://did/app.bsky.feed.post/rkey
  // Build a friendly web URL from the record uri.
  const rkey = uri.split('/').pop();
  const handle = process.env.BLUESKY_IDENTIFIER.replace(/^[^@]*@/, '');
  return { id: uri, url: `https://bsky.app/profile/${handle}/post/${rkey}` };
}

export function publicUrl(uri) {
  if (!uri) return undefined;
  const rkey = uri.split('/').pop();
  const handle = process.env.BLUESKY_IDENTIFIER.replace(/^[^@]*@/, '');
  return `https://bsky.app/profile/${handle}/post/${rkey}`;
}
