#!/usr/bin/env node
// POSSE syndicator: cross-posts each blog to dev.to, Bluesky, Mastodon,
// X (via Buffer), LinkedIn (via Buffer), and — with a local browser session
// saved via `task posse:login` — creates DRAFTS on Medium, Substack, LinkedIn
// Articles, and Indie Hackers (the no-API platforms), falling back to the
// manual syndication package when no session exists.
//
// Native POSSE: per-platform copy + threads (reply-chained) + native image
// attachment. The canonical URL lives in the last post of a thread so posts
// feel native rather than "blurb + link"; the LinkedIn post instead shares
// the confirmed LinkedIn Article's URL as its caption (see linkedin.mjs).
// Assisted drafts stop short of publishing: a human reviews and clicks
// Publish, then `task posse:confirm` records the final URL.
//
// Usage:
//   npm run syndicate -- --dry-run=true                 # preview all unpublished
//   npm run syndicate -- --dry-run=false                # post all unpublished
//   npm run syndicate -- --dry-run=true --blog=embeddings
//   npm run syndicate -- --dry-run=false --blog=embeddings --force=true
//
// --blog scopes the run to one post; it does NOT re-post platforms that
// already have IDs (so a local assisted follow-up after a labeled merge
// fills gaps — drafts the no-API platforms — without duplicating the social
// posts CI just made). --force=true is the explicit escape hatch that
// re-posts/re-drafts everything for that blog.
//
// Credentials are auto-loaded from site/.env via the `--env-file-if-exists=.env`
// flag in the npm script (no `source .env` needed). CI injects secrets directly.
import { loadBlogs, loadBlog } from './lib/blogs.mjs';
import { writeSyndicationIds, writeDraftLinks } from './lib/frontmatter.mjs';
import { mdxToMarkdown, markdownToHtml } from './lib/markdown.mjs';
import { normalizeSocial, teaserBlurb } from './lib/social.mjs';
import { renderOgImage } from './lib/og-image.mjs';
import { screenshotComponent, componentsInBody } from './lib/component-shot.mjs';
import * as devto from './lib/platforms/devto.mjs';
import * as bluesky from './lib/platforms/bluesky.mjs';
import * as mastodon from './lib/platforms/mastodon.mjs';
import * as buffer from './lib/platforms/buffer.mjs';
import * as linkedin from './lib/platforms/linkedin.mjs';
import * as linkedinArticle from './lib/platforms/linkedin-article.mjs';
import * as medium from './lib/platforms/medium.mjs';
import * as substack from './lib/platforms/substack.mjs';
import * as indiehackers from './lib/platforms/indiehackers.mjs';

// ── args ──────────────────────────────────────────────────────────────────
function parseArgs(argv) {
  const args = { dryRun: true, blog: undefined, force: false };
  for (const a of argv.slice(2)) {
    const m = a.match(/^--([^=]+)=(.*)$/);
    if (!m) continue;
    const [, k, v] = m;
    if (k === 'dry-run' || k === 'dry_run') args.dryRun = v !== 'false';
    if (k === 'blog') args.blog = v || undefined;
    if (k === 'force') args.force = v !== 'false';
  }
  return args;
}

const SITE_URL = (process.env.SITE_URL || '').replace(/\/$/, '');
const blogUrl = (slug) => `${SITE_URL}/blog/${slug}/`;

const c = {
  dim: (s) => `\x1b[2m${s}\x1b[0m`,
  green: (s) => `\x1b[32m${s}\x1b[0m`,
  yellow: (s) => `\x1b[33m${s}\x1b[0m`,
  red: (s) => `\x1b[31m${s}\x1b[0m`,
};
const log = (...a) => console.log(...a);

// ── which blogs to syndicate ─────────────────────────────────────────────
async function selectBlogs(opts) {
  if (opts.blog) {
    const e = await loadBlog(opts.blog);
    if (!e) {
      console.error(c.red(`Blog not found: ${opts.blog}`));
      process.exit(1);
    }
    return [e];
  }
  const all = await loadBlogs();
  return all
    .filter((e) => e.data.draft !== true)
    .sort((a, b) => new Date(a.data.pubDate) - new Date(b.data.pubDate));
}

// ── screenshot each interactive component in a blog (dark + light pair) ──
// Returns { ComponentName: { dark, light } } (absolute image URLs) for inlining
// into cross-post Markdown as a <picture>. Best-effort: if a variant fails we
// fall back to whichever we have; if both fail the image is skipped entirely
// (the tag is still stripped, the interactive note still appears). Skipped
// silently if Playwright isn't installed or the harness wasn't built.
const SHOT_THEMES = ['dark', 'light'];

async function screenshotComponentsForBlog(blog, siteUrl, summary) {
  const names = componentsInBody(blog.body);
  if (names.length === 0) return {};

  const images = {};
  for (const name of names) {
    const variants = {};
    let failures = 0;
    for (const theme of SHOT_THEMES) {
      try {
        const result = await screenshotComponent({ componentName: name, slug: blog.slug, theme });
        if (result) {
          variants[theme] = `${siteUrl}${result.url}`;
          summary.push(`  ${c.dim('shot')}  ${name.padEnd(14)} ${theme.padEnd(5)} → ${result.url}`);
        } else {
          failures++;
        }
      } catch (err) {
        summary.push(`  ${c.yellow('warn')} screenshot failed for ${name}/${theme}: ${err.message}`);
      }
    }
    // If every theme returned null, this component has no screenshot harness.
    // That's expected when a blog's body mentions a tag in prose (e.g. an
    // inline-code example like `<Component />`) that `componentsInBody` matched
    // but which isn't a real island — so we stay quiet rather than warn. The tag
    // is still stripped in the cross-post, and the trailing interactive note covers it.
    if (failures === SHOT_THEMES.length) {
      summary.push(`  ${c.dim('skip')} ${name.padEnd(14)} (no harness — likely documentation text, not a real component)`);
    }
    // Record an entry only if we got at least one variant. dark is the primary
    // (it's the <img> fallback on platforms that strip <picture>); if we only
    // have light, we still record it and mdxToMarkdown degrades gracefully.
    if (variants.dark || variants.light) {
      images[name] = variants;
    }
  }
  return images;
}

// ── per-blog platform run ────────────────────────────────────────────────
async function syndicateBlog(blog, opts) {
  const canonicalUrl = blogUrl(blog.slug);
  const { data } = blog;
  const syndication = data.syndication ?? {};
  const draftLinks = data.draftLinks ?? {};
  const newIds = {};
  const newDraftLinks = {};
  const summary = [];

  // Normalize per-platform social copy into { posts: string[], image: boolean }.
  const social = normalizeSocial(data, canonicalUrl);

  // Pre-pass: screenshot each interactive component used in the blog so the
  // images can be inlined into the cross-posted Markdown (dev.to/Medium can't
  // run React). Falls back gracefully if Playwright/harness isn't available.
  const componentImages = await screenshotComponentsForBlog(blog, SITE_URL, summary);
  const bodyMarkdown = await mdxToMarkdown(blog.body, canonicalUrl, componentImages);
  // Paste-ready HTML companion for Medium/Substack rich-text editors. Derived
  // from the sanitized markdown, so JSX stripping / screenshot inlining is reused.
  const bodyHtml = await markdownToHtml(bodyMarkdown);

  // Pre-pass: render the OG image once if any platform wants it.
  let imagePath = null;
  const anyWantsImage = Object.values(social).some((s) => s.image);
  if (anyWantsImage) {
    // Rough reading-time estimate for the OG badge (≈200 wpm).
    const readingMinutes = Math.max(1, Math.round(bodyMarkdown.trim().split(/\s+/).filter(Boolean).length / 200));
    imagePath = await renderOgImage({
      title: data.title,
      tags: data.tags ?? [],
      readingMinutes,
      subtitle: 'The Inkpens',
      brand: 'theinkpens',
      slug: blog.slug,
    }).catch((err) => {
      summary.push(`  ${c.yellow('warn')} og-image render failed: ${err.message}`);
      return null;
    });
  }

  // Each platform: skip if unavailable; skip if already syndicated (unless --force).
  const platforms = [
    {
      key: 'devto',
      label: devto.name,
      available: () => devto.available(),
      run: () => devto.publish({ title: data.title, bodyMarkdown, canonicalUrl, tags: data.tags ?? [], description: data.description, existingId: syndication.devto, dryRun: opts.dryRun }),
      skipIf: () => !opts.force && syndication.devto,
    },
    {
      key: 'bluesky',
      label: bluesky.name,
      available: () => bluesky.available(),
      run: () => bluesky.publish({ posts: social.bluesky.posts, imagePath, dryRun: opts.dryRun }),
      skipIf: () => !opts.force && syndication.bluesky,
    },
    {
      key: 'mastodon',
      label: mastodon.name,
      available: () => mastodon.available(),
      run: () => mastodon.publish({ posts: social.mastodon.posts, imagePath, dryRun: opts.dryRun }),
      skipIf: () => !opts.force && syndication.mastodon,
    },
    {
      key: 'buffer',
      label: buffer.name,
      available: () => buffer.available(),
      run: () => buffer.publish({ posts: social.twitter.posts, slug: blog.slug, dryRun: opts.dryRun }),
      skipIf: () => !opts.force && syndication.buffer,
    },
    {
      // The long-form LinkedIn Article (125k-char, UI-only — no API). Distinct
      // key from the short `linkedin` post so the two surfaces don't collide on
      // the syndication-idempotency field. With a saved linkedin session this
      // adapter creates an Article DRAFT via Playwright (paste + autosave);
      // otherwise it packages the body for a manual paste.
      key: 'linkedinArticle',
      label: linkedinArticle.name,
      available: () => linkedinArticle.available(),
      run: () => linkedinArticle.publish({ title: data.title, bodyMarkdown, bodyHtml, canonicalUrl, tags: data.tags ?? [], slug: blog.slug, dryRun: opts.dryRun }),
      skipIf: () => !opts.force && (syndication.linkedinArticle || draftLinks.linkedinArticle),
    },
    {
      // The short LinkedIn post is the CAPTION for the Article above: it only
      // goes out once the Article is published and confirmed
      // (syndication.linkedinArticle), and shares the Article's public URL
      // instead of the canonical one.
      key: 'linkedin',
      label: linkedin.name,
      available: () => linkedin.available(),
      run: () => linkedin.publish({ posts: social.linkedin.posts, articleUrl: syndication.linkedinArticle, dryRun: opts.dryRun }),
      skipIf: () => !opts.force && syndication.linkedin,
      waiting: () =>
        syndication.linkedinArticle
          ? null
          : draftLinks.linkedinArticle
            ? 'article draft ready — publish it, then task posse:confirm'
            : 'waiting on LinkedIn Article publish + task posse:confirm',
    },
    {
      key: 'medium',
      label: medium.name,
      available: () => medium.available(),
      run: () => medium.publish({ title: data.title, bodyMarkdown, bodyHtml, canonicalUrl, tags: data.tags ?? [], slug: blog.slug, dryRun: opts.dryRun }),
      skipIf: () => !opts.force && (syndication.medium || draftLinks.medium),
    },
    {
      key: 'substack',
      label: substack.name,
      available: () => substack.available(),
      run: () => substack.publish({ title: data.title, bodyMarkdown, bodyHtml, socialPost: teaserBlurb(data), canonicalUrl, slug: blog.slug, dryRun: opts.dryRun }),
      skipIf: () => !opts.force && (syndication.substack || draftLinks.substack),
    },
    {
      key: 'indiehackers',
      label: indiehackers.name,
      available: () => indiehackers.available(),
      run: () => indiehackers.publish({ title: data.title, bodyMarkdown, bodyHtml, socialPost: teaserBlurb(data), canonicalUrl, tags: data.tags ?? [], slug: blog.slug, dryRun: opts.dryRun }),
      skipIf: () => !opts.force && (syndication.indiehackers || draftLinks.indiehackers),
    },
  ];

  for (const p of platforms) {
    if (social[p.key] && social[p.key].posts.length === 0) {
      summary.push(`  ${c.yellow('skip')}  ${p.label.padEnd(14)} ${c.dim('(no social copy)')}`);
      continue;
    }
    if (!p.available()) {
      summary.push(`  ${c.yellow('skip')}  ${p.label.padEnd(14)} ${c.dim('(no credentials)')}`);
      continue;
    }
    if (p.skipIf()) {
      const drafted = !syndication[p.key] && draftLinks[p.key];
      summary.push(`  ${c.yellow('skip')}  ${p.label.padEnd(14)} ${c.dim(drafted ? '(draft ready — publish it, then task posse:confirm)' : '(already posted)')}`);
      continue;
    }
    // Dependent platforms (e.g. the LinkedIn caption needs its Article
    // published first): a non-null waiting reason skips with that message,
    // in dry-run and live runs alike.
    const waitingReason = p.waiting?.();
    if (waitingReason) {
      summary.push(`  ${c.yellow('skip')}  ${p.label.padEnd(14)} ${c.dim(`(${waitingReason})`)}`);
      continue;
    }
    try {
      const result = await p.run();
      if (result.id && !['manual', 'draft', 'dry-run', 'unknown'].includes(result.id)) {
        newIds[p.key] = result.id;
      }
      // Draft links go to their own frontmatter block (a draft is NOT
      // "published" — it must not feed idempotency-for-publish or the
      // Also-published-on footer).
      if (result.id === 'draft' && result.url?.startsWith('http')) {
        newDraftLinks[p.key] = result.url;
      }
      // Manual platforms (id === 'manual') build a package artifact rather than posting.
      const verb = result.id === 'manual'
        ? (opts.dryRun ? 'would-package' : 'packaged')
        : result.id === 'draft'
          ? (opts.dryRun ? 'would-draft' : 'drafted')
          : (opts.dryRun ? 'would-post' : 'posted');
      summary.push(`  ${c.green('✓')} ${p.label.padEnd(14)} ${c.dim(verb)} → ${result.url}`);
    } catch (err) {
      summary.push(`  ${c.red('✗')} ${p.label.padEnd(14)} ${c.red(err.message)}`);
    }
  }

  let wroteBack = false;
  if (!opts.dryRun && Object.keys(newIds).length > 0) {
    wroteBack = await writeSyndicationIds(blog.path, newIds);
  }
  if (!opts.dryRun && Object.keys(newDraftLinks).length > 0) {
    wroteBack = (await writeDraftLinks(blog.path, newDraftLinks)) || wroteBack;
  }
  return { summary, newIds, newDraftLinks, wroteBack };
}

// ── main ──────────────────────────────────────────────────────────────────
async function main() {
  const opts = parseArgs(process.argv);

  if (!SITE_URL) {
    console.error(c.red('SITE_URL env var is required (the canonical origin of your site).'));
    process.exit(1);
  }

  log(`\n${opts.dryRun ? c.yellow('DRY RUN') : c.green('LIVE RUN')} — site: ${SITE_URL}`);
  log(opts.blog ? `blog: ${opts.blog}${opts.force ? ' (forced — re-post/re-draft)' : ''}\n` : `blogs: all unpublished\n`);

  const blogs = await selectBlogs(opts);
  let totalPosted = 0;
  let totalDrafted = 0;
  let totalErrors = 0;
  let wroteAny = false;

  for (const blog of blogs) {
    log(`━━━ ${blog.slug} ━━━`);
    log(`    ${blog.data.title}`);
    const { summary, newIds, newDraftLinks, wroteBack } = await syndicateBlog(blog, opts);
    for (const line of summary) log(line);
    totalPosted += Object.keys(newIds).length;
    totalDrafted += Object.keys(newDraftLinks).length;
    totalErrors += summary.filter((s) => s.includes(c.red('✗'))).length;
    if (wroteBack) wroteAny = true;
    log('');
  }

  log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
  log(`posted ${c.green(String(totalPosted))} platform(s)`);
  if (totalDrafted) log(`drafted ${c.green(String(totalDrafted))} platform(s) ${c.dim('(review + Publish, then task posse:confirm)')}`);
  if (totalErrors) log(`${c.red(String(totalErrors))} error(s)`);
  if (wroteAny) log(c.green('wrote syndication IDs back to frontmatter'));
  if (opts.dryRun) log(c.yellow('\nDry run — nothing was posted or committed. Re-run with --dry-run=false to publish.'));
}

main().catch((err) => {
  console.error(c.red(`\nFatal: ${err.message}`));
  process.exit(1);
});
