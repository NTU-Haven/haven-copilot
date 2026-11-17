# b70-qwen3-32b-awq-L50-4rooms-asrload-20261117-0236

- Engine: `openai-compat:qwen3-32b-awq`
- Date: 2026-11-17T02:50:44+00:00
- Machine: Linux-7.0.0-34-generic-x86_64-with-glibc2.43
- Output tok/s = completion tokens ÷ model time, so it includes reading the prompt.

- Each session run 1 time(s); times and counts are medians, Supported shows the range across runs in brackets.

| Session | Speech | Total | Writer | Verifier | Output tok/s | Supported |
| --- | --- | --- | --- | --- | --- | --- |
| C002-L50 | 2967.4 s | 140.7 s | 126.3 s | 14.4 s | 16.2 | 115/131 |

**4 rooms at once:** all notes done in 133.9 s (slowest session 133.9 s), 158.3 output tokens/s in total.

Sentences the verifier did not pass: 16, listed in `b70-qwen3-32b-awq-L50-4rooms-asrload-20261117-0236-review.csv` for clinical review (`scripts/review_summary.py` counts real errors caught vs. correct sentences blocked).
