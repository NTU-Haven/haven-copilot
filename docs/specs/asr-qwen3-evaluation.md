---
title: Qwen3-ASR 評估與 ASR 決策（Haven Copilot MVP）
date: 2026-11-27
status: desk evaluation done; B70 measurement pending (Validation Gate)
supersedes: haven-copilot-mvp.md §8.1–8.3 and ADR-005 default "No-Go"
---

# Qwen3-ASR 評估與 ASR 決策

> 這份是**文件評估**，資料來自官方模型卡、vLLM 原始碼與第三方論文和模型卡，**還沒有在 B70 上跑過任何一筆音檔**。以下所有效能數字都是原作者自己公布的，不是本專案量測的結果。

## 1. 結論

把 Qwen3-ASR 從「預設 No-Go」改為 **「有條件採用，採 Hybrid 架構，在 11/28–11/29 用 B70 實測決定」**：

- **整合成本比預期低很多。** vLLM v0.26.0 原始碼已經有 `Qwen3ASRForConditionalGeneration`，而且實作了 `SupportsTranscription`。這正是 Haven 在 B70 上已經在用的 vLLM 版本，Haven 也已經透過 `HAVEN_ASR=openai` 呼叫 `/v1/audio/transcriptions`。所以要先試 Qwen3-ASR，理論上只需要換模型、換設定，不用改程式（有一個例外，見 §4 風險 A）。
- **準確度的潛力是真的。** 台灣華語和中英混用的第三方數據都明顯優於 Whisper-large-v3（見 §2.3）。
- **真正的阻礙是時間戳。** Qwen3-ASR 本身只輸出文字。vLLM 的實作沒有 segment，時間戳要靠另一個模型 Qwen3-ForcedAligner-0.6B。這個 Aligner 不在 vLLM 裡、一次最多 5 分鐘、支援的 11 種語言不含閩南語。Haven 的逐句引用、段落回放和說話者更正都需要段落時間戳，所以**不能直接把 Whisper 換成 Qwen3-ASR**。
- **台語沒有證據。** 唯一找到的閩南語數據，原版 Qwen3-ASR 的 D-CER 是 34.61%，而且論文沒有說資料是台灣台語還是閩南地區的閩南語，也沒有 Whisper 的對照數字。台語要靠我們自己量。

**建議架構：Hybrid。** Whisper 負責切段與時間戳（已驗證），再由 Qwen3-ASR 對每一段重新轉寫文字。段落 id、起訖時間都來自 Whisper，所以下游的引用、回放、說話者都不受影響。

## 2. 證據

### 2.1 模型本身（官方 HF 模型卡與 GitHub README，2026-01-29 發布）
- `Qwen/Qwen3-ASR-1.7B`，授權 Apache-2.0。
- 支援 30 種語言、22 種中文方言的語言辨識與 ASR，方言清單有「Minnan」「Fujian」。清單裡沒有「Taiwanese」或「Hokkien」。
- 沒有說明會輸出繁體中文，只在 CommonVoice zh-TW 的評測列出現過。所以 Haven 現有的 opencc 繁化流程要保留。
- 時間戳：要另外傳入 `forced_aligner`，也就是 Qwen3-ForcedAligner-0.6B。它是非自回歸模型，輸出字或詞層級的時間戳，「up to 5 minutes of speech」，支援 11 種語言（中、英、粵等，沒有閩南語）。Aligner 是用 transformers 載入。範例全部寫死 `cuda:0`，完全沒提 Intel XPU。
- 串流模式不支援批次，也不回傳時間戳。
- 長音訊：官方只說「support transcribe long audio」，沒有寫切段方式或最大長度。
- 沒有說明支援 prompt 或熱詞。第三方的 TEA-ASR 範例有一個 `context=` 參數，但官方頁沒有。
- 安裝：官方建議用 CUDA 12.9 的 vLLM nightly。flash-attn 是選用。

### 2.2 vLLM v0.26.0 原始碼（`vllm/model_executor/models/qwen3_asr.py`，共 708 行，全部讀過）
- 這個檔案存在，類別實作了 `SupportsTranscription`，task 支援 `transcribe` 和 `translate`。
- `max_audio_clip_s` 取自 WhisperFeatureExtractor 的 `chunk_length`，實際值要到執行時才知道，**Unknown**。長音訊會不會自動切段由通用的 serving 層決定，我沒有讀到那部分。
- **輸出只有文字**。模型輸出 `language X<asr_text>...`，後處理只保留文字。這個檔案裡沒有任何 segment 或時間戳。
- 可以強制指定語言（`language {Name}<asr_text>`），也有可選的 system prompt（會先過濾掉 ChatML 標記）。
- 這個檔案沒有 flash-attn，也沒有呼叫 `torch.cuda`。但它用到的音訊編碼器 `Qwen3OmniMoeAudioEncoder`，以及 attention backend 能不能在 XPU 上跑，**Unknown**。
- 我確認的是 v0.26.0 tag 的原始碼裡有這個檔案，**沒有確認** `vllm-openai-xpu:v0.26.0` 映像檔裡也有。

### 2.3 準確度（全部是原作者自己報的，各家評測方式不同，不能直接互相比）

**Qwen 官方**（WER/CER，Qwen3-ASR-1.7B 對 Whisper-large-v3）：AISHELL-2 2.71 對 5.06；WenetSpeech meeting 5.88 對 19.11；KeSpeech 方言 5.10 對 28.79；22 種方言對話平均 15.94 對 44.55。官方沒有專門的中英混用評測。

**TEA-ASR-1.1 模型卡**（第三方，用 Qwen3-ASR 在台灣語料上微調，MIT 授權；MER，先轉成簡體再算）：

| 資料集 | TEA-1.1 | Qwen3-ASR-1.7B | Breeze-ASR-25 | Whisper-large-v3 |
|---|---|---|---|---|
| CommonVoice 19 zh-TW | 3.58 | 3.90 | 8.03 | 10.17 |
| ASCEND 中英混用 | 9.60 | 10.57 | 17.53 | 19.61 |
| CSZS 中英混用 | 10.94 | 11.03 | 12.18 | 23.24 |
| NTUML2021 課堂 | 6.67 | 10.12 | 7.50 | 9.68 |

**WenetSpeech-Min**（arXiv 2609.36834，閩南語，D-CER 以閩南語轉寫稿為基準）：原版 Qwen3-ASR 34.61；在 WSM 微調後 17.59；再加內部資料 15.21。論文**沒有 Whisper 對照**，也**沒有說明地區**，閩南語轉寫用的文字系統也沒說。

**Breeze-ASR-25**（MediaTek，基於 Whisper-large-v2，Apache-2.0，有時間戳）：ASCEND 17.74、CSZS 13.01（對照 WLV3 為 23.22、26.43）。

**對 Haven 的意義**：台灣華語和中英混用，Qwen3-ASR 系列有一致且大幅度的領先（三家獨立來源方向一致）。台語方面，所有來源都**沒有**台灣台語的直接證據。

## 3. 與 Haven Pipeline 的相容性

| Haven 需求 | Whisper（現況） | Qwen3-ASR 直接替換 | Hybrid（建議） |
|---|---|---|---|
| 段落時間戳（引用、回放、說話者） | ✅ vLLM 回傳 segments（B70 實測 553 段，段間有空隙，是真實時間戳） | ❌ 只有文字，Haven 會退回依字數均分的估計值 | ✅ 用 Whisper 的段落 |
| 長音訊（50 分鐘） | ✅ 46 分鐘實測 | Unknown（serving 層怎麼切不清楚） | ✅ 每段只有幾秒 |
| 台灣華語、中英混用 | 基準 | 文件顯示明顯較好 | 同 Qwen3-ASR，但要實測驗證 |
| 台語 | Unknown | Unknown，原版在閩南語表現弱 | 同左，要實測 |
| 繁體輸出 | 要靠 opencc | 要靠 opencc | 要靠 opencc |
| XPU | ✅ 已驗證 | Unknown | Unknown（Qwen3-ASR 這部分） |
| Haven 程式改動 | 0 | 約 0（可能要改 `response_format`） | 中：需要依段落切音訊，再逐段送出 |
| 延遲 | 46 分鐘音檔 9.6 s | Unknown | Whisper 加上 N 次短請求，可以批次並行；Unknown |
| 失敗時退路 | – | 退回 Whisper | 每一段失敗就用 Whisper 的文字（自然的退路） |

**Hybrid 的技術前提：要能切音訊。** Python 標準函式庫只能切 WAV。瀏覽器錄音是 webm/opus，上傳檔可能是 m4a 或 mp3，所以需要 **ffmpeg**（主機上的系統工具，不是 pip 依賴）先統一轉成 16 kHz mono WAV。這一步同時解決 MVP 規格 §11.3 的 webm 無法拖動播放的問題（回放改用 WAV，或用轉出的 opus）。所以 ffmpeg 是兩個需求共用的 Validation Gate。

**ForcedAligner 路線（不建議用在 MVP）：** 要在 XPU 上跑 transformers、把音訊切成 5 分鐘以內的窗口、把字級時間戳合回段落，還要另外處理說話者切段。改動最大、XPU 未知，也不支援閩南語。

## 4. 主要風險

- **A. `verbose_json` 可能不支援。** Haven 目前送的是 `response_format=verbose_json`。如果 vLLM 對 Qwen3-ASR 回 400，要退回 `json` 或 `text`，這需要改一行程式（加一個 fallback）。實測時要第一個確認。
- **B. XPU 不相容。** 音訊編碼器或 attention 在 XPU 上可能報錯。時間盒半天，不行就直接 No-Go。
- **C. 映像檔沒有這個模型類別。** 要先檢查 `vllm-openai-xpu:v0.26.0` 裡有沒有 `qwen3_asr.py`。不要為了它升級到 nightly，那會影響已經驗證過的 Qwen3-32B 配置。
- **D. 台語表現不佳。** 黑客松主打「中英台」。如果台語沒有改善，簡報只能說「華語與中英混用改善」，不能說「台語支援」。
- **E. 繁簡問題。** 輸出可能是簡體。opencc（`requirements-asr.txt`）必須裝在執行 Haven 的機器上。
- **F. Hybrid 時間戳的語意。** Qwen 重新轉寫的文字和 Whisper 的段落邊界可能對不齊（例如一句話跨兩段）。可以接受，因為段落是時間窗口，不是語句。但跨段的字可能重複或遺漏，要在評測裡量「漏句／重複」。
- **G. 時程。** 評測加整合大約 2.5–3 person-days，會壓縮 MVP 其他 P0 項目。

## 5. B70 實測計畫（11/28–11/29，時間盒 1.5 天）

### Step 0：可行性煙霧測試（半天以內，任何一步失敗就是 No-Go）
1. 在 Whisper 所在的 card 1（或單卡時同一張卡）用相同映像檔啟動：
   `CARD=1 PORT=8002 UTIL=0.15 CONTAINER=vllm-qasr bash scripts/b70-vllm.sh Qwen/Qwen3-ASR-1.7B qwen3-asr`
   （`--max-model-len` 依啟動錯誤訊息調整。先確認映像檔裡有 `qwen3_asr.py`。）
2. 用 curl 分別以 `response_format=verbose_json` 和 `json` 送一段既有的 23 秒錄音，記下回應格式、文字內容、延遲。
3. 送一段約 10 分鐘和一段 46 分鐘的合成錄音，確認沒有被截斷。
4. 用 `xpu-smi` 記錄記憶體用量，確認 Whisper、Qwen3-ASR 和 Qwen3-32B 擺得下（雙卡時 Qwen3-32B 獨佔 card 0）。

### Step 1：對照評測（1 天）
- **素材**（自製或合成、有授權、不用真實個案）：
  - 6 段台灣華語
  - 6 段中英混用
  - 6 段台語或華台混用
  - 6 段關鍵語意：否定、時間、主詞
  - 1 段 ≥ 30 分鐘的模擬晤談
  - 時間允許的話，加三語切換
- **對照組**：
  - (a) Whisper（現況）
  - (b) Qwen3-ASR 直接轉寫整段
  - (c) Hybrid：Whisper 切段，Qwen3-ASR 重新轉寫每段
  - 選做 (d) TEA-ASR-1.1：架構相同，如果 vLLM 能載入，就只加一列
- **指標**：
  - 中文 CER、英文 WER、混用 MER，正規化規則統一（轉繁、去標點、英文小寫）
  - 關鍵語意錯誤數，人工判定
  - 漏句和重複
  - 長音訊完整性
  - 時間戳：(a) 和 (c) 段落起點誤差，抽 20 段
  - 延遲與 GPU 記憶體
- **台語**：分三種輸出目標分開評，不跨目標比較 CER：
  - 忠實台語漢字或台羅
  - 台語正字
  - 華語語意

### Step 2：Go / No-Go（11/29 中午前決定）
**Hybrid 只有在以下條件全部成立時才採用：**
1. Step 0 全部通過：在 XPU 上穩定、長音訊沒有截斷、記憶體擺得下。
2. 中英混用 MER 相對 Whisper 改善 ≥ 20%，而且台灣華語 CER 不比 Whisper 差。
3. 關鍵語意錯誤 ≤ Whisper。
4. 漏句加重複 ≤ Whisper。
5. 主機上有 ffmpeg，或可以用既有容器裡的 ffmpeg。
6. 整合估計可以在 12/01 前完成，不擋 MVP 的 critical path。

**其他結果：**
- **No-Go**：維持 Whisper。評測結果照樣寫進簡報，作為「做過比較」的證據與 Roadmap。
- **只過條件 1–4、沒有 ffmpeg**：MVP 維持 Whisper。Qwen3-ASR 只當成選用的「重新轉寫此段」功能（P1），或列入 Roadmap。

## 6. 對 MVP Spec 的影響

- ADR-005 改為「Whisper 為主（時間戳），Qwen3-ASR 以 Hybrid 方式有條件採用」。
- T16 拆成三張：
  - **T16a**：Step 0 煙霧測試
  - **T16b**：對照評測，含 `scripts/asr_eval.py` 的多語分組
  - **T16c**：Hybrid 整合，只有 Go 才做，約 1–1.5d
- T08 回放和 T16c 共用新的 **T07a「音訊正規化」**：用 ffmpeg 統一轉成 16 kHz WAV 或 opus，產生的檔案依 §11.2 的規則管理。
- 會後口述大多是單人短音檔，可以考慮直接用 Qwen3-ASR，時間戳用估計值就夠（P1 選項，不必等 Hybrid）。

## 7. 還不知道、需要 B70 回答的事

- 映像檔裡有沒有 Qwen3-ASR、在 XPU 上能不能跑、`verbose_json` 的行為、`max_audio_clip_s` 的實際值、長音訊怎麼切。
- 台灣台語的實際表現。
- ffmpeg 是否可用。
- TEA-ASR-1.1 能不能用 vLLM 載入。

## Sources
- Qwen3-ASR-1.7B 模型卡：https://huggingface.co/Qwen/Qwen3-ASR-1.7B
- Qwen3-ASR GitHub：https://github.com/QwenLM/Qwen3-ASR
- Qwen3-ASR Technical Report：https://arxiv.org/abs/2601.21337
- vLLM v0.26.0 `qwen3_asr.py`：https://github.com/vllm-project/vllm/blob/v0.26.0/vllm/model_executor/models/qwen3_asr.py
- vLLM recipe：https://github.com/vllm-project/recipes/blob/main/Qwen/Qwen3-ASR.md
- TEA-ASR-1.1：https://huggingface.co/JacobLinCool/TEA-ASR-1.1
- Breeze-ASR-25：https://huggingface.co/MediaTek-Research/Breeze-ASR-25
- WenetSpeech-Min：https://arxiv.org/abs/2609.36834
- （docs.vllm.ai 從這個環境無法讀取，以 GitHub 原始碼代替）
