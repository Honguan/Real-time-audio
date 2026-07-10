# Realtime Audio Translator

Windows x64 即時雙向語音翻譯工具。可擷取喇叭與麥克風聲音，轉文字、翻譯、顯示字幕 overlay，並可把翻譯語音送到 VB-CABLE 給 Discord、遊戲或會議軟體使用。

## 最快使用

1. 到 GitHub Releases 下載 `RealtimeAudioTranslator-<tag>-win-x64.zip`。
2. 解壓後執行 `RealtimeAudioTranslator.exe`。
3. 若提示缺 runtime，下載同一個 Release 裡的兩個 runtime 壓縮檔：

```text
RealtimeAudioTranslator-runtime-cuda12-core-<tag>.7z
RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.zip
```

把兩個壓縮檔都解壓到：

```text
%USERPROFILE%\.realtime-audio\runtime\cuda12
```

該資料夾內應直接看到 `faster-whisper-xxl.exe` 與 CUDA12 DLL。主程式已包含 Python runtime，不需要另外安裝 Python。
若檔案總管無法開啟 core `.7z`，請使用 7-Zip 解壓。

## 需要下載哪些檔案

- 必下載：`RealtimeAudioTranslator-<tag>-win-x64.zip`
- NVIDIA CUDA12 runtime 核心：`RealtimeAudioTranslator-runtime-cuda12-core-<tag>.7z`
- NVIDIA CUDA12 DLL：`RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.zip`
- 模型無法由工具下載時才下載：`RealtimeAudioTranslator-models-<model>-<tag>.zip`
- 校驗用：`SHA256SUMS.txt`

模型 zip 若有提供，請解壓到：

```text
%USERPROFILE%\.realtime-audio\models
```

Whisper 模型可放在 `models\whisper-small`；翻譯模型放在 `models\translation`；TTS 模型放在 `models\tts`。

## 第一次設定

1. 安裝 VB-Audio Virtual Cable。
2. 開啟 `RealtimeAudioTranslator.exe`。
3. 選擇「喇叭來源」、「麥克風來源」、「TTS 輸出」、來源語言與目標語言；若要聽對方語音的翻譯，可在進階模式調整「播放對方翻譯」。
4. 選擇「場景」後會自動套用常用場景。
5. 按「一鍵診斷」檢查 runtime、模型、音訊與 API 設定。
6. 按「測試麥克風」與「測試虛擬麥克風」確認主要聲音路由；虛擬麥克風測試會確認 `CABLE Output` 是否真的收到語音。
7. 需要更細的檢查時，切到進階模式按「測試字幕」、「測試喇叭」與「測試 TTS」。
8. 按「開始」開始翻譯。

## VB-CABLE 路由

1. 會議軟體或 Discord 的麥克風選 `CABLE Output (VB-Audio Virtual Cable)`。
2. 本工具的「TTS 輸出」選 `CABLE Input`。
3. 本工具的喇叭選你正在聽對方聲音的裝置。
4. 本工具的麥克風選你的實體麥克風。

## 常用功能

- 「顯示字幕」：顯示或隱藏字幕 bar。
- 進階模式的「字幕最上層」：讓字幕 bar 保持最上層。
- 「顯示原文」/「顯示譯文」：切換原文與譯文。
- 「播放翻譯語音」：開關翻譯語音輸出。
- 「啟動時先靜音」：啟動後先靜音，搭配「按住說話」變成按住才送出我方翻譯語音；不勾選時就是自動發話模式。
- 「輸出到虛擬麥克風」：開啟後才會把我方翻譯語音送到「TTS 輸出」。
- 「播放對方翻譯」：開啟後才會把對方語音翻譯播放到「對方翻譯播放輸出」；留空則使用系統預設喇叭。
- 「儲存對話紀錄」：對話紀錄預設關閉；會議場景要開啟前會詢問，允許後才把對話紀錄存在本機。
- 「開啟紀錄」：開啟紀錄資料夾，`app.log` 會記錄開始、停止、缺模型與字幕匯出事件。
- 「清除快取」/「清除紀錄」/「清除本機資料」：清除本機翻譯快取、暫存音訊與對話紀錄。
- 「匯出字幕」：把最新 JSONL 對話紀錄匯出成 SRT 與 TXT，檔案放在 `%USERPROFILE%\.realtime-audio\exports\subtitles`。
- 「開啟程式資料夾」：開啟 `%USERPROFILE%\.realtime-audio`，設定鏡像在 `config\settings.json`，術語表在 `config\glossary.json`，音訊裝置快照在 `config\audio_devices.json`。
- 「新增術語」：加入固定術語翻譯，例如 `cooldown` → `冷卻`。
- 「顯示語言」：在字幕前顯示語言代碼。
- 「場景」：選擇遊戲、Discord、會議、字幕-only 或雙向翻譯預設後會自動套用；進階模式也可按「套用場景」。
- 「效能模式」：可選 `low_latency`、`balanced`、`quality` 或離線省資源 `offline_light`。
- 「自動優化」：使用 AI 決策中樞依場景、硬體、延遲與診斷結果切換模型、裝置與低延遲設定。
- 「一鍵診斷」：顯示目前缺少的 runtime、模型、音訊或 API 設定。
- 「檢查更新」：檢查 GitHub Releases 是否有新版本。
- 狀態列會顯示信心提示、延遲、翻譯服務、本機/雲端模式與是否可能產生費用。
- 若語言判斷信心偏低，診斷會提示把「來源語言」從 `auto` 改成固定語言。

「按住說話」（Push to talk）是按住才暫時取消靜音；勾選「啟動時先靜音」時按住才輸出我方翻譯語音，未勾選時翻譯語音會自動輸出。

## 翻譯與 TTS

預設本機翻譯不會上傳雲端。進階模式可按「下載離線翻譯模型」，工具會下載目前來源語言與目標語言的雙向 Argos Translate 模型。Release 的 `RealtimeAudioTranslator-models-translation-<tag>.zip` 透過英文中繼支援中文、英文、日文、韓文互譯。下載後模型放在：

```text
%USERPROFILE%\.realtime-audio\models\translation
```

若無法在工具內下載，可從 GitHub Releases 下載 `RealtimeAudioTranslator-models-translation-<tag>.zip`（透過英文中繼支援中文、英文、日文、韓文），解壓到 `%USERPROFILE%\.realtime-audio\models`；資料夾內應保留 `translation`。其他語言配對請在工具內切換語言後下載。若要改用 LibreTranslate，請在進階模式把「本機翻譯 URL」填成例如：

切換到 Google 或 OpenAI 時，工具會先提示語音或文字可能傳送到第三方服務並可能產生費用。

```text
http://127.0.0.1:5000/translate
```

沒有「本機翻譯 URL」時會優先使用下載到程式資料夾的 Argos Translate 離線模型；也保留已安裝 Argos Translate 的相容支援。可改用 OpenAI 或 Google 翻譯服務。OpenAI 使用 `OPENAI_API_KEY` 環境變數，Google 使用 service account JSON 路徑。

「TTS 服務」可選本機、OpenAI 或 Google。進階設定可調「OpenAI 模型」、「OpenAI TTS 聲音」、「OpenAI TTS 模型」與「Google TTS 聲音」。

翻譯快取會保存在 `%USERPROFILE%\.realtime-audio\cache\translation_cache.db`，術語表保存在 `config\glossary.json`，可按「新增術語」加入固定翻譯，按「修正上次翻譯」修正最近一句，確認後加入術語，或按「開啟術語表」直接編輯。

## 常見問題

- 沒有字幕：確認「顯示字幕」已開啟，並按「測試字幕」。
- 聽不到對方聲音：確認喇叭來源選的是 Discord 或遊戲正在播放的裝置，再按「測試喇叭」。
- 找不到 runtime：確認 core `.7z` 與 DLL `.zip` 都已解壓到 `%USERPROFILE%\.realtime-audio\runtime\cuda12`。
- 找不到模型：在工具內下載模型，或解壓模型 zip 到 `%USERPROFILE%\.realtime-audio\models`。
- 找不到離線翻譯模型：在進階模式按「下載離線翻譯模型」，或把翻譯模型 zip 解壓到 `%USERPROFILE%\.realtime-audio\models`。
- 對方聽不到翻譯語音：確認「播放翻譯語音」與「輸出到虛擬麥克風」已開啟，且「TTS 輸出」選 `CABLE Input`。
- Discord 沒有收到虛擬麥克風聲音：Discord 麥克風請選 `CABLE Output (VB-Audio Virtual Cable)`。
- 字幕延遲太高：在進階模式把「效能模式」改成 `low_latency`，並先用較小模型測試。
- GPU 無法使用：把「ASR 裝置」改成 CPU，或確認 CUDA12 runtime 已正確解壓。

## 限制

- 目前只支援 Windows x64。
- 接近即時通常約 1.5 到 3 秒延遲，取決於模型、GPU、API 與網路。
- OpenAI / Google 功能需要網路與有效憑證，API key 不會寫入程式碼。
