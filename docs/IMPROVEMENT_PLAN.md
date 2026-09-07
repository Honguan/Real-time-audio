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

1. **長時間執行**：紀錄保留掃描已量測並改善；GUI 事件佇列、翻譯快取成本與滿載丟棄回饋仍待檢查。
2. **音訊操作**：已補 EXE 辨識硬體參數與需重新開始提示；來源重連仍需實機驗證。
3. **設定輸入**：運算精度選項已與目前 CLI 清單對齊並驗證持久化；損毀或結構錯誤的 commands.json 已回退至既有預設選項，並保留混合清單中的有效字串。
4. **關閉與資料完整性**：writer／引擎停止逾時已串接保留與重試；紀錄寫入錯誤、丟棄數量的使用者回饋仍待檢查。
5. **實機驗證**：麥克風、VB-CABLE、跨螢幕 DPI 與雲端 API 延遲仍需硬體／服務驗證；離線綠燈不能替代。

本輪未加入新框架或相依套件，沒有推送、建立 PR 或發布。

### commands.json 容錯補充

基準 `4c3391a`。共用讀取函式驗證根物件、選項 metadata 與 choices 清單型別，避免合法 JSON 的 `null`／陣列或錯誤欄位型別中斷整批裝置與模型更新。清單僅保留非空字串，去除首尾空白，不把物件或布林值轉成假選項；沿用 GUI 原有 fallback，不覆寫使用者快取檔。

新增 2 項回歸測試，涵蓋不存在、截斷、非 UTF-8、錯誤 JSON 結構與混合型別清單。`scripts/test.ps1`：466 項測試與 compileall 通過；`git diff --check` 通過。仍有既有 `PurePath.is_reserved` 棄用警告，runtime zip 包裝依預期略過。

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
