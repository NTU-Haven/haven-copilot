#!/bin/bash
# Batch 10 on ONE Arc Pro B70: the fixes since batch 9, plus what is still pending.
# Run from the repository root on the B70 machine:
#
#   (AUDIO=~/L50-tts.ogg nohup bash scripts/b70-batch10.sh > batch10.log 2>&1 &)
#   STEPS=long bash scripts/b70-batch10.sh        # some steps only
#
# Steps (STEPS, default all), Qwen3-32B AWQ and Whisper large-v3-turbo on one card:
#   long       C002-L50 with 1, 2 and 4 rooms at once. The writer now sends a session's
#              parts together and asks for fewer sentences per part (batch 9 rerun:
#              297 / 284 / 365 s)
#   asr        the 45-minute recording (AUDIO) alone: transcription time, character error
#              rate, speaker guessing; saved as session C002-T50 and drafted, so the
#              recording-to-draft time can be read off
#   asrload    the same recording transcribed while 4 rooms draft C002-L50
#   repeat     benchmark --repeat 3 on the demo sessions (pass rates under the new writer)
#   verifier   the verifier test set (rules unchanged; checks that nothing moved)
#
# AUDIO is a synthetic reading of C002-L50 (macOS text-to-speech, two voices, 16 kHz
# Opus, 45 min). Synthetic speech is cleaner than a real session, so its error rate is
# a best case, not what a therapy room would give. It is not kept in the repository.
#
# Also, for the whole run:
#   - the servers start with OFFLINE=1 PRIVILEGED=0 (options not tested before). If
#     Whisper does not come up that way, both restart with PRIVILEGED=1 and the
#     summary says so.
#   - every second, the TCP/UDP endpoints on this machine that are not loopback are
#     logged (ss). The summary lists remote addresses seen during the run that were
#     not there in the idle baseline: the evidence for "no outbound connections".
#     The machine's own remote-access session (Jupyter) is in the baseline.
set -u
cd "$(dirname "$0")/.."
STEPS="${STEPS:-long,asr,asrload,repeat,verifier}"
AUDIO="${AUDIO:-$HOME/L50-tts.ogg}"
CARD="${CARD:-0}"
OFFLINE="${OFFLINE:-1}"; PRIVILEGED="${PRIVILEGED:-0}"; export OFFLINE PRIVILEGED
HF=Qwen/Qwen3-32B-AWQ; NAME=qwen3-32b-awq
STAMP="$(date +%Y%m%d-%H%M)"
OUT="docs/b70-batch10/$STAMP"
mkdir -p "$OUT"
COMMIT="$(git rev-parse --short HEAD 2>/dev/null || cat SYNCED_COMMIT 2>/dev/null || echo unknown)"
SUMMARY="$OUT/summary.md"
log() { echo "[$(date +%H:%M:%S)] $*"; echo "- $(date +%H:%M:%S) $*" >> "$OUT/timeline.md"; }

wait_for() {  # port name seconds
  local port="$1" name="$2" limit="$3" t=0
  until curl -s "127.0.0.1:$port/v1/models" | grep -q "$name"; do
    sleep 5; t=$((t + 5))
    if [ "$t" -ge "$limit" ]; then return 1; fi
  done
}

# --- power, memory and network sampling ------------------------------------------
MON_PID=""; NET_PID=""
start_monitor() {
  if command -v xpu-smi >/dev/null 2>&1; then
    ( echo "timestamp, utilization.gpu, power.draw, memory.used"
      while true; do
        xpu-smi --query-gpu=timestamp,utilization.gpu,power.draw,memory.used --id="$CARD" \
          --format=csv,noheader,nounits </dev/null 2>/dev/null
        sleep 1
      done ) > "$OUT/monitor.csv" &
    MON_PID=$!
    log "monitor: xpu-smi on card $CARD -> $OUT/monitor.csv"
  else
    echo "xpu-smi not found; no power or memory figures" > "$OUT/monitor.csv"
    log "monitor: xpu-smi not found, power and memory are NOT recorded"
  fi
}
start_netlog() {
  ( while true; do
      echo "== $(date +%H:%M:%S)"
      ss -Htun 2>/dev/null | awk '{print $1, $5, $6}' | grep -v -E ' (127\.|\[::1\]|::1:|\[::ffff:127\.)'
      sleep 1
    done ) > "$OUT/net.log" &
  NET_PID=$!
}
stop_bg() { for p in "$@"; do [ -n "$p" ] && { pkill -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; }; done; }
trap 'stop_bg "$MON_PID" "$NET_PID"; rm -f data/sessions/C002-T50.json; docker rm -f vllm-haven vllm-asr >/dev/null 2>&1' EXIT

window_stats() {
  python3 - "$OUT/monitor.csv" "$1" "$2" <<'PY'
import csv, re, sys
path, start, end = sys.argv[1:4]
power, mem = [], []
try:
    rows = list(csv.reader(open(path)))
except OSError:
    rows = []
for r in rows[1:]:
    if len(r) < 4:
        continue
    m = re.search(r"(\d\d:\d\d:\d\d)", r[0])
    if not m or not (start <= m.group(1) <= end):
        continue
    try:
        power.append(float(r[2])); mem.append(float(r[3]))
    except ValueError:
        pass
if power:
    print(f"peak memory {max(mem) / 1024:.1f} GiB, mean power {sum(power) / len(power):.0f} W from {len(power)} samples")
else:
    print("no monitor samples")
PY
}

# --- servers ---------------------------------------------------------------------
start_whisper() {
  PORT=8001 UTIL=0.12 CONTAINER=vllm-asr bash scripts/b70-vllm.sh openai/whisper-large-v3-turbo whisper --max-model-len 448 >/dev/null
  wait_for 8001 whisper 600
}
start_servers() {
  docker rm -f vllm-haven vllm-asr >/dev/null 2>&1
  log "starting Whisper (OFFLINE=$OFFLINE PRIVILEGED=$PRIVILEGED)"
  if ! start_whisper; then
    docker logs --tail 60 vllm-asr > "$OUT/whisper-fail-priv$PRIVILEGED.log" 2>&1
    if [ "$PRIVILEGED" = "0" ]; then
      log "Whisper did not start with PRIVILEGED=0 (log: $OUT/whisper-fail-priv0.log); retrying with PRIVILEGED=1"
      PRIVILEGED=1; export PRIVILEGED
      start_whisper || { log "Whisper did not come up"; return 1; }
    else
      log "Whisper did not come up"; return 1
    fi
  fi
  log "Whisper ready"
  log "starting $NAME: 32k context, fp8 context cache"
  UTIL=0.8 bash scripts/b70-vllm.sh "$HF" "$NAME" --max-model-len 32768 --kv-cache-dtype fp8 >/dev/null
  if ! wait_for 8000 "$NAME" 1500; then
    docker logs --tail 60 vllm-haven > "$OUT/$NAME-fail.log" 2>&1
    log "$NAME did not start (log: $OUT/$NAME-fail.log)"; return 1
  fi
  log "$NAME ready (OFFLINE=$OFFLINE PRIVILEGED=$PRIVILEGED)"
}

run_step() {  # label command...
  local label="$1"; shift
  local t0 t1 rc
  t0="$(date +%H:%M:%S)"; log "step $label: $*"
  "$@" > "$OUT/$label.out" 2>&1; rc=$?
  t1="$(date +%H:%M:%S)"
  log "step $label finished (exit $rc)"
  echo "| $label | $t0–$t1 | $rc | $(window_stats "$t0" "$t1") | \`$OUT/$label.out\` |" >> "$OUT/steps.md"
}

# --- run -----------------------------------------------------------------------
{
  echo "# Batch 10 on the B70: $STAMP"
  echo
  echo "- Commit: \`$COMMIT\`"
  echo "- Card: $CARD (one card only, as in the final)"
  echo "- Steps: $STEPS"
  echo "- Whisper large-v3-turbo on the same card at UTIL=0.12; $NAME at UTIL=0.8, 32k context, fp8 cache"
  echo "- Writer parts at once: ${HAVEN_WRITE_PARALLEL:-4}"
  echo "- Recording: $AUDIO ($( [ -f "$AUDIO" ] && du -h "$AUDIO" | cut -f1 || echo missing)), synthetic speech"
} > "$SUMMARY"
echo "| Step | Time | Exit | Card during the step | Output |" > "$OUT/steps.md"
echo "| --- | --- | --- | --- | --- |" >> "$OUT/steps.md"
start_monitor
start_netlog
log "idle baseline: 30 s"; T0="$(date +%H:%M:%S)"; sleep 30
BASE_END="$(date +%H:%M:%S)"
echo "| idle, no servers | $T0 | – | $(window_stats "$T0" "$BASE_END") | – |" >> "$OUT/steps.md"

if start_servers; then
  echo "- Servers ran with OFFLINE=$OFFLINE PRIVILEGED=$PRIVILEGED" >> "$SUMMARY"
  T0="$(date +%H:%M:%S)"; sleep 20
  echo "| both servers loaded, idle | $T0 | – | $(window_stats "$T0" "$(date +%H:%M:%S)") | – |" >> "$OUT/steps.md"
  export HAVEN_LLM=openai HAVEN_MODEL="$NAME" HAVEN_ASR=openai
  case ",$STEPS," in *,long,*)
    run_step "$NAME-long-1room" python3 scripts/benchmark.py --sessions C002-L50 --label "b70-$NAME-L50-$STAMP"
    run_step "$NAME-long-2rooms" python3 scripts/benchmark.py --sessions C002-L50 --rooms 2 --label "b70-$NAME-L50-2rooms-$STAMP"
    run_step "$NAME-long-4rooms" python3 scripts/benchmark.py --sessions C002-L50 --rooms 4 --label "b70-$NAME-L50-4rooms-$STAMP"
    ;;
  esac
  if [ -f "$AUDIO" ]; then
    case ",$STEPS," in *,asr,*)
      run_step "asr-L50-alone" python3 scripts/asr_eval.py "$AUDIO" --session C002-L50 --segments 1-339 --save-as C002-T50 --label "b70-whisper-L50-tts-$STAMP"
      if [ -f data/sessions/C002-T50.json ]; then
        run_step "$NAME-draft-from-recording" python3 scripts/benchmark.py --sessions C002-T50 --label "b70-$NAME-T50-$STAMP"
      fi
      ;;
    esac
    case ",$STEPS," in *,asrload,*)
      T0="$(date +%H:%M:%S)"; log "step asr-under-load: recording transcribed while 4 rooms draft"
      python3 scripts/asr_eval.py "$AUDIO" --session C002-L50 --segments 1-339 --label "b70-whisper-L50-tts-load-$STAMP" > "$OUT/asr-L50-under-load.out" 2>&1 &
      ASR_PID=$!
      python3 scripts/benchmark.py --sessions C002-L50 --rooms 4 --label "b70-$NAME-L50-4rooms-asrload-$STAMP" > "$OUT/$NAME-long-4rooms-asrload.out" 2>&1
      wait "$ASR_PID"
      T1="$(date +%H:%M:%S)"; log "step asr-under-load finished"
      echo "| asr + 4 rooms at once | $T0–$T1 | – | $(window_stats "$T0" "$T1") | \`$OUT/asr-L50-under-load.out\`, \`$OUT/$NAME-long-4rooms-asrload.out\` |" >> "$OUT/steps.md"
      ;;
    esac
  else
    log "no recording at $AUDIO: asr steps skipped"
  fi
  case ",$STEPS," in *,repeat,*)   run_step "$NAME-repeat3"  python3 scripts/benchmark.py --repeat 3 --label "b70-$NAME-repeat3-$STAMP" ;; esac
  case ",$STEPS," in *,verifier,*) run_step "$NAME-verifier" python3 scripts/verifier_eval.py --label "b70-$NAME-$STAMP" ;; esac
  docker logs --tail 30 vllm-haven > "$OUT/$NAME-server-tail.log" 2>&1
  docker logs --tail 30 vllm-asr > "$OUT/whisper-server-tail.log" 2>&1
else
  echo "- Servers did not start; see logs in $OUT" >> "$SUMMARY"
fi
rm -f data/sessions/C002-T50.json
stop_bg "$MON_PID" "$NET_PID"; MON_PID=""; NET_PID=""

# Remote addresses seen during the run that were not there while idle.
python3 - "$OUT/net.log" "$BASE_END" > "$OUT/net-summary.md" <<'PY'
import sys
path, base_end = sys.argv[1:3]
base, during, now = set(), {}, ""
for line in open(path):
    line = line.strip()
    if line.startswith("== "):
        now = line[3:]
        continue
    parts = line.split()
    if len(parts) < 3:
        continue
    proto, local, peer = parts
    key = (proto, peer.rsplit(":", 1)[0])
    if now <= base_end:
        base.add(key)
    elif key not in base:
        during.setdefault(key, (now, local))
print("## Network endpoints (not loopback)")
print()
print(f"Idle baseline (until {base_end}): {len(base)} remote address(es), the machine's own traffic before any server started.")
print()
if during:
    print(f"**{len(during)} new remote address(es) during the run** (check each: the first weight check, the remote-access session reconnecting, or something else):")
    print()
    for (proto, peer), (t, local) in sorted(during.items(), key=lambda x: x[1][0]):
        print(f"- {t} {proto} {local} -> {peer}")
else:
    print("**No remote address appeared during the run** that was not already there while idle.")
PY

{
  echo
  echo "## Steps"
  echo
  cat "$OUT/steps.md"
  echo
  echo "Power and memory are for the whole card while the step ran (both servers loaded)."
  echo
  cat "$OUT/net-summary.md"
  echo
  echo "## Result files"
  echo
  ls docs/verifier-eval/*"$STAMP"* docs/benchmarks/*"$STAMP"* docs/asr-eval/*"$STAMP"* 2>/dev/null | sed 's/^/- /'
  echo
  echo "## Timeline"
  echo
  cat "$OUT/timeline.md"
} >> "$SUMMARY"
log "done: $SUMMARY"
