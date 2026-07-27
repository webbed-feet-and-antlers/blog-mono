// X / Twitter via Buffer — Buffer holds the paid X credentials, so posting to
// your connected X channel is free on Buffer's Free plan. Buffer's new GraphQL
// API (https://developers.buffer.com) takes a personal API key as Bearer token.
// createPost posts to a specific channel id (your connected X account).
const ENDPOINT = 'https://api.buffer.com';

export const name = 'buffer (X)';

export function available() {
  return Boolean(process.env.BUFFER_API_KEY && process.env.BUFFER_X_CHANNEL_ID);
}

/**
 * @param {object} opts
 * @param {string} opts.text          - socialPost blurb + canonical URL (≤280 chars)
 * @param {boolean} opts.dryRun
 * @returns {Promise<{id: string, url: string}>} id is the Buffer update id
 */
export async function publish({ text, dryRun }) {
  const channelId = process.env.BUFFER_X_CHANNEL_ID;
  if (dryRun) {
    return { id: 'dry-run', url: 'https://x.com (would createPost via Buffer)' };
  }

  const mutation = `mutation {
    createPost(input: {
      text: ${JSON.stringify(text.slice(0, 280))}
      channelId: ${JSON.stringify(channelId)}
      mode: shareNow
    }) {
      ... on PostActionSuccess { post { id text status } }
      ... on MutationError { message }
    }
  }`;

  const res = await fetch(ENDPOINT, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${process.env.BUFFER_API_KEY}`,
    },
    body: JSON.stringify({ query: mutation }),
  });
  if (!res.ok) {
    throw new Error(`buffer createPost failed: ${res.status} ${await res.text()}`);
  }
  const json = await res.json();
  const success = json?.data?.createPost?.post;
  if (!success) {
    throw new Error(`buffer createPost returned an error: ${json?.data?.createPost?.message ?? JSON.stringify(json)}`);
  }
  return { id: String(success.id), url: 'https://x.com' };
}

export function publicUrl() {
  return 'https://x.com';
}
