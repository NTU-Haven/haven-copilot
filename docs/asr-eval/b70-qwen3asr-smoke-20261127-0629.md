# Qwen3-ASR smoke test: b70-qwen3asr-smoke-20261127-0629

- Date: 2026-11-27T06:34:03+00:00
- Servers: candidate `qwen3-asr` at http://127.0.0.1:8002; baseline `whisper` at http://127.0.0.1:8001
- Simplified → Traditional before scoring: on (opencc)
- Recordings are not in the repository; full transcripts are in the .json file.

| Recording | Server | Format | HTTP | Seconds | Segments (timestamps) | Chars | CER | Coverage | Last lines found | Error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| L50-tts.ogg | candidate `qwen3-asr` | verbose_json | 400 | 0.09 | 0 | 0 | – | – | – | {"error":{"message":"Currently do not support verbose_json for qwen3-asr","type" |
| L50-tts.ogg | candidate `qwen3-asr` | json | 200 | 11.14 | 0 | 10529 | 1.2% | 1.001 | 5/5 |  |
| L50-tts.ogg | candidate `qwen3-asr` | text | 200 | 7.81 | 0 | 10528 | 1.2% | 1.001 | 5/5 |  |
| L50-tts.ogg | baseline `whisper` | verbose_json | 200 | 9.01 | 553 | 10151 | 7.5% | 0.965 | 5/5 |  |
| L50-tts.ogg | baseline `whisper` | json | 200 | 8.47 | 0 | 8519 | 20.5% | 0.81 | 5/5 |  |
| L50-tts.ogg | baseline `whisper` | text | 200 | 8.34 | 0 | 8519 | 20.5% | 0.81 | 5/5 |  |

Step 0 passes when the candidate answers at least one format for every recording, the long recording's coverage is close to the baseline's and its last lines are found, and no request fails. Segments = 0 means no timestamps (expected for Qwen3-ASR): Haven would need the hybrid design.
