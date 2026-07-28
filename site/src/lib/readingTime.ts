/**
 * Rough reading-time estimate from raw markdown body.
 * ~200 wpm is the common blog default; strips code fences and markup.
 */
export function readingTime(body: string): number {
  const stripped = body
    .replace(/```[\s\S]*?```/g, ' ') // code blocks
    .replace(/`[^`]*`/g, ' ') // inline code
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ') // images
    .replace(/\[[^\]]*\]\([^)]*\)/g, ' ') // links
    .replace(/[#>*_~-]/g, ' '); // markdown symbols
  const words = stripped.trim().split(/\s+/).filter(Boolean).length;
  return Math.max(1, Math.round(words / 200));
}
