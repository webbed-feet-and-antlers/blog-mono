#!/usr/bin/env node
// `task posse:login -- <platform>` — capture a browser session for one of the
// assisted-draft platforms (substack | medium | linkedin | indiehackers).
// Opens a headed Chromium at the platform, you sign in (2FA included), press
// Enter here, and the session is saved to .syndication-output/sessions/.
//
// Usage:
//   npm run posse:login -- medium          # (see Taskfile.posse.yml)
//   node --env-file-if-exists=.env scripts/login.mjs --platform=medium
import { loginInteractive } from './lib/assisted-session.mjs';

const arg = process.argv.find((a) => a.startsWith('--platform='));
const platform = arg ? arg.slice('--platform='.length) : process.argv[2];

if (!platform) {
  console.error('Usage: node scripts/login.mjs --platform=<substack|medium|linkedin|indiehackers>');
  process.exit(1);
}

loginInteractive(platform).catch((err) => {
  console.error(`\nLogin capture failed: ${err.message}`);
  process.exit(1);
});
