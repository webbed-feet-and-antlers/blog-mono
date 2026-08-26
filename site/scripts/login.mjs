#!/usr/bin/env node
// `task posse:login -- <platform>` — capture a browser session for one of the
// assisted-draft platforms (substack | medium | linkedin | indiehackers).
//
// Two routes:
//
//   1. Browser (default): opens your local Chrome; sign in; press Enter.
//      Works for password/2FA logins. NB: Google sign-in blocks automated
//      browsers ("This browser or app may not be secure") and Medium/Substack
//      magic-link emails are unreliable — for those use route 2.
//
//   2. Cookie paste (reliable): copy the session cookie from your REAL
//      browser, where you're already logged in:
//        - open medium.com / <pub>.substack.com / linkedin.com / indiehackers.com
//        - DevTools (F12) → Application → Cookies → <site>
//        - find the cookie for this platform, copy the VALUE column
//          (medium: sid, substack: substack.sid, linkedin: li_at,
//           indiehackers: __session)
//        - task posse:login -- medium --cookie=<paste>
//
// Sessions land in .syndication-output/sessions/<platform>.json (gitignored).
import { loginInteractive, saveSessionFromCookie } from './lib/assisted-session.mjs';

function arg(name) {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : undefined;
}

const platform = arg('platform');
const cookie = arg('cookie');

if (!platform) {
  console.error('Usage: node scripts/login.mjs --platform=<substack|medium|linkedin|indiehackers> [--cookie=<value>]');
  process.exit(1);
}

const run = cookie ? saveSessionFromCookie(platform, cookie) : loginInteractive(platform);
run.catch((err) => {
  console.error(`\nLogin capture failed: ${err.message}`);
  process.exit(1);
});
