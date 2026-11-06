#!/usr/bin/env bash
# Unattended Qwen calibrations on Modal: merger consideration (6c0), then contract entities (5a).
#
# Each chunk is mixed: Qwen/Qwen3-32B-FP8 on the sandbox's H100 labeler takes DeepSeek's slot,
# GLM stays on OpenRouter. The script keeps the Mac awake (caffeinate), deploys the labeler,
# runs both chunks, scores both gates WITHOUT approving them, and stops the Modal app on any
# exit — success, failure or Ctrl-C.
#
# Required env:
#   OPENROUTER_API_KEY, HF_TOKEN   loaded per run, never written to disk
#   INITIALS                       recorded on the runs
#   CAP_6C0, CAP_5A                USD caps; Modal GPU time counts against them
# Optional env:
#   SANDBOX   path to local-mailroom-sandbox (default: ../local-mailroom-sandbox)
#   CHUNKS    which chunks, in order (default: "6c0 5a")
#
# Keep the lid open and the Mac on AC power: caffeinate cannot stop a lid-close sleep on battery.
# If the Mac does sleep, the container scales to zero 120 s after the last request, and
# re-running this script resumes each chunk from its journal (answered documents are not re-sent).
#
# Usage, from the repo root:
#   export OPENROUTER_API_KEY=... HF_TOKEN=... INITIALS=JJB CAP_6C0=2.00 CAP_5A=4.00
#   scripts/labeling/modal_night.sh 2>&1 | tee modal_night.log
set -euo pipefail

: "${OPENROUTER_API_KEY:?set OPENROUTER_API_KEY}"
: "${HF_TOKEN:?set HF_TOKEN}"
: "${INITIALS:?set INITIALS}"
: "${CAP_6C0:?set CAP_6C0 (USD)}"
: "${CAP_5A:?set CAP_5A (USD)}"

REPO="$(git rev-parse --show-toplevel)"
SANDBOX="${SANDBOX:-$REPO/../local-mailroom-sandbox}"
SANDBOX="$(cd "$SANDBOX" && pwd)"
CHUNKS="${CHUNKS:-6c0 5a}"
APP="sandbox-vllm-gt-labeler"
PY="$REPO/.venv/bin/python"
SPY="$SANDBOX/.venv/bin/python"
MAP="deepseek/deepseek-v4.1-flash=Qwen/Qwen3-32B-FP8"

# Keep the Mac awake for as long as this shell lives.
caffeinate -dims -w $$ &

# shellcheck disable=SC2329  # invoked by the EXIT trap
stop_app() {
  echo "== stopping $APP =="
  (cd "$SANDBOX" && PATH="$SANDBOX/.venv/bin:$PATH" bash deploy/teardown_vllm.sh "$APP") \
    || "$SANDBOX/.venv/bin/modal" app stop "$APP" \
    || echo "!! stop failed: run  modal app stop $APP  by hand" >&2
}
trap stop_app EXIT

# Per-run bearer token shared by the deployed app and this client; never stored.
MODAL_VLLM_API_TOKEN="$(openssl rand -hex 24)"
export MODAL_VLLM_API_TOKEN VLLM_API_KEY="$MODAL_VLLM_API_TOKEN"

echo "== posture check =="
(cd "$SANDBOX" && "$SPY" deploy/modal_gt_labeler.py --check)

echo "== deploy $APP (deploy alone starts no GPU) =="
(cd "$SANDBOX" && "$SANDBOX/.venv/bin/modal" deploy deploy/modal_gt_labeler.py)

VLLM_BASE_URL="$("$SPY" -c "import modal; print(modal.Function.from_name('$APP', 'serve').get_web_url())")"
export VLLM_BASE_URL
echo "endpoint resolved (${VLLM_BASE_URL%%.*}…)"

echo "== cold start (about 7 minutes) =="
for _ in $(seq 1 30); do
  if curl -fsS --max-time 60 -H "Authorization: Bearer $VLLM_API_KEY" "$VLLM_BASE_URL/v1/models" >/dev/null; then
    echo "model is up"; break
  fi
  sleep 20
done

cd "$REPO"
export PYTHONPATH=src
labeling() { "$PY" -m mailroom_eda.labeling "$@"; }

run_chunk() {
  local id="$1" cap="$2" rc=0
  local extra=()
  [ "$id" = "5a" ] && extra=(--ack-requires)
  echo "== $id: dry run =="
  labeling chunk "$id" --provider mixed --model-map "$MAP" --cap-usd "$cap" ${extra[@]+"${extra[@]}"} --dry-run
  echo "== $id: run (cap \$$cap) =="
  labeling chunk "$id" --provider mixed --model-map "$MAP" --initials "$INITIALS" --cap-usd "$cap" ${extra[@]+"${extra[@]}"} || rc=$?
  if [ "$rc" -eq 2 ] || [ "$rc" -eq 3 ]; then
    echo "== $id: rc $rc, resuming once from the journal =="
    rc=0
    labeling chunk "$id" --provider mixed --model-map "$MAP" --initials "$INITIALS" --cap-usd "$cap" ${extra[@]+"${extra[@]}"} || rc=$?
  fi
  echo "== $id: gate (scored only; approve in the morning after reading it) =="
  labeling gate "$id" --provider mixed --initials "$INITIALS" || true
  return "$rc"
}

status=0
for id in $CHUNKS; do
  case "$id" in
    6c0) cap="$CAP_6C0" ;;
    5a)  cap="$CAP_5A" ;;
    *)   echo "unknown chunk $id" >&2; status=1; continue ;;
  esac
  run_chunk "$id" "$cap" || status=$?
done

echo "== done (exit $status); spend per chunk is in runs.jsonl (spent_usd, gpu_seconds, gpu_usd) =="
exit "$status"
