// LinkedIn — the short feed post is the CAPTION for the LinkedIn Article
// (drafted by linkedin-article.mjs). The Article format is UI-only, so a
// human publishes the draft and records the public URL via
// `task posse:confirm -- <slug> linkedinArticle <url>`; only then does this
// adapter post the caption — the social.linkedin copy — with the Article's
// URL in the body. LinkedIn renders a linkedin.com article URL as a native
// article card in the feed, so no image asset is attached (an image would
// only compete with the card).
//
// Posted through Buffer (same GraphQL API as the X adapter), not the native
// LinkedIn API. Buffer holds the LinkedIn credentials, so this avoids the
// native LinkedIn developer setup entirely: no Standalone app, no OAuth, no
// 60-day-expiry access token + expiry guard, no person URN, and no binary
// image upload. Just connect the LinkedIn account in Buffer and set
// BUFFER_LINKEDIN_CHANNEL_ID. (LinkedIn personal profiles don't support
// threads, so the copy blocks are joined into one post.)
import { createPost } from '../buffer-client.mjs';

export const name = 'linkedin';

export function available() {
  return Boolean(process.env.BUFFER_API_KEY && process.env.BUFFER_LINKEDIN_CHANNEL_ID);
}

/**
 * @param {object} opts
 * @param {string[]} opts.posts        - joined into a single caption body
 *                                       (LinkedIn personal profiles don't thread)
 * @param {string} opts.articleUrl     - public LinkedIn Article URL, from
 *                                       syndication.linkedinArticle (set by
 *                                       task posse:confirm) — appended in-body
 * @param {boolean} opts.dryRun
 * @returns {Promise<{id: string, url: string}>} id is the Buffer post id
 */
export async function publish({ posts, articleUrl, dryRun }) {
  if (!articleUrl) {
    throw new Error(
      'no LinkedIn Article URL yet — publish the article draft, then run ' +
        '`task posse:confirm -- <slug> linkedinArticle <url>`, and re-run syndication'
    );
  }

  if (dryRun) {
    return {
      id: 'dry-run',
      url: `https://linkedin.com (would post ${posts.length} caption block(s) + article link)`,
    };
  }

  const channelId = process.env.BUFFER_LINKEDIN_CHANNEL_ID;

  // Join caption blocks into one body; the article URL renders as the feed's
  // article card, so no image asset is attached.
  const body = `${posts.join('\n\n')}\n\n${articleUrl}`.trim();
  const input = {
    text: body,
    channelId,
    schedulingType: 'automatic',
    mode: 'shareNow',
  };

  const success = await createPost(input);
  return { id: String(success.id), url: articleUrl };
}

export function publicUrl() {
  return 'https://www.linkedin.com';
}
