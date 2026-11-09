#!/bin/sh
# Autoresearch Verify for the labeler-viewer interface loop: run the synthetic-only
# Chromium smoke and print the number of executed interface expectations (higher is better).
set -e
evidence="${1:-/private/tmp/opencode/autoresearch-interface}"
mkdir -p "$evidence"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}" \
  node tests/browser_review_page.mjs "$evidence" >"$evidence/node.out" 2>&1 \
  || { echo "smoke FAILED (see $evidence/node.out):" >&2; tail -30 "$evidence/node.out" >&2; exit 1; }
node -e 'const r=require(process.argv[1]+"/results.json");if(!r.passed)process.exit(1);console.log(r.checks.length)' "$evidence"
