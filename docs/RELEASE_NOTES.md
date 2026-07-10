# Realtime Audio Translator 發布說明

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
3. 第一次開啟會提示 runtime / model 診斷；可用「場景」選遊戲、Discord、會議、字幕-only 或雙向翻譯場景，選擇後會自動套用預設；進階模式可按「自動優化」讓 AI 決策中樞依場景、硬體、延遲與診斷結果調整設定，再按「一鍵診斷」檢查設定。
4. 低階電腦可在進階模式把「效能模式」改成離線省資源 `offline_light`。
5. 狀態列會顯示信心提示、延遲、翻譯服務、本機/雲端模式與是否可能產生費用。
6. 若語言判斷信心偏低，診斷會提示把「來源語言」從 `auto` 改成固定語言。
7. 可按「檢查更新」檢查 GitHub Releases 是否有新版本。
8. 可按「匯出字幕」把最新 JSONL 對話紀錄匯出成 SRT 與 TXT，檔案放在 `%USERPROFILE%\.realtime-audio\exports\subtitles`；也可按「清除本機資料」一次清除快取與紀錄。
9. 預設是自動發話；勾選「啟動時先靜音」後可用「按住說話」（Push to talk）按住才送出我方翻譯語音。
10. 切換到 Google 或 OpenAI 時，工具會先提示語音或文字可能傳送到第三方服務並可能產生費用。
11. 若提示缺 runtime，按「一鍵安裝 runtime」自動下載、驗證與解壓；自動安裝失敗時，再手動下載並解壓兩個 runtime 壓縮檔：

```text
RealtimeAudioTranslator-runtime-cuda12-core-<tag>.7z
RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.zip
```

兩個壓縮檔都解壓到：

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

主程式不需要安裝 Python。

若 Release 沒有 runtime 檔案，可到 https://github.com/Purfview/whisper-standalone-win/releases 下載 Faster-Whisper-XXL Windows runtime 和 `cuBLAS.and.cuDNN_CUDA12_win_v3.7z`。

本機翻譯預設使用 Argos Translate 離線模型。進階模式按「下載離線翻譯模型」會下載目前語言的雙向模型並放到：

```text
%USERPROFILE%\.realtime-audio\models\translation
```

若無法在 App 內下載，下載 `RealtimeAudioTranslator-models-translation-<tag>.zip`（透過英文中繼支援中文、英文、日文、韓文），解壓到 `%USERPROFILE%\.realtime-audio\models`，保留內含的 `translation` 資料夾。其他語言配對請在 App 內切換語言後下載。也可在進階模式的「本機翻譯 URL」填入 LibreTranslate 端點。

翻譯快取會保存在 `%USERPROFILE%\.realtime-audio\cache\translation_cache.db`，術語可用「新增術語」加入，也可用「修正上次翻譯」修正最近一句，確認後加入術語，或用「開啟術語表」編輯。

對話紀錄預設關閉；會議場景要開啟前會詢問，允許後才把對話紀錄存在本機。需要清除本機資料時，可在進階模式按「清除快取」/「清除紀錄」，清除翻譯快取、暫存音訊與對話紀錄。

## VB-CABLE 設定

1. 會議軟體或 Discord 的麥克風選 `CABLE Output (VB-Audio Virtual Cable)`。
2. 本工具的「TTS 輸出」選 `CABLE Input`。
3. 本工具的喇叭選你正在聽對方聲音的裝置。
4. 若要聽對方語音翻譯，開「播放對方翻譯」，「對方翻譯播放輸出」可留空使用系統預設喇叭。
5. 本工具的麥克風選你的實體麥克風。

## 常見問題

- 沒有字幕：確認「顯示字幕」已開啟，並按「測試字幕」。
- 聽不到對方聲音：確認喇叭來源選的是 Discord 或遊戲正在播放的裝置，再按「測試喇叭」。
- 找不到 runtime：確認 core `.7z` 與 DLL `.zip` 都已解壓到 `%USERPROFILE%\.realtime-audio\runtime\cuda12`。
- 找不到模型：在工具內下載模型，或解壓模型 zip 到 `%USERPROFILE%\.realtime-audio\models`。
- 找不到離線翻譯模型：按「下載離線翻譯模型」，或解壓 `RealtimeAudioTranslator-models-translation-<tag>.zip` 到 `%USERPROFILE%\.realtime-audio\models`。
- 對方聽不到翻譯語音：確認「播放翻譯語音」與「輸出到虛擬麥克風」已開啟，且「TTS 輸出」選 `CABLE Input`。
- Discord 沒有收到虛擬麥克風聲音：Discord 麥克風請選 `CABLE Output (VB-Audio Virtual Cable)`。
- 字幕延遲太高：在進階模式把「效能模式」改成 `low_latency`，並先用較小模型測試。
- GPU 無法使用：把「ASR 裝置」改成 CPU，或確認 CUDA12 runtime 已正確解壓。
