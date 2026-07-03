# Realtime Audio Translator

Windows x64 即時雙向語音翻譯工具。可擷取喇叭與麥克風聲音，轉文字、翻譯、顯示字幕 overlay，並可把翻譯語音送到 VB-CABLE 給 Discord、遊戲或會議軟體使用。

## 最快使用

1. 到 GitHub Releases 下載 `RealtimeAudioTranslator-<tag>-win-x64.zip`。
2. 解壓後執行 `RealtimeAudioTranslator.exe`。
3. 若提示缺 runtime，下載同一個 Release 裡的兩個檔案：

```text
RealtimeAudioTranslator-runtime-cuda12-core-<tag>.7z
RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.7z
```

把兩個檔案都解壓到：

```text
%USERPROFILE%\.realtime-audio\runtime\cuda12
```

該資料夾內應直接看到 `faster-whisper-xxl.exe` 與 CUDA12 DLL。主程式已包含 Python runtime，不需要另外安裝 Python。

## 需要下載哪些檔案

- 必下載：`RealtimeAudioTranslator-<tag>-win-x64.zip`
- NVIDIA CUDA12 runtime：`RealtimeAudioTranslator-runtime-cuda12-core-<tag>.7z` 和 `RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.7z`
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
3. 選擇喇叭、麥克風、`TTS output`、來源語言與目標語言。
4. 選擇 `Scenario` 後按 `Apply scenario` 套用常用場景。
5. 按 `Run diagnostics` 檢查 runtime、模型、音訊與 API 設定。
6. 按 `Subtitle test` 確認字幕 bar 會出現。
7. 按 `Speaker test`、`Mic test`、`TTS test`、`Virtual mic test` 確認聲音路由。
8. 按 `Start` 開始翻譯。

## VB-CABLE 路由

1. 會議軟體或 Discord 的麥克風選 `CABLE Output (VB-Audio Virtual Cable)`。
2. 本工具的 `TTS output` 選 `CABLE Input`。
3. 本工具的喇叭選你正在聽對方聲音的裝置。
4. 本工具的麥克風選你的實體麥克風。

## 常用功能

- `Show overlay`：顯示或隱藏字幕 bar。
- `Overlay topmost`：讓字幕 bar 保持最上層。
- `Show original` / `Show translation`：切換原文與譯文。
- `Speak translations`：開關翻譯語音輸出。
- `Push to talk`：按住才送出我方翻譯語音。
- `Record logs`：儲存對話紀錄。
- `Open logs`：開啟紀錄資料夾，`app.log` 會記錄開始、停止、缺模型與字幕匯出事件。
- `Export subtitles`：把最新 JSONL 對話紀錄匯出成 SRT，檔案放在 `%USERPROFILE%\.realtime-audio\exports\subtitles`。
- `Open app folder`：開啟 `%USERPROFILE%\.realtime-audio`，設定鏡像在 `config\settings.json`，術語表在 `config\glossary.json`，音訊裝置快照在 `config\audio_devices.json`。
- `Show language`：在字幕前顯示語言代碼。
- `Apply scenario`：套用遊戲、Discord、會議、字幕-only 或雙向翻譯預設。
- `Optimize settings`：使用 AI 決策中樞依場景、硬體、延遲與診斷結果切換模型、裝置與低延遲設定。
- `Run diagnostics`：顯示目前缺少的 runtime、模型、音訊或 API 設定。
- `Check updates`：檢查 GitHub Releases 是否有新版本。
- 狀態列會顯示信心提示、延遲、provider、本機/雲端模式與是否可能產生費用。
- 若語言判斷信心偏低，診斷會提示把 `Source language` 從 `auto` 改成固定語言。

`Push to talk` 會 hold it to unmute TTS output，按住才輸出我方翻譯語音。

## 翻譯與 TTS

預設本機翻譯不會上傳雲端。若要使用真正翻譯，請先啟動 LibreTranslate，並把 `Local translate URL` 填成例如：

```text
http://127.0.0.1:5000/translate
```

也可改用 OpenAI 或 Google provider。OpenAI 使用 `OPENAI_API_KEY` 環境變數，Google 使用 service account JSON 路徑。

`TTS provider` 可選本機、OpenAI 或 Google。進階設定可調 `OpenAI model`、`OpenAI TTS voice`、OpenAI TTS model 與 Google TTS voice。

翻譯快取會保存在 `%USERPROFILE%\.realtime-audio\cache\translation_cache.db`，術語表保存在 `config\glossary.json`，可按 `Open glossary` 手動加入固定翻譯。

## 常見問題

- 沒有字幕：確認 `Show overlay` 已開啟，並按 `Subtitle test`。
- 聽不到對方聲音：確認喇叭來源選的是 Discord 或遊戲正在播放的裝置，再按 `Speaker test`。
- 找不到 runtime：確認兩個 runtime 檔案都已解壓到 `%USERPROFILE%\.realtime-audio\runtime\cuda12`。
- 找不到模型：在工具內下載模型，或解壓模型 zip 到 `%USERPROFILE%\.realtime-audio\models`。
- 對方聽不到翻譯語音：確認 `Speak translations` 已開啟，且 `TTS output` 選 `CABLE Input`。
- Discord 沒有收到虛擬麥克風聲音：Discord 麥克風請選 `CABLE Output (VB-Audio Virtual Cable)`。
- 字幕延遲太高：把 `Performance mode` 改成 `low_latency`，並先用較小模型測試。
- GPU 無法使用：把 `Device` 改成 CPU，或確認 CUDA12 runtime 已正確解壓。

## 限制

- 目前只支援 Windows x64。
- 接近即時通常約 1.5 到 3 秒延遲，取決於模型、GPU、API 與網路。
- OpenAI / Google 功能需要網路與有效憑證，API key 不會寫入程式碼。
