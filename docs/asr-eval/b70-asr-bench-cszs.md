# ASR benchmark: b70-asr-bench-cszs

- Date: 2026-11-27T08:19:01+00:00
- Candidate: `qwen3-asr` at http://127.0.0.1:8002 (response_format json; no timestamps)
- Baseline: `whisper` at http://127.0.0.1:8001 (response_format verbose_json, as Haven sends it)
- Simplified → Traditional before scoring: on (opencc s2twp)
- Sets: CAiRE/ASCEND test (Hong Kong bilingual speakers, spontaneous; CC BY-SA 4.0) and ky552/cszs_zh_en test (code-switched sentences; CC BY 4.0). Not Taiwan Mandarin, not counselling speech, no Taiwanese.
- Micro-averaged over each category. Lower is better.

| Category | Utterances | MER Qwen3-ASR | MER Whisper | CER Qwen3-ASR | CER Whisper | WER Qwen3-ASR | WER Whisper | Failed (Q / W) | Seconds (Q / W) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cszs-mixed | 39 | 19.4% | 23.5% | 34.1% | 35.8% | 22.0% | 24.7% | 0 / 0 | 16.3 / 8.5 |
| all | 39 | 19.4% | 23.5% | 34.1% | 35.8% | 22.0% | 24.7% | 0 / 0 | 16.3 / 8.5 |

## Where Qwen3-ASR did worst relative to Whisper

| Id | Reference | Qwen3-ASR | Whisper |
| --- | --- | --- | --- |
| cszs-2607 | 马尔松 is a great challenge that requires both physical and mental endurance. | 马尔松是一项巨大的挑战，它需要两者：身体和精神的耐力。 | MARZONE is a great challenge that requires both physical and mental endurance. |
| cszs-2686 | 安峰山 is the Vice Director of the Press Office. | 安峰山是副部长，是新闻办公室。 | 安 Fengshan is the Vice Director of the Press Office |
| cszs-1501 | 韦斯珀镇区 is a town in Lincoln County, Kansas, under the jurisdiction of the United  | 韦斯珀镇区是美国堪萨斯州林肯县的一个镇。 | 維斯波政區 is a town in Lincoln County, Kansas, under the jurisdiction of United Stat |
| cszs-1264 | Latin letters的传播和Christianity的传播密不可分. | 拉丁字母的传播和基督教的传播密不可分。 | Latin Letters的传播和Christianity的传播密不可分 |
| cszs-1659 | 该物种的模式产地在Malacca. | 该物种的模式产地在马来西亚。 | 该物种的模式产地在Malaka |
