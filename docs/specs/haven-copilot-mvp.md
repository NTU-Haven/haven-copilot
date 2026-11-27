---
title: Haven Copilot — Hackathon MVP Technical Specification
status: ready-for-agent
planning_date: 2026-11-27
feature_freeze: 2026-12-03
submission: 2026-12-04
baseline: main @ 127ed3b (verified 2026-11-27; HEAD = origin/main = 127ed3b, no new commits)
inputs: docs/22_Grillme_result.md (10 GRILL-ME decisions), MVP Gap Audit 2026-11-22, repository inspection 2026-11-27
---

# Haven Copilot — Hackathon MVP Technical Specification

> 標記慣例：**Assumption**（未經使用者驗證的假設）、**Open Question**（待決但不阻塞）、**Validation Gate**（必須以實測通過才能宣稱）、**Deferred Decision**（刻意延後，採最小可逆設計）。
> 「Verified」只指在本文件寫明的環境與條件下實際通過，Mock／Fake／評測腳本不等於產品 E2E。

---

## 0. Repository Baseline（2026-11-27 確認）

| 項目 | 狀態 |
|---|---|
| Branch / HEAD | `main` @ `127ed3b`，與 `origin/main` 相同；自 11/22 稽核後**沒有新 commit** |
| 工作目錄未提交變更 | `README.md`（Windows Quick Start 文字）、未追蹤 `AGENTS.md`、`REPORT-2026-11-12.md`、`docs/22_Grillme_result.md`。本 Spec 不觸碰這些檔案 |
| 技術棧 | Python 3.10+ 標準函式庫（`http.server.ThreadingHTTPServer`，手寫路由），原生 HTML/CSS/JS（無 `package.json`、無框架、無 bundler），SQLite（`sqlite3`），逐字稿為 JSON 檔 |
| 測試 | 316 個 `unittest`；Windows 上 5 個 POSIX 權限斷言失敗（平台差異）、1 個間歇失敗（`reset_demo.py` 備份檔名秒級撞名，真 bug）、16 個因缺選用依賴而略過 |
| 真實模型證據 | 只有 B70 歷史評測（2026-11-14／17，單卡，評測腳本，非瀏覽器 E2E），未在目前 HEAD 重跑 |

---

## 1. Executive Summary

Haven Copilot 是一套地端執行的 **AI 諮商紀錄助手**：把完整晤談錄音或心理師會後口述，轉成**可編輯、逐句可追溯到逐字稿與原音**的質化紀錄草稿，由心理師核對、修改、確認後匯出到諮商所既有系統。AI 不做診斷、風險分級、量表推測或臨床推論。

現有 `haven-copilot` 已完成約 70% 的骨幹：瀏覽器錄音／分段上傳、Whisper（faster-whisper 或 vLLM）、LLM 說話者推測與人工更正、分段平行草稿生成、逐句引用與三層驗證器、伺服器端確認門檻、樂觀鎖、帳號與個案隔離、hash-chain 稽核。**決策：保留 Repo，增量改造（ADR-001）。**

MVP 的缺口集中在五處：
1. **AI Safety Boundary 衝突**：Risk Agent（風險分級、檢核表、法定通報提示、確認門檻）與 Outcome Agent（量表可靠變化、not-on-track、自動寫入量表句）超出新版產品邊界，需停用／隔離；SOAP/DAP 的 A/P 欄需改為「人工填寫或僅整理已明說內容」。
2. **審閱工作流缺件**：逐字稿文字無法編輯；重新生成會覆蓋人工修改（伺服器端沒有 revision 檢查）；沒有「逐字稿已改、紀錄可能過時」的狀態。
3. **音檔生命週期與設計相反**：ASR 後立即刪除音檔，沒有回放；需改為受控暫存＋授權回放＋到期清除。
4. **沒有匯出**，也沒有「已下載 vs 已歸檔」的區分。
5. **沒有背景工作**：ASR＋說話者＋草稿在同一個 HTTP request 同步執行，沒有 job 狀態、重試或重啟恢復。

估計 P0 剩餘工程量 **約 10–12 person-days**（不含評測與簡報）。以兩名成員加 coding agent、11/27–12/03 計，**可行但無餘裕**；第 14 節列出依序可延後的項目。

---

## 2. Problem Statement

心理師每次晤談後需花時間撰寫質化紀錄（**Assumption**：每次 15–30 分鐘，未經本專案量測）。現有工具二擇一：純 ASR 只給一大段逐字稿，仍需人工整理；雲端 AI 紀錄工具（Upheal、Mentalyc 等，依 2026-10 各官網閱讀）把高度敏感資料交給第三方處理者，且 LLM 摘要可能遺漏、顛倒否定、錯置說話者或捏造臨床判斷，而一份錯誤紀錄可能被保存多年。

小型諮商所（3–10 位心理師）沒有法務或資安人力評估雲端處理者，也需要多位心理師共用一套系統但彼此資料隔離，且不希望管理者預設能讀所有諮商內容。

## 3. Solution（使用者視角）

心理師登入後，為一次晤談選擇「完整晤談錄音」或「會後口述」，在瀏覽器錄音或上傳音檔，勾選同意聲明。系統在背景轉寫、推測說話者、依選定模板產生草稿，處理時頁面顯示進度、失敗時說明原因並可重試。

審閱頁左欄是逐字稿（可回聽任一段原音、可修改文字與說話者），右欄是模板化草稿，每句都標示來源段落；點句子跳到逐字稿，點逐字稿聽原音。AI 寫的、心理師改過的、心理師確認過的內容在畫面上可區分。修改逐字稿後，系統提示哪些句子的來源變了，但**不會**自動改寫心理師已編輯的內容；要重新生成必須明確選擇，舊草稿會保留。

心理師可跨天回來繼續，完成核對後按「確認紀錄」，伺服器重新驗證後鎖定，接著匯出 Markdown／純文字。匯出狀態分為「已下載」與「已歸檔（由心理師標記）」。原音在確認後或到期時依政策清除，清除失敗會重試並在畫面上顯示。

---

## 4. User Stories

**帳號與工作區**
1. As a 心理師, I want to sign in with my own account, so that only I can see the sessions assigned to me.
2. As a 心理師, I want to be signed out after inactivity, so that an unattended screen does not expose session content.
3. As a 心理師, I want a list of my sessions with status (處理中／草稿／需重新核對／已確認／匯出狀態), so that I can resume unfinished work.
4. As a 心理師, I want my edits to persist across sign-out and across days, so that I can finish a note later.
5. As a 諮商所負責人, I want to create accounts and assign clients to therapists without reading session content, so that I can manage the practice without breaching confidentiality.
6. As a 諮商所負責人, I want any exceptional access to records to require a stated reason and be logged, so that access is accountable.

**建立晤談與輸入**
7. As a 心理師, I want to choose between 完整晤談錄音 and 會後口述 when creating a session, so that the system treats the source correctly.
8. As a 心理師, I want to record in the browser, so that I need no separate recorder.
9. As a 心理師, I want to upload an existing audio file, so that I can use a recorder when browser recording is unreliable.
10. As a 心理師, I want to confirm that the client consented to recording before upload, so that consent is captured each time.
11. As a 心理師, I want to see processing progress and a clear failure reason, so that I know whether to wait, retry or re-upload.
12. As a 心理師, I want to retry a failed job without creating a duplicate session, so that failures do not leave junk records.
13. As a 心理師, I want an interrupted job (server restart) to be shown as interrupted and retryable, so that work is not silently lost.
14. As a 心理師 using 會後口述, I want my recap to be recorded as my account, not as the client's words, so that the record does not misattribute statements.

**草稿生成**
15. As a 心理師, I want to choose a note template before generating, so that the draft matches my practice's format.
16. As a 心理師, I want every AI sentence to cite transcript segments, so that I can check it quickly.
17. As a 心理師, I want fields requiring professional judgement left as 待心理師填寫, so that the AI never writes my clinical judgement.
18. As a 心理師, I want information not in the source marked 未提及／待確認, so that gaps are visible rather than filled in.
19. As a 心理師, I want to start reviewing the draft without first correcting the whole transcript, so that review effort stays proportional.
20. As a 心理師, I want to be told when the local model failed and the fallback engine wrote the draft, so that I know the draft's quality.

**審閱與編輯**
21. As a 心理師, I want to click a draft sentence and see its source segments highlighted, so that I can verify it.
22. As a 心理師, I want to click a transcript segment and hear that part of the original audio, so that I can check ASR errors.
23. As a 心理師, I want to correct transcript text without losing the segment's id and timestamps, so that citations and playback still work.
24. As a 心理師, I want to correct and re-correct speaker labels, so that a mistaken confirmation can be fixed.
25. As a 心理師, I want to edit, delete and add draft sentences, so that the note says what I mean.
26. As a 心理師, I want to see which sentences are AI-generated, human-edited or human-confirmed, so that I know what I have reviewed.
27. As a 心理師, I want sentences whose cited segments I changed to be flagged 來源已修改, so that I re-check exactly those.
28. As a 心理師, I want regeneration never to overwrite my edits silently, so that my work is safe.
29. As a 心理師, I want to see 已儲存／尚未儲存, so that I trust my edits are kept.
30. As a 心理師, I want a clear message when another tab or person saved first, so that I do not overwrite their edits.

**確認與匯出**
31. As a 心理師, I want confirmation to be refused while sentences are unsupported or the note is stale against the transcript, so that I cannot confirm by accident.
32. As a 心理師, I want a confirmed note to be read-only, with corrections as addenda, so that the record is stable.
33. As a 心理師, I want to export a confirmed note as Markdown or plain text, so that I can file it in our existing records system.
34. As a 心理師, I want to mark an export as 已歸檔 separately from downloading, so that the system does not claim filing it cannot see.

**音檔生命週期**
35. As a 心理師, I want the original audio kept only until I finish review or a maximum period, so that sensitive audio does not linger.
36. As a 心理師, I want to be told when the audio has expired, and what remains (transcript, draft), so that nothing disappears silently.
37. As a 諮商所負責人, I want audio cleanup failures retried and visible, so that a failed delete is not forgotten.
38. As a 心理師, I want audio playback to require my authorization for that session, so that knowing an id is not enough.

**安全與稽核**
39. As a 心理師, I want another therapist to be unable to read, edit, play, export or delete my sessions even with their ids, so that client data is isolated.
40. As a 諮商所負責人, I want key actions (upload, transcript edit, confirm, export, audio play, audio cleanup) in a tamper-evident audit log without clinical content, so that the practice can account for data handling.
41. As a 評審／買方, I want to see that no session content leaves the machine, so that the privacy claim is credible.

---

## 5. MVP Scope

**P0 — Must Have**（Release Blocker 標 ★）
- ★ AI Safety Boundary：停用 Risk/Outcome 衍生內容；新模板 A/P 判斷欄改人工。
- ★ 至少一種模板（「單次晤談質化紀錄」），模板定義與生成流程分離。
- ★ `input_mode`：完整錄音＋會後口述（口述最小版）。
- ★ 逐字稿文字編輯＋說話者再更正；transcript_revision 與「紀錄可能過時」。
- ★ 重新生成不覆蓋人工修改（伺服器端保證）。
- ★ 音檔受控暫存、授權回放（段落級）、到期／確認後清除、清除重試、稽核。
- ★ 人工確認（沿用現有 sign gate，改名「確認紀錄」，加 staleness 條件）。
- ★ 匯出（Markdown、純文字）＋匯出狀態（未匯出／已下載／已歸檔）。
- ★ 跨心理師授權驗收（Session／Audio／Export／Job id）。
- ★ 真實 ASR → LLM → Draft E2E 在 B70 上跑通至少一次（Live 或 Recorded，明確標示）。
- 背景 Job（狀態查詢、失敗、重試、重啟後標記 interrupted）。若進度落後可降級（見 §14）。
- 基本模型品質驗收（固定案例：未支持的臨床判斷 0、重大否定／時間／說話者錯誤 0）。

**P1 — Should Have**：第二模板（安全版 SOAP）；工作區列表 UX；ASR Benchmark 完整版＋Qwen3-ASR；多心理師並行測試；探索性效率比較；工作資料依政策清除；DOCX 匯出。

**P2 — Deferred**：跨次晤談回顧（含現有 Case Memory 的擴充）；外部病歷 API 整合；自訂模板編輯器；量表人工填寫介面；完整版本歷史 UI；PostgreSQL／FastAPI／前端框架遷移；聲學 diarization；商用部署與法遵審查。

**Explicit Non-goals**：AI 臨床診斷、AI 風險評估、AI 治療建議、LLM 產生量表分數、完整電子病歷、Multi-agent 框架、即時串流 ASR、多機構 SaaS。

---

## 6. System Architecture

Modular Monolith，單一 Python 程序，外加兩個本機模型伺服器（vLLM）。

```
Browser (vanilla JS workspace)
  │  HTTPS (clinic) / SSH tunnel to loopback (hackathon)
  ▼
Haven Python process ── ThreadingHTTPServer, hand-rolled routes
  ├─ Auth & Session (accounts)            [Existing → Reuse]
  ├─ Authorization (may_open, 404 policy) [Existing → Reuse, extend to new ids]
  ├─ Audio Lifecycle (store, ticket, Range, sweeper)      [New]
  ├─ Job Manager (jobs table + worker threads)            [New]
  ├─ Transcript Mgmt (JSON file, segments, revision)      [Modify]
  ├─ Template Registry (template definitions)             [New, from writer TEMPLATES]
  ├─ Note Generation (writer, chunked, citations)         [Modify prompts]
  ├─ Verification (rule layers + batched LLM check)       [Reuse, +recap rules]
  ├─ Revision & Review (revision, staleness, regenerate)  [Modify]
  ├─ Human Approval (signing gate → "Confirm")            [Modify: drop risk gate, add staleness]
  ├─ Export (md/txt, export status)                       [New]
  ├─ Audit (hash-chained audit_log)                       [Reuse]
  ├─ Risk Agent / Outcome Agent / checklist               [Disable behind flag]
  └─ Case Memory (keyword Q&A)                            [Defer: hide in MVP UI]
  │                    │
  ▼                    ▼
SQLite (data/haven.db)   Protected files: data/sessions/*.json, data/audio/<audio_id>.* (0700 dir, 0600 files)
  │
  ▼ only loopback/RFC1918 via llm._checked_socket
vLLM #1 Whisper large-v3-turbo (:8001)    vLLM #2 Qwen3-32B-AWQ (:8000)
```

**Component status**
- Existing → Reuse：accounts、HTTP guard（Host、X-Haven、CSP、錯誤處理）、llm transport、verifier、store/audit、files/crypto、recording chunk protocol、speaker agent、sentence editor UI。
- Modified：server routes（upload/finish 回 job、process 加 revision、sign 加 staleness）、orchestrator（不跑 risk/outcome）、writer（模板來源、口述 prompt、判斷欄）、transcript（`input_mode`、`asr_text`、revision）、app.js（逐字稿編輯、回放、狀態、匯出）。
- New：audio lifecycle module、job module、template registry、export module。
- Disabled：Risk Agent 輸出、風險確認門檻、R 欄強制補引、Outcome alerts／量表句／圖表、法定通報提示。
- Deferred：Case Memory UI、量表輸入、聲學 diarization。

---

## 7. Technology Stack（摘要，詳見 ADR）

| 層 | 決策 |
|---|---|
| Frontend | 沿用原生 HTML/CSS/JS，將 `app.js` 新功能拆成獨立 ES module 檔（審閱頁、音訊、匯出），不導入框架 |
| Backend | 沿用 `http.server`；新功能放新模組，`server.py` 只加路由分派 |
| DB | SQLite；`timeout=30`（已等同 busy_timeout）；新增 `PRAGMA journal_mode=WAL`；新增三張表 |
| Audio | 本機檔案系統 `data/audio/`，DB 只存 metadata |
| ASR | Whisper large-v3-turbo（vLLM）baseline；Qwen3-ASR-1.7B 限時 challenger |
| LLM | Qwen3-32B-AWQ on vLLM 0.26.0 XPU（已實測），thinking off，JSON mode |
| Speakers | 現有 LLM 文字推測＋人工更正（無聲學 diarization） |
| Deploy（hackathon） | Intel Cloud，2×B70；Haven 綁 loopback，評審／組員以 SSH port-forward 開 `http://localhost:8765`（secure context，可用麥克風） |

---

## 8. Model Selection & Inference

### 8.1 ASR
- **Baseline（保留）**：`openai/whisper-large-v3-turbo` 以 vLLM 0.26.0 XPU 服務，`HAVEN_ASR=openai`，`--max-model-len 448`，`VLLM_MAX_AUDIO_DECODE_DURATION_S=10800`，200 MB 上限。**Verified（歷史，B70 單卡，評測腳本）**：46 分鐘合成語音，CER 7.5%（轉繁後），回傳 553 個帶真實時間戳的 segments（段間有間隔，非均分推估），轉寫 9.6 s。真人語音只有一段 23 秒，CER 14.1%。
- **注意**：`transcript._openai_server` 在伺服器只回 `text` 時，會依字數均分時間戳（`_sentences`）。回放功能必須偵測並標示這種「推估時間戳」（見 §11.3）。
- **Challenger**：`Qwen3-ASR-1.7B`。**Unknown**：XPU runtime、是否能由現有 vLLM 映像服務、是否輸出 segment 時間戳（若需另接 Qwen3-ForcedAligner，等同新 pipeline）。不預設支援台語對齊。

### 8.2 ASR Benchmark（時間盒：11/28 一天）
- 素材：6 段台灣華語、6 段中英混用、6 段台語或華台混用、6 段關鍵語意（否定、時間、主詞），1 段 ≥ 30 分鐘模擬晤談；可行時加三語切換。全部為團隊錄製或合成，有授權，非真實個案。
- Ground Truth：人工逐字校對。台語分三種輸出目標分開評：(a) 忠實台語轉寫（台羅或漢字），(b) 台語正字，(c) 華語語意轉換。**不跨目標比較 CER**。
- 指標：中文 CER、英文 WER、混語 MER、關鍵語意錯誤數（人工判定）、漏句／重複、長音訊完整性、時間戳品質（段落起點誤差，抽 20 段人工量）、與說話者推測相容性、E2E latency、GPU 記憶體（`xpu-smi`）、XPU 相容性。
- 沿用 `scripts/asr_eval.py` 的 CER 與輸出格式，新增多語分組（P1 ticket）。

### 8.3 ASR Go / No-Go Gate（11/29 中午前決定）
**已由 [asr-qwen3-evaluation.md](asr-qwen3-evaluation.md)（2026-11-27 文件評估）更新。** 摘要：vLLM v0.26.0 原始碼已有 Qwen3-ASR，而且走 Haven 已在用的 transcription API，所以試用成本低；第三方數據顯示台灣華語和中英混用明顯優於 Whisper-large-v3；但模型**只輸出文字、沒有時間戳**，台語也沒有證據。決策改為：**Whisper 負責切段和時間戳，Qwen3-ASR 以 Hybrid 方式重新轉寫每段文字，條件式採用**。是否採用依評估文件 §5 的六項條件，在 B70 實測後決定。No-Go 時保留 Whisper，評測結果仍作為簡報證據。

### 8.4 LLM
- **配置（Verified 歷史，單卡）**：`Qwen/Qwen3-32B-AWQ`、served name `qwen3-32b-awq`、vLLM 0.26.0（`vllm/vllm-openai-xpu:v0.26.0`）、`--dtype=bfloat16 --enforce-eager --async-scheduling --block-size 64`、與 Whisper 同卡 `UTIL=0.8`、batch 9/10 用 `--max-model-len 32768 --kv-cache-dtype fp8`、`OFFLINE=1 PRIVILEGED=0`。生成參數 `temperature=0.1`、`response_format=json_object`、`chat_template_kwargs.enable_thinking=false`。Tensor parallel：無（單卡）。GPU driver 版本：**Unknown**（repo 沒有記錄，T15 補記）。
- **雙卡方案（Validation Gate）**：Qwen 用 `CARD=0 UTIL=0.9`，Whisper（及 benchmark 時的 Qwen3-ASR）用 `CARD=1`。好處是 KV cache 更大、ASR 不和草稿搶卡。這個配置未實測，若啟動失敗就回到已驗證的單卡配置。
- 不做 TP=2：32B-AWQ 單卡放得下，TP 在 XPU 上未驗證。

### 8.5 Speakers
Repo **沒有聲學 diarization 模型**。`SpeakerAgent` 用同一個 Qwen 依文字內容逐段猜 therapist/client，標為 `speaker_source="model"`，UI 顯示「（推測）」。模型沒回應時標 `unknown`，不強制推論。MVP 保留這個做法，加上「已確認也可再改」。會後口述模式**不跑** SpeakerAgent。

### 8.6 Generation Pipeline
Transcript segments → Template definition（欄位＋每欄 `ai_mode`）→ 分段 prompt（每段 60 segments，平行 4 段，沿用）→ JSON 輸出 → **結構驗證**（欄位屬於模板、`evidence` 為存在的 segment id、句長限制，沿用 `signing.parse` 的規則）→ **語意驗證**（verifier 三層：硬規則、詞彙重疊、批次 LLM 檢查）→ 可編輯草稿。

處理規則：
- 引用不存在：句子標 `unsupported`，不丟棄（沿用）。
- JSON 失敗或模型沒回應：該段落以規則引擎引述，草稿標示「本機模型未回應」（沿用）。
- 跨段整合：每段獨立生成後合併（沿用；**已知限制**，段落間可能重複，UI 不處理，列入 Risk）。
- 未知資訊：`ai_mode=human_only` 的欄位不送模型，直接填「待心理師填寫」；`generate` 欄位在沒有來源時回空陣列，UI 顯示「未提及」。
- 不使用模型信心分數做任何判斷。

---

## 9. Data Models（最小必要變更）

### 9.1 既有（沿用）
- `results(session_id PK, payload JSON, created_at)`：工作草稿。payload 已含 `note`、`ai_note`、`revision`、`edited_by/at`、`verification`、`transcript` 副本。
- `signed_notes(session_id PK, note, signed_by, signed_at, transcript_sha256, note_sha256, verifier_version)`：= **Confirmed note**，insert-only。
- `addenda`、`verdicts`、`audit_log(hash chain)`。
- `data/sessions/<SID>.json`：Transcript（segments 有穩定整數 `id`、`start`、`end`、`speaker`、`speaker_source`、`text`）。
- `data/users.json`：User（username、display_name、role therapist|admin、scrypt hash）。
- `data/clients.json`：Client（`therapist_user` = 授權心理師，`sessions` 清單）。

### 9.2 變更

**User**：沿用。Organization reference：單一諮商所隱含，**Deferred**。Account status：目前只有「存在／刪除」，MVP 沿用。

**Session（Transcript JSON 加欄位）**
- `input_mode`: `"session"` | `"recap"`（既有檔案缺欄位時視為 `"session"`）
- `transcript_revision`: int，從 0 起，每次文字或說話者修改 +1
- 每個 segment 加 `asr_text`（第一次修改時寫入原 ASR 文字，之後不再變）與 `edited_by`、`edited_at`
- `timestamps_estimated`: bool（ASR 只回 text 時為 true）
- Processing status 放在 `jobs` 表，不放 JSON

**SessionNote（results.payload 加欄位）**
- `template_id`、`template_version`
- `revision`（既有，作為整個 workspace 的 optimistic lock token，所有寫入 API 都檢查）
- `generated_from_transcript_revision`：草稿生成時的 transcript_revision
- `reviewed_against_transcript_revision`：心理師最後一次「已依最新逐字稿核對」時的值，初始等於 generated_from
- `review_status`（衍生，不另存）：`editing` | `needs_review`（reviewed_against < transcript_revision）| `confirmed`（signed_notes 有列）
- 每句 sentence 加 `origin`: `"ai"` | `"ai_edited"` | `"human"`，以及 `source_changed`: bool（引用的 segment 在核對後被修改過）
- `superseded_drafts`: 最多 3 份被重新生成取代的工作草稿（含時間與 revision）

**新表：audio_assets**
```
audio_id TEXT PK (random 128-bit, url-safe)
session_id TEXT NOT NULL
storage_ref TEXT NOT NULL      -- file name under data/audio/, never a path from the client
mime TEXT NOT NULL
bytes INTEGER, duration_s REAL
status TEXT NOT NULL           -- uploading|processing|available|cleanup_pending|deleted|failed|expired
created_at, expires_at TEXT NOT NULL
cleanup_status TEXT            -- none|pending|done|failed
cleanup_attempts INTEGER DEFAULT 0, last_error TEXT
```

**新表：jobs**
```
job_id TEXT PK, session_id TEXT NOT NULL, kind TEXT NOT NULL   -- transcribe|draft|regenerate
status TEXT NOT NULL      -- queued|running|succeeded|failed|interrupted
attempt INTEGER NOT NULL DEFAULT 1, error_code TEXT, message_zh TEXT
created_at, updated_at TEXT NOT NULL
UNIQUE (session_id, kind) WHERE status IN ('queued','running')   -- partial unique index: one active job per session+kind
```

**新表：exports**
```
export_id TEXT PK, session_id TEXT NOT NULL, format TEXT NOT NULL, note_sha256 TEXT NOT NULL
exported_by TEXT NOT NULL, exported_at TEXT NOT NULL
status TEXT NOT NULL        -- downloaded|archived
archived_by TEXT, archived_at TEXT, archive_ref TEXT   -- archive_ref: free text such as the external system's record no.
```

**NoteTemplate**：不放 DB。模板是程式內的資料定義（id、version、name、fields[{key, label, ai_mode: generate|human_only, instructions}]、system rules），由 Template Registry 提供。新增模板只加一份定義。

FK：不加（既有 schema 沒有 FK，session 主鍵是字串且跨 JSON 檔與 DB）。一致性由 session lock＋交易保證。

### 9.3 Revision 規則
1. **防止舊頁面覆蓋**：所有寫入 API（verify/save、transcript、speakers、process/regenerate、restore、confirm）都必須帶 `base_revision`，不等於目前 `revision` 時回 409 `conflict=true`，附 `edited_by`、`edited_at`。現況 `/process` 沒有檢查，要補上。腳本呼叫可省略 base_revision 的現行例外，MVP 改為只有 `/open` 可省略。
2. **逐字稿修改後**：`transcript_revision += 1`，`revision += 1`；引用到被修改 segment 的句子設 `source_changed=true`，草稿文字不變；verifier 對整份草稿重跑（沿用 `Orchestrator.refresh`，它保留句子文字與確認）；`review_status` 變成 `needs_review`。
3. **避免重新生成覆蓋人工編輯**：若任何句子 `origin != "ai"` 或有確認紀錄，`regenerate` 必須帶 `discard_edits=true`，否則 409。帶了也不刪除：舊工作草稿推進 `superseded_drafts`，稽核紀錄寫入。
4. **確認條件（伺服器端）**：沿用現有 gate（無 unsupported、無未確認的 held 句、非空），加上 `reviewed_against_transcript_revision == transcript_revision`。心理師在 UI 按「我已依目前逐字稿核對」會設定這個值（`POST .../reviewed`，帶 base_revision）。拿掉「高風險線索未引用」條件（隨 Risk Agent 停用）。
5. **保存確認版本**：`signed_notes` insert-only＋hash（沿用），另存 `transcript_revision` 到 note JSON。
6. **並行**：同一 session 的寫入由 `_session_lock` 序列化（`sign` 目前沒拿鎖，要補）；跨程序不支援（單一程序部署）。

---

## 10. API Contracts

共通：所有 `/api/*` 必須帶 `X-Haven: 1`；帳號模式下必須有有效 cookie；任何 session、audio、job、export id 都先解析到 session，再用 `_my_session` 檢查，無權與不存在一律 **404**（不洩漏存在性）。錯誤格式 `{error, message_zh, ...extra}`；409 為狀態衝突，400 為格式錯誤。

| # | Method & Route | Request | Response | 狀態 |
|---|---|---|---|---|
| A1 | `POST /api/login`、`POST /api/logout`、`GET /api/me` | 既有 | 既有 | Existing |
| S1 | `GET /api/clients`、`GET /api/clients/<cid>/brief` | – | brief 加每次 session 的 `review_status`、`export_status`、`job_status`、`audio_status`；拿掉 outcome alerts | Modify |
| S2 | `POST /api/sessions/<sid>/open` | – | session view（加 §9 欄位） | Modify |
| R1 | `POST /api/recordings` | `{client, session_id, consent:true, simulated, mime, input_mode}` | `{recording_id, session_id}` | Modify（+input_mode） |
| R2 | `POST /api/recordings/<rid>/chunk?seq=n` | binary ≤20 MB | `{received, bytes}`；缺段 409 `expected` | Existing |
| R3 | `POST /api/recordings/<rid>/finish` | – | **202** `{job_id, session_id}`（改為非同步） | Modify |
| R4 | `POST /api/recordings/<rid>/cancel` | – | `{cancelled}` | Existing |
| U1 | `POST /api/upload?client&session_id&consent=1&simulated&input_mode` | binary ≤200 MB，`X-Filename` | **202** `{job_id, session_id}` | Modify |
| J1 | `GET /api/jobs/<job_id>` | – | `{job_id, session_id, kind, status, attempt, error_code, message_zh, updated_at}` | New |
| J2 | `POST /api/jobs/<job_id>/retry` | – | 202 新 attempt；status 不是 failed/interrupted 時 409；音檔已刪除時 409 `audio_gone` | New |
| P1 | `POST /api/sessions/<sid>/audio/ticket` | `{base_revision?}` 不需 | `{url:"/media/audio/<ticket>", expires_in:600, timestamps_estimated}`；音檔不可用時 409 `audio_status` | New |
| P2 | `GET /media/audio/<ticket>` | `Range: bytes=a-b`（單一範圍） | 200／206＋`Content-Range`、`Accept-Ranges: bytes`、`Cache-Control: no-store`；ticket 無效或過期 404 | New |
| T1 | `GET /api/sessions/<sid>` | – | 既有 session view | Existing |
| T2 | `POST /api/sessions/<sid>/transcript` | `{segments:{"<id>": "text"}, base_revision}`；每段 ≤2000 字，id 必須存在，不能改 start/end | session view；已確認 409；revision 衝突 409 | New |
| T3 | `POST /api/sessions/<sid>/speakers` | `{speakers:{"<id>": therapist\|client\|unknown}, base_revision}` | session view；允許改已確認段落；`input_mode=recap` 時 409 | Modify |
| G1 | `POST /api/sessions/<sid>/process?template=<id>` | `{base_revision, discard_edits?}` | **202** `{job_id}`；有人工編輯且無 discard_edits 時 409 `has_edits` | Modify |
| D1 | `POST /api/sessions/<sid>/verify` | `{template, sections, base_revision}` | 既有（同時是草稿存檔） | Existing |
| D2 | `POST /api/sessions/<sid>/restore` | `{base_revision}` | 既有 | Existing |
| V1 | `POST /api/sessions/<sid>/reviewed` | `{base_revision}` | 設 `reviewed_against = transcript_revision`、清除 `source_changed` | New |
| C1 | `POST /api/sessions/<sid>/sign`（UI 稱「確認紀錄」） | 既有＋`base_revision` 必填 | signature；stale 409 `needs_review`；拿掉 risk_uncovered 條件；改拿 session lock | Modify |
| C2 | `POST /api/sessions/<sid>/addendum` | 既有 | 既有 | Existing |
| E1 | `GET /api/sessions/<sid>/export?format=md\|txt` | – | 檔案下載（`Content-Disposition: attachment`），header `X-Export-Id`；未確認 409；同時寫 exports 列（status downloaded）與稽核 | New |
| E2 | `POST /api/sessions/<sid>/exports/<export_id>/archived` | `{archive_ref?}` ≤100 字 | `{export}`；已歸檔則冪等回傳 | New |
| X1 | `POST /api/sessions/<sid>/audio/delete` | `{}` | 立即清除（心理師主動），回 audio status | New |

說明：
- E1 是 GET 但會寫稽核與 exports 列，與現有「GET 讀取也寫稽核」一致；只有已確認的紀錄能匯出，所以不改變臨床狀態。
- P2 不帶 `X-Haven`（`<audio>` 無法加 header），所以用短效 ticket：128-bit 隨機值，綁定 user、audio_id、到期時間，存在記憶體；每次 Range 請求都重新檢查使用者對該 session 的權限。Ticket 是不透明值，不含個資。同時檢查 Host header 與 SameSite cookie。稽核在簽發 ticket 時寫一筆 `audio_play_ticket`，不是每個 Range 請求寫一筆。
- 所有 job 類 API 回 202 後，前端每 2 秒輪詢 J1（處理中頁面），完成後呼叫 S2。

---

## 11. State Machines

### 11.1 Job
```
queued → running → succeeded
               └→ failed ──retry──→ queued (attempt+1)
running/queued at startup → interrupted ──retry──→ queued
```
- 重試冪等：transcribe 重試時若 transcript 檔已存在就跳過 ASR，直接做 draft；同一 session+kind 同時只能有一個 active job（partial unique index）。
- 長時間推論不持有 DB 交易：job 只在狀態轉換時短暫寫入。
- Worker：程序內 `ThreadPoolExecutor`，`HAVEN_JOB_WORKERS`（預設 2）。

### 11.2 Audio
```
uploading ──finish──→ processing ──ASR ok──→ available ──confirm (policy) or expiry or manual delete──→ cleanup_pending ──unlink ok──→ deleted
    │                     └─ASR fail─→ failed (file kept until expiry, retry allowed)
    └─ no chunk 30 min / cancel ─→ deleted (abandoned)
available ──expires_at passed──→ expired → cleanup_pending
cleanup_pending ──unlink error──→ cleanup_failed (attempts+1, retry with backoff: 1, 5, 30 min; then every sweep)
```
- 保存期限：`HAVEN_AUDIO_TTL_HOURS`，**測試預設 72 小時，明確標示為測試設定，未經法遵確認**。確認紀錄後是否立即清除由 `HAVEN_AUDIO_DELETE_ON_CONFIRM`（預設 1）決定。
- Sweeper：啟動時、每 10 分鐘、以及每次 ticket 請求時檢查到期。
- 衍生檔：錄音分段先 append 到 `data/tmp/record-*`，finish 時 rename 到 `data/audio/<audio_id>.<ext>`（同一檔案系統，原子）；目前沒有轉碼，所以沒有其他衍生檔。若 T-audio 驗證發現瀏覽器 webm 無法 seek 而引入 remux，remux 輸出必須寫在 `data/tmp`、成功後取代原檔、失敗時刪除。
- 啟動時 `files.clear_temp()` 仍清 `data/tmp`（未完成的錄音視為 abandoned，寫稽核）。
- 音檔不可用時，UI 在逐字稿上方顯示「原始錄音已於 <時間> 清除（原因：到期／已確認／手動）；逐字稿與草稿仍保留」，播放鍵停用。
- 稽核事件：`audio_stored`、`audio_play_ticket`、`audio_expired`、`audio_deleted`（含原因）、`audio_cleanup_failed`（只記錯誤類別）。

### 11.3 Playback
- `<audio>` 元素載入 ticket URL；點逐字稿段落時 `currentTime = segment.start`，播放到 `segment.end` 自動暫停（段落級，不做逐字對齊）。
- 所有時間戳都基於同一條完整音訊時間軸（ASR 對整段音檔轉寫，沿用）。修改逐字稿文字不改 `id/start/end`。
- `timestamps_estimated=true` 時，UI 顯示「時間位置為估計值」。
- **Validation Gate（T07 的第一步）**：Chrome 播放 MediaRecorder 產生的分段串接 webm，是否能用 Range 正確 seek。若不行：(a) 優先以 ffmpeg `-c copy` remux（需確認 B70 主機已有 ffmpeg，**不得因此新增 Python 依賴**），(b) 否則瀏覽器錄音停用回放、上傳檔（m4a/mp3/wav）保留回放，列為 MVP 限制。

### 11.4 Transcript / Note
```
(job succeeded) → Draft Generated ──any edit──→ Editing
Editing ──transcript edited──→ Needs Review ──POST reviewed──→ Editing
Editing ──confirm (server gate passes)──→ Confirmed (immutable; addenda only)
```
Export Status（獨立於上面）：`not_exported → downloaded → archived`（archived 只能由心理師標記，或未來外部系統回報）。

---

## 12. Frontend UX

沿用現有頁面骨架（登入 → 個案列表 → 個案頁 → 會談審閱頁）與雙欄審閱。新功能拆成獨立 JS module 檔，`app.js` 只做接線，以降低多人修改衝突。

- **工作區**：個案頁的 session 列表加狀態徽章（處理中／失敗可重試／草稿／需重新核對／已確認／已下載／已歸檔）與「原音剩餘 N 小時」。不新增 Dashboard。
- **建立晤談**：上傳與錄音表單加 `input_mode` 單選（完整晤談錄音／會後口述），同意勾選（口述模式文案改為「本段口述僅含心理師整理，不含個案錄音」，仍需勾選處理同意）。提交後進入處理中畫面（輪詢 J1、顯示階段：轉寫 → 說話者 → 草稿、失敗訊息與重試鍵）。
- **審閱頁左欄**：每段顯示時間、說話者（下拉，含已確認段落）、文字（點「編輯」後 contenteditable，Enter 存檔，Esc 取消；改過的段落顯示「已修改」，滑過顯示原 ASR 文字）、播放鍵。上方固定一條迷你播放器。
- **審閱頁右欄**：模板欄位與句子。三種外觀：AI（無標記）、人工修改（左邊框＋「已修改」）、人工確認（✓）。`source_changed` 的句子標黃「來源已修改，請重新核對」。`human_only` 欄位顯示「待心理師填寫」佔位。頂部狀態列：已儲存 hh:mm／尚未儲存／版本衝突（附重新載入鍵）。`needs_review` 時顯示橫幅與「我已依目前逐字稿核對」鍵；確認鍵在此狀態停用（伺服器也會拒絕）。
- **重新生成**：對話框說明「目前草稿會保留在『先前版本』，不會刪除」，並需勾選才能送出。
- **確認後**：唯讀檢視、匯出鍵（Markdown／純文字）、「標記為已歸檔」與歸檔編號欄位、補充說明。
- 移除：風險面板、檢核表、成效圖表、Case Memory 問答框（隱藏，程式保留）。
- 文案原則：「確認紀錄」取代「簽核」；不使用「風險等級」「評估」等 AI 判斷字眼。

---

## 13. Security, Privacy & Compliance

### 13.1 技術控制（MVP 必須）
| 控制 | 狀態 |
|---|---|
| 同意勾選（錄音／口述） | Existing，加 input_mode 文案 |
| 帳號、scrypt、鎖定、閒置登出、cookie HttpOnly＋SameSite=Strict＋Secure（HTTPS） | Existing（in-process tested） |
| Session-level 授權，無權回 404 | Existing；擴充到 audio ticket、job、export id |
| CSRF：X-Haven header＋SameSite=Strict＋Host 檢查 | Existing；`/media/audio/<ticket>` 以 ticket＋cookie＋Host 取代 header |
| 非公開音檔儲存（data/audio，0700/0600，隨機檔名，DB 存 ref，不接受客戶端路徑） | New |
| 檔案類型（副檔名白名單 + MIME）與大小（200 MB／20 MB chunk） | Existing |
| Path traversal：session id 正則、audio 檔名由伺服器產生、static 路徑檢查 | Existing＋New |
| 稽核事件（不含內容） | Existing＋New 事件 |
| 一般 log 不含敏感資訊（`log_message` 關閉、例外只印類別與位置） | Existing |
| HTTPS：診所模式必須有憑證；黑客松以 SSH tunnel 到 loopback | Existing＋部署說明 |
| 加密（`HAVEN_ENCRYPT=1`，AES-256-GCM）：音檔**不在**加密範圍內 | Existing；音檔加密列為 Deferred，靠全碟加密 |

**Windows 註記**：NTFS 不套用 chmod，5 個權限測試在 Windows 失敗。部署目標是 Linux（Intel Cloud），Release Gate 必須在 Linux 上跑全套測試。

### 13.2 Security Acceptance Test（必過）
心理師 A 知道心理師 B 的 session id、audio ticket、job id、export id，對下列每個操作都得到 404 且沒有副作用（沒有稽核以外的狀態改變）：讀取 session、讀取 brief、修改逐字稿、修改說話者、存草稿、重新生成、確認、補充說明、取得 audio ticket、用 B 的 ticket 播放、查 job、重試 job、匯出、標記歸檔、刪除音檔。另：admin 未申請調閱時讀不到任何草稿、逐字稿或音檔；調閱也不含音檔。

### 13.3 台灣法規與倫理脈絡（非技術宣稱）
- 相關規範：《心理師法》（業務紀錄與保存）、《個人資料保護法》（醫療相關個資屬特種個資）、諮商心理師專業倫理守則（錄音同意、保密）。**具體條號與保存年限須以全國法規資料庫（law.moj.gov.tw）現行條文核對後才能引用**；README 現有引用（心理師法第 25 條、個資法第 6 條）列為待核對。
- 原始音檔與正式紀錄的生命週期不同：音檔只是製作紀錄的暫時材料，正式紀錄由諮商所既有系統依法保存。
- 不宣稱：地端即合法；台灣法律禁止雲端；MVP 已通過任何合規認證；音檔須依紀錄期限保存；勾選同意即可免除其他義務。
- 展示與測試只用合成、模擬或有授權的資料。

---

## 14. Acceptance Criteria（Given / When / Then）

以 HTTP API 為主要測試 seam（in-process server），真實模型另設 Release Gate。

**AC-01 Safety Boundary**
- Given 預設設定，When 產生任何草稿，Then 草稿沒有量表句、沒有 R 欄強制引述、session view 沒有 risk flags／checklist／outcome alerts，確認不需處理風險線索。
- Given 模板有 `human_only` 欄位，When 生成，Then 該欄位不送模型且內容為「待心理師填寫」佔位，佔位句不可被確認為正式內容（確認前需由心理師改寫或刪除）。
- Model Quality：Given 固定 golden set（≥ 10 段腳本，含誘導診斷、風險分級、量表推測、否定、時間、說話者陷阱），When 以 Qwen3-32B-AWQ 生成並人工審閱，Then 未經來源支持的臨床判斷 = 0、重大否定／時間／說話者錯誤 = 0；不通過時記錄案例、修正策略、剩餘風險。

**AC-02 Template Registry**
- Given 新增一份模板定義，When 選用它生成，Then 不需修改 writer 或 pipeline 程式即可產生該模板欄位的草稿。
- Failure：模板 id 不存在 → 400。

**AC-03 Input Mode**
- Happy：Given `input_mode=recap` 上傳 2 分鐘口述，When job 完成，Then 所有 segment `speaker="therapist"`、`speaker_source="recap"`，沒有呼叫 SpeakerAgent，草稿句子不出現「個案表示／個案說」等直接引述個案的寫法，口述內容以「心理師口述…」呈現。
- Failure：recap session 呼叫 speakers API → 409。

**AC-04 Upload & Jobs**
- Happy：When 上傳，Then 202＋job_id，J1 依序為 queued → running → succeeded，S2 可開啟草稿。
- Failure：ASR 伺服器關閉 → job failed，`message_zh` 說明，音檔保留，retry 後成功且 session 只有一個。
- Recovery：job running 時重啟程序 → 啟動後該 job 為 interrupted，retry 成功。
- Concurrency：同一 session 同時兩次 retry → 一個 202、一個 409。
- 其他 API 在 job 執行中仍在 1 秒內回應（以 `/api/status` 量測）。

**AC-05 Transcript Editing**
- Happy：When 修改 segment 12 的文字，Then id/start/end 不變，`asr_text` 保留原文，`transcript_revision` +1，引用 #12 的句子 `source_changed=true` 且文字不變，`review_status=needs_review`。
- Persistence：重新登入後修改仍在。
- Concurrency：兩個分頁用同一 base_revision 送出，第二個 409 `conflict`。
- Failure：不存在的 segment id → 400；已確認 → 409。

**AC-06 Speakers**
- When 把已確認段落從 client 改回 therapist，Then 接受並 refresh 驗證。

**AC-07 Regenerate**
- Given 草稿有人工修改，When 不帶 discard_edits 重新生成 → 409 `has_edits`；帶 discard_edits → 202，完成後舊草稿在 `superseded_drafts`，稽核有紀錄。
- Given base_revision 過期 → 409。

**AC-08 Confirm**
- Given `needs_review`，When 確認 → 409；When 先 `reviewed` 再確認 → 成功。
- 既有條件（unsupported、held 句、空紀錄、已確認）維持（沿用 test_signing）。
- 確認時同時有逐字稿修改請求 → 兩者序列化，確認的 transcript_sha256 與確認時的檔案一致。

**AC-09 Export**
- Happy：已確認 → md 與 txt 下載，內容含模板欄位、確認者、時間、note_sha256、「AI 輔助產生、心理師確認」聲明，不含逐字稿；exports 一列 status downloaded；稽核 `export_note`。
- Then 標記 archived 後 status archived，重複標記冪等。
- Failure：未確認 → 409。

**AC-10 Audio Lifecycle**
- Happy：上傳後 audio `available`，`expires_at = created + TTL`；確認紀錄後（預設政策）→ `deleted`，檔案不存在。
- Expiry：把時鐘推過 TTL 後 sweep → `deleted`，UI 顯示已清除訊息，逐字稿與草稿仍在。
- Cleanup failure：模擬 unlink 失敗 → `cleanup_failed`、attempts +1，下次 sweep 重試成功 → `deleted`。
- Abandoned：錄音 30 分鐘無 chunk → 檔案刪除、稽核 `recording_abandoned`（沿用）。
- 程序重啟後 `data/audio` 內的 available 音檔仍可播放；`data/tmp` 殘留被清除。

**AC-11 Playback**
- Happy：取得 ticket 後 `Range: bytes=0-1023` → 206、正確 `Content-Range`；Chrome 中點段落從 start 播放。
- Failure：ticket 過期 → 404；音檔已刪除 → ticket 請求 409 `audio_status=deleted`。
- AuthZ：見 13.2。

**AC-12 Real E2E（Release Gate，B70，Linux）**
- Given vLLM Whisper＋Qwen3-32B-AWQ 執行，When 在 Chrome 經 SSH tunnel 上傳一段 ≥ 30 分鐘模擬晤談與一段口述，Then 兩者都產生草稿、引擎標示為模型名稱（不是規則引擎）、可回放、可編輯、可確認、可匯出。記錄各階段 latency、GPU 記憶體、成功／失敗。結果標示 Live／Recorded。

**AC-13 Resume**
- 登出後重新登入，工作區列出未完成 session，打開後草稿、逐字稿修改、revision 狀態都在。

---

## 15. Evaluation Plan

1. **Functional**：AC-01～AC-13。自動測試沿用 `tests/helpers.py` 的 `IsolatedData`＋in-process server，Fake Whisper 用 `tests/test_asr.py` 的 `FakeWhisper` HTTP 伺服器，Fake LLM 用各測試的 `generate_json` stub。這些是 **Simulated**，不能代替 AC-12。
2. **Model Quality**：golden set（新檔，腳本化模擬晤談＋人工標註應有與不應有的內容）；驗證器回歸沿用 `scripts/verifier_eval.py`（48 句，30 句有錯），擴充 recap 與禁止臨床推論的案例。
3. **LLM 實驗（P1）**：同一份人工校正逐字稿，一般摘要 prompt vs 新模板 prompt；比較未支持主張率、重要資訊覆蓋（人工列出的要點清單）、引用正確率、關鍵語意錯誤、人工修改量（編輯字元數）。
4. **ASR 錯誤傳遞（P1）**：同一音檔，人工校正逐字稿→LLM vs 原始 ASR→LLM，比較上述指標。
5. **Performance**：沿用 `scripts/b70-batch10.sh` 的 xpu-smi 監測，記錄音長、ASR、說話者、草稿、E2E latency、GPU 記憶體、job 成功率；至少一段 ≥ 30 分鐘。不得引用 11/17 的 4.4 分鐘作為新版產品數據。
6. **User Validation（Exploratory）**：若能找到 1–3 位心理師，記錄人工撰寫 vs AI 輔助完成時間（含等待、核對、修改）、完整性、忠實度、操作負擔、錄音接受度、使用意願。標示為探索性，不宣稱代表性。

---

## 16. Deployment

1. **黑客松（實際可驗證）**：Intel Cloud Linux 主機，2×B70。vLLM Whisper（:8001）與 Qwen（:8000）只聽 127.0.0.1，`OFFLINE=1 PRIVILEGED=0`。Haven 綁 127.0.0.1:8765，不開 LAN 模式。組員與評審以 `ssh -L 8765:127.0.0.1:8765` 存取，瀏覽器開 `http://localhost:8765`（secure context，麥克風可用）。帳號模式開啟（建立 2 個心理師＋1 個 admin，用於授權展示）。
2. **未來診所內網**：一台主 PC，`HAVEN_HOST=0.0.0.0`＋`HAVEN_TLS_CERT/KEY`＋`HAVEN_HOSTNAMES`（已實作，未在真實診所網路驗證）。NGINX 為候選，不是 MVP 必要。
3. **尚未完成的商用控制**：vLLM 容器 `--net=host`／`--ipc=host`、映像未以 digest 釘選、vLLM 無 API key、無 2FA、sign-in session 只在記憶體、音檔不加密、無備份與還原流程、無正式保存政策、無封包層級的外連驗證（只有 batch 10 的 socket 列表）。

**不得宣稱** Intel Cloud 環境等同已完成的診所地端部署。

---

## 17. Architecture Decision Records

### ADR-001 Reuse Existing Repository
- Context：骨幹功能已存在且有 316 個測試；剩 1 週。
- Options：(a) 增量改造；(b) Greenfield 重寫。
- Decision：(a)。
- Rationale：安全、確認門檻、驗證器、稽核等難以短期重建；缺口是功能而不是架構。
- Trade-offs：沿用手寫路由與 1,700 行 `app.js`，多人同時修改易衝突。
- Risks：`server.py`／`app.js` 成為衝突熱點 → 新功能放新模組，路由只加分派。
- Reconsider：若 P0 改造在 12/01 前仍無法跑通 AC-12。

### ADR-002 Frontend Framework
- Context：原生 JS，無 build，嚴格 CSP（無 inline），`el()` 輔助函式。
- Options：(a) 原生＋ES module 拆檔；(b) React＋Vite＋TS。
- Decision：(a)。
- Rationale：遷移成本（重寫審閱頁、CSP、build pipeline）遠大於剩餘功能；狀態同步需求由伺服器 revision 承擔。
- Trade-offs：沒有元件化與型別檢查。
- Risks：審閱頁狀態複雜度上升 → 以單一 `session` 物件為真實來源，每次寫入後以伺服器回應重繪。
- Reconsider：MVP 後若要做自訂模板編輯器或完整版本歷史 UI。

### ADR-003 Backend Framework
- Context：`http.server` 手寫路由、guard、Range 尚未支援。
- Options：(a) 沿用；(b) FastAPI。
- Decision：(a)。Range、job、ticket 在標準函式庫內實作。
- Rationale：沿用全部既有測試與安全行為（Host、CSRF、錯誤格式、slow-loris timeout）；FastAPI 需重寫路由與測試、新增依賴，回歸風險高。
- Trade-offs：沒有自動 schema 驗證／OpenAPI；以手寫驗證與測試替代。
- Reconsider：商用化、API 對外整合時。

### ADR-004 Database
- Context：SQLite、每次連線一個交易、`timeout=30`、無 WAL、無 FK、單一程序。
- Decision：沿用 SQLite，加 WAL、三張新表、JSON payload 欄位擴充。
- Rationale：單機、低並行（≤10 位心理師）；長推論不持有交易。
- Trade-offs：results.payload 整列覆寫，靠 session lock 與 revision 保證一致。
- Risks：WAL 在網路檔案系統不適用（部署在本機磁碟）。
- Reconsider：多程序部署、出現經驗證的 `database is locked`、或需要跨機構。

### ADR-005 ASR Model（2026-11-27 修訂）
- Context：Haven 依賴段落時間戳。Qwen3-ASR 在 vLLM v0.26.0 只輸出文字；第三方數據顯示它在台灣華語和中英混用明顯優於 Whisper。
- Options：(a) 只用 Whisper；(b) 直接換成 Qwen3-ASR；(c) 對齊式 Hybrid（Whisper 的段落與時間戳，Qwen3-ASR 的全文按字對齊切回各段）；(d) Qwen3-ASR＋ForcedAligner。
- Decision：預設 (a)，條件式升級到 (c)，依 [asr-qwen3-evaluation.md](asr-qwen3-evaluation.md) §5 決定。11/27 B70 實測：華語 CER 8.1% vs 17.8%（ASCEND）、1.1% vs 7.4%（L50），否定詞與時間詞錯誤較少；中英混用只改善 15%。Go/No-Go 以華語為準（11/27 決定，標準在看到結果後修改），只剩 L50 Hybrid 切點重跑一項（evaluation §0.1）。不採 (b)：沒有時間戳。不採 (d)：5 分鐘上限、需要 transformers on XPU、不支援閩南語。
- Rationale：(c) 保留已驗證的時間軸，下游的引用、回放、說話者都不用改；每段失敗時可以退回 Whisper 的文字。
- Trade-offs：每次轉寫要送兩個請求（Whisper＋Qwen3-ASR，46 分鐘約多 8 秒）；兩個模型共用 card 1 的記憶體。不需要 ffmpeg。
- Risks：跨段落有字重複或遺漏；`language=zh` 時英文被翻成中文，有時意思會變；台語沒有評估。（XPU 相容與映像檔模型類別已在 Step 0 確認。）
- Reconsider：Qwen3-ASR 原生支援時間戳，或 ForcedAligner 支援 XPU 和更長音訊時。

### ADR-006 LLM & Inference
- Decision：Qwen3-32B-AWQ，vLLM 0.26.0 XPU，單卡已驗證配置為基準；雙卡分配（Qwen card 0、ASR card 1）為 Validation Gate。thinking off，JSON mode，temperature 0.1，分段 60＋平行 4。
- Rationale：已實測可在 4.4 分鐘內完成 46 分鐘合成錄音（舊版程式）。
- Risks：新模板 prompt 改變輸出長度與品質 → 以 golden set 重測。
- Reconsider：雙卡配置失敗 → 退回單卡；32B 品質不達標且 8B 更好 → 換 Qwen3-8B（已在 B70 測過）。

### ADR-007 Audio Storage & Lifecycle
- Context：現況 ASR 後立即刪除，無回放。
- Decision：受控暫存（data/audio、隨機 id、DB metadata、TTL、確認後清除、sweeper＋重試、短效播放 ticket＋Range）。
- Rationale：GRILL-ME #10 要求回聽核對；ticket 解決 `<audio>` 不能帶 X-Haven 的限制。
- Trade-offs：違反舊版「Haven keeps no recording」承諾，README 與同意書文字必須同步修改；音檔不在 `HAVEN_ENCRYPT` 範圍。
- Risks：瀏覽器錄的 webm seek 問題（§11.3 Gate）。
- Reconsider／降級：回放無法安全完成時，停用回放並恢復 ASR 後立即刪除，列為 MVP 限制。

### ADR-008 Human Review & Revision Consistency
- Decision：單一 workspace `revision` 作為 optimistic lock；另有 `transcript_revision`、`generated_from_*`、`reviewed_against_*`；逐字稿修改只標記不改寫；重新生成需 `discard_edits` 並保留舊版；確認需 reviewed_against == transcript_revision。
- Rationale：最小變更、可在 results.payload 內完成、沿用既有 `_check_revision` 與 `refresh`。
- Trade-offs：沒有逐句 merge，也沒有完整版本歷史 UI。
- Reconsider：多人同時審同一份草稿成為常態時。

### ADR-009 Deployment & Security Boundary
- Decision：黑客松 Haven 綁 loopback，以 SSH tunnel 取得 secure context；不做 LAN 模式展示。帳號模式開啟以展示隔離。
- Rationale：免去自簽憑證安裝、符合麥克風要求、攻擊面最小。
- Trade-offs：展示的不是診所內網部署，需在簡報中說明差異。
- Risks：Intel Cloud 主機上的其他程序可呼叫 loopback API（README 已記載 loopback 不是安全邊界）。
- Reconsider：有實體診所試點時。

---

## 18. Repository Gap Analysis（快照，2026-11-27 @ 127ed3b）

狀態：Impl = Existing／Partial／Missing；Verif = Verified／Simulated／Failed／Unknown；Action = Reuse／Modify／Add／Disable／Defer。Effort 以 person-day（d）計。

| # | 功能 | Impl / Verif | 證據（檔案·函式·路由·測試） | Gap | Action / Priority / Effort / Risk |
|---|---|---|---|---|---|
| 1 | 瀏覽器錄音→分段上傳 | Existing / Simulated | `server.py` `_recording_start/_chunk/_finish/_cancel`；`POST /api/recordings*`；`app.js` 錄音區（MediaRecorder，5 s timeslice）；`tests/test_accounts.py`、`test_server_robust.py` 程式內測試；README：只用產生的音調測過 | 未以真麥克風驗證；finish 同步執行全流程；沒有 input_mode | Modify / P0 / 0.5d / 中（真機麥克風） |
| 2 | 音檔上傳→ASR | Existing / Verified（in-process demo + 501 smoke）；真實 ASR Unknown（本機） | `_upload`、`_transcribe_and_store`、`transcript.transcribe_audio`、`_openai_server`、`_faster_whisper`；`tests/test_asr.py`（FakeWhisper = Simulated）；`docs/asr-eval/b70-*`（歷史） | 同步；ASR 後刪音檔；沒有 job | Modify / P0 / 含於 #10 / 中 |
| 3 | ASR→說話者 | Existing / Simulated（測試）＋Verified 歷史（530/553 正確，合成語音） | `agents/speakers.py` `SpeakerAgent.run`（Qwen 文字推測，無聲學模型）；`POST .../speakers`；UI `confirmSpeakers` | 已確認段落無法在 UI 再改；recap 不應執行 | Modify / P0 / 0.3d / 低 |
| 4 | 逐字稿→LLM 生成 | Existing / Simulated（stub）＋Verified 歷史（batch 10） | `agents/writer.py` `NoteWriter.run`、`TEMPLATES`、`SYSTEM_PROMPT`、`_rule_based`；`llm.OpenAICompatLLM.generate_json`；`tests/test_verifier_writer.py`、`test_agents.py` | 模板寫死在 writer；A/P 欄由 AI 生成；R 欄強制補引風險原文；沒有 recap prompt | Modify / P0 / 1.5d / 高（prompt 品質需真模型重測） |
| 5 | LLM 輸出→草稿儲存 | Existing / Verified（smoke + tests） | `Orchestrator.process_session`、`store.save_result`；`POST .../open` | 插入量表句、跑 risk/outcome；沒有 template_id/version、generated_from | Modify / P0 / 0.5d / 低 |
| 6 | 逐字稿／說話者修改→引用驗證 | Partial（說話者有、文字無）/ Simulated | `_speakers` → `ORCH.refresh`；`verdicts` 以 transcript sha 為 key | 逐字稿文字不可編輯（Missing）；沒有 transcript_revision、source_changed、needs_review | Add / P0 / 1.5d / 中 |
| 7 | 草稿修訂→確認→匯出 | Partial / Verified（smoke：verify 200、sign 200、re-sign 409、audit ok） | `_verify`、`_restore`、`_check_revision`、`_sign`、`signing.check/blocking_sentences/uncovered_risk`、`store.sign_note`；`tests/test_signing.py` | `/process` 不檢查 revision 且覆蓋人工修改；`_sign` 未拿 session lock；確認含風險門檻；**沒有匯出（Missing）** | Modify＋Add / P0 / 1.5d / 中 |
| 8 | Session 授權→音檔存取 | 授權 Existing / Verified（tests）；音檔存取 Missing | `accounts.may_open`、`_my_client`、`_my_session`；`tests/test_accounts.py` | 沒有 audio、job、export 的 id 授權 | Add / P0 / 含於 #9 / 高（安全驗收） |
| 9 | 音檔儲存→清除 | Partial（只有「立即刪除」）/ Verified（`TempAudioTest` + 501 smoke） | `_remove`、`_abandon_audio`、`_sweep_recordings`、`files.clear_temp`、`files.temp_dir` | 需保留＋TTL＋確認後清除＋重試＋metadata 表＋ticket＋Range | Add / P0 / 2d / 高（webm seek） |
| 10 | 背景 Job→失敗／重試／恢復 | Missing / – | 全部同步於 request；`UPLOADING`、`RECORDINGS` 只在記憶體 | 沒有 job 表、狀態 API、重試、重啟標記 | Add / P0（可降級）/ 1.5d / 中 |
| 11 | Risk Agent／檢核表／通報提示 | Existing / Verified（`test_risk_recall.py`、`test_safety.py`） | `agents/risk.py`；`signing.uncovered_risk`；UI 風險面板 | 與 Safety Boundary 衝突 | Disable（flag，預設關）/ P0 / 0.5d / 中（相關測試需調整而非刪除） |
| 12 | Outcome／量表 | Existing / Verified（`test_outcome_memory.py`） | `agents/outcome.py`、`Orchestrator.measure_sentences`；`clients.json` `scores` 寫死；UI 圖表 | 衍生臨床解讀；沒有人工輸入 | Disable / P0 / 含於 #11；人工輸入 Defer P2 |
| 13 | Case Memory | Existing / Verified（tests） | `agents/memory.py`；`POST /api/clients/<id>/ask` | 跨次回顧屬 Roadmap | Defer（UI 隱藏）/ P2 |
| 14 | 帳號、閒置登出、admin 調閱 | Existing / Verified（in-process）；真實內網 Unknown | `accounts.py`、`_login`、`_open_records` | 無 | Reuse |
| 15 | SQLite | Existing / Verified | `store.connect`（timeout 30）、`_prepare`（ALTER 遷移） | 無 WAL；新表 | Modify / P0 / 0.3d / 低 |
| 16 | 稽核 hash chain | Existing / Verified（smoke `audit ok`） | `store._audit`、`verify_audit` | 新事件 | Reuse |
| 17 | 本機連線限制 | Existing / Verified（tests） | `llm.assert_local`、`_checked_socket` | 無 | Reuse |
| 18 | Demo reset | Existing / **Failed（間歇）** | `scripts/reset_demo.py:58`；`test_ui.DemoWalkthroughTest.test_5` `FileExistsError` | 秒級檔名撞名；POSIX 會覆蓋舊備份 | Modify / P0（小）/ 0.1d / 低 |
| 19 | 檔案權限 | Existing / Failed on Windows（5 tests，平台差異），Linux Unknown（本次未跑） | `files.secure_data_dir`、`write_private` | Windows 無 ACL | Defer（部署目標 Linux）；Release Gate 在 Linux 跑全套 |
| 20 | 模型品質 golden set | Partial / Simulated＋歷史 | `scripts/verifier_eval.py`、`data/verifier_eval/cases.json`、`scripts/benchmark.py` | 沒有禁止臨床推論與 recap 的案例；沒有 writer golden set | Add / P0 / 1d / 中 |
| 21 | 真實 E2E（瀏覽器＋模型） | Missing / Unknown | 只有評測腳本 batch 10 | 從未以瀏覽器跑過完整產品流程 | Add / P0 Release Gate / 0.5–1d / 高 |
| 22 | README／同意書 | Existing / – | README「Haven keeps no recording」、Limitations 說「UI 不能改說話者」（過時）、`docs/consent-form.md` | 與新音檔政策、Safety Boundary 不一致 | Modify / P0 / 0.3d / 中（對外說法錯誤） |

**環境限制 vs 測試問題 vs 產品缺陷**
- 環境限制：本機沒有 faster-whisper、opencc、cryptography、GPU；Windows NTFS 權限。
- 測試問題：`python -m unittest tests.test_x` 會找不到 `helpers`（必須用 `discover -s tests`）；5 個 POSIX 斷言在 Windows 失敗。
- 產品缺陷：`reset_demo.py` 撞名、`/process` 無 revision 檢查且覆蓋修改、`_sign` 未加鎖、README 過時說法；功能缺失見上表。
- Windows loopback：11/22 與 11/27 的測試與 smoke 中**沒有**重現 loopback 失敗（`localhost` 連線 0.28 s）；先前報告的 loopback 問題列為 Unknown，Release Gate 改在 Linux 執行。

---

## 19. Implementation Dependency Graph

```
T01 Safety flags ─────────────┐
T02 Template registry ──→ T03 Prompts & recap writer ──→ T14 Golden set & model QA ─┐
T04 Schema & WAL ──┬──→ T05 Jobs ──→ T06 input_mode in upload/recording ──┐          │
                   ├──→ T07 Audio store/TTL/cleanup ──→ T08 Ticket + Range ──→ T11 Review UI audio
                   ├──→ T09 Transcript edit API + revisions ──→ T10 Regenerate/confirm guards ──→ T12 Export
                   │                                   └──────→ T11 Review UI (transcript edit, states)
                   └──→ T12 Export (exports table)
T13 Security acceptance tests  ← T05, T08, T09, T12
T15 B70 deploy & two-card gate  (parallel from day 1)
T16a Qwen3-ASR smoke (done) → T16b benchmark & Go/No-Go (11/29 noon) ──Go──→ T16c alignment hybrid
T17 Real E2E Release Gate       ← T03, T06, T08, T10, T11, T12, T15
T18 Docs: README, consent, demo script ← T07, T01, T17
T19 Small fixes (reset_demo, sign lock)  (parallel, any time)
```

- **Critical Path**：T04 → T09 → T10 → T11 → T17（約 5.5d），與 T02 → T03 → T14 並行。
- **Must be first**：T04（schema）、T01（flags，避免後續測試反覆改）。
- **Parallel**：T15、T16、T19 從第一天開始；T07/T08 與 T09/T10 可由不同人同時做（不同模組，`server.py` 只加路由行）。
- **Release Blockers**：T01、T03、T09、T10、T12、T13、T17。
- **Model Decision Gate**：T16b 結束（11/29 中午）決定 ASR：Go 就做 T16c（+1–1.5d，從 Scope Reduction 第 2–3 項挪時間），No-Go 就只保留評測結果；T15 結束決定單卡或雙卡；T14 決定 prompt 是否達標（12/01）。

---

## 20. Suggested Ticket Breakdown

| ID | Goal | Scope | Non-goals | Deps | Likely files | Acceptance | Pri | Effort | Risk |
|---|---|---|---|---|---|---|---|---|---|
| T01 | 依 Safety Boundary 停用臨床衍生功能 | `HAVEN_CLINICAL_AIDS` flag 預設關：不跑 risk/outcome，不插量表句，不做 R 強制引述，確認不檢查風險線索，UI 隱藏風險面板、成效圖、Case Memory | 刪除程式碼 | – | orchestrator、signing、server（`_sign`、brief）、writer（R 補引）、app.js、tests（flag on 跑舊測試） | AC-01 前半；舊 risk/outcome 測試在 flag on 下仍通過 | P0 | 0.5d | 中 |
| T02 | 模板定義與生成流程分離 | Template registry（id、version、fields、ai_mode、instructions）；把 SOAP/DAP 搬成定義；新增「單次晤談質化紀錄」 | 模板編輯器 | – | 新 templates 模組、writer、signing（section keys） | AC-02 | P0 | 0.5d | 低 |
| T03 | 新 prompt 與口述生成 | 新模板 prompt；`human_only` 欄不送模型；recap prompt（心理師口述語態）；verifier recap 規則 | 新 agent | T02 | writer、verifier、llm（不改 transport） | AC-01 後半、AC-03 生成部分 | P0 | 1.5d | 高 |
| T04 | Schema 擴充 | WAL；`audio_assets`、`jobs`、`exports` 表與遷移；payload 新欄位預設值；transcript JSON 新欄位讀取相容 | 改既有表 | – | store、models、transcript | 舊 DB 啟動後自動遷移；全套測試通過 | P0 | 0.5d | 低 |
| T05 | 背景 Job | job 模組、worker pool、J1/J2、啟動時 interrupted、冪等重試；upload/finish 回 202 | 分散式 queue | T04 | 新 jobs 模組、server（upload、finish、process） | AC-04 | P0 | 1.5d | 中 |
| T06 | input_mode | 上傳／錄音參數、recap 跳過 SpeakerAgent、speakers API 拒絕 recap；UI 單選與文案 | 口述專用頁面 | T05, T03 | server、transcript、app.js | AC-03 | P0 | 0.5d | 低 |
| T07 | 音檔暫存與清除 | finish/upload 後移入 data/audio；TTL、確認後清除、手動刪除 X1、sweeper、重試、稽核；ASR 失敗保留 | 音檔加密、轉碼 | T04 | 新 audio 模組、server、files | AC-10 | P0 | 1d | 中 |
| T08 | 授權回放 | ticket（P1）、`/media/audio/<ticket>` Range（P2）；**先做 webm seek 驗證**，失敗時走 §11.3 降級 | 逐字對齊 | T07 | audio 模組、server（新路由，guard 例外）、CSP 確認 | AC-11 | P0 | 1d | 高 |
| T09 | 逐字稿編輯與 revision | T2 API；asr_text；transcript_revision；source_changed；needs_review；V1 `reviewed`；speakers 可再改 | 逐字詞 diff UI | T04 | server、transcript、orchestrator.refresh、signing | AC-05、AC-06 | P0 | 1.5d | 中 |
| T10 | 重新生成與確認守門 | `/process` 檢查 base_revision 與 has_edits、superseded_drafts；`_sign` 加 session lock、staleness 條件；sentence `origin` | 版本歷史 UI | T09, T05 | server、signing | AC-07、AC-08 | P0 | 1d | 中 |
| T11 | 審閱頁 UI | 處理中畫面（輪詢）、逐字稿編輯、說話者下拉、播放器、句子三態、source_changed、needs_review 橫幅、儲存狀態、衝突提示；拆成新 JS module | 視覺重設計 | T08, T09, T10 | web/（新 module 檔）、app.js 接線、style.css | 手動測試清單＋`tests/test_ui.py` 靜態檢查 | P0 | 2d | 中（app.js 衝突） |
| T12 | 匯出 | E1（md、txt）、E2 archived、exports 表、稽核、UI 按鈕 | DOCX、PDF、外部 API | T04, T10 | 新 export 模組、server、app.js | AC-09 | P0 | 0.5d | 低 |
| T13 | 安全驗收測試 | §13.2 全部組合，兩位心理師＋admin | 滲透測試 | T05, T08, T09, T12 | tests/（新檔） | 全部 404 且無副作用 | P0 | 0.5d | 低 |
| T14 | 模型品質 golden set | ≥ 10 段腳本＋人工標註；擴充 verifier_eval 案例；在 B70 跑並人工審閱 | 統計顯著評估 | T03, T15 | data/（新案例檔）、scripts/ | AC-01 Model Quality | P0 | 1d | 中 |
| T15 | B70 部署與雙卡 Gate | 記錄 driver、image digest、啟動指令；嘗試 Qwen card0／Whisper card1；SSH tunnel 說明 | NGINX、LAN 模式 | – | scripts/（新啟動說明或參數）、docs | 兩個 vLLM 服務可用、配置記錄完整 | P0 | 0.5d | 中 |
| T16a | Qwen3-ASR 可行性煙霧測試 | 確認映像檔有這個模型、XPU 能啟動、`verbose_json` 和 `json` 的行為、10 與 46 分鐘音檔不截斷、記憶體擺得下 | 準確度評測 | T15 | scripts/（啟動參數）、docs/asr-eval | asr-qwen3-evaluation.md §5 Step 0 | P0 | 0.5d | 中 |
| T16b | ASR 對照評測＋Go/No-Go | 用 `scripts/asr_benchmark.py` 評 ASCEND 60 句、CSZS 40 句加 L50；Qwen3（json）vs Whisper（verbose_json）；L50 加做對齊式 Hybrid 抽樣；人工抽查否定與時間句；決策紀錄。**不評台語**。11/27 已跑完，只剩 L50 Hybrid 重跑 | 整合；自製素材 | T16a（✅ 11/27 通過） | docs/asr-eval | asr-qwen3-evaluation.md §5 Step 2，11/29 中午前決策 | P0 | 0.5d | 低 |
| T07a | 音訊正規化（只給回放用） | 用 ffmpeg 把瀏覽器錄的 webm 轉成可拖動播放的格式；衍生檔依 §11.2 管理 | 新 pip 依賴；ASR 前處理 | T07 | audio 模組 | 轉檔成功與失敗都會清理；webm 能拖動播放 | P0（只在 T08 的 seek Gate 失敗時做） | 0.5d | 中（ffmpeg 不一定有） |
| T16c | 對齊式 Hybrid ASR 整合（只在 Go 時做） | Whisper `verbose_json` 段落＋Qwen3-ASR `json` 全文，用 `align()`（從 scripts/asr_benchmark.py 移入）切回段落；某段對不上或 Qwen 失敗時保留 Whisper 文字；段落記錄 `asr_engine` 與 Whisper 原文；`HAVEN_ASR_REFINE` flag；`_openai_server` 依模型選擇 response_format | ForcedAligner；切音訊 | T16b（Go） | transcript、新 asr_refine 模組、config | 段落 id 和時間不變；fake 伺服器測試＋B70 實測 | P0（條件式） | 1d | 中 |
| T17 | 真實 E2E Release Gate | AC-12、AC-13；performance 紀錄；錄製備援影片並標示 | – | T03, T06, T08, T10, T11, T12, T15 | docs（結果） | AC-12 通過或記錄失敗與限制 | P0 | 1d | 高 |
| T18 | 文件同步 | README（音檔政策、Safety Boundary、Limitations）、同意書、demo 腳本、架構圖、法規引用待核對標記 | 簡報製作 | T01, T07, T17 | README、docs/consent-form.md、docs/video-script.md | 文件與行為一致 | P0 | 0.5d | 中 |
| T19 | 小修 | reset_demo 備份檔名；（若 T10 未先做）sign 加鎖 | – | – | scripts/reset_demo.py | test_ui 連跑 10 次不失敗 | P0 | 0.1d | 低 |
| T20 | 第二模板（安全版 SOAP） | A/P 改人工、S/O 來源支持 | – | T02, T03 | templates | AC-02 | P1 | 0.5d | 低 |
| T21 | 探索性使用者驗證 | 1–3 位心理師操作與計時 | 統計結論 | T17 | docs | 紀錄表 | P1 | 1d | 中（招募） |

**檔案衝突提示**：`server.py` 被 T01、T05、T06、T07、T08、T09、T10、T12 觸及 → 每個 ticket 只在路由分派加最少行，業務邏輯放各自新模組；建議合併順序 T04 → T01 → T05 → T09 → T07 → T10 → T08 → T12 → T06。`app.js` 只由 T11（主）與 T01、T06、T12（各一小段）修改。

---

## 21. Milestones（依實際進度重估）

剩餘：11/27–12/03 七天，兩名成員（其中一名主要負責產品／臨床，**Assumption**：工程主要由一人＋coding agents 完成）。P0 估計 10–12 person-days → 需 agent 平行與嚴格範圍控制。

| Milestone | 日期 | 內容 | 出口條件 |
|---|---|---|---|
| M0 | 11/27–11/29 中午 | T04、T01、T19、T15、T16a、T16b、T02 | ASR Go/No-Go 決定（11/29 中午）；vLLM 服務可用；schema 合併 |
| M1 | 11/28–11/30 | T03、T09、T10、T05 | 新模板＋口述草稿；逐字稿編輯；守門 API 通過 AC-05～AC-08 |
| M2 | 11/29–12/01 | T07、T08、T12、T06、T13 | 回放 Gate 決定（含降級）；匯出；安全驗收通過 |
| M3 | 12/01–12/02 | T11、T14、T17、T18 | AC-12 Live 通過一次並錄影；golden set 結果 |
| M4 | 12/03–12/04 | Feature freeze（12/03 00:00）、只修 critical bug、彩排、提交 | 提交 |

**Scope Reduction（依序）**：(1) Qwen3-ASR 比較 → 只記錄 Whisper；(2) 第二模板；(3) 視覺細節；(4) 量表（已 Deferred）；(5) 回放進階互動（只保留段落播放；webm 不可 seek 時只支援上傳檔回放）；(6) 背景 Job 降級為「同步處理＋job 列只做記錄」，保留重試但不支援重啟恢復；(7) 非必要資料管理。**不得削減**：資料隔離、人工確認守門、Safety Boundary、不覆蓋人工修改。

回放無法安全完成 → 停用回放、恢復 ASR 後立即刪除，列為 MVP 限制。真實模型整合有重大障礙 → 只修一條主流程（上傳 → Whisper → Qwen → 審閱 → 確認 → 匯出）。Demo 備援必須標示 Live／Recorded／Mock。

---

## 22. Risks

| # | 風險 | 影響 | 緩解 |
|---|---|---|---|
| R1 | 新模板 prompt 在 Qwen3-32B 上品質不穩（判斷欄外洩、口述語態錯） | Safety Boundary 失守 | T14 golden set＋verifier 硬規則攔截診斷詞；`human_only` 欄位根本不送模型 |
| R2 | MediaRecorder webm 無法 seek | 錄音回放失效 | T08 第一天驗證；remux 或降級為僅上傳檔回放 |
| R3 | 工程人力不足、`server.py`／`app.js` 衝突 | 延誤 | 新模組、合併順序、Scope Reduction 清單 |
| R4 | 真實 E2E 首次在瀏覽器跑出未知問題（麥克風、tunnel、超時） | Demo 失敗 | T15 第一天建環境；T17 至少提前一天；錄製備援 |
| R5 | 雙卡或新配置啟動失敗、Intel Cloud 環境變動 | 無模型可用 | 單卡已驗證配置為退路；記錄 image 與指令 |
| R6 | 停用 Risk Agent 後失去「揭露不遺漏」保護 | 重要敘述漏寫 | 新模板保留「安全相關敘述（僅原文整理）」欄位（**Open Question**：需心理師確認）；evaluation 的覆蓋率指標 |
| R7 | 對外說法與行為不一致（README 仍寫不保留錄音） | 信任與評分 | T18 列為 Release Blocker |

---

## 23. Business Feasibility & Roadmap（全部為 Assumption／Scenario）

- **客群**：3–10 位心理師的小型諮商所。Buyer＝負責人；End User＝心理師；Data Subject＝個案。
- **痛點（待驗證）**：紀錄時間、質化內容整理、對雲端 AI 的顧慮。目前只有 1 位心理師的類別建議（2026-11-17），沒有時間量測。
- **節省時間**：無數據。以 §15.6 探索性量測取得第一筆；簡報中以 scenario 呈現（例：若每份紀錄從 X 分鐘降到 Y 分鐘），必須標示為假設。
- **地端價值**：資料不交給第三方處理者，諮商所自己掌握保存與刪除。
- **硬體成本差異**：競賽用 2×B70（32 GB）；未來單卡 B70 已實測可同時載入 Whisper＋Qwen3-32B-AWQ（單卡配置），較小模型 Qwen3-8B 也已在 B70 測過速度。商用硬體價格未查證，不在本文件給數字。
- **維運成本**：備份、更新、憑證、帳號管理、磁碟加密都需有人負責；可作為服務方案的一部分（**Assumption**）。
- **整合**：API-ready（匯出格式穩定、exports 表可接外部回報 archived），MVP 不串接。
- **錄音接受度不足時**：會後口述不錄個案聲音，提供較低門檻的導入路徑。
- **Roadmap**：跨次晤談回顧（授權讀取既有紀錄）、外部系統 API、自訂模板、量表人工輸入與確定性計分、聲學 diarization、Qwen3-ASR／Breeze-ASR、單卡與小模型、商用部署與法遵審查。

---

## 24. Testing Decisions

- **好測試的定義**：只測外部行為（HTTP 狀態碼、回應內容、持久化後再讀取的結果、檔案是否存在），不測內部函式呼叫順序。
- **主要 seam（唯一）**：in-process HTTP server＋隔離資料夾（`tests/helpers.py` 的 `IsolatedData` 與伺服器啟動方式）。模型邊界以 Fake HTTP 伺服器（`tests/test_asr.py` 的 `FakeWhisper` 模式）與 `generate_json` stub 取代。新增的 job worker 在測試中提供同步執行模式（同一 seam 內等待 job 完成），不另開 seam。
- **時間依賴**（TTL、ticket 到期）：audio／job 模組接受可注入的 clock，測試從 HTTP 層觸發 sweep。
- **Prior art**：`tests/test_signing.py`（確認門檻）、`test_accounts.py`（授權 404）、`test_privacy.py`（暫存音檔清除）、`test_server_robust.py`（錯誤與並行）、`test_asr.py`（假 ASR 伺服器）、`test_ui.py`（demo 流程）。
- **真實模型**：不進 unittest；以 T14（golden set 腳本）與 T17（E2E 紀錄）作為 Release Gate，結果寫入 `docs/`。
- **平台**：Release Gate 在 Linux 跑全套 `python -m unittest discover -s tests`。

## 25. Out of Scope

見 §5 Explicit Non-goals 與 P2。另外：不改既有 `signed_notes` 的 hash 定義；不遷移任何框架；不安裝新 Python 依賴（ffmpeg 若使用，僅限主機既有的系統工具，需在 T08 先確認）。

## 26. Open Questions / Validation Gates

- **Open Question**：新模板欄位清單需心理師確認（尤其是否保留「安全相關敘述（僅原文整理）」）。不阻塞：先以 §12 草案實作，欄位是資料定義，可改。
- **Open Question**：確認後是否立即刪音檔，或保留到 TTL？預設立即刪除（較保守），可用 flag 調整。
- **Validation Gate**：webm seek（T08）、雙卡配置（T15）、Qwen3-ASR（T16）、golden set（T14）、真實 E2E（T17）。
- **Deferred Decision**：音檔加密、正式保存期限、工作資料自動清除政策、組織層級資料模型。

## 27. Final Review

- **Product**：核心流程（錄音／口述 → 草稿 → 重點核對 → 確認 → 匯出）完整，Safety Boundary 由伺服器保證；移除的功能都不是減少紀錄負擔的核心。
- **Engineering**：重用約 70% 既有模組；新功能為 4 個新模組＋路由；無框架遷移、無新依賴。
- **Model**：ASR 比較有固定素材、分目標的台語評估與 Go/No-Go；預設不切換。
- **Reliability**：引用＋三層驗證沿用；新增 revision、staleness、不覆蓋規則；跨段整合與遺漏仍是已知限制。
- **Security**：音檔保留帶來新風險，以 ticket、TTL、清除重試、授權測試處理；音檔未加密已明列。
- **UX**：不要求先校正全文；只標記來源被改的句子；狀態與衝突明確。
- **Business**：所有數字標為 Assumption／Scenario。
- **Delivery**：P0 約 10–12 person-days，七天內需 agent 平行與嚴格降級順序；可行但沒有緩衝。
