# Haven — demo video script (≤ 3:00), v3: the MVP flow

English voice-over over the deck and a screen recording. About 380 words of narration at ~140 words per minute, plus screen time without narration, comes to about 2:55.

> v3 replaces the Stage I script (October 2026), which showed the risk panel, the checklist and the outcome charts. Those are off by default now (`HAVEN_CLINICAL_AIDS`), so the demo follows the MVP flow: upload, processing view, review with transcript editing, playback and placeholders, 「我已依目前逐字稿核對」, sign, export, audio cleared. The Stage I version is in the git history.

**What needs what.** Each segment is marked:
- **[demo engine]** works on a laptop with `HAVEN_LLM=demo` and no model; the note is the rule engine's quotes of the transcript.
- **[B70]** needs the local servers (Whisper, the note model and, for the hybrid, Qwen3-ASR). Record these segments on the B70 only after [docs/release-gate/README.md](release-gate/README.md) has been run and passed; **that run is still pending**, so nothing in this script may be presented as a measured result until it has a result file. Deployment: [docs/deploy/b70.md](deploy/b70.md) (commands written, not yet run).

**Before recording**
- Clean state: `python3 scripts/reset_demo.py`. A note signed in an earlier run stays signed, so do this again after every practice run. It also plants the 「安排下週五提前會談。」 error in C001-S04 when the demo engine writes the note.
- For the upload segments, use a **simulated or synthetic** recording only (for example the synthetic `L50-tts.ogg`, or a recording two team members read from `python3 scripts/print_script.py C001-S04`), saved as m4a, mp3 or wav. Do **not** record the upload in the browser: playback is disabled for browser recordings. Tick 「這是模擬會談」.
- Create two therapist accounts and one admin only if you want to show access control (`scripts/add_user.py`); otherwise run without accounts on `127.0.0.1`.
- Keep the audio kept long enough: default `HAVEN_AUDIO_TTL_HOURS=72` is fine; leave `HAVEN_AUDIO_DELETE_ON_CONFIRM` at its default (1) so the last step shows the audio being cleared.
- Set the browser to about 1400 px wide and 110–125% zoom so the Chinese text is readable in a 1080p video.

---

## 0:00–0:12 · Hook — slide 1 (cover)

**ON SCREEN (first 3 s, small caption):** *Content note: includes a simulated conversation about suicidal thoughts.*

**VO:**
Every week, therapists write notes about the most private conversations people ever have. Most AI tools that help them send those conversations to the cloud. Haven doesn't.

## 0:12–0:32 · Problem and solution — slides 2 to 4

**VO:**
Therapists have to keep session records, and the writing never stops. Health data is a special category under Taiwan's data protection law, and the leading AI note tools are cloud services. Haven is an AI documentation assistant that runs on one machine inside the clinic. It transcribes the session and drafts a note in which every sentence cites the transcript. The therapist decides what goes in the record.

*(Keep the legal wording as a general statement. The article numbers and the retention period have not been checked against the current law; see README "Safety Boundary".)*

## 0:32–0:52 · Upload and the processing view — **[demo engine]** for the screens, **[B70]** for real timings

**ON SCREEN:**
1. Client page → 錄音或上傳錄音. Choose 完整晤談錄音, tick the consent box and 這是模擬會談, upload the file.
2. The page goes straight to the processing view: 轉寫 → 說話者 → 草稿. Point at the stage names. (Without speech-to-text installed, show this on the B70 recording or skip.)

**VO:**
The therapist uploads a recording with the client's consent. Processing runs in the background on the clinic machine, so the therapist can leave the page. Speech to text, a guess at who is speaking, then the draft.

## 0:52–1:30 · Review: transcript, playback, placeholders — **[B70]** for a real note; **[demo engine]** shows the same controls

**ON SCREEN:**
1. Review page: transcript on the left, note on the right. Click a note sentence's segment number so the transcript lines behind it highlight.
2. Press the play button on one line: the original audio plays from that line. Point at the bar showing how many hours the audio will be kept.
3. Edit a transcript line. It shows 「已修改」, the sentences that cite it show 「來源已修改，請重新核對」, a banner appears.
4. Scroll to the template's judgement fields (QUAL: 心理師評估與概念化, 後續計畫): the placeholder 「待心理師填寫」. Write a sentence of your own.
5. Show the red sentence and its reason; on the demo engine this is the planted 「安排下週五提前會談。」 (時間詞「下週」不在引用的逐字稿中). Fix it to 「下次會談提前到這週五。」 and press 重新驗證.

**VO:**
In the review page every sentence points to the lines it came from, and a sentence the transcript doesn't back is held. The therapist can listen back to the original audio from any line, and correct the transcript. The fields where judgement lives, the assessment and the plan, are never written by the model. They start empty, as a placeholder for the therapist.

## 1:30–1:55 · Check against the current transcript, then sign — same screen

**ON SCREEN:**
1. Press **我已依目前逐字稿核對** (the signing is refused by the server until this is done, because a transcript line was changed).
2. Press **核對完成，簽核**. The dialog lists the sentences kept after review; tick 「我已逐句核對」, enter a name, confirm.
3. The page shows the signed note, read-only, with its hashes.

**VO:**
After the transcript was corrected, the server refuses to sign until the therapist says they re-read the note against it. Signing is decided by the server, not the browser, and a signed note is locked. Corrections are added as addenda.

## 1:55–2:15 · Export and the audio is cleared

**ON SCREEN:**
1. Press **匯出 Markdown**; open the file: template fields, signer, time, hashes and the statement 「本紀錄由 AI 輔助產生草稿，經心理師核對確認」, no transcript. Then 「標記為已歸檔」.
2. The page now says 「原始錄音已於 … 清除（原因：已確認）」; the transcript and the note remain. The play buttons are off.

**VO:**
The note exports as Markdown or plain text, without the transcript, ready for the clinic's own record system. The therapist marks it filed. And the original recording, kept only so the therapist could check, is deleted when the note is signed, or after a time limit.

## 2:15–2:35 · Architecture and hardware — slides 8 and 9

**VO:**
Everything runs on one machine in the clinic, and Haven's code refuses any address outside the local network. On the ASUS UGen Arc Pro B70, 32 gigabytes holds a 32-billion-parameter model and speech recognition together.

*(Say "B-seventy". The memory and four-room figures are from the 2026-11-14 and 2026-11-17 runs in docs/RESULTS.md; do not add numbers that are not there.)*

## 2:35–2:52 · Limits — slide 11

**ON SCREEN:** a slide with four lines: *Tested on public datasets and synthetic speech, not real counselling sessions · Taiwanese Hokkien not tested · Mixed Chinese and English improved only modestly · End-to-end release run on the B70: pending*.

**VO:**
We are open about what isn't proven. Our speech tests used public datasets and synthetic voices, not real counselling sessions. Taiwanese is untested, mixed Chinese and English improved only a little, and the full run on the clinic machine is still to be done.

## 2:52–3:00 · Close — slide 14

**ON SCREEN:** GitHub link. End card: *Haven 心港 · Team NTU心港*. Small line underneath: *In Taiwan, the 1925 安心專線 is available 24 hours a day.*

**VO:**
Haven: AI help with the paperwork, without the data ever leaving the room. Thank you.

---

## Optional segment (cut first, not in the 3:00): clinical aids on

Start with `HAVEN_CLINICAL_AIDS=1` and open C001-S04: the outcome reminders on 小安's page, the risk panel with exact quotes and the checklist. These are keyword and rule based, off by default, and their word lists have not been checked by a licensed psychologist. Do not present them as risk assessment.

## Notes

- **Timing.** If you run long, cut the optional segment, then the export-file view. Never cut the held sentence and its fix, or the audio-cleared line: they show the verification and the audio policy.
- **Pronunciation.** Say "B-seventy".
- **Upload.** Upload to YouTube as **Unlisted**. Burn in subtitles or upload an .srt. Put the YouTube and GitHub links in the submission form.
- **Honesty checks before publishing.** No number on a slide may be missing from docs/RESULTS.md or docs/release-gate/. The release gate and the golden-set run are pending; do not say "tested end to end" until their result files exist.

---

## Audio to record

The video is assembled from deck slides, screen captures, captions and these voice-over files. Nobody appears on camera. Record in a quiet room with a phone held about 20 cm away, as WAV or M4A, with 1 second of silence before and after each take.

| File | Content | Length |
|---|---|---|
| `vo-01-hook.m4a` | 0:00–0:12 VO | ~10 s |
| `vo-02-problem-solution.m4a` | 0:12–0:32 VO | ~20 s |
| `vo-03-upload.m4a` | 0:32–0:52 VO | ~15 s |
| `vo-04-review.m4a` | 0:52–1:30 VO | ~28 s |
| `vo-05-sign.m4a` | 1:30–1:55 VO | ~18 s |
| `vo-06-export-audio.m4a` | 1:55–2:15 VO | ~18 s |
| `vo-07-hardware.m4a` | 2:15–2:35 VO | ~10 s |
| `vo-08-limits.m4a` | 2:35–2:52 VO | ~15 s |
| `vo-09-close.m4a` | 2:52–3:00 VO | ~6 s |

If a VO file runs longer than its length, that's fine: the visuals are timed to the audio afterwards, and the "Timing" note says what to cut.
