# Release Gate：真實端到端（Ticket 17，AC-12／AC-13）

狀態：**程序已寫好，尚未在 B70 上執行。** 結果填在 `docs/release-gate/<日期>-result.md`（複製下方〈結果表〉）。
本文件只描述步驟；在使用者實際跑過之前，任何數字都不存在。

所有錄音都必須是模擬或合成內容，不得使用真實諮商錄音。

## 0. 準備（B70 主機，JupyterLab terminal）

```bash
cd ~/haven-copilot && git fetch origin && git checkout <要驗收的 branch> && git pull
bash scripts/b70-deploy.sh                     # 三個模型服務；結果在 docs/deploy/b70-dual-<時間>.md
python3 -m unittest discover -s tests          # Linux 上全套（含 5 個 POSIX 權限測試）必須全過
python3 scripts/add_user.py add wang --name 王心理師 --clients C001,C002   # 心理師 A（會問兩次密碼）
python3 scripts/add_user.py add lin --name 林心理師                          # 心理師 B，沒有個案
python3 scripts/add_user.py add admin --name 所長 --role admin
```

素材：
- **完整晤談（≥ 30 分鐘）**：`~/L50-tts.ogg`（C002-L50 劇本的合成朗讀，46 分鐘）。
- **會後口述（約 2 分鐘）**：由組員以心理師身分朗讀一段**虛構**口述（例如下方範本），用 Chrome 的錄音功能錄，或事先錄成檔案上傳。

> 口述範本（虛構）：「今天是第三次會談。個案這週提到工作上和主管的衝突，說週二開會時被當眾指正，回家後整晚睡不著。我們討論了當時的想法和身體反應，個案說以前沒有注意過自己會先肩膀緊繃。下次會談前，個案同意記錄兩次類似情境。」

## 1. 啟動 Haven（記錄時間）

```bash
HAVEN_LLM=openai HAVEN_LLM_URL=http://127.0.0.1:8000 HAVEN_MODEL=qwen3-32b-awq \
HAVEN_ASR=openai HAVEN_ASR_URL=http://127.0.0.1:8001 HAVEN_ASR_MODEL=whisper \
HAVEN_ASR_REFINE=1 python3 -m haven 2>&1 | tee docs/release-gate/haven-$(date +%Y%m%d-%H%M).log
```

另開一個 terminal 記錄 GPU 記憶體（整個流程期間）：

```bash
while true; do date +%T; xpu-smi stats -d 0 | grep -i memory; xpu-smi stats -d 1 | grep -i memory; sleep 10; done \
  > docs/release-gate/xpu-$(date +%Y%m%d-%H%M).log
```

## 2. 筆電：SSH tunnel，Chrome 開 http://localhost:8765

```bash
ssh -L 8765:127.0.0.1:8765 <user>@<b70-host>
```

（只能經 JupyterLab 時，用平台提供的 port forwarding，並在結果表註明。）

### 2b. 不能 SSH 時：經 JupyterLab 的 jupyter-server-proxy（B70 實際用法，2026-11-28）

B70 上已裝 `jupyter_server_proxy 4.6.0`。Haven 仍綁 127.0.0.1:8765，瀏覽器改開
`https://<JupyterLab 主機>/proxy/8765/`（**結尾的 `/` 不能省**），先過 Jupyter 的登入。
網頁的 API、匯出、回放都用相對路徑，所以可以在 `/proxy/8765/` 底下運作（本機以模擬 proxy 驗證過）。
proxy 會把 JupyterLab 的主機名稱放在 Host header，Haven 啟動時要把它加進 `HAVEN_HOSTNAMES`
（只放寬這一個名稱的 DNS-rebinding 檢查，Haven 仍只聽 loopback）。注意：這不是 secure context 以外的問題，
HTTPS 由 JupyterLab 提供，瀏覽器錄音可用；但 cookie 沒有 Secure 旗標（Haven 自己是 HTTP）。結果表的「tunnel 方式」請寫 jupyter-server-proxy。

## 3. 流程（兩段錄音都做一次）

| # | 動作 | 要看到 | 計時 |
|---|---|---|---|
| 1 | 以心理師 A 登入，選 C002，上傳 `L50-tts.ogg`，模式「完整晤談錄音」，勾選同意 | 立刻進入處理中畫面（202），階段 轉寫 → 說話者 → 草稿 | 上傳到 job succeeded 的總時間；各階段時間看 job 畫面或 Haven log |
| 2 | 完成後自動開啟審閱頁 | 草稿引擎標示為 `qwen3-32b-awq`（**不是**規則引擎）；模板為單次晤談質化紀錄 | |
| 3 | 點任一逐字稿段落的 ▶ | 從該段起點播放原音 | |
| 4 | 修改一段逐字稿文字 | 段落標「已修改」，引用句標「來源已修改」，出現需要重新核對橫幅 | |
| 5 | 改寫人工欄位的「待心理師填寫」，處理紅色句子，按「我已依目前逐字稿核對」 | 確認鍵可用 | |
| 6 | **登出，再登入**（AC-13），打開同一 session | 草稿修改、逐字稿修改、版本狀態都在 | |
| 7 | 確認紀錄 | 成功；原音狀態變成已清除（預設政策） | |
| 8 | 匯出 Markdown 與純文字，標記已歸檔 | 檔案含模板欄位、確認者、時間、雜湊與聲明，不含逐字稿 | |
| 9 | 口述：模式「會後口述」，瀏覽器錄音或上傳 | 所有段落為心理師（口述），沒有說話者確認；草稿為「心理師口述…」語態 | 同上 |
| 10 | 口述重做 3–8 | 同上 | |

另外：心理師 B 登入後，看不到 C002，直接輸入 A 的 session 網址得到 404。

## 4. 備援影片

用螢幕錄影把第 3 節流程錄下來（完整晤談可剪掉等待），檔名標示 `Recorded-<日期>`，不放進 repo（含 UI 截圖時確認只有模擬資料）。

## 5. 結果表（複製到 `docs/release-gate/<日期>-result.md`）

```
日期／commit：
Live 或 Recorded：
部署：dual / single（docs/deploy/...md）
tunnel 方式：

| 階段 | 完整晤談（46 分） | 口述 |
|---|---|---|
| 上傳到 job 開始 | | |
| 轉寫（Whisper＋Qwen3-ASR） | | |
| 說話者 | | （跳過） |
| 草稿 | | |
| 上傳到可審閱 | | |
| 草稿引擎名稱 | | |
| GPU 記憶體峰值 card0／card1 | | |
| 回放／編輯／確認／匯出 | ✓/✗ | ✓/✗ |
| 登出再登入後狀態都在（AC-13） | ✓/✗ | ✓/✗ |
| 失敗與處理 | | |
```
