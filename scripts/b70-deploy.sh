#!/bin/bash
# Ticket 13: starts Haven's three model servers on the B70 machine and records the deployment,
# so it can be reproduced. Run from the repository root in a terminal on the B70 (JupyterLab:
# File > New > Terminal):
#
#   bash scripts/b70-deploy.sh                 # LAYOUT=dual: Qwen3-32B-AWQ on card 0, Whisper + Qwen3-ASR on card 1
#   LAYOUT=single bash scripts/b70-deploy.sh   # the verified single-card layout (everything on card 0)
#
# Steps, each logged to docs/deploy/b70-<layout>-<time>/steps.md:
#   1. environment: kernel, GPU driver packages, xpu-smi discovery, docker version, image digest;
#   2. Whisper (:8001) then Qwen3-ASR (:8002) on the ASR card, waiting for each to answer
#      (two servers loading at once upset each other's memory check);
#   3. Qwen3-32B-AWQ (:8000) on the note card;
#   4. one request to each server: chat completion (JSON mode, thinking off) to Qwen, a transcription
#      of AUDIO (if the file exists) to Whisper (verbose_json) and Qwen3-ASR (json);
#   5. card memory with everything loaded (xpu-smi), container command lines, and a summary
#      docs/deploy/b70-<layout>-<time>.md to commit.
# A server that fails to start stops the script with the step and its log; in LAYOUT=dual, the summary
# says to rerun with LAYOUT=single (the fallback, verified 2026-11-17 and 2026-11-27).
#
# Settings: OFFLINE=1 (weights already in ~/hf; 0 on the first start) PRIVILEGED=0 (1 restores --privileged)
# AUDIO=~/L50-tts.ogg  QWEN_UTIL / ASR_UTIL override the per-layout memory shares.
# All servers listen on 127.0.0.1 only. Haven itself is started separately (see docs/deploy/b70.md).
set -u
cd "$(dirname "$0")/.."
IMAGE=vllm/vllm-openai-xpu:v0.26.0
LAYOUT="${LAYOUT:-dual}"
OFFLINE="${OFFLINE:-1}"; PRIVILEGED="${PRIVILEGED:-0}"; export OFFLINE PRIVILEGED
AUDIO="${AUDIO:-$HOME/L50-tts.ogg}"
case "$LAYOUT" in
  dual)   QWEN_CARD=0; ASR_CARD=1; QWEN_UTIL="${QWEN_UTIL:-0.9}"; WHISPER_UTIL="${WHISPER_UTIL:-0.12}"; QASR_UTIL="${QASR_UTIL:-0.3}"
          QWEN_ARGS="--max-model-len 32768 --kv-cache-dtype fp8" ;;
  single) QWEN_CARD=0; ASR_CARD=0; QWEN_UTIL="${QWEN_UTIL:-0.5}"; WHISPER_UTIL="${WHISPER_UTIL:-0.12}"; QASR_UTIL="${QASR_UTIL:-0.15}"
          QWEN_ARGS="--max-model-len 32768 --kv-cache-dtype fp8" ;;
  *) echo "LAYOUT must be dual or single" >&2; exit 2 ;;
esac
# Single card: Whisper 0.12 + Qwen3-ASR 0.15 + Qwen 0.5 of the card. Without Qwen3-ASR the verified
# single-card setting was Whisper 0.12 + Qwen 0.8 (batch 9/10); if the three do not fit, run without
# HAVEN_ASR_REFINE: SKIP_QASR=1 QWEN_UTIL=0.8 LAYOUT=single bash scripts/b70-deploy.sh
SKIP_QASR="${SKIP_QASR:-0}"
STAMP="$(date +%Y%m%d-%H%M)"
OUT="docs/deploy/b70-$LAYOUT-$STAMP"
mkdir -p "$OUT"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$OUT/steps.md"; }
FAILED=""
fail() {
  log "FAILED at step $1: $2"
  FAILED="step $1: $2"
  summary
  exit 1
}

wait_for() {  # wait_for <port> <served-name> <container>
  local t=0
  until curl -s "127.0.0.1:$1/v1/models" | grep -q "$2"; do
    if ! docker ps --format '{{.Names}}' | grep -qx "$3"; then
      docker logs --tail 300 "$3" > "$OUT/$3.log" 2>&1; return 1
    fi
    sleep 5; t=$((t + 5))
    if [ "$t" -ge 1500 ]; then docker logs --tail 300 "$3" > "$OUT/$3.log" 2>&1; return 1; fi
  done
  log "  $3 answering on :$1 after ${t} s"
}

start() {  # start <step> <card> <port> <util> <container> <hf-model> <served-name> [args...]
  local step=$1 card=$2 port=$3 util=$4 name=$5 model=$6 served=$7; shift 7
  log "starting $name: $model as $served on card $card, :$port, util $util, args: $*"
  CARD=$card PORT=$port UTIL=$util CONTAINER=$name bash scripts/b70-vllm.sh "$model" "$served" "$@" > "$OUT/$name-start.log" 2>&1 \
    || fail "$step" "docker run for $name failed (see $OUT/$name-start.log)"
}

summary() {
  {
    echo "# B70 deployment: layout $LAYOUT, $STAMP"
    echo
    echo "- commit: \`$(git rev-parse --short HEAD 2>/dev/null || echo unknown)\`"
    echo "- result: $([ -z "$FAILED" ] && echo "all three servers answered" || echo "FAILED, $FAILED")"
    echo "- image: \`$IMAGE\`, digest \`$(cat "$OUT/image-digest.txt" 2>/dev/null || echo unknown)\`"
    echo "- cards: Qwen3-32B-AWQ card $QWEN_CARD (util $QWEN_UTIL), Whisper card $ASR_CARD (util $WHISPER_UTIL), Qwen3-ASR card $ASR_CARD (util $QASR_UTIL, skipped: $SKIP_QASR)"
    echo "- OFFLINE=$OFFLINE PRIVILEGED=$PRIVILEGED"
    echo
    echo "## Environment"; echo; echo '```'; cat "$OUT/environment.txt" 2>/dev/null; echo '```'
    echo; echo "## Requests"; echo; echo '```'; cat "$OUT/requests.txt" 2>/dev/null; echo '```'
    echo; echo "## Card memory (everything loaded)"; echo; echo '```'; cat "$OUT"/xpu-smi-card*.txt 2>/dev/null | grep -iE "memory|card|device" ; echo '```'
    echo; echo "## Container command lines"; echo; echo '```'
    for c in vllm-asr vllm-qwen3-asr vllm-haven; do
      docker inspect --format "$c: {{.Config.Image}} {{json .Config.Env}} {{json .Args}}" "$c" 2>/dev/null \
        | sed -E 's/"HF_TOKEN=[^"]*"/"HF_TOKEN=<removed>"/g'
    done
    echo '```'
    echo; echo "## Decision"; echo
    if [ -z "$FAILED" ] && [ "$LAYOUT" = dual ]; then
      echo "Dual-card layout works: use it (Qwen card 0, ASR card 1). Fallback: \`LAYOUT=single bash scripts/b70-deploy.sh\`."
    elif [ -z "$FAILED" ]; then
      echo "Single-card layout works."
    else
      echo "Did not come up. If this was LAYOUT=dual, stop the containers and run \`LAYOUT=single bash scripts/b70-deploy.sh\`."
    fi
  } > "docs/deploy/b70-$LAYOUT-$STAMP.md"
  log "summary: docs/deploy/b70-$LAYOUT-$STAMP.md (logs in $OUT/)"
}

# 1. Environment.
command -v docker >/dev/null || fail 1 "docker is not available in this terminal"
{
  echo "date: $(date -Is)"; echo "kernel: $(uname -r)"
  echo "os: $(. /etc/os-release 2>/dev/null; echo "$PRETTY_NAME")"
  echo "docker: $(docker --version)"
  echo "--- GPU driver packages"
  (dpkg -l 2>/dev/null | grep -iE "intel-level-zero|level-zero|intel-opencl|libze|xpu-smi|intel-i915|intel-fw|xe-" | awk '{print $2, $3}') || true
  (rpm -qa 2>/dev/null | grep -iE "level-zero|intel-opencl|xpu-smi") || true
  echo "--- kernel module"; (modinfo xe 2>/dev/null | grep -E "^(filename|version|vermagic)") || (modinfo i915 2>/dev/null | grep -E "^(filename|version|vermagic)") || echo "modinfo not available"
  echo "--- xpu-smi discovery"; (xpu-smi discovery 2>&1) || echo "xpu-smi not found"
} > "$OUT/environment.txt" 2>&1
docker image inspect --format '{{index .RepoDigests 0}}' "$IMAGE" > "$OUT/image-digest.txt" 2>/dev/null \
  || { docker pull "$IMAGE" >/dev/null && docker image inspect --format '{{index .RepoDigests 0}}' "$IMAGE" > "$OUT/image-digest.txt"; }
log "step 1 ok: environment in $OUT/environment.txt, image $(cat "$OUT/image-digest.txt")"

# 2. ASR servers, one at a time.
start 2 "$ASR_CARD" 8001 "$WHISPER_UTIL" vllm-asr openai/whisper-large-v3-turbo whisper --max-model-len 448
wait_for 8001 whisper vllm-asr || fail 2 "Whisper did not answer (see $OUT/vllm-asr.log)"
if [ "$SKIP_QASR" != "1" ]; then
  start 2 "$ASR_CARD" 8002 "$QASR_UTIL" vllm-qwen3-asr Qwen/Qwen3-ASR-1.7B qwen3-asr --max-model-len 8192
  wait_for 8002 qwen3-asr vllm-qwen3-asr || fail 2 "Qwen3-ASR did not answer (see $OUT/vllm-qwen3-asr.log)"
fi

# 3. Note model.
# shellcheck disable=SC2086
start 3 "$QWEN_CARD" 8000 "$QWEN_UTIL" vllm-haven Qwen/Qwen3-32B-AWQ qwen3-32b-awq $QWEN_ARGS
wait_for 8000 qwen3-32b-awq vllm-haven || fail 3 "Qwen3-32B-AWQ did not answer (see $OUT/vllm-haven.log)"

# 4. One request each.
{
  echo "--- qwen3-32b-awq chat completion"
  curl -s -w "\nHTTP %{http_code} in %{time_total} s\n" 127.0.0.1:8000/v1/chat/completions -H 'Content-Type: application/json' -d '{
    "model":"qwen3-32b-awq","temperature":0.1,"max_tokens":64,"response_format":{"type":"json_object"},
    "chat_template_kwargs":{"enable_thinking":false},
    "messages":[{"role":"user","content":"只回 JSON：{\"ok\": true}"}]}' | tail -c 600
  if [ -f "$AUDIO" ]; then
    echo "--- whisper verbose_json on $(basename "$AUDIO")"
    curl -s -o "$OUT/whisper.json" -w "HTTP %{http_code} in %{time_total} s\n" 127.0.0.1:8001/v1/audio/transcriptions \
      -F model=whisper -F language=zh -F response_format=verbose_json -F "file=@$AUDIO"
    python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print('segments:', len(d.get('segments') or []))" "$OUT/whisper.json" 2>&1
    if [ "$SKIP_QASR" != "1" ]; then
      echo "--- qwen3-asr json on $(basename "$AUDIO")"
      curl -s -o "$OUT/qwen3-asr.json" -w "HTTP %{http_code} in %{time_total} s\n" 127.0.0.1:8002/v1/audio/transcriptions \
        -F model=qwen3-asr -F language=zh -F response_format=json -F "file=@$AUDIO"
      python3 -c "import json,sys; print('characters:', len(json.load(open(sys.argv[1])).get('text','')))" "$OUT/qwen3-asr.json" 2>&1
    fi
  else
    echo "no audio at $AUDIO: transcription requests skipped (/v1/models answered)"
  fi
} > "$OUT/requests.txt" 2>&1
log "step 4: requests in $OUT/requests.txt"
# Transcripts of the test audio are not kept in the repository.
rm -f "$OUT/whisper.json" "$OUT/qwen3-asr.json"

# 5. Memory.
if command -v xpu-smi >/dev/null; then
  for c in $(echo "$QWEN_CARD $ASR_CARD" | tr ' ' '\n' | sort -u); do xpu-smi stats -d "$c" > "$OUT/xpu-smi-card$c.txt" 2>&1 || true; done
  log "step 5: card memory recorded"
else
  log "step 5: xpu-smi not found, memory NOT recorded"
fi
summary
