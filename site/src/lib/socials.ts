/**
 * The Inkpens' social profiles. Single source of truth — used by the Footer
 * (icons row) and the About page (full list with handles).
 *
 * `handle` is the platform-native handle (shown on the About page);
 * `href` is the canonical profile URL.
 */
export interface SocialLink {
  label: string;
  handle: string;
  href: string;
  /** Inline SVG path data for the brand glyph (24x24 viewBox, currentColor stroke/fill). */
  icon: 'devto' | 'medium' | 'substack' | 'bluesky' | 'mastodon' | 'x' | 'rss';
}

export const SOCIALS: SocialLink[] = [
  {
    label: 'X',
    handle: '@theinkpens',
    href: 'https://x.com/theinkpens',
    icon: 'x',
  },
  {
    label: 'Bluesky',
    handle: '@theinkpens.bsky.social',
    href: 'https://bsky.app/profile/theinkpens.bsky.social',
    icon: 'bluesky',
  },
  {
    label: 'Mastodon',
    handle: '@theinkpens@mastodon.social',
    href: 'https://mastodon.social/@theinkpens',
    icon: 'mastodon',
  },
  {
    label: 'DEV',
    handle: '@theinkpens',
    href: 'https://dev.to/theinkpens',
    icon: 'devto',
  },
  {
    label: 'Medium',
    handle: '@thetwoinkpens',
    href: 'https://medium.com/@thetwoinkpens',
    icon: 'medium',
  },
  {
    label: 'Substack',
    handle: 'theinkpens.substack.com',
    href: 'https://theinkpens.substack.com/',
    icon: 'substack',
  },
];
