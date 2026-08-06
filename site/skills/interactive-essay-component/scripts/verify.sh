#!/usr/bin/env bash
# verify.sh <ComponentName>
#
# Audits a React island component against the interactive-essay-component rules.
# Run from the site/ directory. Exits non-zero if any HARD rule fails; prints
# actionable WARNs for things to check manually.
#
# Usage:  scripts/verify.sh BinPacker        # from site/
#         bash skills/.../scripts/verify.sh BinPacker
#
# What it checks:
#   HARD  - component file exists, default export present
#   HARD  - no charting-library imports (recharts/d3/chart.js/victory)
#   HARD  - root div has `not-prose`
#   WARN  - every bare zinc/brand color class has a matching `dark:` partner
#   WARN  - registered in the screenshot harness (import + both KNOWN sets + render branch)
# NOTE: deliberately NOT using `set -e` — this is a lint script that runs many
# grep checks returning non-zero on no-match, and we track pass/fail explicitly
# via HARD_FAIL / WARN_COUNT. `set -o pipefail` + subshells also caused SIGPIPE
# exits; explicit tracking is more robust here.
set -uo pipefail

COMPONENT="${1:-}"
SITE_DIR="$(git rev-parse --show-toplevel 2>/dev/null)/site"
if [ ! -d "$SITE_DIR" ]; then
  # Fall back: assume script lives under site/skills/.../scripts
  SITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
fi

if [ -z "$COMPONENT" ]; then
  echo "Usage: verify.sh <ComponentName>   (e.g. verify.sh BinPacker)"
  echo "Run from the site/ directory (or anywhere in the repo)."
  exit 2
fi

FILE="$SITE_DIR/src/components/react/${COMPONENT}.tsx"
HARNESS="$SITE_DIR/src/pages/sshot/[component]/[theme].astro"
HARD_FAIL=0
WARN_COUNT=0

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
dim()    { printf '\033[2m%s\033[0m\n' "$*"; }

echo "Auditing: $COMPONENT"
dim "  file: $FILE"
echo ""

# ─── HARD: file exists ───────────────────────────────────────────────────────
if [ ! -f "$FILE" ]; then
  red "FAIL  component file not found: $FILE"
  exit 1
fi

# ─── HARD: default export ────────────────────────────────────────────────────
if ! grep -qE 'export[[:space:]]+default' "$FILE"; then
  red "FAIL  no default export found (components must default-export)"
  HARD_FAIL=1
else
  green "ok    default export present"
fi

# ─── HARD: no charting libraries ─────────────────────────────────────────────
if grep -qiE "from[[:space:]]+['\"](recharts|d3|d3-|chart\.js|victory|visx|nivo)['\"]" "$FILE"; then
  red "FAIL  charting-library import detected (recharts/d3/etc). Hand-roll SVG/flexbox instead — see spec §4."
  grep -nE "from[[:space:]]+['\"](recharts|d3|d3-|chart\.js|victory|visx|nivo)" "$FILE" | sed 's/^/      /'
  HARD_FAIL=1
else
  green "ok    no charting-library imports"
fi

# ─── HARD: not-prose on root ─────────────────────────────────────────────────
if ! grep -q 'not-prose' "$FILE"; then
  red "FAIL  'not-prose' missing — the root div MUST start with not-prose (spec §3). Without it the prose container rewrites your styles."
  HARD_FAIL=1
else
  green "ok    not-prose wrapper present"
fi

# ─── WARN: dark/light class pairing ──────────────────────────────────────────
# The #1 bug: a bare zinc/brand color class with no `dark:` partner on the same
# className string. For every bare (text|bg|border|fill|stroke|ring)-<color>-<shade>
# we check the same line also has a dark:<same-property> reference.
#
# Skip variant-prefixed utilities (hover:/focus:/active:/disabled:/group-hover:)
# — those are interaction-state colors that are intentionally the same on both themes.
# `text-white` and primary `bg-brand-600` are also intentionally single-mode.
PAIRING_ISSUES=0
PAIRING_OUTPUT=""
COLOR_RE='(text|bg|border|fill|stroke|ring|ring-offset)-(zinc|brand|emerald|sky|fuchsia|amber|rose|green|indigo|cyan|violet|teal|lime|orange|pink|red|blue|yellow|purple)-[0-9]{2,4}'

while IFS= read -r line; do
  # Skip comment lines.
  case "$line" in *//*) continue ;; esac
  # Pull candidate bare color tokens on this line.
  toks=$(echo "$line" | grep -oE "$COLOR_RE" || true)
  [ -z "$toks" ] && continue
  while IFS= read -r tok; do
    [ -z "$tok" ] && continue
    prop="${tok%%-*}"
    # Skip state-prefixed variants — only audit BARE utilities.
    # If the token is immediately preceded by 'hover:'/'focus:'/'disabled:'/'group-hover:'/'active:', skip.
    if echo "$line" | grep -qE "(hover|focus|focus-visible|active|disabled|group-hover|group-focus):${tok}"; then
      continue
    fi
    # text-white is intentionally single-mode.
    [ "$tok" = "text-white" ] && continue
    # If this same line has NO dark:<prop> at all, it's unpaired.
    if ! echo "$line" | grep -qE "dark:${prop}-"; then
      PAIRING_OUTPUT+="$(yellow "WARN  bare '${tok}' with no dark:${prop}-* partner on the same line:")"$'\n'
      PAIRING_OUTPUT+="$(echo "$line" | sed 's/^[[:space:]]*/      /' | dim)"$'\n'
      PAIRING_ISSUES=$((PAIRING_ISSUES + 1))
    fi
  done <<< "$toks"
done < "$FILE"

if [ -n "$PAIRING_OUTPUT" ]; then
  printf '%s' "$PAIRING_OUTPUT"
fi
if [ "$PAIRING_ISSUES" -gt 0 ]; then
  WARN_COUNT=$((WARN_COUNT + 1))
  echo ""
  yellow "      ${PAIRING_ISSUES} unpaired bare color(s) above. Each bare color class needs a"
  yellow "      dark: partner (bare = dark default, dark: = light). Confirm any intentional single-mode use."
else
  green "ok    all bare color classes have dark: partners"
fi

# ─── WARN: harness registration ──────────────────────────────────────────────
echo ""
if [ -f "$HARNESS" ]; then
  MISSING_HARNESS=0
  if ! grep -q "import ${COMPONENT} from" "$HARNESS"; then
    yellow "WARN  not imported in the screenshot harness (spec §7a)."
    MISSING_HARNESS=1
  fi
  # KNOWN appears twice (getStaticPaths scope + page scope). Count occurrences.
  KNOWN_COUNT=$(grep -c "'${COMPONENT}'" "$HARNESS" || true)
  if [ "$KNOWN_COUNT" -lt 2 ]; then
    yellow "WARN  '${COMPONENT}' found only ${KNOWN_COUNT}/2 times in the harness KNOWN sets (spec §7b,c — needs both the getStaticPaths-scoped and page-scoped sets)."
    MISSING_HARNESS=1
  fi
  if ! grep -qE "component === '${COMPONENT}'" "$HARNESS"; then
    yellow "WARN  no render branch '{component === '${COMPONENT}' && ...}' in the harness (spec §7d)."
    MISSING_HARNESS=1
  fi
  if [ "$MISSING_HARNESS" -eq 0 ]; then
    green "ok    registered in screenshot harness (import + KNOWN ×2 + render branch)"
  else
    WARN_COUNT=$((WARN_COUNT + 1))
    yellow "      Without full registration the component won't be screenshotted for POSSE."
    yellow "      (If this component is intentionally not for cross-posting, ignore.)"
  fi
else
  yellow "WARN  screenshot harness not found at $HARNESS — skipping registration check."
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "──────────────────────────────────────────"
if [ "$HARD_FAIL" -ne 0 ]; then
  red "RESULT: HARD FAILURES — fix the items above before building."
  exit 1
fi
if [ "$WARN_COUNT" -gt 0 ]; then
  yellow "RESULT: PASSED hard checks, but ${WARN_COUNT} warning group(s) to review."
  exit 0
fi
green "RESULT: ALL CHECKS PASSED"
exit 0
