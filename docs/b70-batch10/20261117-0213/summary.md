# Batch 10 on the B70: 20261117-0213

- Commit: `8eb605c`
- Card: 0 (one card only, as in the final)
- Steps: asr,asrload
- Whisper large-v3-turbo on the same card at UTIL=0.12; qwen3-32b-awq at UTIL=0.8, 32k context, fp8 cache
- Writer parts at once: 4
- Recording: /home/devcloud/L50-tts.ogg (7.0M), synthetic speech
- Servers ran with OFFLINE=1 PRIVILEGED=0

## Steps

| Step | Time | Exit | Card during the step | Output |
| --- | --- | --- | --- | --- |
| idle, no servers | 02:13:29 | – | peak memory 5.0 GiB, mean power 57 W from 23 samples | – |
| both servers loaded, idle | 02:18:02 | – | peak memory 30.4 GiB, mean power 58 W from 16 samples | – |
| asr-L50-alone | 02:18:22–02:24:06 | 0 | peak memory 30.8 GiB, mean power 221 W from 266 samples | `docs/b70-batch10/20261117-0213/asr-L50-alone.out` |
| qwen3-32b-awq-draft-from-recording | 02:24:06–02:26:45 | 0 | peak memory 31.0 GiB, mean power 229 W from 124 samples | `docs/b70-batch10/20261117-0213/qwen3-32b-awq-draft-from-recording.out` |
| asr + 4 rooms at once | 02:26:45–02:33:39 | – | peak memory 31.0 GiB, mean power 225 W from 321 samples | `docs/b70-batch10/20261117-0213/asr-L50-under-load.out`, `docs/b70-batch10/20261117-0213/qwen3-32b-awq-long-4rooms-asrload.out` |

Power and memory are for the whole card while the step ran (both servers loaded).

## Network endpoints (not loopback)

Idle baseline (until 02:13:59): 4 remote address(es), the machine's own traffic before any server started.

**4 new remote address(es) during the run** (check each: the first weight check, the remote-access session reconnecting, or something else):

- 02:15:07 udp 192.168.2.2:56240 -> 192.168.2.1
- 02:15:09 tcp 192.168.2.2:46000 -> 34.120.177.193
- 02:29:34 tcp 192.168.2.2:54982 -> 151.101.192.223
- 02:30:49 udp 192.168.2.2:40427 -> 67.210.96.32

## Result files

- docs/asr-eval/b70-whisper-L50-tts-20261117-0213.json
- docs/asr-eval/b70-whisper-L50-tts-20261117-0213.md
- docs/asr-eval/b70-whisper-L50-tts-load-20261117-0213.json
- docs/asr-eval/b70-whisper-L50-tts-load-20261117-0213.md
- docs/benchmarks/b70-qwen3-32b-awq-L50-4rooms-asrload-20261117-0213-review.csv
- docs/benchmarks/b70-qwen3-32b-awq-L50-4rooms-asrload-20261117-0213.json
- docs/benchmarks/b70-qwen3-32b-awq-L50-4rooms-asrload-20261117-0213.md
- docs/benchmarks/b70-qwen3-32b-awq-T50-20261117-0213-review.csv
- docs/benchmarks/b70-qwen3-32b-awq-T50-20261117-0213.json
- docs/benchmarks/b70-qwen3-32b-awq-T50-20261117-0213.md

## Timeline

- 02:13:29 monitor: xpu-smi on card 0 -> docs/b70-batch10/20261117-0213/monitor.csv
- 02:13:29 idle baseline: 30 s
- 02:14:00 starting Whisper (OFFLINE=1 PRIVILEGED=0)
- 02:16:01 Whisper ready
- 02:16:01 starting qwen3-32b-awq: 32k context, fp8 context cache
- 02:18:02 qwen3-32b-awq ready (OFFLINE=1 PRIVILEGED=0)
- 02:18:22 step asr-L50-alone: python3 scripts/asr_eval.py /home/devcloud/L50-tts.ogg --session C002-L50 --segments 1-339 --save-as C002-T50 --label b70-whisper-L50-tts-20261117-0213
- 02:24:06 step asr-L50-alone finished (exit 0)
- 02:24:06 step qwen3-32b-awq-draft-from-recording: python3 scripts/benchmark.py --sessions C002-T50 --label b70-qwen3-32b-awq-T50-20261117-0213
- 02:26:45 step qwen3-32b-awq-draft-from-recording finished (exit 0)
- 02:26:45 step asr-under-load: recording transcribed while 4 rooms draft
- 02:33:39 step asr-under-load finished
