# Realtime Audio Translator 發布說明

## 最快使用

1. 到 GitHub Releases 下載 `RealtimeAudioTranslator-<tag>-win-x64.zip`。
2. 解壓後執行 `RealtimeAudioTranslator.exe`。
3. 第一次啟動若提示缺 runtime，下載 `RealtimeAudioTranslator-runtime-cuda12-<tag>.zip`，解壓到：

```text
%USERPROFILE%\.realtime-audio\runtime\cuda12
```

解壓後該資料夾內應直接看到 `faster-whisper-xxl.exe`。

## 下載哪個檔案

- 主程式：`RealtimeAudioTranslator-<tag>-win-x64.zip`
- Whisper runtime/CUDA12：`RealtimeAudioTranslator-runtime-cuda12-<tag>.zip`
- 模型可選包：`RealtimeAudioTranslator-models-<model>-<tag>.zip`
- 檔案校驗：`SHA256SUMS.txt`

模型 zip 若有提供，請解壓到：

```text
%USERPROFILE%\.realtime-audio\models
```

主程式不需要安裝 Python；zip 內的 PyInstaller 版本已包含 Python runtime。

## VB-CABLE 設定

1. 會議軟體的麥克風選 `CABLE Output (VB-Audio Virtual Cable)`。
2. 本工具的 `TTS output` 選 `CABLE Input`。
3. 本工具的喇叭選你正在聽對方聲音的裝置。
4. 本工具的麥克風選你的實體麥克風。

## 常見問題

- 缺 runtime：下載 runtime zip，解壓到 `%USERPROFILE%\.realtime-audio\runtime\cuda12`。
- 缺模型：在工具內下載模型，或解壓模型 zip 到 `%USERPROFILE%\.realtime-audio\models`。
- 沒有字幕：確認 `Show overlay` 已開啟，並按 `Subtitle test`。
- 沒有聲音：確認 Windows 裝置、會議軟體麥克風、`TTS output` 都選對。

## 發布規則

大型更新或修復錯誤後推新的 `v*` tag，例如 `v0.1.1`。GitHub Actions 會重新打包 zip、產生 `SHA256SUMS.txt`，並更新 GitHub Releases 頁面。
