/**
 * Umami analytics — same setup as the blog (cookieless, env-gated).
 *
 * Set VITE_UMAMI_WEBSITE_ID to enable; leave unset to ship zero analytics.
 * VITE_UMAMI_HOST defaults to the EU cloud ingest (data-host-url below is
 * load-bearing: without it the tracker sends to the global gateway, which
 * does not route EU-region websites). data-domains confines tracking to
 * the production domain so localhost and the staging fly.dev URL stay
 * clean even in builds that carry the ID.
 *
 * Page views for this SPA (including route changes) are automatic: the
 * script hooks the History API. Product events are the app's own
 * telemetry (api/track.ts) — forwarding selected ones to Umami is a
 * natural follow-up.
 */

export function initAnalytics(): void {
  const websiteId = import.meta.env.VITE_UMAMI_WEBSITE_ID as string | undefined;
  if (!websiteId) return;

  const host = (
    (import.meta.env.VITE_UMAMI_HOST as string | undefined) ||
    "https://eu.umami.is"
  ).replace(/\/$/, "");

  const script = document.createElement("script");
  script.defer = true;
  script.src = `${host}/script.js`;
  script.dataset.websiteId = websiteId;
  script.dataset.hostUrl = host;
  script.dataset.domains = "study.inkpens.tech";
  document.head.appendChild(script);
}
