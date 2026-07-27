#!/usr/bin/env node
// POSSE syndicator: posts each essay to dev.to, Bluesky, Mastodon, X (Buffer),
// Medium, and emits a Substack teaser. Run with --dry-run=true to preview.
//
// Usage:
//   node scripts/syndicate.mjs --dry-run=true           # preview all unpublished
//   node scripts/syndicate.mjs --dry-run=false          # post all unpublished
//   node scripts/syndicate.mjs --dry-run=true --essay=embeddings
//
// Idempotency: a platform is skipped if its ID is already in frontmatter,
// unless --essay=<slug> forces a (re)syndicate of that essay.
import { loadEssays, loadEssay } from './lib/essays.mjs';
import { writeSyndicationIds } from './lib/frontmatter.mjs';
import { mdxToMarkdown } from './lib/markdown.mjs';
import * as devto from './lib/platforms/devto.mjs';
import * as bluesky from './lib/platforms/bluesky.mjs';
import * as mastodon from './lib/platforms/mastodon.mjs';
import * as buffer from './lib/platforms/buffer.mjs';
import * as medium from './lib/platforms/medium.mjs';
import * as substack from './lib/platforms/substack.mjs';

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

function essayUrl(slug) {
  return `${SITE_URL}/blog/${slug}/`;
}

function log(...a) {
  console.log(...a);
}
function dim(s) {
  return `\x1b[2m${s}\x1b[0m`;
}
function green(s) {
  return `\x1b[32m${s}\x1b[0m`;
}
function yellow(s) {
  return `\x1b[33m${s}\x1b[0m`;
}
function red(s) {
  return `\x1b[31m${s}\x1b[0m`;
}

// ── which essays to syndicate ─────────────────────────────────────────────
async function selectEssays(opts) {
  if (opts.essay) {
    const e = await loadEssay(opts.essay);
    if (!e) {
      console.error(red(`Essay not found: ${opts.essay}`));
      process.exit(1);
    }
    return [e];
  }
  const all = await loadEssays();
  return all
    .filter((e) => e.data.draft !== true)
    .sort((a, b) => new Date(a.data.pubDate) - new Date(b.data.pubDate));
}

// ── per-essay platform run ────────────────────────────────────────────────
async function syndicateEssay(essay, opts) {
  const canonicalUrl = essayUrl(essay.slug);
  const { title, description, socialPost, tags = [], syndication = {} } = essay.data;
  const tagsArr = tags;
  const newIds = {};
  const summary = [];

  const postText = `${socialPost ?? description}\n\n${canonicalUrl}`;

  // Long-form Markdown body for dev.to / Medium.
  const bodyMarkdown = await mdxToMarkdown(essay.body, canonicalUrl);

  // Each platform: skip if unavailable; skip if already syndicated (unless forced).
  const platforms = [
    {
      key: 'devto',
      label: devto.name,
      available: () => devto.available(),
      run: () => devto.publish({ title, bodyMarkdown, canonicalUrl, tags: tagsArr, description, existingId: syndication.devto, dryRun: opts.dryRun }),
      skipIf: () => opts.essay === undefined && syndication.devto,
    },
    {
      key: 'bluesky',
      label: bluesky.name,
      available: () => bluesky.available(),
      run: () => bluesky.publish({ text: postText, canonicalUrl, title, description, dryRun: opts.dryRun }),
      skipIf: () => opts.essay === undefined && syndication.bluesky,
    },
    {
      key: 'mastodon',
      label: mastodon.name,
      available: () => mastodon.available(),
      run: () => mastodon.publish({ text: postText, existingId: syndication.mastodon, dryRun: opts.dryRun }),
      skipIf: () => opts.essay === undefined && syndication.mastodon,
    },
    {
      key: 'buffer',
      label: buffer.name,
      available: () => buffer.available(),
      run: () => buffer.publish({ text: postText, dryRun: opts.dryRun }),
      skipIf: () => opts.essay === undefined && syndication.buffer,
    },
    {
      key: 'medium',
      label: medium.name,
      available: () => medium.available(),
      run: () => medium.publish({ title, bodyMarkdown, canonicalUrl, tags: tagsArr, dryRun: opts.dryRun }),
      skipIf: () => opts.essay === undefined && syndication.medium,
    },
    {
      key: 'substack',
      label: substack.name,
      available: () => substack.available(),
      run: () => substack.publish({ title, socialPost: socialPost ?? '', canonicalUrl, slug: essay.slug }),
      skipIf: () => opts.essay === undefined && syndication.substack,
    },
  ];

  for (const p of platforms) {
    if (!p.available()) {
      summary.push(`  ${yellow('skip')}  ${p.label.padEnd(14)} ${dim('(no credentials)')}`);
      continue;
    }
    if (p.skipIf()) {
      summary.push(`  ${yellow('skip')}  ${p.label.padEnd(14)} ${dim('(already posted)')}`);
      continue;
    }
    try {
      const result = await p.run();
      if (result.id && result.id !== 'manual' && result.id !== 'dry-run') {
        newIds[p.key] = result.id;
      }
      const verb = opts.dryRun ? 'would-post' : 'posted';
      summary.push(`  ${green('✓')} ${p.label.padEnd(14)} ${dim(verb)} → ${result.url}`);
    } catch (err) {
      summary.push(`  ${red('✗')} ${p.label.padEnd(14)} ${red(err.message)}`);
    }
  }

  // Write IDs back to frontmatter (never in dry-run).
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
    console.error(red('SITE_URL env var is required (the canonical origin of your site).'));
    process.exit(1);
  }

  log(`\n${opts.dryRun ? yellow('DRY RUN') : green('LIVE RUN')} — site: ${SITE_URL}`);
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
    totalErrors += summary.filter((s) => s.includes(red('✗'))).length;
    if (wroteBack) wroteAny = true;
    log('');
  }

  log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
  log(`posted ${green(totalPosted)} platform(s)`);
  if (totalErrors) log(`${red(String(totalErrors))} error(s)`);
  if (wroteAny) log(green('wrote syndication IDs back to frontmatter'));
  if (opts.dryRun) log(yellow('\nDry run — nothing was posted or committed. Re-run with --dry-run=false to publish.'));
}

main().catch((err) => {
  console.error(red(`\nFatal: ${err.message}`));
  process.exit(1);
});
