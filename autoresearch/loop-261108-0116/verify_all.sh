#!/bin/sh
# Autoresearch combined Verify: all relevant gates in ONE run, each dimension scored, appended
# to dimensions.tsv so the loop's coverage ledger grows with every iteration. A new check is
# added dynamically by wiring its capture line here — registered dimensions are evaluated on
# every later run. Overall ledger = checks_executed (grows as the page, prompts and corpus do).
#
# Usage: verify_all.sh [evidence-dir]   (AXE_PATH=/path/axe.min.js adds the a11y axe-core scan)
set -eu
root="$(cd "$(dirname "$0")" && pwd)"
cd "$root/../.."                       # repo root (loop dir -> autoresearch -> repo)
evidence="${1:-/private/tmp/opencode/autoresearch-all}"
mkdir -p "$evidence"
python=.venv/bin/python
[ -x "$python" ] || python=python3
ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
export PYTHONPATH=src

"$root/verify_interface.sh" "$evidence/interface"
"$python" "$root/rubric_prompt.py" > "$evidence/rubric.txt"
"$python" -m pytest "tests/test_labeling.py::test_every_cap_covers_its_projection_with_headroom" > "$evidence/capguard.txt" 2>&1 || true
"$python" -m mailroom_eda.labeling.smoke_fixtures check  > "$evidence/fixtures.txt" 2>&1 || true
echo n | "$python" -m mailroom_eda.labeling.smoke_fixtures replay > "$evidence/replay.txt" 2>&1 || true
"$python" -m pytest tests/test_labeling_review_portal.py tests/test_labeling_launch.py > "$evidence/quick.txt" 2>&1 || true
"$python" -m pytest tests/test_labeling.py > "$evidence/labeling.txt" 2>&1 || true
cov=skip
if "$python" scripts/audit/coverage_matrix.py --check > "$evidence/coverage.txt" 2>&1; then
  cov=pass
elif grep -qi "not built\|no data\|no such file\|not found" "$evidence/coverage.txt"; then
  cov=skip
else
  cov=fail
fi
axe=skip
if [ -n "${AXE_PATH:-}" ]; then
  CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}" \
  node tests/browser_review_page.mjs "$evidence/axe" > "$evidence/axe.out" 2>&1
  # clean only when the whole smoke PASSED and axe printed zero violations
  if grep -q 'PASS: integrated synthetic review browser smoke' "$evidence/axe.out" \
     && grep -q 'AXE {"violations":\[\]' "$evidence/axe.out"; then axe=clean; else axe=violations; fi
fi

"$python" - "$evidence" "$ts" "$axe" "$root" "$cov" <<'PYEOF'
import json, re, sys
from pathlib import Path
ev = Path(sys.argv[1]); ts = sys.argv[2]; axe = sys.argv[3]; root = sys.argv[4]; cov = sys.argv[5]
def suites(name):
    """(passed, failed) from the pytest summary; a crashed run with no summary counts as failed."""
    text = (ev / name).read_text()
    pm = re.search(r'(\d+) passed', text); fm = re.search(r'(\d+) failed', text)
    if not pm and not fm and text.strip():
        return 0, 1
    p = int(pm.group(1)) if pm else 0
    f = int(fm.group(1)) if fm else 0
    return p, f
def okcount(name):
    return sum(1 for l in (ev / name).read_text().splitlines() if l.lower().startswith("ok"))
qp, qf = suites("quick.txt"); lp, lf = suites("labeling.txt")
capg_p, capg_f = suites("capguard.txt")
capg = "pass" if capg_p == 1 and capg_f == 0 else "fail"
iface = json.loads((ev / "interface" / "results.json").read_text())
res = {"timestamp_utc": ts, "axe": axe, "dimensions": [
    ("interface_named_checks", len(iface["checks"]), "higher"),
    ("a11y_axe_scan", axe, "clean when scanned"),
    ("prompt_rubric_percent", int((ev / "rubric.txt").read_text().strip()), "higher"),
    ("smoke_fixtures_current", okcount("fixtures.txt"), "stable per release"),
    ("smoke_fixture_replays", okcount("replay.txt"), "stable per release"),
    ("cap_headroom_guard", capg, "pass"),
    ("portal_launch_tests", f"{qp}/{qp+qf}", "all pass"),
    ("labeling_tests", f"{lp}/{lp+lf}", "all pass"),
    ("docclass_coverage_check", cov, "pass; honest skip when data/ absent"),
]}
# axe passes are read from the scan's own axe.json (never a hardcoded count)
axe_passes = 0
if axe == "clean":
    axe_passes = int(json.loads((ev / "axe" / "axe.json").read_text()).get("passes", 0))
res["checks_executed"] = (len(iface["checks"]) + res["dimensions"][2][1] + res["dimensions"][3][1]
                          + res["dimensions"][4][1] + qp + lp + axe_passes)
(ev / "results-all.json").write_text(json.dumps(res, indent=1) + "\n")
tsv = Path(root) / "dimensions.tsv"
if not tsv.exists():
    tsv.write_text("# timestamp\tinterface_checks\trubric\taxe\tfixtures\treplays\tcap_guard"
                   "\tportal+launch\tlabeling\tcoverage\ttotal_executed\n")
tsv.write_text(tsv.read_text() + "\t".join(str(x) for x in (
    ts, len(iface["checks"]), res["dimensions"][2][1], axe, res["dimensions"][3][1],
    res["dimensions"][4][1], capg, res["dimensions"][6][1], res["dimensions"][7][1],
    cov, res["checks_executed"])) + "\n")
print("ALL: " + json.dumps(res["dimensions"]) + " ledger=" + str(tsv))
PYEOF
