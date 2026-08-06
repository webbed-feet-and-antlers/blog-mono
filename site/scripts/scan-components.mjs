// Statically scans every React island component (src/components/react/*.tsx) for
// risky patterns an AI-generated component might accidentally introduce. This is
// the build-time half of the site's defense-in-depth (the runtime half is the
// Content-Security-Policy in Base.astro).
//
// The CSP blocks EXFILTRATION at runtime (connect-src 'self'); this scanner
// catches the SOURCES of risky behavior before they ship — dynamic code loading,
// DOM injection, storage access, prototype pollution — and fails the PR/CI run.
//
// Exit codes: 0 = clean (only LOW/info findings allowed); 1 = HIGH/MED findings.
//
// Usage:  node scripts/scan-components.mjs          # from site/
//         npm run scan
import { readdir, readFile } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const COMPONENTS_DIR = join(__dirname, '..', 'src', 'components', 'react');

// ANSI colors (degrade gracefully if piped / not a TTY).
const C = process.stdout.isTTY
  ? { red: '\x1b[31m', yellow: '\x1b[33m', green: '\x1b[32m', dim: '\x1b[2m', reset: '\x1b[0m' }
  : { red: '', yellow: '', green: '', dim: '', reset: '' };

// ─── Pattern catalog ──────────────────────────────────────────────────────────
// Each: { id, severity, regex, msg }. `regex` matches the risky token; the
// scanner reports file:line. Severity gates the exit code.
//   HIGH = code execution / injection / dynamic loading — must not ship
//   MED  = exfiltration-adjacent / pollution — review required
//   LOW  = informational (mutation/messaging) — flagged but non-blocking
const PATTERNS = [
  // ── HIGH: code execution & dynamic code loading ────────────────────────────
  { id: 'eval', severity: 'HIGH', regex: /\beval\s*\(/, msg: 'eval() — arbitrary code execution. Never needed in a presentational island.' },
  { id: 'new-function', severity: 'HIGH', regex: /\bnew\s+Function\s*\(/, msg: 'new Function() — compiles a string into a function (eval-equivalent).' },
  { id: 'document-write', severity: 'HIGH', regex: /\bdocument\.write\s*\(/, msg: 'document.write() — clobbers the document / DOM injection vector.' },
  { id: 'settimeout-string', severity: 'HIGH', regex: /\b(setTimeout|setInterval)\s*\(\s*['"`]/, msg: 'setTimeout/setInterval with a STRING arg — implicit eval.' },
  { id: 'dynamic-import', severity: 'HIGH', regex: /\bimport\s*\(/, msg: 'Dynamic import() — could load remote/dynamic code. Use static ESM imports only.' },

  // ── HIGH: DOM injection (assigning to HTML-accepting sinks) ─────────────────
  { id: 'innerhtml', severity: 'HIGH', regex: /\.(innerHTML|outerHTML)\s*=/, msg: 'Assignment to innerHTML/outerHTML — HTML injection if the value contains user/AI data.' },
  { id: 'insertadjacenthtml', severity: 'HIGH', regex: /\.insertAdjacentHTML\s*\(/, msg: 'insertAdjacentHTML — HTML injection sink.' },

  // ── HIGH: storage / credential access ───────────────────────────────────────
  // Low value on a session-less static site, but a presentational island has no
  // reason to touch these — their presence signals something to review.
  { id: 'document-cookie', severity: 'HIGH', regex: /\bdocument\.cookie\b/, msg: 'document.cookie access — a presentational island should never read/write cookies.' },
  { id: 'storage', severity: 'HIGH', regex: /\b(localStorage|sessionStorage|indexedDB)\b/, msg: 'Browser storage access — islands should be stateless across reloads (use React state only).' },

  // ── MED: external network calls (the exfiltration vector) ──────────────────
  // Components shouldn't fetch external URLs at all — all data is local/static.
  // CSP connect-src 'self' would block the actual request, but this catches the
  // intent at build time with a clearer message.
  { id: 'fetch-external', severity: 'MED', regex: /\bfetch\s*\(\s*['"`]https?:\/\//, msg: 'fetch() to an external URL — islands must not make network requests. Keep data local.' },
  { id: 'xhr-external', severity: 'MED', regex: /new\s+XMLHttpRequest\s*\(/, msg: 'XMLHttpRequest — islands must not make network requests.' },
  { id: 'websocket', severity: 'MED', regex: /new\s+WebSocket\s*\(/, msg: 'WebSocket — islands must not open network connections.' },
  { id: 'external-url-literal', severity: 'MED', regex: /['"`]https?:\/\/(?!fonts\.googleapis\.com|fonts\.gstatic\.com|cdn\.jsdelivr\.net)/, msg: 'External URL literal — verify it is not a telemetry/exfil endpoint. Islands should not contact external hosts.' },

  // ── MED: prototype pollution ─────────────────────────────────────────────────
  { id: 'proto-access', severity: 'MED', regex: /(__proto__|prototype\[)/, msg: 'Direct prototype manipulation — prototype-pollution risk.' },
  { id: 'object-assign-window', severity: 'MED', regex: /Object\.assign\s*\(\s*(window|globalThis)/, msg: 'Object.assign onto window/globalThis — global namespace pollution.' },

  // ── LOW: navigation / messaging (informational) ─────────────────────────────
  { id: 'location-mutation', severity: 'LOW', regex: /(window\.location|location\.href)\s*=/, msg: 'Navigation via location assignment — unusual for an island; confirm intended.' },
  { id: 'postmessage', severity: 'LOW', regex: /\.postMessage\s*\(/, msg: 'postMessage usage — ensure the receiver validates event.origin.' },
];

const SEVERITY_RANK = { HIGH: 3, MED: 2, LOW: 1 };

async function scanFile(filePath) {
  const src = await readFile(filePath, 'utf8');
  const lines = src.split('\n');
  const findings = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    // Skip comment-only lines (// or * or /*) — but still scan inline code.
    const trimmed = line.trim();
    if (/^\s*(\/\/|\*|\/\*)/.test(trimmed)) continue;
    for (const pat of PATTERNS) {
      if (pat.regex.test(line)) {
        findings.push({
          file: filePath,
          line: i + 1,
          severity: pat.severity,
          id: pat.id,
          msg: pat.msg,
          snippet: trimmed.length > 90 ? trimmed.slice(0, 87) + '...' : trimmed,
        });
      }
    }
  }
  return findings;
}

async function main() {
  let files;
  try {
    files = (await readdir(COMPONENTS_DIR)).filter((f) => f.endsWith('.tsx'));
  } catch {
    console.error(`${C.red}error${C.reset}  components dir not found: ${COMPONENTS_DIR}`);
    process.exit(2);
  }
  if (files.length === 0) {
    console.error(`${C.red}error${C.reset}  no .tsx components found in ${COMPONENTS_DIR}`);
    process.exit(2);
  }

  console.log(`Scanning ${files.length} component(s) in src/components/react/\n`);

  let allFindings = [];
  for (const f of files) {
    const findings = await scanFile(join(COMPONENTS_DIR, f));
    allFindings = allFindings.concat(findings);
  }

  // Sort: highest severity first, then by file/line.
  allFindings.sort(
    (a, b) =>
      (SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity]) ||
      a.file.localeCompare(b.file) ||
      a.line - b.line,
  );

  // Group by severity for output.
  const counts = { HIGH: 0, MED: 0, LOW: 0 };
  for (const f of allFindings) counts[f.severity]++;

  const colorFor = (sev) => (sev === 'HIGH' ? C.red : sev === 'MED' ? C.yellow : C.dim);

  for (const f of allFindings) {
    const rel = f.file.replace(join(__dirname, '..') + '/', '');
    const sev = `${colorFor(f.severity)}${f.severity.padEnd(4)}${C.reset}`;
    console.log(`${sev} ${rel}:${f.line}  ${C.dim}[${f.id}]${C.reset}  ${f.msg}`);
    console.log(`        ${C.dim}${f.snippet}${C.reset}`);
  }

  console.log('\n──────────────────────────────────────────');
  const blocking = counts.HIGH + counts.MED;
  if (blocking > 0) {
    console.log(
      `${C.red}RESULT: ${blocking} blocking finding(s) — ${counts.HIGH} HIGH, ${counts.MED} MED.${C.reset}`,
    );
    console.log(`${C.dim}        HIGH/MED must be resolved before merge. LOWs are informational.${C.reset}`);
    process.exit(1);
  }
  if (counts.LOW > 0) {
    console.log(`${C.yellow}RESULT: clean of HIGH/MED; ${counts.LOW} LOW (informational) finding(s).${C.reset}`);
    process.exit(0);
  }
  console.log(`${C.green}RESULT: ALL CLEAN — no risky patterns found.${C.reset}`);
  process.exit(0);
}

main();
