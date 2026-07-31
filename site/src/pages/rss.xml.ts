import rss from '@astrojs/rss';
import { getCollection } from 'astro:content';
import type { APIContext } from 'astro';

export async function GET(context: APIContext) {
  const all = await getCollection('essays', ({ data }) => !data.draft);
  const essays = all.sort(
    (a, b) => b.data.pubDate.valueOf() - a.data.pubDate.valueOf(),
  );

  // @astrojs/rss does NOT prepend the configured `base`, so we do it manually.
  const base = import.meta.env.BASE_URL;
  const baseWithTrailingSlash = base.endsWith('/') ? base : base + '/';

  return rss({
    title: 'The Inkpens',
    description:
      'Data science and machine learning notes from Becky & Nathan Inkpen.',
    // site must be the origin WITHOUT base; we prefix each item link instead.
    site: context.site ?? 'https://inkpens.tech',
    items: essays.map((entry) => ({
      title: entry.data.title,
      description: entry.data.description,
      pubDate: entry.data.pubDate,
      link: `${baseWithTrailingSlash}blog/${entry.id.replace(/\.(md|mdx)$/, '')}/`,
      categories: entry.data.tags,
    })),
    customData: '<language>en-us</language>',
  });
}
