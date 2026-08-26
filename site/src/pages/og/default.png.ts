// Prerendered default OG card for non-blog pages (homepage, about) — same
// satori pipeline as the per-blog /og/<slug>.png cards, so sharing any page
// on social media gets a large-image summary card instead of a bare title.
import type { APIRoute } from 'astro';
import { renderOgImage } from '../../lib/og-image';

export const GET: APIRoute = async () => {
  const png = await renderOgImage({
    title: 'The Inkpens',
    tags: ['data science', 'machine learning'],
    readingMinutes: undefined,
    subtitle: 'Notes from Becky & Nathan',
    brand: 'theinkpens',
    slug: 'default',
  });
  if (!png) {
    return new Response('OG image generation failed (font unavailable).', { status: 500 });
  }
  // Wrap the bytes in a fresh Uint8Array over its own ArrayBuffer so the TS DOM
  // lib's BodyInit accepts it (same as the per-blog route).
  const body = new Uint8Array(png);
  return new Response(body, {
    headers: { 'Content-Type': 'image/png', 'Cache-Control': 'public, max-age=3600' },
  });
};
