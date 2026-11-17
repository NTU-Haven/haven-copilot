| Step | Time | Exit | Card during the step | Output |
| --- | --- | --- | --- | --- |
| idle, no servers | 02:13:29 | – | peak memory 5.0 GiB, mean power 57 W from 23 samples | – |
| both servers loaded, idle | 02:18:02 | – | peak memory 30.4 GiB, mean power 58 W from 16 samples | – |
| asr-L50-alone | 02:18:22–02:24:06 | 0 | peak memory 30.8 GiB, mean power 221 W from 266 samples | `docs/b70-batch10/20261117-0213/asr-L50-alone.out` |
| qwen3-32b-awq-draft-from-recording | 02:24:06–02:26:45 | 0 | peak memory 31.0 GiB, mean power 229 W from 124 samples | `docs/b70-batch10/20261117-0213/qwen3-32b-awq-draft-from-recording.out` |
| asr + 4 rooms at once | 02:26:45–02:33:39 | – | peak memory 31.0 GiB, mean power 225 W from 321 samples | `docs/b70-batch10/20261117-0213/asr-L50-under-load.out`, `docs/b70-batch10/20261117-0213/qwen3-32b-awq-long-4rooms-asrload.out` |
