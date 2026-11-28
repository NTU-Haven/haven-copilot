---
title: Qwen3-ASR 評估與 ASR 決策（Haven Copilot MVP）
date: 2026-11-27
status: GO for the alignment hybrid (2026-11-27), with the limitations in §0.2; evaluation closed
supersedes: haven-copilot-mvp.md §8.1–8.3 and ADR-005 default "No-Go"
---

# Qwen3-ASR 評估與 ASR 決策

> §1–§4 是**文件評估**，資料來自官方模型卡、vLLM 原始碼與第三方論文和模型卡，裡面的效能數字都是原作者自己公布的。§0 是本專案在 B70 上的實測。

## 0. Step 0 實測結果（B70，2026-11-27，`scripts/qwen3-asr-smoke.sh`）

**結果：通過。** 條件：card 1 同時跑 Whisper（`UTIL=0.12`，:8001）與 Qwen3-ASR-1.7B（`UTIL=0.3`，`--max-model-len 8192`，:8002），映像為 `vllm/vllm-openai-xpu:v0.26.0`，未升級 vLLM。錄音是 `~/L50-tts.ogg`：C002-L50 劇本 1–339 段，由 macOS `say` 的兩個合成聲音朗讀，約 46 分鐘，以劇本為參考。報告：`docs/asr-eval/b70-qwen3asr-smoke-20261127-0629.md`。

| 項目 | Qwen3-ASR | Whisper（Haven 的呼叫方式：verbose_json） |
|---|---|---|
| XPU 啟動 | ✅ `device_config=xpu`，約 2 分 15 秒可回應 | 既有 |
| `verbose_json` | ❌ 400「Currently do not support verbose_json for qwen3-asr」 | ✅ 553 個帶時間戳的 segments |
| `json`／`text` | ✅ 200 | 200，但**掉字**（見下） |
| CER（轉繁後） | **1.2%**（json 與 text 相同） | **7.5%**（和 11/17 batch 10 的數字相同，基準可重現） |
| Coverage／最後 5 句 | 1.001／5 of 5，**沒有截斷** | 0.965／5 of 5 |
| 46 分鐘的轉寫時間 | 7.8–11.1 s | 9.0 s |
| card 1 記憶體 | 16.0 GiB（49%），兩個模型合計，為 vLLM 預先保留的量 | – |

發現：
1. **只有文字，沒有時間戳**，和 §2.2 的預測一致，必須採 Hybrid。
2. **Whisper 用 `json`／`text` 會掉字**：CER 20.5%、Coverage 0.81。Haven 用的是 `verbose_json`，不受影響；以後修改 ASR 呼叫時不能改用 `json`。
3. 準確度差距只能說明「在乾淨的合成華語上明顯比較好」：單一錄音、合成語音、純華語。**不能直接當作產品效能引用**。

**Step 1 的改變（11/27 決定）**：不另外錄製素材。改用兩個公開資料集（不需要 token），再加上 L50。**不評估台語**，簡報不宣稱台語能力。另外改用「對齊式 Hybrid」（§3.1），不需要 ffmpeg。

### 0.1 Step 1 實測結果（B70，2026-11-27）

報告：`docs/asr-eval/b70-asr-bench.md`（ASCEND 60 句＋L50）、`docs/asr-eval/b70-asr-bench-cszs.md`（CSZS 39 句）。第一次執行時 CSZS 沒有抓到資料，另外補跑。評分前兩邊都用 opencc s2twp 轉繁。

| 類別 | 句數 | Qwen3-ASR | Whisper | 相對改善 |
|---|---|---|---|---|
| `ascend-zh` CER | 40 | **8.1%** | 17.8% | −54% |
| L50 CER（合成語音，46 分鐘） | 1 | **1.1%** | 7.4% | −85% |
| `ascend-mixed` MER | 8 | 14.3% | 13.6% | +5%（樣本太小） |
| `cszs-mixed` MER | 39 | 19.4% | 23.5% | −17% |
| 中英混用合併 MER（mixed＋cszs，723 tokens） | 47 | 18.4% | 21.6% | **−15%** |
| `ascend-en` MER | 12 | 33.3% | 114.8% | Whisper 在 `language=zh` 下把英文翻成中文 |
| 失敗數 | 100 | 0 | 0 | |

**否定詞與時間詞**：用規則從 json 數出參考稿與轉寫之間的差異。簡繁先折疊，否則「沒／没」會被算成錯。這是全部句子的計數，取代原本計畫的「各抽 10 句人工看」。

| | 參考稿出現次數 | Qwen3-ASR 漏掉／多出 | Whisper 漏掉／多出 |
|---|---|---|---|
| 否定詞（L50＋短句） | 375 | **0／2** | 8／2 |
| 時間詞（L50＋短句） | 95 | 4／4 | 11／2 |

人工看過 Qwen3-ASR 的每一個差異：
- 多出的 2 個否定詞，一個是「從來不敢」變成「从来不不敢」（重複，意思沒變）；一個是 `football` 變成「这不。」（真的錯）。
- L50 的時間差異有 2 個只是寫法不同（7→七、4→四）。另一個是「覺察到這一點」變成「觉得查到九一点」，這是語意錯誤，但不是時間詞。
- Whisper 漏掉的時間詞包括「昨天」1 次、「今天」2 次。

**新發現的風險**：在 `language=zh` 下，Qwen3-ASR 也會把英文翻成中文，而且有時候意思會變，例如 Malacca 變成「马来西亚」、Latin letters 變成「拉丁字母」。這是中英混用改善不明顯的主要原因。見 §4 風險 H。

**對齊式 Hybrid（L50）**：553 段中有 2 段沒有分到文字（0.4%）。抽樣 20 段中 **17 段切點正確**，未達 18 段的門檻。切錯的 3 段都從 Whisper 30 秒窗格的起點開始（960.0、1230.0、1980.0），都是邊界附近幾個字跑到前一段。11/27 已修改 `align()`（commit b2431aa）：兩邊文字不一致的切點，改靠到 Qwen3-ASR 前後 6 字內最近的標點；兩邊一致的切點不動。

**重跑結果**（`docs/asr-eval/b70-asr-bench-l50-snap.md`）：有 5 個切點被移動，抽樣仍是同樣 3 段和 Whisper 不一致。查過全文後發現，**問題不在對齊**：
- 這 3 段都在 30 秒音訊接縫上。Qwen3-ASR 自己的文字在接縫處會多一個「。」，有時把一個詞切成兩半，偶爾錯一個字：「就能知道是。情是不是」、「這個方。法很適合」、「那種感覺。很難受」、「我從來不。不敢問」。Whisper 在同樣的接縫也會出錯，而且錯得更多：「世界上的事情。這件事情」（多 7 字）、「就個方這種方法」、「那種感覺我感到很難受」（「我感到」是多出來的）。
- 原本的判斷方式是「兩欄相同就算對」，等於把 Whisper 當作標準答案；在接縫處這個前提不成立。**改用劇本當參考**：1980.0 那段 Qwen3-ASR 是對的，是 Whisper 多出「我感到」，所以是 **18/20**。剩下 2 段的詞剛好跨在 30 秒接縫上，被分到相鄰兩段，Whisper 也一樣會把這種詞切開。
- 判讀方式也是在看到結果之後才改的，理由是原本的方式在接縫處會把 Whisper 的錯誤算到 Hybrid 頭上。

### 0.2 決定與限制（11/27）

**結論：Go，採用對齊式 Hybrid。** 條件 1–5 通過（條件 2 和條件 5 的判讀方式都是看到結果之後修改的，見上）。條件 6（12/01 前整合完成）由 T16c 的排程決定。專案負責人決定評測到此為止，不足的部分列為限制，不再加做實驗。

**限制**（簡報與文件應如實說明）：
1. **素材不是台灣晤談。** 短句是香港雙語者（ASCEND）和中英混用朗讀句（CSZS）；長錄音是合成語音（L50），所以 1.1% 的 CER 偏樂觀。沒有真實晤談錄音。
2. **沒有評估台語。** 不能宣稱支援台語。
3. **中英混用只小幅改善**（MER −15%）。兩個模型在 `language=zh` 下都會把英文翻成中文，Qwen3-ASR 有時會改變意思（風險 H）。沒有測過不送 `language` 的情況。
4. **30 秒接縫。** 兩個模型每 30 秒都有一個接縫（46 分鐘約 90 個）。Qwen3-ASR 會在接縫處多一個「。」，有時把詞切開或錯一個字。Hybrid 的段落文字在接縫附近可能有 1–3 個字跑到相鄰段落。這會影響逐句引用和驗證器在接縫附近的比對，但比 Whisper 在同一位置的錯誤少。
5. **樣本小。** `ascend-mixed` 只有 8 句；Hybrid 只人工看了 20 段；否定詞和時間詞是用規則計數，人工只看了差異的部分。
6. **只測一次。** 沒有重複測量，也沒有測延遲的變異。
7. **只在 B70 的這個配置上驗證過**（vLLM 0.26.0 XPU，card 1 同時跑兩個 ASR 模型）。

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

### 3.1 對齊式 Hybrid（Step 0 之後的建議做法）
Step 0 顯示 Qwen3-ASR 能一次完整轉寫 46 分鐘，不會截斷，所以不必切音訊：
1. Whisper（`verbose_json`）照常產生帶時間戳的段落，段落 id、`start`、`end` 都以它為準。
2. Qwen3-ASR（`json`）對整段音檔轉寫一次，取得全文。
3. 用字元層級的序列對齊（標準函式庫 `difflib.SequenceMatcher`，比對前先統一轉繁、去標點）把 Qwen 全文切回 Whisper 的段落。對齊相同的地方照字數對應；兩邊替換的地方依比例切開。

好處：不需要 ffmpeg，只多一個請求，Qwen 看得到整段上下文，某一段對不上時可以保留 Whisper 原文。
風險：Whisper 在某個地方錯很多時，切點可能偏一兩個字，下一段的開頭因此跑到上一段。
本地測試：在 L50 的 339 段上，先讓「Whisper」那一方每 15 個字錯 1 個字，再對齊回正確文字，339 段全部切對，耗時 0.4 秒。真實音檔的驗證要看 `scripts/asr_benchmark.py` 輸出的抽樣表。

切段式 Hybrid 與 ffmpeg 改為**備案**：只有在對齊式 Hybrid 的抽樣檢查不合格時才考慮。

**ForcedAligner 路線（不建議用在 MVP）：** 要在 XPU 上跑 transformers、把音訊切成 5 分鐘以內的窗口、把字級時間戳合回段落，還要另外處理說話者切段。改動最大、XPU 未知，也不支援閩南語。

## 4. 主要風險

- **A. `verbose_json` 可能不支援。** Haven 目前送的是 `response_format=verbose_json`。如果 vLLM 對 Qwen3-ASR 回 400，要退回 `json` 或 `text`，這需要改一行程式（加一個 fallback）。實測時要第一個確認。
- **B. XPU 不相容。** 音訊編碼器或 attention 在 XPU 上可能報錯。時間盒半天，不行就直接 No-Go。
- **C. 映像檔沒有這個模型類別。** 要先檢查 `vllm-openai-xpu:v0.26.0` 裡有沒有 `qwen3_asr.py`。不要為了它升級到 nightly，那會影響已經驗證過的 Qwen3-32B 配置。
- **D. 台語表現不佳。** 黑客松主打「中英台」。如果台語沒有改善，簡報只能說「華語與中英混用改善」，不能說「台語支援」。
- **E. 繁簡問題。** 輸出可能是簡體。opencc（`requirements-asr.txt`）必須裝在執行 Haven 的機器上。
- **F. Hybrid 時間戳的語意。** Qwen 重新轉寫的文字和 Whisper 的段落邊界可能對不齊（例如一句話跨兩段）。可以接受，因為段落是時間窗口，不是語句。但跨段的字可能重複或遺漏，要在評測裡量「漏句／重複」。
- **G. 時程。** 評測加整合大約 2.5–3 person-days，會壓縮 MVP 其他 P0 項目。
- **H. 英文被翻成中文。** Haven 送 `language=zh` 時，兩個模型都會把英文翻成中文。Qwen3-ASR 的翻譯有時會改變意思（Malacca→马来西亚）。逐句引用的是轉寫稿本身，所以這種錯誤會直接進入紀錄。整合時可以試試不送 `language`、讓模型自動判斷，但目前沒有測過。

## 5. B70 實測計畫（11/28–11/29，時間盒 1.5 天）

### Step 0：可行性煙霧測試（半天以內，任何一步失敗就是 No-Go）
1. 在 Whisper 所在的 card 1（或單卡時同一張卡）用相同映像檔啟動：
   `CARD=1 PORT=8002 UTIL=0.15 CONTAINER=vllm-qasr bash scripts/b70-vllm.sh Qwen/Qwen3-ASR-1.7B qwen3-asr`
   （`--max-model-len` 依啟動錯誤訊息調整。先確認映像檔裡有 `qwen3_asr.py`。）
2. 用 curl 分別以 `response_format=verbose_json` 和 `json` 送一段既有的 23 秒錄音，記下回應格式、文字內容、延遲。
3. 送一段約 10 分鐘和一段 46 分鐘的合成錄音，確認沒有被截斷。
4. 用 `xpu-smi` 記錄記憶體用量，確認 Whisper、Qwen3-ASR 和 Qwen3-32B 擺得下（雙卡時 Qwen3-32B 獨佔 card 0）。

### Step 1：對照評測（半天，`scripts/asr_benchmark.py`）
- **素材**（11/27 改為公開資料集，不需要 token，也不另外錄製）：

  | 資料集 | 內容 | 抽樣 | 授權 | 限制 |
  |---|---|---|---|---|
  | `CAiRE/ASCEND` test（1,315 句） | 自然對話，23 位香港雙語者，每句標註 zh／en／mixed | 平均間隔抽 60 句 | CC BY-SA 4.0 | 不是台灣口音；參考稿是簡體 |
  | `ky552/cszs_zh_en` test（3,176 句）的 `correct_*` | 中英混用句子（ICASSP 2024 benchmark） | 平均間隔抽 40 句 | CC BY 4.0 | 講者與錄製方式未說明；參考稿是簡體 |
  | `~/L50-tts.ogg`（C002-L50 1–339） | 46 分鐘合成語音的模擬晤談 | 1 段 | 本專案 | 合成語音；用來檢查對齊式 Hybrid |

  兩個公開資料集都是 TEA-ASR 和 Breeze-ASR 報告中用過的，結果方向可以對照（但評分方式不同，不能直接比數字）。**不評估台語**。
- **對照組**：Qwen3-ASR（`json`）vs Whisper（`verbose_json`，Haven 的呼叫方式）；L50 另外做對齊式 Hybrid。TEA-ASR 與切段式 Hybrid 不做。
- **指標**：
  - MER（中文字＋英文詞）、CER（只算中文字）、WER（只算英文詞），依類別分開做 micro-average。
  - 評分前兩邊都用 opencc s2twp 轉繁、轉小寫、去標點，簡繁不同不算錯。
  - 失敗數、總轉寫時間。
  - 列出 Qwen 相對 Whisper 表現最差的 5 句。
  - L50：Hybrid 的空段數、段落與 Whisper 的差異中位數、抽 20 段對照表，**人工看過**才算數。
- **關鍵語意錯誤**（否定、時間、主詞）：不另做資料集。從結果的 json 檔裡，人工檢查含否定詞或時間詞的句子各 10 句。

### Step 2：Go / No-Go（11/29 中午前決定）
**對齊式 Hybrid 只有在以下條件全部成立時才採用：**
1. ✅ Step 0 全部通過：在 XPU 上穩定、長音訊沒有截斷、記憶體擺得下（11/27 已確認）。
2. ✅（11/27 修改後的標準）華語：`ascend-zh` 的 CER 不比 Whisper 差（實測 −54%），L50 也不比 Whisper 差（−85%）。中英混用只作參考，不作為門檻（實測 −15%）。
   - 原本的標準是「`ascend-mixed` 與 `cszs-mixed` 的 MER 改善 ≥ 20%」，實測沒有達到。**這個標準是在看到結果之後才修改的**，11/27 由專案負責人決定。理由：Haven 的晤談以華語為主，中英混用只是少數，而且有改善、沒有變差；Qwen3-ASR 的主要優勢在華語。簡報只能說「華語明顯改善，中英混用小幅改善」。
3. ✅ 否定詞與時間詞的錯誤，Qwen ≤ Whisper（§0.1）。
4. ✅ 失敗數為 0，L50 沒有截斷。
5. ✅ L50 的 Hybrid 抽樣 20 段中，至少 18 段切點正確，而且空段數 ≤ 1%。空段 0.4%；以劇本為參考是 18/20（判讀方式於 11/27 修改，見 §0.1）。
6. 整合估計可以在 12/01 前完成，不擋 MVP 的 critical path。

**其他結果：**
- **No-Go**：維持 Whisper。評測結果照樣寫進簡報，作為「做過比較」的證據與 Roadmap。
- **條件 2–4 通過、條件 5 不通過**：先檢查是不是對齊參數的問題（時間盒半天）。修不好就考慮切段式 Hybrid（需要 ffmpeg），否則 MVP 維持 Whisper，Qwen3-ASR 列入 Roadmap。

## 6. 對 MVP Spec 的影響

- ADR-005 改為「Whisper 為主（時間戳），Qwen3-ASR 以 Hybrid 方式有條件採用」。
- T16 拆成三張：
  - **T16a**：Step 0 煙霧測試。✅ 11/27 完成。
  - **T16b**：對照評測，用 `scripts/asr_benchmark.py`（公開資料集＋L50）。
  - **T16c**：對齊式 Hybrid 整合，只有 Go 才做，約 1 天。不需要 ffmpeg；對齊函式從 `scripts/asr_benchmark.py` 的 `align()` 移進 Haven。
- **T07a「音訊正規化」**不再是 ASR 的前提，只和 T08 回放（webm 無法拖動播放）有關。
- 會後口述大多是單人短音檔，可以考慮直接用 Qwen3-ASR，時間戳用估計值就夠（P1 選項，不必等 Hybrid）。

## 7. 還不知道、需要 B70 回答的事

- 映像檔裡有沒有 Qwen3-ASR、在 XPU 上能不能跑、`verbose_json` 的行為、`max_audio_clip_s` 的實際值、長音訊怎麼切。
- 台灣台語的實際表現。
- ffmpeg 是否可用。
- TEA-ASR-1.1 能不能用 vLLM 載入。

## 8. 下一階段：全 Qwen3-ASR 路線（Deferred Decision，2026-11-28 記錄）

**狀態：** 不在 MVP 實作，下一階段開發再決定。

**為什麼要記下來：** B70 的 L50 實測（46 分鐘合成語音）中，Qwen3-ASR 單獨的 CER 是 1.1%，目前的對齊式 Hybrid 是 4.8%，只用 Whisper 是 7.5%。只看文字，全用 Qwen3-ASR 表現最好。

**為什麼 MVP 不採用：** Qwen3-ASR 在 vLLM v0.26.0 只輸出文字，沒有時間戳。Haven 有三個功能依賴段落和起訖時間：
- 逐句引用：段落編號；
- 段落回放：起點和終點；
- 說話者推測：逐段判斷。

Hybrid 把 Qwen3-ASR 的字對齊、填回 Whisper 的段落，保留時間軸，但有兩種損失：
1. Whisper 的幻覺段沒有 Qwen 的字可以替換（L50 有一段，約 2 個百分點；已用 `transcript.HALLUCINATIONS` 過濾已知句型）；
2. Whisper 漏掉整句的地方，Qwen 的字補不回去。

**全 Qwen3-ASR 需要先解決時間戳。** 候選做法：

| 做法 | 好處 | 未解的問題 |
|---|---|---|
| Qwen3-ForcedAligner 對 Qwen 全文做強制對齊 | 時間戳和 Qwen 文字一致，沒有 Whisper 的幻覺或漏句 | 單次上限 5 分鐘（需切段後拼接）；要在 XPU 上跑 transformers（未驗證）；不支援閩南語 |
| 先用 VAD／靜音偵測切段，每段分別送 Qwen3-ASR | 段落時間來自切點，準確度接近 Qwen 單獨 | 需要 ffmpeg 或 VAD 模型（不得新增 pip 依賴的限制要重新評估）；段長與斷句品質要調；請求數變多 |
| Qwen3-ASR 原生支援時間戳（等上游） | 不用自己做 | 時程不明 |

**決定時要量的東西：**
- 段落起點誤差（抽 20 段人工量，對照 Whisper）；
- CER；
- 46 分鐘的總轉寫時間；
- XPU 記憶體；
- 與說話者推測、引用、回放的相容性。

前提是在台灣華語真實模擬晤談素材上測，不能只用合成語音。台語仍需另外評估。

**觸發條件：** MVP 之後；或 Hybrid 在真實素材上的錯誤明顯高於 Qwen3-ASR 單獨（例如差距超過 2 個百分點）；或上游提供時間戳。

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
