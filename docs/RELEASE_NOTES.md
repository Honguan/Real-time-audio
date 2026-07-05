# Realtime Audio Translator 發布說明

## 最快使用

1. 到 GitHub Releases 下載 `RealtimeAudioTranslator-<tag>-win-x64.zip`。
2. 解壓後執行 `RealtimeAudioTranslator.exe`。
3. 第一次開啟會提示 runtime / model 診斷；可用 `Scenario` 選遊戲、Discord、會議、客服、字幕-only、自己說話翻譯或雙向翻譯場景，按 `Apply scenario` 套用預設，也可按 `Optimize settings` 讓 AI 決策中樞依場景、硬體、延遲與診斷結果調整設定，再按 `Run diagnostics` 檢查設定。
4. 低階電腦可把 `Performance mode` 改成離線省資源 `offline_light`。
5. 狀態列會顯示信心提示、延遲、provider、本機/雲端模式與是否可能產生費用。
6. 若語言判斷信心偏低，診斷會提示把 `Source language` 從 `auto` 改成固定語言。
7. 可按 `Check updates` 檢查 GitHub Releases 是否有新版本。
8. 可按 `Export subtitles` 把最新 JSONL 對話紀錄匯出成 SRT 與 TXT，檔案放在 `%USERPROFILE%\.realtime-audio\exports\subtitles`。
9. 切換到 Google 或 OpenAI 時，工具會先提示語音或文字可能傳送到第三方服務並可能產生費用。
10. 若提示缺 runtime，下載並解壓這兩個檔案：

```text
RealtimeAudioTranslator-runtime-cuda12-<tag>.zip
```

解壓位置：

```text
%USERPROFILE%\.realtime-audio\runtime\cuda12
```

解壓後該資料夾內應直接看到 `faster-whisper-xxl.exe` 與 CUDA12 DLL。

## 下載檔案

- 主程式：`RealtimeAudioTranslator-<tag>-win-x64.zip`
- CUDA12 runtime：`RealtimeAudioTranslator-runtime-cuda12-<tag>.zip`
- 模型可選包：`RealtimeAudioTranslator-models-<model>-<tag>.zip`
- 檔案校驗：`SHA256SUMS.txt`

模型 zip 若有提供，請解壓到：

```text
%USERPROFILE%\.realtime-audio\models
```

主程式不需要安裝 Python。

若 Release 沒有 runtime 檔案，可到 https://github.com/Purfview/whisper-standalone-win/releases 下載 Faster-Whisper-XXL Windows runtime 和 `cuBLAS.and.cuDNN_CUDA12_win_v3.7z`。本機翻譯會優先使用已安裝的 Argos Translate 離線模型，也可在 `Local translate URL` 填入 LibreTranslate 端點。

翻譯快取會保存在 `%USERPROFILE%\.realtime-audio\cache\translation_cache.db`，術語可用 `Add glossary term` 加入，也可用 `Fix last translation` 修正最近一句，或用 `Open glossary` 編輯。

## VB-CABLE 設定

1. 會議軟體或 Discord 的麥克風選 `CABLE Output (VB-Audio Virtual Cable)`。
2. 本工具的 `TTS output` 選 `CABLE Input`。
3. 本工具的喇叭選你正在聽對方聲音的裝置。
4. 若要聽對方語音翻譯，開 `Speak opponent`，`Speaker TTS output` 可留空使用系統預設喇叭。
5. 本工具的麥克風選你的實體麥克風。

## 常見問題

- 沒有字幕：確認 `Show overlay` 已開啟，並按 `Subtitle test`。
- 聽不到對方聲音：確認喇叭來源選的是 Discord 或遊戲正在播放的裝置，再按 `Speaker test`。
- 找不到 runtime：確認 `RealtimeAudioTranslator-runtime-cuda12-<tag>.zip` 已解壓到 `%USERPROFILE%\.realtime-audio\runtime\cuda12`。
- 找不到模型：在工具內下載模型，或解壓模型 zip 到 `%USERPROFILE%\.realtime-audio\models`。
- 對方聽不到翻譯語音：確認 `Speak translations` 與 `Virtual mic output` 已開啟，且 `TTS output` 選 `CABLE Input`。
- Discord 沒有收到虛擬麥克風聲音：Discord 麥克風請選 `CABLE Output (VB-Audio Virtual Cable)`。
- 字幕延遲太高：把 `Performance mode` 改成 `low_latency`，並先用較小模型測試。
- GPU 無法使用：把 `Device` 改成 CPU，或確認 CUDA12 runtime 已正確解壓。
