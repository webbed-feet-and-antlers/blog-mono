// Shared Buffer GraphQL client used by the per-platform adapters that post
// through Buffer (X/Twitter and LinkedIn). Buffer holds the platform
// credentials, so a connected channel is free to post to via one personal
// API key.
//
//   Endpoint:  https://api.buffer.com  (Bearer auth, always POST)
//   Docs:      https://developers.buffer.com  (createPost mutation)
//
// Buffer fetches media by public URL at publish time (no upload endpoint), so
// image attachment needs a stable public URL — returned by ogImageUrl() only
// when PUBLIC_OG_BASE_URL is set.

const ENDPOINT = 'https://api.buffer.com';
const CREATE_POST_MUTATION = `mutation CreateThreadedPost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id status } }
    ... on MutationError { message }
  }
}`;

/**
 * Public OG image URL for a blog slug, or undefined if PUBLIC_OG_BASE_URL
 * (the deployed site origin) isn't set — in which case the post goes text-only.
 * @param {string} slug
 * @returns {string|undefined}
 */
export function ogImageUrl(slug) {
  const ogBase = (process.env.PUBLIC_OG_BASE_URL || '').replace(/\/$/, '');
  return ogBase ? `${ogBase}/og/${slug}.png` : undefined;
}

/**
 * POST the createPost GraphQL mutation to Buffer with Bearer auth.
 * @param {object} input   - CreatePostInput (channelId, text, assets, mode, metadata, ...)
 * @returns {Promise<{id: string, status: string}>} the created post object
 * @throws on HTTP error or a MutationError response
 */
export async function createPost(input) {
  const res = await fetch(ENDPOINT, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${process.env.BUFFER_API_KEY}`,
    },
    body: JSON.stringify({ query: CREATE_POST_MUTATION, variables: { input } }),
  });
  if (!res.ok) {
    throw new Error(`buffer createPost failed: ${res.status} ${await res.text()}`);
  }
  const json = await res.json();
  const success = json?.data?.createPost?.post;
  if (!success) {
    throw new Error(`buffer createPost returned an error: ${json?.data?.createPost?.message ?? JSON.stringify(json)}`);
  }
  return success;
}
