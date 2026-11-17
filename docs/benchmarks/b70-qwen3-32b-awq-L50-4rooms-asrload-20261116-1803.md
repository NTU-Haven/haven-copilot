# b70-qwen3-32b-awq-L50-4rooms-asrload-20261116-1803

- Engine: `openai-compat:qwen3-32b-awq`
- Date: 2026-11-16T18:21:29+00:00
- Machine: Linux-7.0.0-34-generic-x86_64-with-glibc2.43
- Output tok/s = completion tokens ÷ model time, so it includes reading the prompt.

- Each session run 1 time(s); times and counts are medians, Supported shows the range across runs in brackets.

| Session | Speech | Total | Writer | Verifier | Output tok/s | Supported |
| --- | --- | --- | --- | --- | --- | --- |
| C002-L50 | 2967.4 s | 109.4 s | 88.9 s | 20.4 s | 22.2 | 116/141 |

**4 rooms at once:** all notes done in 135.1 s (slowest session 135.1 s), 182.7 output tokens/s in total.

Sentences the verifier did not pass: 25, listed in `b70-qwen3-32b-awq-L50-4rooms-asrload-20261116-1803-review.csv` for clinical review (`scripts/review_summary.py` counts real errors caught vs. correct sentences blocked).
