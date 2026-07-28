// LinkedIn — Posts API (rest/posts, li-lms-2026-07).
// https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
//
// Native POSSE pattern: post the body as a text/image post WITHOUT the canonical
// URL (LinkedIn hard-demotes posts with external links in the body), then post a
// COMMENT containing the canonical link. The post carries real hosted media.
//
// Token: personal OAuth access tokens expire every 60 days and non-MDP apps get
// NO refresh token, so re-auth is manual. This adapter guards against silent
// expiry by checking LINKEDIN_TOKEN_ISSUED and failing loudly within 5 days of
// the 60-day window.
const API = 'https://api.linkedin.com/rest';
const HEADERS = {
  Authorization: `Bearer ${process.env.LINKEDIN_TOKEN}`,
  'X-Restli-Protocol-Version': '2.0.0',
  'Linkedin-Version': '202607',
  'Content-Type': 'application/json',
};
const TOKEN_TTL_DAYS = 60;
const EXPIRY_WARN_DAYS = 5;

export const name = 'linkedin';

export function available() {
  return Boolean(process.env.LINKEDIN_TOKEN && process.env.LINKEDIN_PERSON_URN);
}

/**
 * Fail loudly if the token is near or past its 60-day lifetime. Personal
 * (non-MDP) LinkedIn apps get no refresh token, so re-auth is unavoidable.
 * Returns null if OK; returns an error string if the token looks expired.
 */
function checkTokenExpiry() {
  const issued = process.env.LINKEDIN_TOKEN_ISSUED;
  if (!issued) {
    return 'LINKEDIN_TOKEN_ISSUED not set (expected an ISO date when the token was generated). Set it so expiry can be checked, or the token may fail silently.';
  }
  const issuedMs = Date.parse(issued);
  if (Number.isNaN(issuedMs)) {
    return `LINKEDIN_TOKEN_ISSUED is not a valid date: "${issued}"`;
  }
  const ageDays = (Date.now() - issuedMs) / 86_400_000;
  const remaining = TOKEN_TTL_DAYS - ageDays;
  if (remaining <= 0) {
    return `LinkedIn token EXPIRED ${Math.abs(remaining).toFixed(0)} day(s) ago. Re-authorize and update LINKEDIN_TOKEN + LINKEDIN_TOKEN_ISSUED.`;
  }
  if (remaining <= EXPIRY_WARN_DAYS) {
    return `LinkedIn token expires in ${remaining.toFixed(1)} day(s). Re-authorize soon and update LINKEDIN_TOKEN + LINKEDIN_TOKEN_ISSUED.`;
  }
  return null;
}

async function uploadImage(imagePath, personUrn) {
  const { readFile } = await import('node:fs/promises');
  // Step 1: initialize upload -> get uploadUrl + image URN.
  const init = await fetch(`${API}/images?action=initializeUpload`, {
    method: 'POST',
    headers: HEADERS,
    body: JSON.stringify({ initializeUploadRequest: { owner: personUrn } }),
  });
  if (!init.ok) throw new Error(`linkedin initializeUpload failed: ${init.status} ${await init.text()}`);
  const initJson = await init.json();
  const { uploadUrl } = initJson.value;
  const imageUrn = initJson.value.image;

  // Step 2: PUT the binary to the upload URL.
  const bytes = await readFile(imagePath);
  const up = await fetch(uploadUrl, {
    method: 'PUT',
    headers: { Authorization: `Bearer ${process.env.LINKEDIN_TOKEN}`, 'Content-Type': 'image/png' },
    body: bytes,
  });
  if (!up.ok) throw new Error(`linkedin image upload failed: ${up.status} ${await up.text()}`);
  return imageUrn; // urn:li:image:...
}

/**
 * @param {object} opts
 * @param {string[]} opts.posts        - joined into a single post body (LinkedIn
 *                                       personal profiles don't support threads)
 * @param {string} opts.canonicalUrl   - posted as a SEPARATE comment (link penalty)
 * @param {string|null} opts.imagePath
 * @param {boolean} opts.dryRun
 * @returns {Promise<{id: string, url: string}>} id is the share URN
 */
export async function publish({ posts, canonicalUrl, imagePath, dryRun }) {
  const personUrn = process.env.LINKEDIN_PERSON_URN;

  const expiry = checkTokenExpiry();
  if (expiry) {
    throw new Error(expiry);
  }

  if (dryRun) {
    return {
      id: 'dry-run',
      url: `https://linkedin.com (would post ${posts.length} block(s)${imagePath ? ' + image' : ''} + comment with link)`,
    };
  }

  // Join thread blocks into one post body (LinkedIn has no personal threads).
  const body = posts.join('\n\n');
  const imageUrn = imagePath ? await uploadImage(imagePath, personUrn) : null;

  const postBody = {
    author: personUrn,
    commentary: body,
    visibility: 'PUBLIC',
    distribution: { feedDistribution: 'MAIN_FEED', targetEntities: [], thirdPartyDistributionChannels: [] },
    lifecycleState: 'PUBLISHED',
    isReshareDisabledByAuthor: false,
    ...(imageUrn ? { content: { media: { id: imageUrn, altText: 'Essay preview' } } } : {}),
  };

  const res = await fetch(`${API}/posts`, { method: 'POST', headers: HEADERS, body: JSON.stringify(postBody) });
  if (!res.ok) throw new Error(`linkedin post failed: ${res.status} ${await res.text()}`);
  // The share URN is in the x-restli-id header.
  const shareUrn = res.headers.get('x-restli-id');

  // Post the canonical URL as a comment (LinkedIn demotes in-body links).
  if (shareUrn && canonicalUrl) {
    try {
      await postComment(shareUrn, personUrn, canonicalUrl);
    } catch (err) {
      // A failed comment shouldn't fail the whole syndication — the post went up.
      console.warn(`  linkedin: post created but comment failed: ${err.message}`);
    }
  }

  return { id: shareUrn ?? 'unknown', url: 'https://www.linkedin.com' };
}

async function postComment(shareUrn, personUrn, text) {
  // Resolve the activity URN for the share (comments need urn:li:activity:...).
  const got = await fetch(`${API}/posts/${encodeURIComponent(shareUrn)}`, { headers: HEADERS });
  let activityUrn = shareUrn;
  if (got.ok) {
    const j = await got.json();
    activityUrn = j.activity || j['activityUrn'] || shareUrn;
  }
  await fetch(`${API}/socialActions/${encodeURIComponent(shareUrn)}/comments`, {
    method: 'POST',
    headers: HEADERS,
    body: JSON.stringify({ actor: personUrn, object: activityUrn, message: { text } }),
  });
}

export function publicUrl() {
  return 'https://www.linkedin.com';
}
