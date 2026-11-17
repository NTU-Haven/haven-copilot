# Batch 10 on the B70: 20261116-1803

- Commit: `a91c85e`
- Card: 0 (one card only, as in the final)
- Steps: long,asr,asrload,repeat,verifier
- Whisper large-v3-turbo on the same card at UTIL=0.12; qwen3-32b-awq at UTIL=0.8, 32k context, fp8 cache
- Writer parts at once: 4
- Recording: /home/devcloud/L50-tts.ogg (7.0M), synthetic speech
- Servers ran with OFFLINE=1 PRIVILEGED=0

## Steps

| Step | Time | Exit | Card during the step | Output |
| --- | --- | --- | --- | --- |
| idle, no servers | 18:03:39 | – | peak memory 0.0 GiB, mean power 58 W from 24 samples | – |
| both servers loaded, idle | 18:08:16 | – | peak memory 30.4 GiB, mean power 57 W from 15 samples | – |
| qwen3-32b-awq-long-1room | 18:08:36–18:10:14 | 0 | peak memory 30.6 GiB, mean power 228 W from 76 samples | `docs/b70-batch10/20261116-1803/qwen3-32b-awq-long-1room.out` |
| qwen3-32b-awq-long-2rooms | 18:10:14–18:13:20 | 0 | peak memory 30.6 GiB, mean power 230 W from 145 samples | `docs/b70-batch10/20261116-1803/qwen3-32b-awq-long-2rooms.out` |
| qwen3-32b-awq-long-4rooms | 18:13:20–18:17:24 | 0 | peak memory 30.6 GiB, mean power 230 W from 189 samples | `docs/b70-batch10/20261116-1803/qwen3-32b-awq-long-4rooms.out` |
| asr-L50-alone | 18:17:24–18:17:24 | 1 | peak memory 30.6 GiB, mean power 229 W from 1 samples | `docs/b70-batch10/20261116-1803/asr-L50-alone.out` |
| asr + 4 rooms at once | 18:17:24–18:21:29 | – | peak memory 30.6 GiB, mean power 230 W from 190 samples | `docs/b70-batch10/20261116-1803/asr-L50-under-load.out`, `docs/b70-batch10/20261116-1803/qwen3-32b-awq-long-4rooms-asrload.out` |
| qwen3-32b-awq-repeat3 | 18:21:29–18:25:04 | 0 | peak memory 30.6 GiB, mean power 230 W from 166 samples | `docs/b70-batch10/20261116-1803/qwen3-32b-awq-repeat3.out` |
| qwen3-32b-awq-verifier | 18:25:04–18:25:15 | 0 | peak memory 30.6 GiB, mean power 241 W from 9 samples | `docs/b70-batch10/20261116-1803/qwen3-32b-awq-verifier.out` |

Power and memory are for the whole card while the step ran (both servers loaded).

## Network endpoints (not loopback)

Idle baseline (until 18:04:09): 2 remote address(es), the machine's own traffic before any server started.

**5 new remote address(es) during the run** (check each: the first weight check, the remote-access session reconnecting, or something else):

- 18:04:43 tcp 192.168.2.2:4096 -> 192.168.2.2
- 18:04:43 tcp [::ffff:192.168.2.2]:45357 -> [::ffff:192.168.2.2]
- 18:05:09 tcp 192.168.2.2:52890 -> 34.120.177.193
- 18:07:52 udp 192.168.2.2:38617 -> 91.189.91.157
- 18:11:38 udp 192.168.2.2:40600 -> 192.168.2.1

## Result files

- docs/benchmarks/b70-qwen3-32b-awq-L50-20261116-1803-review.csv
- docs/benchmarks/b70-qwen3-32b-awq-L50-20261116-1803.json
- docs/benchmarks/b70-qwen3-32b-awq-L50-20261116-1803.md
- docs/benchmarks/b70-qwen3-32b-awq-L50-2rooms-20261116-1803-review.csv
- docs/benchmarks/b70-qwen3-32b-awq-L50-2rooms-20261116-1803.json
- docs/benchmarks/b70-qwen3-32b-awq-L50-2rooms-20261116-1803.md
- docs/benchmarks/b70-qwen3-32b-awq-L50-4rooms-20261116-1803-review.csv
- docs/benchmarks/b70-qwen3-32b-awq-L50-4rooms-20261116-1803.json
- docs/benchmarks/b70-qwen3-32b-awq-L50-4rooms-20261116-1803.md
- docs/benchmarks/b70-qwen3-32b-awq-L50-4rooms-asrload-20261116-1803-review.csv
- docs/benchmarks/b70-qwen3-32b-awq-L50-4rooms-asrload-20261116-1803.json
- docs/benchmarks/b70-qwen3-32b-awq-L50-4rooms-asrload-20261116-1803.md
- docs/benchmarks/b70-qwen3-32b-awq-repeat3-20261116-1803-review.csv
- docs/benchmarks/b70-qwen3-32b-awq-repeat3-20261116-1803.json
- docs/benchmarks/b70-qwen3-32b-awq-repeat3-20261116-1803.md
- docs/verifier-eval/b70-qwen3-32b-awq-20261116-1803.json
- docs/verifier-eval/b70-qwen3-32b-awq-20261116-1803.md

## Timeline

- 18:03:39 monitor: xpu-smi on card 0 -> docs/b70-batch10/20261116-1803/monitor.csv
- 18:03:39 idle baseline: 30 s
- 18:04:09 starting Whisper (OFFLINE=1 PRIVILEGED=0)
- 18:06:10 Whisper ready
- 18:06:10 starting qwen3-32b-awq: 32k context, fp8 context cache
- 18:08:16 qwen3-32b-awq ready (OFFLINE=1 PRIVILEGED=0)
- 18:08:36 step qwen3-32b-awq-long-1room: python3 scripts/benchmark.py --sessions C002-L50 --label b70-qwen3-32b-awq-L50-20261116-1803
- 18:10:14 step qwen3-32b-awq-long-1room finished (exit 0)
- 18:10:14 step qwen3-32b-awq-long-2rooms: python3 scripts/benchmark.py --sessions C002-L50 --rooms 2 --label b70-qwen3-32b-awq-L50-2rooms-20261116-1803
- 18:13:20 step qwen3-32b-awq-long-2rooms finished (exit 0)
- 18:13:20 step qwen3-32b-awq-long-4rooms: python3 scripts/benchmark.py --sessions C002-L50 --rooms 4 --label b70-qwen3-32b-awq-L50-4rooms-20261116-1803
- 18:17:24 step qwen3-32b-awq-long-4rooms finished (exit 0)
- 18:17:24 step asr-L50-alone: python3 scripts/asr_eval.py /home/devcloud/L50-tts.ogg --session C002-L50 --segments 1-339 --save-as C002-T50 --label b70-whisper-L50-tts-20261116-1803
- 18:17:24 step asr-L50-alone finished (exit 1)
- 18:17:24 step asr-under-load: recording transcribed while 4 rooms draft
- 18:21:29 step asr-under-load finished
- 18:21:29 step qwen3-32b-awq-repeat3: python3 scripts/benchmark.py --repeat 3 --label b70-qwen3-32b-awq-repeat3-20261116-1803
- 18:25:04 step qwen3-32b-awq-repeat3 finished (exit 0)
- 18:25:04 step qwen3-32b-awq-verifier: python3 scripts/verifier_eval.py --label b70-qwen3-32b-awq-20261116-1803
- 18:25:15 step qwen3-32b-awq-verifier finished (exit 0)
