/**
 * Clerk session-token plumbing for API calls.
 *
 * Clerk signs requests via a short-lived session JWT (~60s). React-context
 * hooks can't be used inside the plain fetch/XHR/beacon helpers, so
 * <ClerkTokenBridge /> (rendered once inside <ClerkProvider>) keeps the
 * latest token in this module-level store, refreshing every 50s and on
 * window focus. Headers-capable calls send it as a Bearer token; URLs
 * that cannot carry headers (<img src>, downloads, sendBeacon) append it
 * as a ?token= query parameter (the backend accepts either).
 */

let latestToken: string | null = null;

export function setAuthToken(token: string | null): void {
  latestToken = token;
}

/** The freshest session token (null before first load / when signed out). */
export function authToken(): string | null {
  return latestToken;
}

/** Bearer header value for fetch/XHR, or null when signed out. */
export function authHeaders(): Record<string, string> {
  return latestToken ? { Authorization: `Bearer ${latestToken}` } : {};
}

/** Append ?token= to a URL for consumers that can't send headers. */
export function withToken(url: string): string {
  if (!latestToken) return url;
  return `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(latestToken)}`;
}
