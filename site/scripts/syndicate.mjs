#!/usr/bin/env node
// POSSE syndicator: cross-posts each essay to dev.to, Bluesky, Mastodon,
// X (via Buffer), LinkedIn (via Buffer), Medium, and emits Substack + Indie
// Hackers teasers.
//
// Native POSSE: per-platform copy + threads (reply-chained) + native image
// attachment. The canonical URL lives in the last post of a thread (or a
// LinkedIn comment) so posts feel native rather than "blurb + link".
//
// Usage:
//   npm run syndicate -- --dry-run=true                 # preview all unpublished
//   npm run syndicate -- --dry-run=false                # post all unpublished
//   npm run syndicate -- --dry-run=true --essay=embeddings
//
// Credentials are auto-loaded from site/.env via the `--env-file-if-exits=.env`
// flag in the npm script (no `source .env` needed). CI injects secrets directly.
import { loadEssays, loadEssay } from './lib/essays.mjs';
import { writeSyndicationIds } from './lib/frontmatter.mjs';
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
  const args = { dryRun: true, essay: undefined };
  for (const a of argv.slice(2)) {
    const m = a.match(/^--([^=]+)=(.*)$/);
    if (!m) continue;
    const [, k, v] = m;
    if (k === 'dry-run' || k === 'dry_run') args.dryRun = v !== 'false';
    if (k === 'essay') args.essay = v || undefined;
  }
  return args;
}

const SITE_URL = (process.env.SITE_URL || '').replace(/\/$/, '');
const essayUrl = (slug) => `${SITE_URL}/blog/${slug}/`;

const c = {
  dim: (s) => `\x1b[2m${s}\x1b[0m`,
  green: (s) => `\x1b[32m${s}\x1b[0m`,
  yellow: (s) => `\x1b[33m${s}\x1b[0m`,
  red: (s) => `\x1b[31m${s}\x1b[0m`,
};
const log = (...a) => console.log(...a);

// ── which essays to syndicate ─────────────────────────────────────────────
async function selectEssays(opts) {
  if (opts.essay) {
    const e = await loadEssay(opts.essay);
    if (!e) {
      console.error(c.red(`Essay not found: ${opts.essay}`));
      process.exit(1);
    }
    return [e];
  }
  const all = await loadEssays();
  return all
    .filter((e) => e.data.draft !== true)
    .sort((a, b) => new Date(a.data.pubDate) - new Date(b.data.pubDate));
}

// ── screenshot each interactive component in an essay (dark + light pair) ──
// Returns { ComponentName: { dark, light } } (absolute image URLs) for inlining
// into cross-post Markdown as a <picture>. Best-effort: if a variant fails we
// fall back to whichever we have; if both fail the image is skipped entirely
// (the tag is still stripped, the interactive note still appears). Skipped
// silently if Playwright isn't installed or the harness wasn't built.
const SHOT_THEMES = ['dark', 'light'];

async function screenshotComponentsForEssay(essay, siteUrl, summary) {
  const names = componentsInBody(essay.body);
  if (names.length === 0) return {};

  const images = {};
  for (const name of names) {
    const variants = {};
    let failures = 0;
    for (const theme of SHOT_THEMES) {
      try {
        const result = await screenshotComponent({ componentName: name, slug: essay.slug, theme });
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
    // That's expected when an essay's body mentions a tag in prose (e.g. an
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

// ── per-essay platform run ────────────────────────────────────────────────
async function syndicateEssay(essay, opts) {
  const canonicalUrl = essayUrl(essay.slug);
  const { data } = essay;
  const syndication = data.syndication ?? {};
  const newIds = {};
  const summary = [];

  // Normalize per-platform social copy into { posts: string[], image: boolean }.
  const social = normalizeSocial(data, canonicalUrl);

  // Pre-pass: screenshot each interactive component used in the essay so the
  // images can be inlined into the cross-posted Markdown (dev.to/Medium can't
  // run React). Falls back gracefully if Playwright/harness isn't available.
  const componentImages = await screenshotComponentsForEssay(essay, SITE_URL, summary);
  const bodyMarkdown = await mdxToMarkdown(essay.body, canonicalUrl, componentImages);
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
      slug: essay.slug,
    }).catch((err) => {
      summary.push(`  ${c.yellow('warn')} og-image render failed: ${err.message}`);
      return null;
    });
  }

  // Each platform: skip if unavailable; skip if already syndicated (unless forced).
  const platforms = [
    {
      key: 'devto',
      label: devto.name,
      available: () => devto.available(),
      run: () => devto.publish({ title: data.title, bodyMarkdown, canonicalUrl, tags: data.tags ?? [], description: data.description, existingId: syndication.devto, dryRun: opts.dryRun }),
      skipIf: () => opts.essay === undefined && syndication.devto,
    },
    {
      key: 'bluesky',
      label: bluesky.name,
      available: () => bluesky.available(),
      run: () => bluesky.publish({ posts: social.bluesky.posts, imagePath, dryRun: opts.dryRun }),
      skipIf: () => opts.essay === undefined && syndication.bluesky,
    },
    {
      key: 'mastodon',
      label: mastodon.name,
      available: () => mastodon.available(),
      run: () => mastodon.publish({ posts: social.mastodon.posts, imagePath, dryRun: opts.dryRun }),
      skipIf: () => opts.essay === undefined && syndication.mastodon,
    },
    {
      key: 'buffer',
      label: buffer.name,
      available: () => buffer.available(),
      run: () => buffer.publish({ posts: social.twitter.posts, slug: essay.slug, dryRun: opts.dryRun }),
      skipIf: () => opts.essay === undefined && syndication.buffer,
    },
    {
      key: 'linkedin',
      label: linkedin.name,
      available: () => linkedin.available(),
      run: () => linkedin.publish({ posts: social.linkedin.posts, canonicalUrl, slug: essay.slug, dryRun: opts.dryRun }),
      skipIf: () => opts.essay === undefined && syndication.linkedin,
    },
    {
      // The long-form LinkedIn Article (125k-char, UI-only — no API). Distinct
      // key from the short `linkedin` post so the two surfaces don't collide on
      // the syndication-idempotency field. Mirrors Medium/Substack: packages the
      // full body for a manual paste into LinkedIn's "Write an article" UI.
      key: 'linkedinArticle',
      label: linkedinArticle.name,
      available: () => linkedinArticle.available(),
      run: () => linkedinArticle.publish({ title: data.title, bodyMarkdown, bodyHtml, canonicalUrl, tags: data.tags ?? [], slug: essay.slug }),
      skipIf: () => opts.essay === undefined && syndication.linkedinArticle,
    },
    {
      key: 'medium',
      label: medium.name,
      available: () => medium.available(),
      run: () => medium.publish({ title: data.title, bodyMarkdown, bodyHtml, canonicalUrl, tags: data.tags ?? [], slug: essay.slug }),
      skipIf: () => opts.essay === undefined && syndication.medium,
    },
    {
      key: 'substack',
      label: substack.name,
      available: () => substack.available(),
      run: () => substack.publish({ title: data.title, bodyMarkdown, bodyHtml, socialPost: teaserBlurb(data), canonicalUrl, slug: essay.slug }),
      skipIf: () => opts.essay === undefined && syndication.substack,
    },
    {
      key: 'indiehackers',
      label: indiehackers.name,
      available: () => indiehackers.available(),
      run: () => indiehackers.publish({ title: data.title, bodyMarkdown, bodyHtml, socialPost: teaserBlurb(data), canonicalUrl, tags: data.tags ?? [], slug: essay.slug }),
      skipIf: () => opts.essay === undefined && syndication.indiehackers,
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
      summary.push(`  ${c.yellow('skip')}  ${p.label.padEnd(14)} ${c.dim('(already posted)')}`);
      continue;
    }
    try {
      const result = await p.run();
      if (result.id && !['manual', 'dry-run', 'unknown'].includes(result.id)) {
        newIds[p.key] = result.id;
      }
      // Manual platforms (id === 'manual') build a package artifact rather than posting.
      const verb = result.id === 'manual'
        ? (opts.dryRun ? 'would-package' : 'packaged')
        : (opts.dryRun ? 'would-post' : 'posted');
      summary.push(`  ${c.green('✓')} ${p.label.padEnd(14)} ${c.dim(verb)} → ${result.url}`);
    } catch (err) {
      summary.push(`  ${c.red('✗')} ${p.label.padEnd(14)} ${c.red(err.message)}`);
    }
  }

  let wroteBack = false;
  if (!opts.dryRun && Object.keys(newIds).length > 0) {
    wroteBack = await writeSyndicationIds(essay.path, newIds);
  }
  return { summary, newIds, wroteBack };
}

// ── main ──────────────────────────────────────────────────────────────────
async function main() {
  const opts = parseArgs(process.argv);

  if (!SITE_URL) {
    console.error(c.red('SITE_URL env var is required (the canonical origin of your site).'));
    process.exit(1);
  }

  log(`\n${opts.dryRun ? c.yellow('DRY RUN') : c.green('LIVE RUN')} — site: ${SITE_URL}`);
  log(opts.essay ? `essay: ${opts.essay} (forced)\n` : `essays: all unpublished\n`);

  const essays = await selectEssays(opts);
  let totalPosted = 0;
  let totalErrors = 0;
  let wroteAny = false;

  for (const essay of essays) {
    log(`━━━ ${essay.slug} ━━━`);
    log(`    ${essay.data.title}`);
    const { summary, newIds, wroteBack } = await syndicateEssay(essay, opts);
    for (const line of summary) log(line);
    totalPosted += Object.keys(newIds).length;
    totalErrors += summary.filter((s) => s.includes(c.red('✗'))).length;
    if (wroteBack) wroteAny = true;
    log('');
  }

  log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
  log(`posted ${c.green(String(totalPosted))} platform(s)`);
  if (totalErrors) log(`${c.red(String(totalErrors))} error(s)`);
  if (wroteAny) log(c.green('wrote syndication IDs back to frontmatter'));
  if (opts.dryRun) log(c.yellow('\nDry run — nothing was posted or committed. Re-run with --dry-run=false to publish.'));
}

main().catch((err) => {
  console.error(c.red(`\nFatal: ${err.message}`));
  process.exit(1);
});
