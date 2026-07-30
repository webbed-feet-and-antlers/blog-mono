// X / Twitter via Buffer — Buffer holds the paid X credentials, so posting to
// your connected X channel is free on Buffer's Free plan. Buffer's GraphQL API
// (https://developers.buffer.com) takes a personal API key as Bearer token.
//
// Threads: pass metadata.twitter.thread as an array; Buffer reply-chains them
// automatically. Images via assets:[{image:{url}}] — Buffer needs a PUBLIC URL
// it can fetch, so the OG image is only attached if PUBLIC_OG_BASE_URL is set
// (the deployed site origin). Otherwise the thread posts text-only.
const ENDPOINT = 'https://api.buffer.com';

export const name = 'buffer (X)';

export function available() {
  return Boolean(process.env.BUFFER_API_KEY && process.env.BUFFER_X_CHANNEL_ID);
}

/**
 * @param {object} opts
 * @param {string[]} opts.posts        - thread body; canonical URL in last post
 * @param {string} opts.slug           - essay slug, for the OG image URL
 * @param {boolean} opts.dryRun
 * @returns {Promise<{id: string, url: string}>} id is the Buffer update id
 */
export async function publish({ posts, slug, dryRun }) {
  const channelId = process.env.BUFFER_X_CHANNEL_ID;

  // Buffer fetches media by public URL. Only attach if we have a public base.
  const ogBase = (process.env.PUBLIC_OG_BASE_URL || '').replace(/\/$/, '');
  const imageUrl = ogBase ? `${ogBase}/og-${slug}.png` : undefined;

  if (dryRun) {
    return {
      id: 'dry-run',
      url: `https://x.com (would thread ${posts.length} post(s)${imageUrl ? ' + image' : ''}${!imageUrl ? ' (no PUBLIC_OG_BASE_URL)' : ''})`,
    };
  }

  const thread = posts.map((text) => ({ text: text.slice(0, 280) }));
  const input = {
    text: thread[0].text, // top-level text must match the first thread entry
    channelId,
    schedulingType: 'automatic',
    mode: 'shareNow',
    metadata: { twitter: { thread } },
    ...(imageUrl ? { assets: [{ image: { url: imageUrl } }] } : {}),
  };

  const mutation = `mutation CreateThreadedPost($input: CreatePostInput!) {
    createPost(input: $input) {
      ... on PostActionSuccess { post { id status } }
      ... on MutationError { message }
    }
  }`;

  const res = await fetch(ENDPOINT, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${process.env.BUFFER_API_KEY}`,
    },
    body: JSON.stringify({ query: mutation, variables: { input } }),
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
