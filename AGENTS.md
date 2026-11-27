# AGENTS.md

This file guides coding agents (Claude Code, Codex) working in this repository. It plays the role of CLAUDE.md and is shared across tools.

Haven 心港 is an on-premises AI documentation assistant for psychotherapists in Taiwan. It takes a session recording, transcribes it (Whisper), guesses speakers (local LLM), drafts a SOAP/DAP note in which every sentence cites transcript segments (local Qwen), verifies each sentence against the transcript, and the therapist signs it. UI strings, prompts and lexicons are Traditional Chinese (Taiwan). All data in the repo is simulated.

## Commands

Python 3.10+. The core uses the standard library only. Optional extras: `pip install -r requirements-asr.txt` (faster-whisper and opencc), and `pip install cryptography` (for `HAVEN_ENCRYPT=1`).

```bash
HAVEN_LLM=demo python -m haven                      # rule-based engine, no model; http://127.0.0.1:8765
python scripts/reset_demo.py                        # clean, unsigned demo DB (moves the old haven.db aside)
python -m unittest discover -s tests -v             # full suite (~40 s); tests force HAVEN_LLM=demo
python -m unittest discover -s tests -p test_signing.py        # one file (tests import `helpers`, so run via discover -s tests)
python -m unittest discover -s tests -k test_sign_rules        # one test by name pattern
python scripts/verifier_eval.py                     # verifier regression on data/verifier_eval/cases.json
python scripts/benchmark.py --rooms 4               # per-agent timings, writes docs/benchmarks/
```

On PowerShell set env vars with `$env:HAVEN_LLM = 'demo'`. A single test module run as `python -m unittest tests.test_ui` fails with `No module named 'helpers'`; use `discover -s tests`.

Real models: `HAVEN_LLM=openai HAVEN_LLM_URL=http://127.0.0.1:8000 HAVEN_MODEL=qwen3-32b-awq HAVEN_ASR=openai` (vLLM on the B70, started with `scripts/b70-vllm.sh`; start Whisper first) or `HAVEN_LLM=ollama HAVEN_MODEL=qwen2.5:7b`. All configuration lives in `haven/config.py` (env vars `HAVEN_*`).

### Known test behaviour on Windows
- 5 tests assert POSIX file modes (0600/0700) and fail on NTFS: `test_password_is_stored_as_a_hash`, `test_https_with_the_made_certificate`, `test_database_and_transcripts_are_private`, `test_start_up_tightens_existing_files`, `test_temp_file_is_in_the_data_dir_and_removed_after_success`. This is a platform difference, not a regression.
- `test_ui.DemoWalkthroughTest.test_5_reset_again_*` is intermittent: `scripts/reset_demo.py` names its backup with second resolution, so two resets in one second collide (`FileExistsError` on Windows; on POSIX `rename` would silently overwrite the earlier backup).
- 15 tests skip without `cryptography`/`opencc`; the `.gitignore` test skips outside a git checkout.

## Architecture

The request path is `web/app.js` → `haven/server.py` (one `ThreadingHTTPServer`, hand-rolled routing in `Handler._get` / `Handler._post`) → `haven/orchestrator.py` → `haven/agents/*` → `haven/store.py` (SQLite at `data/haven.db`) and transcript JSON files in `data/sessions/`.

- **Session lifecycle.** Upload (`POST /api/upload`) or browser recording (`/api/recordings` → `chunk` → `finish`) writes audio to a temp file in `data/tmp/`. `_transcribe_and_store` calls `transcript.transcribe_audio`, **always deletes the audio**, runs `SpeakerAgent`, saves the transcript, appends the session to `data/clients.json`, then `_open_session` drafts the note **synchronously in the same HTTP request**. `Orchestrator.process_session` runs writer → verifier → risk → outcome and stores the result in the `results` table. `Orchestrator.refresh` re-runs risk/checklist/verification on an existing draft (after a speaker change, a restore, or a verifier-version change).
- **Drafts vs signed notes.** The working draft lives in `results.payload` (JSON, which also holds `ai_note`, `revision`, `edited_by`). `POST .../verify` saves the edited draft; `_check_revision` rejects stale `base_revision` (409, `conflict=True`). Signing (`server._sign` + `haven/signing.py`) re-checks everything server-side: it trusts only sentence text, `evidence`, `confirmed_by_therapist` and `keep_reason` from the browser, and ignores client-sent `status`/`support`. A signed note goes to `signed_notes` (insert-only; a second insert raises `AlreadySignedError` → 409) together with its audit rows in one transaction. Corrections after signing are addenda only.
- **Verifier** (`agents/verifier.py`): hard rules (numbers, time words, pronouns, risk words and negation) → bigram/clause overlap → one batched LLM check. Verdicts are cached in the `verdicts` table keyed by transcript SHA-256 and `VERIFIER_VERSION`; bump `VERIFIER_VERSION` when rules change so stale drafts are refreshed on open.
- **Templates** (`haven/templates.py`, Template Registry): each template is a definition (id, version, name, fields with key/label/`ai_mode`/instructions/hint, plus `measures_field` and `risk_field`). SOAP and DAP live there; a new template is one `register(...)` call. Writer, signing, server (`?template=`, `GET /api/templates`, `template` in the session view) and app.js section titles read from it; an unknown id raises `UnknownTemplate` → 400. Results store `template_id`/`template_version`.
- **Writer** (`agents/writer.py`): builds the prompt from the template's `generate` fields (human_only fields are never sent); `SYSTEM_PROMPT` holds the global rules. SOAP/DAP end with an `R` (風險與安全) section (`risk_field`), and uncited high-level risk lines are force-appended there. Long transcripts are written in parts of 60 segments in parallel (`HAVEN_WRITE_PARALLEL`). With no LLM, `_rule_based` quotes the transcript.
- **Risk** (`agents/risk.py`) is lexicon/regex only, with no model. **Outcome** (`agents/outcome.py`) computes reliable change from questionnaire totals stored in `clients.json` `scores`. Score sentences are generated server-side and cannot be edited (the signing gate compares the exact text).
- **Speakers**: there is no acoustic diarization. `SpeakerAgent` asks the LLM to label each segment therapist/client (`speaker_source="model"`). `POST .../speakers` sets `speaker_source="confirmed"`, and prepared scripts carry `"script"`. Several rules (risk level, checklist, verifier support) branch on `speaker_source`.
- **LLM/ASR transport** (`haven/llm.py`): every outbound request goes through `assert_local` / `_checked_socket` (loopback or RFC1918 only, no proxy, no redirects). Qwen3 is called with `chat_template_kwargs.enable_thinking=False`, and `_parse_json` strips `<think>` blocks. Do not add new HTTP clients that bypass this opener.
- **Accounts** (`haven/accounts.py`): if `data/users.json` has any user, sign-in is required. `may_open` compares `client.therapist_user` with the username, and an inaccessible client/session answers 404. Admin record access is a time-limited grant with a reason (`_open_records`). Admin endpoints are loopback-only unless `HAVEN_ADMIN_FROM_NETWORK=1`. A non-loopback `HAVEN_HOST` refuses to start without accounts, TLS and `HAVEN_HOSTNAMES` (`lan_problems`).
- **Storage helpers**: write files with `files.write_private` / `read_private` (they handle 0600 and optional AES-GCM via `crypto.seal`/`open_`). `store.audit` appends to a hash-chained `audit_log`; never write audit rows with raw SQL. Audit details hold ids and counts, never clinical content.

## Invariants that tests enforce

- Every `/api/*` request needs the header `X-Haven: 1`; GETs never write state (except audit rows). State-changing actions are POSTs.
- Haven keeps no audio: the temp file is removed on success and on every failure path, and `files.clear_temp()` runs at start.
- Session ids are upper-cased (`_session_id`), and an upload's id must start with its client id.
- Errors are JSON with `error` (English) and `message_zh` (shown in the UI); `Refused` → 409, `BadRequest`/`BadNote` → 400, unknown → 500 with no detail.
- The web UI has no inline scripts/styles (strict CSP) and inserts all text as text (`el()` in app.js), never as HTML.
- Only simulated transcripts are tracked by git (`.gitignore` and `scripts/pre_commit_check.py`). `data/clients.json` is tracked and is modified by uploads and admin actions, so do not commit incidental changes to it.
