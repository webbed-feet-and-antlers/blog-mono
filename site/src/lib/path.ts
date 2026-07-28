/**
 * Join the configured base path with a route segment, producing a correct
 * root-relative URL regardless of whether BASE_URL ends in a slash.
 *
 *   path('about')        -> '/blog-mono/about'
 *   path('/about/')      -> '/blog-mono/about/'
 *   path('')             -> '/blog-mono/'
 *   path()               -> '/blog-mono/'
 *
 * Astro's import.meta.env.BASE_URL reflects the `base` config option.
 */
export function path(route = ''): string {
  const base = import.meta.env.BASE_URL; // e.g. '/blog-mono' or '/'
  const cleanBase = base.endsWith('/') ? base : base + '/';
  const cleanRoute = route.replace(/^\/+/, ''); // strip leading slashes
  return cleanBase + cleanRoute;
}
