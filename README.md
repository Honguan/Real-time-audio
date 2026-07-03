# Realtime Audio Translator

Windows x64 即時雙向語音翻譯工具。主程式會擷取喇叭與麥克風聲音，轉文字、翻譯、顯示字幕 overlay，並可把翻譯語音送到 VB-CABLE 給會議軟體使用。

## 最快使用

1. 到 GitHub Releases 下載 `RealtimeAudioTranslator-<tag>-win-x64.zip`。
2. 解壓後執行 `RealtimeAudioTranslator.exe`。
3. 第一次啟動若提示缺 runtime，到同一個 Release 下載 `RealtimeAudioTranslator-runtime-cuda12-core-<tag>.zip` 和 `RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.zip`，兩個都解壓到：

```text
%USERPROFILE%\.realtime-audio\runtime\cuda12
```

該資料夾內應直接看到 `faster-whisper-xxl.exe` 與 CUDA12 DLL。主程式 zip 已包含 Python runtime，不需要另外安裝 Python 或 venv。

模型可由工具下載；若 GitHub Releases 有提供模型 zip，請解壓到：

```text
%USERPROFILE%\.realtime-audio\models
```

下載後可用 `SHA256SUMS.txt` 檢查 zip 是否完整。

## 需要下載哪些檔

- 必下載：`RealtimeAudioTranslator-<tag>-win-x64.zip`
- NVIDIA CUDA12 runtime：`RealtimeAudioTranslator-runtime-cuda12-core-<tag>.zip` 和 `RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.zip`
- 無法線上下載模型時才下載：`RealtimeAudioTranslator-models-<model>-<tag>.zip`
- 校驗用：`SHA256SUMS.txt`

## 第一次設定

1. 安裝 VB-Audio Virtual Cable。
2. 開啟 `RealtimeAudioTranslator.exe`。
3. 在工具內選喇叭、麥克風、`TTS output`、來源語言與目標語言。
4. 按 `Subtitle test` 確認字幕 bar 會出現。
5. 按 `Speaker test`、`Mic test`、`TTS test` 確認聲音路由。

## VB-CABLE 路由

1. 會議軟體的麥克風選 `CABLE Output (VB-Audio Virtual Cable)`。
2. 本工具的 `TTS output` 選 `CABLE Input`。
3. 本工具的喇叭選你正在聽對方聲音的裝置。
4. 本工具的麥克風選你的實體麥克風。

## 常用按鈕與設定

- `Start` / `Stop`：開始或停止即時字幕與翻譯。
- `Show overlay`：顯示或隱藏字幕 bar。
- `Show language`：在字幕前顯示語言代碼。
- `Show original` / `Show translation`：切換字幕中的原文與譯文。
- `Overlay topmost`：讓字幕 bar 保持最上層。
- `Copy subtitles`：複製目前字幕。
- `Speak translations`：開關翻譯語音輸出。
- `Mute/unmute`：靜音或恢復翻譯語音。
- `Push to talk`：hold it to unmute TTS output，按住才送出我方翻譯語音。
- `Record logs`：開啟對話紀錄。
- `Open logs`：開啟紀錄資料夾。
- `Open app folder`：開啟 `%USERPROFILE%\.realtime-audio`。

`Overlay opacity`、`Overlay font size`、`Overlay hold seconds` 可調字幕透明度、大小與停留時間。

## 翻譯與 TTS

預設 `Provider` 可使用本機模式，不會呼叫雲端翻譯。若要真的翻譯文字，請先啟動 LibreTranslate，並把 `Local translate URL` 填成例如 `http://127.0.0.1:5000/translate`；沒有填 URL 時只會套用 glossary 詞彙表並保留原文。若要使用雲端：

- OpenAI：設定環境變數 `OPENAI_API_KEY`，再選 OpenAI provider。
- Google：填入 Google service account JSON 路徑，並啟用 Cloud Translation / Text-to-Speech。

`TTS provider` 可選本機 Windows TTS、Google TTS 或 OpenAI TTS。`OpenAI model`、`OpenAI TTS voice`、`OpenAI TTS model`、`Google TTS voice` 可在進階設定調整。

## 模型

推薦模式會依硬體選模型。一般 GPU 可先用 `large-v3-turbo`，CPU 或低 VRAM 可先用 `medium`。模型位置固定為：

```text
%USERPROFILE%\.realtime-audio\models
```

## 發布與打包

本專案不再產生 Inno Setup installer，也不再產生 `RealtimeAudioTranslatorSetup.exe` 或 `.bin` 分片。

本機建置 app：

```powershell
.\scripts\build.ps1
```

本機產生 GitHub Releases 用 zip：

```powershell
.\scripts\package.ps1
```

輸出位置：

```text
release-output
```

若要一起打 runtime zip：

```powershell
.\scripts\package.ps1 -RuntimeSource "%USERPROFILE%\.realtime-audio\runtime\cuda12"
```

大型更新或修復錯誤後推新的 `v*` tag，例如 `v0.1.1`；GitHub Actions 會重新打包 zip，並用 `docs/RELEASE_NOTES.md` 更新 GitHub Releases 說明。

## 常見問題

- 沒有字幕：確認 `Show overlay` 已開啟，按 `Subtitle test`。
- 聽不到對方聲音：確認喇叭來源選的是正在播放 Discord 或遊戲語音的裝置，再按 `Speaker test`。
- 對方聽不到翻譯語音：確認 `Speak translations` 已開啟，且 `TTS output` 選 `CABLE Input`。
- 找不到 runtime：下載 `RealtimeAudioTranslator-runtime-cuda12-core-<tag>.zip` 和 `RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.zip`，兩個都解壓到 `%USERPROFILE%\.realtime-audio\runtime\cuda12`。若 Release 沒有 runtime zip，可改到 https://github.com/Purfview/whisper-standalone-win/releases 下載 Faster-Whisper-XXL Windows runtime 和 `cuBLAS.and.cuDNN_CUDA12_win_v3.7z`。
- 找不到模型：在工具內下載模型，或解壓模型 zip 到 `%USERPROFILE%\.realtime-audio\models`。
- Discord 沒有收到虛擬麥克風聲音：Discord 麥克風請選 `CABLE Output (VB-Audio Virtual Cable)`。
- 字幕延遲太高：把 `Performance mode` 改成 `low_latency`，並先用較小模型測試。
- GPU 無法使用：把 `Device` 改成 CPU 或確認 CUDA12 runtime 與 CUDA DLL 已正確解壓。
- 想看紀錄：開啟 `Record logs`，再按 `Open logs`。

## 限制

- 第一版只支援 Windows x64。
- 接近即時通常約 1.5 到 3 秒延遲，取決於模型、GPU、API 與網路。
- Google / OpenAI 功能需要網路與有效憑證，API key 不會寫入程式碼。
