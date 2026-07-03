# Realtime Audio Translator 發布說明

## 最快使用

1. 到 GitHub Releases 下載 `RealtimeAudioTranslator-<tag>-win-x64.zip`。
2. 解壓後執行 `RealtimeAudioTranslator.exe`。
3. 第一次啟動若提示缺 runtime，到同一個 Release 下載 `RealtimeAudioTranslator-runtime-cuda12-core-<tag>.7z` 和 `RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.7z`，兩個都解壓到：

```text
%USERPROFILE%\.realtime-audio\runtime\cuda12
```

解壓後該資料夾內應直接看到 `faster-whisper-xxl.exe` 與 CUDA12 DLL。

## 下載哪個檔案

- 主程式：`RealtimeAudioTranslator-<tag>-win-x64.zip`
- Whisper runtime/CUDA12：`RealtimeAudioTranslator-runtime-cuda12-core-<tag>.7z` 和 `RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.7z`
- 模型可選包：`RealtimeAudioTranslator-models-<model>-<tag>.zip`
- 檔案校驗：`SHA256SUMS.txt`

模型 zip 若有提供，請解壓到：

```text
%USERPROFILE%\.realtime-audio\models
```

主程式不需要安裝 Python；zip 內的 PyInstaller 版本已包含 Python runtime。

預設本機翻譯不會上傳雲端。若要真的翻譯文字，請先啟動 LibreTranslate，並在 `Local translate URL` 填入例如 `http://127.0.0.1:5000/translate`；未填 URL 時只會套用 glossary 詞彙表並保留原文。

## VB-CABLE 設定

1. 會議軟體的麥克風選 `CABLE Output (VB-Audio Virtual Cable)`。
2. 本工具的 `TTS output` 選 `CABLE Input`。
3. 本工具的喇叭選你正在聽對方聲音的裝置。
4. 本工具的麥克風選你的實體麥克風。

## 常見問題

- 沒有字幕：確認 `Show overlay` 已開啟，並按 `Subtitle test`。
- 聽不到對方聲音：確認喇叭來源選的是 Discord 或遊戲正在播放的裝置，再按 `Speaker test`。
- 對方聽不到翻譯語音：確認 `Speak translations` 已開啟，且 `TTS output` 選 `CABLE Input`。
- 找不到 runtime：下載 `RealtimeAudioTranslator-runtime-cuda12-core-<tag>.7z` 和 `RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.7z`，兩個都解壓到 `%USERPROFILE%\.realtime-audio\runtime\cuda12`。若 Release 沒有 runtime 壓縮檔，可改到 https://github.com/Purfview/whisper-standalone-win/releases 下載 Faster-Whisper-XXL Windows runtime 和 `cuBLAS.and.cuDNN_CUDA12_win_v3.7z`。
- 找不到模型：在工具內下載模型，或解壓模型 zip 到 `%USERPROFILE%\.realtime-audio\models`。
- Discord 沒有收到虛擬麥克風聲音：Discord 麥克風請選 `CABLE Output (VB-Audio Virtual Cable)`。
- 字幕延遲太高：把 `Performance mode` 改成 `low_latency`，並先用較小模型測試。
- GPU 無法使用：把 `Device` 改成 CPU 或確認 CUDA12 runtime 與 CUDA DLL 已正確解壓。

## 發布規則

大型更新或修復錯誤後推新的 `v*` tag，例如 `v0.1.1`。GitHub Actions 會重新打包 zip、產生 `SHA256SUMS.txt`，並更新 GitHub Releases 頁面。
