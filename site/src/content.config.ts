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
     * Short blurb used for the Substack teaser and as a fallback for any platform
     * that has no entry in `social` below. Keep under ~280 chars.
     * @deprecated prefer the structured `social` object for per-platform copy + threads.
     */
    socialPost: z.string().optional(),
    /**
     * Per-platform social copy. Each value may be a string (single post) or an
     * array of strings (a thread — reply-chained on X/Bluesky/Mastodon). The
     * canonical URL is appended to the last post automatically. Set `image: false`
     * to skip the auto-generated OG image attachment for this essay.
     *
     * POSSE: post on your own site (canonical), syndicate everywhere with
     * platform-native formatting rather than a generic "link + blurb".
     */
    social: z
      .object({
        twitter: z.union([z.string(), z.array(z.string())]).optional(),
        linkedin: z.union([z.string(), z.array(z.string())]).optional(),
        bluesky: z.union([z.string(), z.array(z.string())]).optional(),
        mastodon: z.union([z.string(), z.array(z.string())]).optional(),
        image: z.boolean().optional(),
      })
      .optional(),
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
        linkedin: z.string().optional(), // share URN (urn:li:share:...)
        medium: z.string().optional(), // Medium post id
        substack: z.string().optional(), // left null; manual platform (no API)
      })
      .optional(),
  }),
});

export const collections = { essays };
