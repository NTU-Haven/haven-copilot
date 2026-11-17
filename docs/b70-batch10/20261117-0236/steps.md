| Step | Time | Exit | Card during the step | Output |
| --- | --- | --- | --- | --- |
| idle, no servers | 02:36:42 | – | peak memory 0.0 GiB, mean power 57 W from 23 samples | – |
| both servers loaded, idle | 02:41:14 | – | peak memory 30.4 GiB, mean power 58 W from 16 samples | – |
| asr-L50-alone | 02:41:34–02:43:53 | 0 | peak memory 30.7 GiB, mean power 207 W from 107 samples | `docs/b70-batch10/20261117-0236/asr-L50-alone.out` |
| qwen3-32b-awq-draft-from-recording | 02:43:53–02:46:09 | 0 | peak memory 30.7 GiB, mean power 228 W from 105 samples | `docs/b70-batch10/20261117-0236/qwen3-32b-awq-draft-from-recording.out` |
| asr + 4 rooms at once | 02:46:09–02:50:44 | – | peak memory 30.7 GiB, mean power 229 W from 213 samples | `docs/b70-batch10/20261117-0236/asr-L50-under-load.out`, `docs/b70-batch10/20261117-0236/qwen3-32b-awq-long-4rooms-asrload.out` |
