# 工程改善企劃與進度

基準：2026-09-07，`928d045`。前一輪介面改善見 [UI_REVIEW.md](UI_REVIEW.md)。本企劃持續追蹤功能、操作、可靠性、架構與效能，以可重現證據安排優先順序。

## 本輪落地

| 面向 | 已確認問題 | 實作與驗收 |
| --- | --- | --- |
| 設定正確性 | 隱藏布林值被 StringVar 轉成字串，False 可能變回 True | 只讀取有介面的設定，保留其他設定型別；回歸測試確認快取與自動優化保持關閉 |
| 設定操作 | 兩個音量共用例外處理，一個填錯重設兩個 | 使用既有數值範圍逐欄轉換；25 與 invalid 轉為 25 與 100 |
| 設定一致性 | UI 最大語句可為 100，存檔卻回復 2 | UI 共用設定範圍與正規化，儲存後同步顯示實際值；最大語句限制為 3 秒 |
| 資料可靠性 | 存檔失敗時引擎及記憶體已套用新設定 | 先儲存再套用；套用失敗還原原設定檔；還原失敗也明確回報 |
| 舊版遷移 | 舊版布林別名字串 false 會啟用紀錄 | 使用已驗證的布林值；首次載入獨立 state 同樣正規化 |
| 引擎架構 | 新紀錄 writer 建立失敗會丟失舊 writer 參照 | 先準備新資源，成功後才切換設定與 writer；失敗仍保留原設定 |
| 匯出功能 | 只能匯出最新紀錄 | 選擇任意 JSONL，另提供開啟匯出資料夾 |
| 操作效能 | 讀取及格式化紀錄占用 Tk 主執行緒 | 使用既有 AppController 背景工作；延遲匯出測試確認 Tk 計時回呼仍正常執行 |
| 資料一致性 | SRT/TXT 各讀一次，排序與內容可能不一致 | 共用一次讀取、驗證與排序的快照，檔案讀取次數由 2 次減為 1 次 |
| 匯出可靠性 | 寫入失敗可能覆蓋舊檔或只留下其中一檔 | 暫存目錄完成兩份檔案後才發布至唯一子目錄；舊單檔 API 仍保留且改用原子替換 |
| 錯誤提示 | 非物件 JSON、非有限時間與空紀錄處理不完整 | 格式錯誤回報行號；僅統計紀錄跳過、全空則明確拒絕；保留舊時間 fallback |

## 後續檢查項目

以下保留在目標內，尚未宣稱完成；需要以量測或重現決定修法，避免只憑推測重寫架構。

1. **長時間執行**：紀錄保留掃描與翻譯快取讀取已量測並改善，紀錄滿載已有累計遺失提示；GUI 事件泵已分批交還 Tk 控制權。
2. **音訊操作**：已補 EXE 辨識硬體參數與需重新開始提示，修正 WASAPI 取樣率及專案內模型路徑解析，完成虛擬音訊往返與麥克風擷取；來源實際拔插重連仍待驗證。
3. **設定輸入**：運算精度選項已與目前 CLI 清單對齊並驗證持久化；損毀或結構錯誤的 commands.json 已回退至既有預設選項，並保留混合清單中的有效字串。
4. **關閉與資料完整性**：writer／引擎停止逾時已串接保留與重試；紀錄寫入／清理錯誤及佇列遺失數量已加入執行狀態與停止結果。EXE 辨識已接上取消事件，停止時終止所啟動的程序並等待退出後清理暫存檔。
5. **實機驗證**：兩組麥克風擷取、VB-CABLE 測試音往返、實際 Whisper EXE 辨識與取消已通過；來源拔插重連、跨螢幕 DPI 與雲端 API 延遲仍待驗證。單句合成語音成功不等於所有語音情境的品質驗收。

本輪未加入新框架或相依套件，沒有推送、建立 PR 或發布。

### commands.json 容錯補充

基準 `4c3391a`。共用讀取函式驗證根物件、選項 metadata 與 choices 清單型別，避免合法 JSON 的 `null`／陣列或錯誤欄位型別中斷整批裝置與模型更新。清單僅保留非空字串，去除首尾空白，不把物件或布林值轉成假選項；沿用 GUI 原有 fallback，不覆寫使用者快取檔。

新增 2 項回歸測試，涵蓋不存在、截斷、非 UTF-8、錯誤 JSON 結構與混合型別清單。`scripts/test.ps1`：466 項測試與 compileall 通過；`git diff --check` 通過。仍有既有 `PurePath.is_reserved` 棄用警告，runtime zip 包裝依預期略過。

### 紀錄錯誤與遺失回饋

基準 `fb04c9d`。引擎現在把 writer 的佇列丟棄數量及寫入／清理錯誤加入後續翻譯狀態與停止結果，支援繁中、英文。切換紀錄設定後，已結束的 writer 只保留累計數量與錯誤文字；停止逾時重試不會重複計算。寫入與清理共用既有錯誤欄位，因此不宣稱錯誤等同整筆未寫入；JSONL 成功、Markdown 失敗也可能發生。

新增 3 項真實 writer 回歸測試，覆蓋最後一筆寫入失敗、滿載丟棄兩筆、停用／切換紀錄 policy 與停止重試。`scripts/test.ps1` 469 項及 compileall 通過；最後將已結束 writer 的例外改存文字後，另重跑這 3 項及 compileall 通過。既有棄用警告與 runtime zip 略過不變。沒有新增輪詢；最後一筆非同步錯誤會在下一次翻譯狀態或停止時顯示。

### GUI 事件處理公平性

基準 `2c6d692`。原本事件泵持續清空佇列，慢回呼或持續新增的事件會延後 Tk 計時器與重繪。現在每輪最多處理 100 件或約 8 毫秒；尚有事件時以 1 毫秒計時器接續，閒置仍維持 50 毫秒。保留 FIFO、既有來源過期判斷與關閉回呼；不丟棄正常事件。時間預算只在回呼之間檢查，不能中斷單一耗時回呼。

新增 2 項測試，確認慢回呼分批且保持順序、回呼持續新增事件也會讓出控制權；原有 1000 筆字幕壓力測試仍驗證完整交付。真實 Tk 使用 20 個各約 5 毫秒的回呼，計時器在完成第 2 個時執行，之後 20 個依序完成。80 項 GUI 測試及 `scripts/test.ps1` 全部 471 項、compileall 通過，`git diff --check` 通過。既有棄用警告與 runtime zip 略過不變。

### 翻譯快取讀取成本

基準 `83d693a`。`_ensure_cache` 原本每次讀取都執行 `PRAGMA user_version = 2`，即使版本已相同，仍要求寫入鎖。現在只在版本升級時設定版本號；保留每次 schema 檢查、舊資料遷移及新版拒絕，沒有加入連線池或快取 schema。

本機暫存資料庫包含一筆命中紀錄，前後版本暖機後交錯 5 輪，每輪 100 次 `cached_translation`。每次讀取中位數由 **12.244 降至 4.284 毫秒（降低約 65.0%）**；限本機檔案系統與當時負載，不代表翻譯端到端延遲。基準函式由 `git show 83d693a:realtime_audio_translator/ai_memory.py` 載入，兩版使用相同資料庫與查詢鍵。

新增 `test_cache_hit_does_not_require_writer_lock`，另一條真實 SQLite 連線持有 `BEGIN IMMEDIATE` 時驗證命中與未命中查詢。以基準程式執行此測試，確認會出現 `database is locked`；修正版通過。18 項快取測試、`scripts/test.ps1` 全部 472 項與 compileall 通過；`git diff --check` 通過。既有棄用警告與 runtime zip 略過不變。

### EXE 辨識取消

基準 `535dd9b`。引擎將該次執行的取消事件傳入 `AudioTranscriber`；EXE 每 50 毫秒檢查取消。取消後終止所啟動的程序、等待退出，再清理暫存音訊；等待辨識鎖的下一個呼叫也會檢查取消，避免停止後另開 EXE。標準輸出與錯誤使用暫存檔，避免大量輸出堵塞管線；正常完成仍保留 JSON 與非零退出錯誤處理。未提供取消事件的直接呼叫維持原行為。

新增 2 項測試：真實子程序正常完成、非零退出、大量 stdout／stderr 後取消，以及等待辨識鎖時取消；確認程序退出、暫存檔移除、取消結果不含字幕。另驗證引擎開始與停止共用同一取消事件。122 項音訊／引擎測試、`scripts/test.ps1` 全部 474 項與 compileall 通過；`git diff --check` 通過。既有棄用警告與 runtime zip 略過不變。

限制：本輪使用受控子程序，尚未驗證實際 Whisper EXE；Python 模型的原生推論仍無法強制中斷，持續由既有停止逾時與重試處理。本機裝置列舉可找到 WASAPI 麥克風及 VB-CABLE，但列舉不等於錄音、重連或播放驗證。

### WASAPI 取樣率與實機音訊

基準 `fa35dbd`。實機 `check_input_settings`／`check_output_settings` 確認裝置原生 48 kHz 可用，但固定 16／24 kHz 會拋出 `Invalid sample rate`。共用 `play_linear16` 現在僅對 WASAPI 啟用 `WasapiSettings(auto_convert=True)`，交由 Windows 在共享模式轉換取樣率；測試音也改走此共用路徑。麥克風音量測試改用既有 `capture_audio`，沿用原生取樣率、單聲道轉換與 overflow 回報。

依據 [sounddevice WASAPI 設定](https://github.com/spatialaudio/python-sounddevice/blob/master/doc/api/platform-specific-settings.md) 與[版本紀錄](https://github.com/spatialaudio/python-sounddevice/blob/master/doc/version-history.md)，`auto_convert` 自 0.4.7 提供；專案已要求 `sounddevice>=0.5`，不新增相依套件。

- 真實 VB-CABLE：以服務的 `test_tone` 播放 24 kHz、440 Hz、0.25 秒測試音；48 kHz 輸入回收峰值為 **440.43 Hz**、RMS **0.088851**，未回報 callback 錯誤。
- 兩組真實 WASAPI 麥克風：使用服務的 `test_microphone` 各擷取 0.5 秒成功，只計算音量，沒有保存錄音；不以安靜環境的音量推論辨識品質。
- 新增 3 項測試，確認 WASAPI／其他 host API 的設定分流、測試音共用播放路徑，以及擷取 RMS 與例外回報。59 項相關測試與 `scripts/test.ps1` 全部 **477 項**、compileall 通過，`git diff --check` 通過；既有棄用警告及 runtime zip 略過不變。

本機 runtime 原先缺少安裝清單，已使用既有原地匯入流程建立並驗證雜湊。CUDA 探測第一次超過原有 5 秒上限；獨立重試在 3.66 秒回傳 `CUDA device: 1`。另確認診斷能找到專案 `_models` 的模型，但辨識器仍只查使用者模型目錄，後續須修正兩者不一致。沒有把本機 runtime、錄音或測試產物加入 Git。

### 本機模型解析與實際 EXE

基準 `bb22fc3`。辨識器現在重用 `models.model_path`，優先選擇完整的設定目錄模型，再查專案 `_models`／`models`；不選擇帶有 `.partial` 的未完成模型。Python 使用解析後的完整目錄，EXE 使用該目錄的父目錄與模型名稱，避免忽略已存在的模型而重新下載。

本機 Faster-Whisper-XXL r245.4 不接受將絕對路徑直接傳給 `--model`，它會在 `--model_dir` 下尋找 `faster-whisper-<模型名>`。因此 EXE 路徑會明確拒絕其他目錄命名，避免 Python 選到 `small` 而 EXE 默默使用旁邊另一份 `faster-whisper-small`；Python 仍可使用這類目錄。這個限制由實際 CLI 輸出確認，沒有改名或移動使用者模型。

- 新增 3 項測試，涵蓋設定目錄、兩種專案目錄、完整模型優先、未完成模型跳過，以及 EXE 不使用不同的相鄰模型。
- 以修正後建構流程通過實際 runtime 雜湊檢查，強制進入 EXE 備援，確認使用專案 `large-v3-turbo` 模型辨識出 `This is a real-time audio translation test.`，未下載另一份模型。
- 同一辨識器再次執行時，0.5 秒發出取消，**0.51 秒**返回空結果；驗證取消不交付過期字幕。
- 52 項音訊測試、`scripts/test.ps1` 全部 **480 項**與 compileall、`git diff --check` 通過。既有棄用警告與 runtime zip 略過不變。

本機建構階段含 5.5 GB runtime 完整性驗證，共耗時 **77.97 秒**；這是啟動成本，不是單句辨識延遲，尚未降低該驗證成本。實際拔插重連需要使用者配合，目前尚無結果。

## 驗證紀錄

- `scripts/test.ps1`：451 項測試通過，compileall 通過；新增 13 項回歸測試。
- 真實 Tk 視窗：繁中 1000×760、英文 720×520，欄位邊界、工具搜尋、複製、背景匯出與事件回呼通過。
- `git diff --check` 通過。測試仍回報原有 `PurePath.is_reserved` 棄用警告，包裝測試依預期略過 runtime zip。
- 修改程式：`config.py`、`engine.py`、`gui.py`、`localization.py`、`subtitle_export.py`。
- 修改測試：`test_config.py`、`test_engine.py`、`test_gui_logic.py`、`test_subtitle_export.py`。另更新 `README.md` 與本企劃，共 11 檔。

## 第二輪：執行設定、停止收尾與紀錄成本

基準 `b1028ef`。

- EXE 備援原本忽略所選 `device` 與 `compute_type`，現在明確傳入兩個 CLI 參數。測試強制進入 EXE 路徑並驗證 CPU／float32 傳遞。依據為專案 `help.txt` 與 [Faster Whisper 文件](https://github.com/SYSTRAN/faster-whisper/blob/master/README.md)。
- 設定 schema 現在保留 CLI 提供的完整精度選項，包含原本選得到卻會被重設的 float32；裝置與精度選單使用共用 schema 作為 fallback 並過濾不支援項目。
- 開始時保留擷取設定快照；來源／模型／切句設定變更後，底部持續列出需停止重開的項目，避免把「設定已儲存」誤認為目前擷取已切換。即時音量不列入重開項目。
- 切換紀錄 policy 改為非阻塞收尾；尚未結束的 writer 留在引擎內，停止時一起等待。真實 writer 阻塞且佇列已滿的測試，確認解除阻塞後兩筆紀錄仍完整保存。
- 控制器停止逾時／例外時保留引擎，完成旗標由 worker 自行釋放；過期 UI 完成回呼不會覆寫下一次停止的狀態。
- 關閉期間仍交付控制器完成回呼。若停止未完成，取消關閉、保留視窗與錯誤提示，使用者可稍後重試，不進入無限自動重試。
- 紀錄保留使用單次掃描的檔案 metadata，移除重複 stat 與陣列首項刪除；每次仍驗證檔頭與外部更動，並立即執行期限／容量政策。兩項限制皆停用時不掃描。

### 效能量測

第二輪完整測試：`scripts/test.ps1` 464 項通過、compileall 通過。真實 Tk 視窗另驗證停止逾時取消關閉、保留引擎與再次停止成功；最後狀態存檔失敗的關閉防護另以 GUI 回歸測試確認。

500 個模擬 session、1000 檔，每個版本暖機後交錯執行 7 次。中位數 **247.20 → 127.58 ms（降低 48.4%）**；此數字限本機檔案系統與當時背景負載，不代表音訊端到端延遲。

重現方式（僅在暫存目錄建立模擬紀錄）：

```powershell
@'
import json, statistics, subprocess, tempfile, time
from pathlib import Path
from realtime_audio_translator.logbook import enforce_log_retention
baseline = {}
source = subprocess.run(
    ["git", "show", "b1028ef:realtime_audio_translator/logbook.py"],
    capture_output=True, encoding="utf-8", check=True,
).stdout
exec(source, baseline)
functions = {"before": baseline["enforce_log_retention"], "after": enforce_log_retention}
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    for index in range(500):
        stem = f"session-{index}"
        (root / f"{stem}.md").write_text(f"# Conversation {stem}\n", encoding="utf-8")
        row = dict(session_id=stem, timestamp="now", direction="audio",
                   source_language="en", target_language="zh", provider="local")
        (root / f"{stem}.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    for function in functions.values():
        function(root, 7, 100 * 1024 * 1024)
    samples = {name: [] for name in functions}
    for _ in range(7):
        for name, function in functions.items():
            started = time.perf_counter()
            function(root, 7, 100 * 1024 * 1024)
            samples[name].append((time.perf_counter() - started) * 1000)
    for name, values in samples.items():
        print(name, "median_ms =", round(statistics.median(values), 2))
'@ | python -
```
