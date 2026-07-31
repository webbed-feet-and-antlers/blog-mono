// LinkedIn — posted through Buffer (same GraphQL API as the X adapter), not
// the native LinkedIn API. Buffer holds the LinkedIn credentials, so this
// avoids the native LinkedIn developer setup entirely: no Standalone app, no
// OAuth, no 60-day-expiry access token + expiry guard, no person URN, and no
// binary image upload. Just connect the LinkedIn account in Buffer and set
// BUFFER_LINKEDIN_CHANNEL_ID.
//
// Link handling: the canonical URL goes IN the post body. The previous native
// adapter posted it as a separate comment to dodge LinkedIn's in-feed link
// penalty, but Buffer's first-comment is a paid-plan-only feature, so we put
// the link in the body for free-plan compatibility. (LinkedIn personal
// profiles don't support threads, so the copy blocks are joined into one post.)
//
// Images via assets:[{image:{url}}] — Buffer fetches by public URL, so the OG
// image only attaches if PUBLIC_OG_BASE_URL is set; otherwise text-only.
import { createPost, ogImageUrl } from '../buffer-client.mjs';

export const name = 'linkedin';

export function available() {
  return Boolean(process.env.BUFFER_API_KEY && process.env.BUFFER_LINKEDIN_CHANNEL_ID);
}

/**
 * @param {object} opts
 * @param {string[]} opts.posts        - joined into a single post body
 *                                       (LinkedIn personal profiles don't thread)
 * @param {string} opts.canonicalUrl   - appended to the post body (in-body link)
 * @param {string} opts.slug           - essay slug, for the OG image URL
 * @param {boolean} opts.dryRun
 * @returns {Promise<{id: string, url: string}>} id is the Buffer post id
 */
export async function publish({ posts, canonicalUrl, slug, dryRun }) {
  const channelId = process.env.BUFFER_LINKEDIN_CHANNEL_ID;
  const imageUrl = ogImageUrl(slug);

  if (dryRun) {
    return {
      id: 'dry-run',
      url: `https://linkedin.com (would post ${posts.length} block(s) + in-body link${imageUrl ? ' + image' : ''}${!imageUrl ? ' (no PUBLIC_OG_BASE_URL)' : ''})`,
    };
  }

  // Join thread blocks into one body and append the canonical link in-body.
  const body = `${posts.join('\n\n')}\n\n${canonicalUrl}`.trim();
  const input = {
    text: body,
    channelId,
    schedulingType: 'automatic',
    mode: 'shareNow',
    ...(imageUrl ? { assets: [{ image: { url: imageUrl } }] } : {}),
  };

  const success = await createPost(input);
  return { id: String(success.id), url: 'https://www.linkedin.com' };
}

export function publicUrl() {
  return 'https://www.linkedin.com';
}
