import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';

// Essays live at the repo root in /essays (outside site/), pulled in via the glob loader.
const essays = defineCollection({
  loader: glob({
    // Resolved relative to the Astro project root (site/), so one level up = repo root.
    base: '../essays',
    pattern: '**/*.{md,mdx}',
  }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    pubDate: z.coerce.date(),
    updatedDate: z.coerce.date().optional(),
    tags: z.array(z.string()).default([]),
    draft: z.boolean().default(false),
    heroImage: z.string().optional(),
    /**
     * Short blurb used for social cross-posts (X, Bluesky, Mastodon, Substack teaser).
     * Keep under ~280 chars so it fits a single X post. POSSE: post own site, syndicate everywhere.
     */
    socialPost: z.string().optional(),
    /**
     * Machine-managed: per-platform post IDs written back by scripts/syndicate.mjs.
     * Presence of an ID means "already syndicated to this platform" — used for idempotency
     * and to render an "Also published on…" footer on the essay page.
     */
    syndication: z
      .object({
        devto: z.union([z.string(), z.number()]).optional(),
        bluesky: z.string().optional(), // at:// record uri
        mastodon: z.string().optional(), // status id
        buffer: z.string().optional(), // Buffer update id (the X post)
        medium: z.string().optional(), // Medium post id
        substack: z.string().optional(), // left null; manual platform (no API)
      })
      .optional(),
  }),
});

export const collections = { essays };
