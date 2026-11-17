# b70-qwen3-32b-awq-L50-4rooms-asrload-20261117-0213

- Engine: `openai-compat:qwen3-32b-awq`
- Date: 2026-11-17T02:31:22+00:00
- Machine: Linux-7.0.0-34-generic-x86_64-with-glibc2.43
- Output tok/s = completion tokens ÷ model time, so it includes reading the prompt.

- Each session run 1 time(s); times and counts are medians, Supported shows the range across runs in brackets.

| Session | Speech | Total | Writer | Verifier | Output tok/s | Supported |
| --- | --- | --- | --- | --- | --- | --- |
| C002-L50 | 2967.4 s | 129.6 s | 111.4 s | 18.2 s | 17.5 | 86/105 |

**4 rooms at once:** all notes done in 147.0 s (slowest session 147.0 s), 153.3 output tokens/s in total.

Sentences the verifier did not pass: 19, listed in `b70-qwen3-32b-awq-L50-4rooms-asrload-20261117-0213-review.csv` for clinical review (`scripts/review_summary.py` counts real errors caught vs. correct sentences blocked).
