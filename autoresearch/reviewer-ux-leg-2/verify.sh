#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
evidence="${1:-/private/tmp/opencode/reviewer-ux-leg-2/current}"
mkdir -p "$evidence"
node tests/browser_review_page.mjs "$evidence" > "$evidence/browser.log" 2>&1
.venv/bin/python -m pytest tests/test_labeling_review_page.py tests/test_labeling_review_portal.py tests/test_labeling_local_backup.py -ra > "$evidence/pytest.log" 2>&1
.venv/bin/python - "$evidence" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
result = json.loads((root / 'ux-tasks.json').read_text())
print(result['score'])
PY
