# Speech-to-text results

`scripts/asr_eval.py` writes one `<label>.md` and `<label>.json` here per run.

## Runs

| Label | Where | Result files in this repository |
| --- | --- | --- |
| `b70-whisper-turbo-real-voices` | Arc Pro B70, 2026-11-10 | [b70-whisper-turbo-real-voices.md](b70-whisper-turbo-real-voices.md) and `.json`, copied from the B70 on 2026-11-13 unchanged |

### b70-whisper-turbo-real-voices

- Speech-to-text: whisper-large-v3-turbo served by vLLM (the result file names it `openai:whisper`, the served model name), no prompt
- Recording: C001-S04 lines #10–#12 read aloud by two people (real voices), 23.0 s
- Character error rate: 14.1% against the script (85 characters). Some of the difference may be the readers' own wording (「好累喔」, 「吧」, and 「我想多了解一點」 missing); the recording was not kept, so reading slips and mishearing cannot be told apart. The result file lists both texts
- Transcription time: 0.5 s
- Speaker guess: 3 of 3 segments labelled as in the script, by Qwen3-32B-AWQ, in 2.3 s

Made with the code of commit 6cf8441, before the 2026-11-12 change that passes segments to the speaker model as JSON.

What this run is and is not: one 23-second clip of three utterances shows that the path works end to end on the card. It is not an accuracy figure for 50-minute sessions, it says nothing about overlapping speech or a quiet client, and 14.1% is above the 10% target in the README.

## Still to measure (needs the B70)

- A recording of 10 minutes or more: character error rate, real-time factor, and the time from recording to draft note.
- Speaker guessing on a full session, against the script.

## Alignment hybrid on the 46-minute L50 recording (to run on the B70)

Ticket 12 adds `HAVEN_ASR_REFINE=1` (Whisper's segments and times, Qwen3-ASR's text; see `haven/asr_refine.py`). Run on the B70 on 2026-11-27 (results below). To compare with the evaluation (`docs/specs/asr-qwen3-evaluation.md` §3.1), run on the B70 with Whisper on :8001 and Qwen3-ASR on :8002 (`scripts/b70-vllm.sh`). `scripts/asr_eval.py` goes through `transcript.transcribe_audio` and computes the character error rate against the script. The reference is all 339 segments of `C002-L50`:

```bash
# Whisper only (baseline)
HAVEN_ASR=openai HAVEN_ASR_URL=http://127.0.0.1:8001 HAVEN_LLM=demo \
  python3 scripts/asr_eval.py ~/L50-tts.ogg --session C002-L50 --segments 1-339 --label b70-L50-whisper-only

# Alignment hybrid
HAVEN_ASR=openai HAVEN_ASR_URL=http://127.0.0.1:8001 \
HAVEN_ASR_REFINE=1 HAVEN_ASR_REFINE_URL=http://127.0.0.1:8002 HAVEN_ASR_REFINE_MODEL=qwen3-asr HAVEN_LLM=demo \
  python3 scripts/asr_eval.py ~/L50-tts.ogg --session C002-L50 --segments 1-339 --label b70-L50-hybrid
```

Compare `cer` in the two `.md`/`.json` files with the evaluation's figures (Whisper 7.4%, Qwen3-ASR alone 1.1% on L50). The run prints, on stderr, how many segments took Qwen3-ASR's words and how many seam full stops were dropped. Check by eye that no segment is empty or starts with punctuation and that segment times equal the Whisper-only run. `HAVEN_LLM=demo` makes the speaker step rule-based; its speaker count in the report is not meaningful here.

### Result on the B70 (2026-11-27, commit f32b9f6, dual-card deployment)

| Run | CER | Transcription time | Report |
|---|---|---|---|
| Whisper only | 7.5% | 9.0 s | `b70-L50-whisper-only.md` |
| Alignment hybrid | **4.8%** | 17.1 s | `b70-L50-hybrid.md` |
| (evaluation) Qwen3-ASR alone, no timestamps | 1.1% | 7.5 s | `b70-asr-bench-l50-snap.md` |

- Segment ids and start/end times are identical in both runs (553 segments); no segment is empty or starts with punctuation. 371 of 553 segment texts changed.
- The hybrid does not reach Qwen3-ASR's own 1.1%. Where the ~530 character errors are (difflib over the whole text, punctuation removed):
  - **Speech Whisper did not segment.** Where Whisper dropped whole sentences (53 and 42 characters in two places), and at the end of the recording, where Whisper wrote a caption hallucination (「請不吝點贊 訂閱 轉發 打賞支援明鏡與點點欄目」) *over* about 200 characters of real speech, Qwen3-ASR heard the words, but the alignment only fills Whisper's own segments, so they are lost.
  - **One duplicated passage** of 80 characters (「…我最近也很忙…我們可以一起找時間」) appears in two segments.
  - The rest: ordinary word errors and Qwen3-ASR's own mistakes.
- **Rerun with the hallucination filter (2026-11-28, `b70-L50-hybrid-v2`, commit 9f60477): CER 4.8%, unchanged.** The hallucinated segment is gone (552 segments), but the ~200 characters it covered are still missing, so the error only changes kind (wrong text → missing text). An earlier note here said the hallucination accounted for ~2 points; that was a misreading of the diff. The real gain is to place Qwen3-ASR words that fall between or under unusable Whisper segments, not to delete the hallucination.
- Seen once: a full stop inside a word (「跟他對。抗」), and Qwen3-ASR's own semantic error from the evaluation (「覺察到這一點」→「覺得查到九一點」).
- Conclusion: consistent with the evaluation (hybrid between Whisper and Qwen3-ASR alone, times unchanged). The known gap is Qwen3-ASR words with no Whisper segment to go into (dropped or hallucinated stretches), plus one seam duplicate.
