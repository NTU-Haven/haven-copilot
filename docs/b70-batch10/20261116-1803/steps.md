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
