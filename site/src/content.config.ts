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
  }),
});

export const collections = { essays };
