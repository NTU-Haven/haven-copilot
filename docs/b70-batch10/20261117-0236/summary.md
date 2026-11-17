# Batch 10 on the B70: 20261117-0236

- Commit: `9c8a857`
- Card: 0 (one card only, as in the final)
- Steps: asr,asrload
- Whisper large-v3-turbo on the same card at UTIL=0.12; qwen3-32b-awq at UTIL=0.8, 32k context, fp8 cache
- Writer parts at once: 4
- Recording: /home/devcloud/L50-tts.ogg (7.0M), synthetic speech
- Servers ran with OFFLINE=1 PRIVILEGED=0

## Steps

| Step | Time | Exit | Card during the step | Output |
| --- | --- | --- | --- | --- |
| idle, no servers | 02:36:42 | – | peak memory 0.0 GiB, mean power 57 W from 23 samples | – |
| both servers loaded, idle | 02:41:14 | – | peak memory 30.4 GiB, mean power 58 W from 16 samples | – |
| asr-L50-alone | 02:41:34–02:43:53 | 0 | peak memory 30.7 GiB, mean power 207 W from 107 samples | `docs/b70-batch10/20261117-0236/asr-L50-alone.out` |
| qwen3-32b-awq-draft-from-recording | 02:43:53–02:46:09 | 0 | peak memory 30.7 GiB, mean power 228 W from 105 samples | `docs/b70-batch10/20261117-0236/qwen3-32b-awq-draft-from-recording.out` |
| asr + 4 rooms at once | 02:46:09–02:50:44 | – | peak memory 30.7 GiB, mean power 229 W from 213 samples | `docs/b70-batch10/20261117-0236/asr-L50-under-load.out`, `docs/b70-batch10/20261117-0236/qwen3-32b-awq-long-4rooms-asrload.out` |

Power and memory are for the whole card while the step ran (both servers loaded).

## Network endpoints (not loopback)

Idle baseline (until 02:37:12): 2 remote address(es), the machine's own traffic before any server started.

**9 new remote address(es) during the run** (check each: the first weight check, the remote-access session reconnecting, or something else):

- 02:37:46 tcp [::ffff:192.168.2.2]:38003 -> [::ffff:192.168.2.2]
- 02:37:47 tcp 192.168.2.2:47978 -> 192.168.2.2
- 02:43:49 tcp 192.168.2.2:40312 -> 91.189.91.82
- 02:43:49 tcp 192.168.2.2:56516 -> 185.125.190.23
- 02:43:49 tcp 192.168.2.2:42594 -> 91.189.90.40
- 02:43:49 tcp 192.168.2.2:47696 -> 99.86.101.2
- 02:43:49 tcp 192.168.2.2:42720 -> 3.163.189.119
- 02:45:10 tcp 192.168.2.2:51746 -> 34.120.177.193
- 02:49:47 udp 192.168.2.2:51904 -> 192.168.2.1

## Result files

- docs/asr-eval/b70-whisper-L50-tts-20261117-0236.json
- docs/asr-eval/b70-whisper-L50-tts-20261117-0236.md
- docs/asr-eval/b70-whisper-L50-tts-load-20261117-0236.json
- docs/asr-eval/b70-whisper-L50-tts-load-20261117-0236.md
- docs/benchmarks/b70-qwen3-32b-awq-L50-4rooms-asrload-20261117-0236-review.csv
- docs/benchmarks/b70-qwen3-32b-awq-L50-4rooms-asrload-20261117-0236.json
- docs/benchmarks/b70-qwen3-32b-awq-L50-4rooms-asrload-20261117-0236.md
- docs/benchmarks/b70-qwen3-32b-awq-T50-20261117-0236-review.csv
- docs/benchmarks/b70-qwen3-32b-awq-T50-20261117-0236.json
- docs/benchmarks/b70-qwen3-32b-awq-T50-20261117-0236.md

## Timeline

- 02:36:42 monitor: xpu-smi on card 0 -> docs/b70-batch10/20261117-0236/monitor.csv
- 02:36:42 idle baseline: 30 s
- 02:37:12 starting Whisper (OFFLINE=1 PRIVILEGED=0)
- 02:39:13 Whisper ready
- 02:39:13 starting qwen3-32b-awq: 32k context, fp8 context cache
- 02:41:14 qwen3-32b-awq ready (OFFLINE=1 PRIVILEGED=0)
- 02:41:34 step asr-L50-alone: python3 scripts/asr_eval.py /home/devcloud/L50-tts.ogg --session C002-L50 --segments 1-339 --save-as C002-T50 --label b70-whisper-L50-tts-20261117-0236
- 02:43:53 step asr-L50-alone finished (exit 0)
- 02:43:53 step qwen3-32b-awq-draft-from-recording: python3 scripts/benchmark.py --sessions C002-T50 --label b70-qwen3-32b-awq-T50-20261117-0236
- 02:46:09 step qwen3-32b-awq-draft-from-recording finished (exit 0)
- 02:46:09 step asr-under-load: recording transcribed while 4 rooms draft
- 02:50:44 step asr-under-load finished
