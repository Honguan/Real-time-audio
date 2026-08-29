# Realtime Audio Translator 發布說明

- 設定改用單一具 schema 版本的 `config/settings.json`，透過原子寫入與備份復原避免中斷或並行保存造成損毀；執行狀態分離至 `config/state.json`。

## v0.1.34

- 修正 runtime ASR 輸出時間戳混入字幕，以及離線翻譯的 SentencePiece 空白標記。

## v0.1.33

- 場景下拉選單改為顯示繁中名稱，設定檔仍保持舊版 key 相容。

## v0.1.32

- 補上客服對話與自己說話翻譯場景，雙向翻譯會自動啟用虛擬麥克風輸出。

## v0.1.31

- 更新壓縮包與 Release 頁面的 runtime 一鍵安裝說明。

## v0.1.30

- 新增「一鍵安裝 runtime」，會下載最新版 CUDA12 runtime、驗證 SHA-256 並用 Windows 內建工具解壓。

## v0.1.29

- 修正 Release exe 無法啟動的入口匯入錯誤。

## v0.1.28

- 「測試虛擬麥克風」現在會確認 `CABLE Output` 是否實際收到 TTS 音訊。

## v0.1.27

- 修正 Windows WASAPI loopback，喇叭／系統聲音現在可正常擷取。

## v0.1.26

- 修正只設定 `runtime_path` 時，App 未使用指定 runtime 的相容性問題。

## 最快使用

1. 到 GitHub Releases 下載 `RealtimeAudioTranslator-<tag>-win-x64.zip`。
2. 解壓後執行 `RealtimeAudioTranslator.exe`。
3. 第一次開啟會提示 runtime / model 診斷；可用「場景」選遊戲、Discord、會議、客服對話、字幕-only、自己說話翻譯或雙向翻譯場景，選擇後會自動套用預設；進階模式可按「自動優化」查看 AI 決策中樞依場景與硬體提出的設定前後預覽，只有確認後才會儲存，單次延遲、語速或 TTS 量測不會產生可持久化建議，再按「一鍵診斷」檢查設定。
4. 低階電腦可在進階模式把「效能模式」改成離線省資源 `offline_light`。
5. 進階狀態列會區分未校準模型分數、供應商品質訊號與啟發式提示，並顯示本機/雲端模式與費用風險；沒有可信分數時會明確顯示無法取得。
6. 診斷不再把語言判斷與 ASR 模型分數當成正確率，必要時會提示手動調整「來源語言」；自動調校也不會依未校準分數永久變更設定。
7. 可按「檢查更新」檢查 GitHub Releases 是否有新版本。
8. 可按「匯出字幕」把最新 JSONL 對話紀錄匯出成 SRT 與 TXT；schema v2 會依實際音訊起訖產生字幕，舊紀錄沒有音訊時間時沿用每段 3 秒。檔案放在 `%USERPROFILE%\.realtime-audio\exports\subtitles`；也可按「清除本機資料」一次清除快取與紀錄。
9. 預設是自動發話；勾選「虛擬麥克風啟動時靜音」後可用「按住說話」（Push to talk）按住才送出我方翻譯語音，且不影響本機翻譯播放。
10. 切換到 Google 或 OpenAI 時，工具會先提示語音或文字可能傳送到第三方服務並可能產生費用。
11. 若提示缺 runtime，按「一鍵安裝 runtime」自動下載、驗證與安裝；自動安裝失敗時，再手動下載兩個 runtime 壓縮檔：

```text
RealtimeAudioTranslator-runtime-cuda12-core-<tag>.7z
RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.zip
```

兩個壓縮檔解壓到同一個暫存資料夾，再於程式內按「手動匯入 runtime」選擇該資料夾；程式會驗證後安裝到：

```text
%USERPROFILE%\.realtime-audio\runtime\cuda12
```

解壓後該資料夾內應直接看到 `faster-whisper-xxl.exe` 與 CUDA12 DLL。
若檔案總管無法開啟 core `.7z`，請使用 7-Zip 解壓。

## 下載檔案

- 主程式：`RealtimeAudioTranslator-<tag>-win-x64.zip`
- CUDA12 runtime 核心：`RealtimeAudioTranslator-runtime-cuda12-core-<tag>.7z`
- CUDA12 runtime DLL：`RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.zip`
- 模型可選包：`RealtimeAudioTranslator-models-<model>-<tag>.zip`
- 檔案校驗：`SHA256SUMS.txt`

模型 zip 若有提供，請解壓到：

```text
%USERPROFILE%\.realtime-audio\models
```

主程式不需要安裝 Python。Whisper 模型資料夾會快速驗證 `config.json`、`model.bin`、`tokenizer.json` 與 `vocabulary.*`；診斷會區分模型缺失與不完整／損毀，並提示重新下載。

若 Release 沒有 runtime 檔案，可到 https://github.com/Purfview/whisper-standalone-win/releases 下載 Faster-Whisper-XXL Windows runtime 和 `cuBLAS.and.cuDNN_CUDA12_win_v3.7z`。

本機翻譯預設使用 Argos Translate 離線模型。進階模式按「下載離線翻譯模型」會下載目前語言的雙向模型並放到：

```text
%USERPROFILE%\.realtime-audio\models\translation
```

若無法在 App 內下載，下載 `RealtimeAudioTranslator-models-translation-<tag>.zip`（透過英文中繼支援中文、英文、日文、韓文），解壓到 `%USERPROFILE%\.realtime-audio\models`，保留內含的 `translation` 資料夾。其他語言配對請在 App 內切換語言後下載。也可在進階模式的「本機翻譯 URL」填入 LibreTranslate 端點。

翻譯快取會保存在 `%USERPROFILE%\.realtime-audio\cache\translation_cache.db`，術語可用「新增術語」加入，也可用「修正上次翻譯」修正最近一句，確認後加入術語，或用「開啟術語表」編輯。

對話紀錄預設關閉，任何場景都不會自動開啟；每次程式執行開啟前會詢問同意，並顯示內容、位置與期限，允許後才存在本機。進階設定可選原文與譯文、僅原文、僅譯文或不含對話文字；預設保留 7 天、上限 100 MB，使用有界背景佇列寫入並自動輪替清理，不阻塞字幕處理。對話檔位於 `%USERPROFILE%\.realtime-audio\logs\conversations`，應用程式事件另存於 `logs\app.log`。需要清除本機資料時，可按「清除快取」/「清除紀錄」，清除翻譯快取、暫存音訊與對話紀錄。

## VB-CABLE 設定

1. 會議軟體或 Discord 的麥克風選 `CABLE Output (VB-Audio Virtual Cable)`。
2. 本工具的「TTS 輸出」選 `CABLE Input`。
3. 本工具的喇叭選你正在聽對方聲音的裝置。
4. 若要聽對方語音翻譯，開「播放對方翻譯」，「對方翻譯播放輸出」可留空使用系統預設喇叭。
5. 本工具的麥克風選你的實體麥克風。

## 常見問題

- 沒有字幕：確認「顯示字幕」已開啟，並按「測試字幕」。
- 聽不到對方聲音：確認喇叭來源選的是 Discord 或遊戲正在播放的裝置，再按「測試喇叭」。
- 找不到 runtime：把 core `.7z` 與 DLL `.zip` 解壓到同一暫存資料夾，再使用「手動匯入 runtime」完成驗證安裝。
- 找不到模型：在工具內下載模型，或解壓模型 zip 到 `%USERPROFILE%\.realtime-audio\models`。
- 找不到離線翻譯模型：按「下載離線翻譯模型」，或解壓 `RealtimeAudioTranslator-models-translation-<tag>.zip` 到 `%USERPROFILE%\.realtime-audio\models`。
- 對方聽不到翻譯語音：確認「播放翻譯語音」與「輸出到虛擬麥克風」已開啟，且「TTS 輸出」選 `CABLE Input`。
- Discord 沒有收到虛擬麥克風聲音：Discord 麥克風請選 `CABLE Output (VB-Audio Virtual Cable)`。
- 字幕延遲太高：在進階模式把「效能模式」改成 `low_latency`，並先用較小模型測試。
- GPU 無法使用：把「ASR 裝置」改成 CPU，或確認 CUDA12 runtime 已正確解壓。
