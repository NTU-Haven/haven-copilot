# ASR benchmark: b70-asr-bench

- Date: 2026-11-27T08:11:30+00:00
- Candidate: `qwen3-asr` at http://127.0.0.1:8002 (response_format json; no timestamps)
- Baseline: `whisper` at http://127.0.0.1:8001 (response_format verbose_json, as Haven sends it)
- Simplified → Traditional before scoring: on (opencc s2twp)
- Sets: CAiRE/ASCEND test (Hong Kong bilingual speakers, spontaneous; CC BY-SA 4.0) and ky552/cszs_zh_en test (code-switched sentences; CC BY 4.0). Not Taiwan Mandarin, not counselling speech, no Taiwanese.
- Micro-averaged over each category. Lower is better.

| Category | Utterances | MER Qwen3-ASR | MER Whisper | CER Qwen3-ASR | CER Whisper | WER Qwen3-ASR | WER Whisper | Failed (Q / W) | Seconds (Q / W) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ascend-en | 12 | 33.3% | 114.8% | – | – | 29.6% | 81.5% | 0 / 0 | 3.0 / 1.2 |
| ascend-mixed | 8 | 14.3% | 13.6% | 12.5% | 10.0% | 40.0% | 40.0% | 0 / 0 | 3.3 / 1.2 |
| ascend-zh | 40 | 8.1% | 17.8% | 8.1% | 17.8% | – | – | 0 / 0 | 10.2 / 3.8 |
| long-zh | 1 | 1.1% | 7.4% | 1.1% | 7.4% | 66.7% | 33.3% | 0 / 0 | 8.3 / 8.4 |
| all | 61 | 1.6% | 8.3% | 1.4% | 8.2% | 33.8% | 68.8% | 0 / 0 | 24.8 / 14.6 |

## Alignment hybrid on C002-L50:1-339

553 Whisper segments; 2 got no Qwen3-ASR text; median difference between a segment's Qwen3-ASR piece and Whisper's own text 0.0% (this includes Whisper's own errors). Read the sample: a boundary is right when both columns say the same thing.

| Time | Whisper | Hybrid (Qwen3-ASR text) |
| --- | --- | --- |
| 0.0–1.54 | 嗨,最近过得怎么样? | 嗨，最近过得怎么样？ |
| 132.16–134.34 | 听起来你很清楚自己的问题。 | 听起来你很清楚自己的问题。 |
| 304.06–310.68 | 那次经验让我觉得,也许我真的不是做不到,而是我一直在告诉自己做不到。 | 那次经验让我觉得，也许我真的不是做不到，而是我一直在告诉自己做不到。 |
| 378.26–379.58 | 我写下来了 | 我写下来了。 |
| 450.0–452.7 | 这代表你在建立新的对话方式。 | 就代表你在建立新的对话方式。 |
| 591.62–593.7 | 这正是我们练习的部分 | 这正是我们练习的部分。 |
| 674.38–680.74 | 我想要找到一个属于自己的声音,而不是一直被那个害怕的声音控制。 | 我想要找到一个属于自己的声音，而不是一直被那个害怕的声音控制。 |
| 793.1–798.54 | 但我当时真的觉得自己很尴尬,而且之后就不太敢问问题了。 | 但我当时真的觉得自己很尴尬，而且之后就不太敢问问题了。 |
| 960.0–962.84 | 这件事情是不是真的像我想的那样糟糕? | 情是不是真的像我想的那样糟糕？ |
| 1103.04–1104.32 | 你已经在前进了。 | 你已经在前进了。 |
| 1230.0–1231.72 | 这种方法很适合你目前的阶段。 | 方。法很适合你目前的阶段。 |
| 1370.32–1379.8 | 对,我常常会想,如果我跟他说了,他会不会觉得我太小气,或者他会不会觉得我是在针对他? | 对我常常会想，如果我跟他说了，他会不会觉得我太小气？或者他会不会觉得我是在针对他？ |
| 1466.06–1467.44 | 而是对自己负责, | 而是对自己负责。 |
| 1565.92–1568.3 | 我会先想一下 | 我会先想一下， |
| 1720.16–1722.74 | 我不再觉得自己一定要完美 | 我不再觉得自己一定要完美 |
| 1871.98–1873.44 | 这是一个很好的策略。 | 这是一个很好的策略。 |
| 1980.0–2009.3 | 我感到很难受,像是被否定,然后自己也开始怀疑自己,我开始觉得,我永远都不够好,永远都达不到他们的期望。你现在还会这样想吗?有时候会,特别是在我觉得自己做得不够好的时候,我会想,如果我考不好,他们会失望,会觉得我让他们失望了。你觉得他们会失望吗?我不知道,我从来不够。 | 很难受，像是被否定，然后自己也开始怀疑自己。我开始觉得我永远都不够好，永远都达不到他们的期望。你现在还会这样想吗？有时候会，特别是在我觉得自己做的不够好的时候，我会想，如果我考不好，他们会失望，会觉得我让他们失望了。你觉得他们会失望吗？我不知道，我从来不。 |
| 2196.34–2198.58 | 其实就是从那种否定来的, | 其实就是从那种否定来的。 |
| 2334.96–2338.18 | 你觉得这样的改变,会影响你未来的决定吗? | 你觉得这样的改变会影响你未来的决定吗？ |
| 2490.0–2492.32 | 看法中在转变,这很关键。 | 看法正在转变，这很关键。 |

## Where Qwen3-ASR did worst relative to Whisper

| Id | Reference | Qwen3-ASR | Whisper |
| --- | --- | --- | --- |
| ascend-504 | football | 这不。 | football |
| ascend-42 | ok | Okay. | OK |
| ascend-1197 | 那没有 | 能没？ | 那没有 |
| ascend-462 | so uh | 还有呃。 | 所以 |
| ascend-1071 | 就是 [UNK] surfing the internet | 就是开开始服务了，internet。 | 就是开Serving Internet |
