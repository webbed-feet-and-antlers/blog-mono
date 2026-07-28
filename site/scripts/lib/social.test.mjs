import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeSocial, teaserBlurb } from './social.mjs';

const URL = 'https://theinkpens.com/blog/x/';

test('normalizeSocial: appends canonical URL to the LAST post of thread platforms', () => {
  const out = normalizeSocial({ social: { twitter: ['hook', 'detail'] } }, URL);
  assert.equal(out.twitter.posts.length, 2);
  assert.ok(out.twitter.posts[1].includes(URL), 'last post has the URL');
  assert.ok(!out.twitter.posts[0].includes(URL), 'first post has no URL');
});

test('normalizeSocial: appends URL to single-post thread platforms too', () => {
  const out = normalizeSocial({ social: { bluesky: 'one post' } }, URL);
  assert.equal(out.bluesky.posts.length, 1);
  assert.ok(out.bluesky.posts[0].includes(URL));
});

test('normalizeSocial: does NOT append URL to LinkedIn (it goes in a comment)', () => {
  const out = normalizeSocial({ social: { linkedin: 'body text' } }, URL);
  assert.equal(out.linkedin.posts.length, 1);
  assert.ok(!out.linkedin.posts[0].includes(URL), 'LinkedIn body must not contain the link');
});

test('normalizeSocial: accepts a string or array per platform', () => {
  const s = normalizeSocial({ social: { mastodon: 'single' } }, URL);
  assert.deepEqual(s.mastodon.posts, [`single\n\n${URL}`]);
  const a = normalizeSocial({ social: { mastodon: ['a', 'b'] } }, URL);
  assert.equal(a.mastodon.posts.length, 2);
});

test('normalizeSocial: fallback chain social.<p> -> socialPost -> description -> empty', () => {
  // platform present (single string -> 1 post, URL appended to it)
  const t = normalizeSocial({ social: { twitter: 'T' }, socialPost: 'S' }, URL).twitter;
  assert.equal(t.posts.length, 1);
  assert.ok(t.posts[0].includes('T'));
  assert.ok(t.posts[0].includes(URL));
  // falls back to socialPost
  const viaPost = normalizeSocial({ socialPost: 'from socialPost' }, URL);
  assert.equal(viaPost.twitter.posts[0].split('\n')[0], 'from socialPost');
  // falls back to description
  const viaDesc = normalizeSocial({ description: 'desc fallback' }, URL);
  assert.equal(viaDesc.bluesky.posts[0].split('\n')[0], 'desc fallback');
  // empty when nothing
  const empty = normalizeSocial({}, URL);
  assert.deepEqual(empty.mastodon.posts, []);
});

test('normalizeSocial: image flag defaults true, only false when explicitly false', () => {
  assert.equal(normalizeSocial({}, URL).twitter.image, true);
  assert.equal(normalizeSocial({ social: { image: false } }, URL).twitter.image, false);
  assert.equal(normalizeSocial({ social: { image: true } }, URL).twitter.image, true);
  // other falsy values don't disable
  assert.equal(normalizeSocial({ social: { image: undefined } }, URL).twitter.image, true);
});

test('normalizeSocial: trims and drops empty/whitespace posts', () => {
  const out = normalizeSocial({ social: { twitter: ['  real  ', '', '   '] } }, URL);
  assert.equal(out.twitter.posts.length, 1);
});

test('teaserBlurb: resolution order linkedin -> twitter -> socialPost -> description', () => {
  assert.equal(teaserBlurb({ social: { linkedin: 'li', twitter: 'tw' } }), 'li');
  assert.equal(teaserBlurb({ social: { twitter: 'tw' } }), 'tw');
  assert.equal(teaserBlurb({ socialPost: 'sp' }), 'sp');
  assert.equal(teaserBlurb({ description: 'd' }), 'd');
  assert.equal(teaserBlurb({}), '');
});
