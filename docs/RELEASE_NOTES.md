# Realtime Audio Translator 發布說明

## 最快使用

1. 下載 `RealtimeAudioTranslator-<tag>-win-x64.zip`。
2. 解壓後執行 `RealtimeAudioTranslator.exe`。
3. 若提示缺 runtime，下載並解壓這兩個檔案：

```text
RealtimeAudioTranslator-runtime-cuda12-core-<tag>.7z
RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.7z
```

解壓位置：

```text
%USERPROFILE%\.realtime-audio\runtime\cuda12
```

解壓後該資料夾內應直接看到 `faster-whisper-xxl.exe` 與 CUDA12 DLL。

## 下載檔案

- 主程式：`RealtimeAudioTranslator-<tag>-win-x64.zip`
- CUDA12 runtime：`RealtimeAudioTranslator-runtime-cuda12-core-<tag>.7z` 和 `RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.7z`
- 模型可選包：`RealtimeAudioTranslator-models-<model>-<tag>.zip`
- 檔案校驗：`SHA256SUMS.txt`

模型 zip 若有提供，請解壓到：

```text
%USERPROFILE%\.realtime-audio\models
```

主程式不需要安裝 Python。

## VB-CABLE 設定

1. 會議軟體或 Discord 的麥克風選 `CABLE Output (VB-Audio Virtual Cable)`。
2. 本工具的 `TTS output` 選 `CABLE Input`。
3. 本工具的喇叭選你正在聽對方聲音的裝置。
4. 本工具的麥克風選你的實體麥克風。

## 常見問題

- 沒有字幕：確認 `Show overlay` 已開啟，並按 `Subtitle test`。
- 找不到 runtime：確認兩個 runtime 檔案都已解壓到 `%USERPROFILE%\.realtime-audio\runtime\cuda12`。
- 找不到模型：在工具內下載模型，或解壓模型 zip 到 `%USERPROFILE%\.realtime-audio\models`。
- 對方聽不到翻譯語音：確認 `Speak translations` 已開啟，且 `TTS output` 選 `CABLE Input`。
- Discord 沒有收到聲音：Discord 麥克風請選 `CABLE Output (VB-Audio Virtual Cable)`。
- 字幕延遲太高：把 `Performance mode` 改成 `low_latency`，並先用較小模型測試。
- GPU 無法使用：把 `Device` 改成 CPU，或確認 CUDA12 runtime 已正確解壓。
