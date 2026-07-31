/**
 * Join the configured base path with a route segment, producing a correct
 * root-relative URL regardless of whether BASE_URL ends in a slash.
 *
 *   path('about')        -> '/about'
 *   path('/about/')      -> '/about/'
 *   path('')             -> '/'
 *   path()               -> '/'
 *
 * Astro's import.meta.env.BASE_URL reflects the `base` config option.
 */
export function path(route = ''): string {
  const base = import.meta.env.BASE_URL; // '/' (root) by current config
  const cleanBase = base.endsWith('/') ? base : base + '/';
  const cleanRoute = route.replace(/^\/+/, ''); // strip leading slashes
  return cleanBase + cleanRoute;
}
