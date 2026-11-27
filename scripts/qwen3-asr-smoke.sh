#!/bin/bash
# Step 0 of the Qwen3-ASR evaluation on the B70 (docs/specs/asr-qwen3-evaluation.md §5).
# Run from the repository root in a terminal on the B70 machine (JupyterLab: File > New > Terminal):
#
#   bash scripts/qwen3-asr-smoke.sh
#   AUDIO="$HOME/L50-tts.ogg=C002-L50:1-339 $HOME/clip23s.ogg=C001-S04:10-12" bash scripts/qwen3-asr-smoke.sh
#
# What it does, stopping at the first step that fails (each failure is a No-Go finding, not a bug to chase):
#   1. checks that the vLLM XPU image Haven already uses contains the Qwen3-ASR model class;
#   2. starts Qwen/Qwen3-ASR-1.7B in that image on CARD (default 1, the card without the note model),
#      127.0.0.1:PORT only, through scripts/b70-vllm.sh, and waits for it;
#   3. records card memory with xpu-smi;
#   4. runs scripts/qwen3_asr_smoke.py on AUDIO against it, and against Whisper on :8001 if Whisper is running.
# Everything goes to docs/asr-eval/qwen3-smoke-<time>/ (logs) and docs/asr-eval/b70-qwen3asr-smoke-<time>.{md,json}.
#
# Settings: CARD=1 PORT=8002 UTIL=0.3 MAXLEN=8192 OFFLINE=0 (first start downloads ~4 GB) KEEP=1 (leave it running
# for the benchmark; KEEP=0 stops it at the end) WHISPER_URL=http://127.0.0.1:8001 WHISPER_MODEL=whisper.
# If Whisper already runs on CARD, lower UTIL so both fit (Whisper takes about 0.12 of a card).
set -u
cd "$(dirname "$0")/.."
IMAGE=vllm/vllm-openai-xpu:v0.26.0
CARD="${CARD:-1}"; PORT="${PORT:-8002}"; UTIL="${UTIL:-0.3}"; MAXLEN="${MAXLEN:-8192}"
OFFLINE="${OFFLINE:-0}"; PRIVILEGED="${PRIVILEGED:-0}"; KEEP="${KEEP:-1}"
CONTAINER="${CONTAINER:-vllm-qwen3-asr}"; NAME=qwen3-asr
AUDIO="${AUDIO:-$HOME/L50-tts.ogg=C002-L50:1-339}"
WHISPER_URL="${WHISPER_URL:-http://127.0.0.1:8001}"; WHISPER_MODEL="${WHISPER_MODEL:-whisper}"
STAMP="$(date +%Y%m%d-%H%M)"
OUT="docs/asr-eval/qwen3-smoke-$STAMP"
mkdir -p "$OUT"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$OUT/steps.md"; }
fail() { log "NO-GO at step $1: $2"; exit 1; }

log "commit $(git rev-parse --short HEAD 2>/dev/null || echo unknown); card $CARD; port $PORT; util $UTIL; max-model-len $MAXLEN"
command -v docker >/dev/null || fail 1 "docker is not available in this terminal"

# 1. Is Qwen3-ASR in the image Haven already uses? Upgrading vLLM is out of scope (it would touch the verified note model).
if docker run --rm --entrypoint python3 "$IMAGE" -c \
  "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('vllm.model_executor.models.qwen3_asr') else 1)" \
  > "$OUT/image-check.log" 2>&1; then
  log "step 1 ok: $IMAGE has vllm.model_executor.models.qwen3_asr"
else
  fail 1 "$IMAGE has no Qwen3-ASR model class (see $OUT/image-check.log)"
fi

# 2. Start it on the XPU and wait (first start downloads the weights into ~/hf).
OFFLINE="$OFFLINE" PRIVILEGED="$PRIVILEGED" CARD="$CARD" PORT="$PORT" UTIL="$UTIL" CONTAINER="$CONTAINER" \
  bash scripts/b70-vllm.sh Qwen/Qwen3-ASR-1.7B "$NAME" --max-model-len "$MAXLEN" > "$OUT/start.log" 2>&1 \
  || fail 2 "docker run failed (see $OUT/start.log)"
log "step 2: started $CONTAINER, waiting for 127.0.0.1:$PORT (up to 20 minutes)"
t=0
until curl -s "127.0.0.1:$PORT/v1/models" | grep -q "$NAME"; do
  if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    docker logs --tail 200 "$CONTAINER" > "$OUT/server.log" 2>&1
    fail 2 "the server exited while loading (last lines in $OUT/server.log)"
  fi
  sleep 5; t=$((t + 5))
  if [ "$t" -ge 1200 ]; then
    docker logs --tail 200 "$CONTAINER" > "$OUT/server.log" 2>&1
    fail 2 "not answering after 20 minutes (see $OUT/server.log)"
  fi
done
log "step 2 ok: answering after ${t} s"

# 3. Card memory with everything loaded.
if command -v xpu-smi >/dev/null; then
  xpu-smi stats -d "$CARD" > "$OUT/xpu-smi-card$CARD.txt" 2>&1 || true
  log "step 3: card memory in $OUT/xpu-smi-card$CARD.txt"
else
  log "step 3: xpu-smi not found, memory NOT recorded"
fi

# 4. The requests Haven would send.
COMPARE=()
if curl -s "${WHISPER_URL}/v1/models" | grep -q "$WHISPER_MODEL"; then
  COMPARE=(--compare-url "$WHISPER_URL" --compare-model "$WHISPER_MODEL")
  log "step 4: comparing with Whisper at $WHISPER_URL"
else
  log "step 4: Whisper not answering at $WHISPER_URL, candidate only"
fi
# shellcheck disable=SC2086
python3 scripts/qwen3_asr_smoke.py $AUDIO --url "http://127.0.0.1:$PORT" --model "$NAME" "${COMPARE[@]}" \
  --label "b70-qwen3asr-smoke-$STAMP" 2>&1 | tee "$OUT/smoke.out"
STATUS=${PIPESTATUS[0]}
docker logs --tail 200 "$CONTAINER" > "$OUT/server.log" 2>&1
[ "$KEEP" = "1" ] || docker rm -f "$CONTAINER" >/dev/null 2>&1
[ "$STATUS" = "0" ] || fail 4 "no request to Qwen3-ASR succeeded (see $OUT/smoke.out, $OUT/server.log)"
log "step 4 done: read docs/asr-eval/b70-qwen3asr-smoke-$STAMP.md; server left running: $([ "$KEEP" = "1" ] && echo yes || echo no)"
