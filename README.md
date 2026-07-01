# Realtime Audio Translator

Windows 即時雙向語音翻譯工具。它擷取目前喇叭與麥克風聲音，使用本資料夾的 Whisper 環境做近即時辨識，預設使用本機模式，並可切換 OpenAI 或 Google 翻譯；TTS 可用 Windows 內建語音或 Google/OpenAI TTS 播放到 VB-CABLE。

## 需求

- Windows 10/11
- VB-Audio Virtual Cable
- 可選：Google Cloud 服務帳戶 JSON，並啟用 Cloud Translation 與 Text-to-Speech
- 可選：`OPENAI_API_KEY`，用於 OpenAI 翻譯或 OpenAI TTS；本機 TTS 不需要 API key
- 可選打包：Inno Setup，需可執行 `iscc.exe`

## Runtime

精簡安裝包不內建 Whisper runtime、CUDA DLL 或模型。

1. 下載 Faster-Whisper-XXL：<https://github.com/Purfview/whisper-standalone-win/releases>
2. 下載 Windows CUDA12 套件：`cuBLAS.and.cuDNN_CUDA12_win_v3.7z`
3. 解壓 Faster-Whisper-XXL 與 CUDA12 套件。
4. 在 GUI 按 `Import extracted runtime` 選擇解壓後含 `faster-whisper-xxl.exe` 的資料夾，工具會複製到：

```text
%USERPROFILE%\.realtime-audio\runtime
```

模型仍由工具下載或放到：

```text
%USERPROFILE%\.realtime-audio\models
```

翻譯快取只保留在同一次執行期間；重複短句會減少延遲與 API 請求。
`Source language` 與 `Target language` 提供 `auto`、`zh`、`en`、`ja`、`ko` 快速選項，也可手動輸入其他語言代碼；`auto` 會讓 Whisper 與支援的翻譯服務自行偵測來源語言。
Provider 預設為 `local`，不呼叫雲端翻譯，字幕會保留原文，適合離線檢查 ASR 與字幕流程。
預設只顯示常用設定；勾選 `Advanced settings` 可展開雲端憑證、靈敏度與字幕調校欄位。
`Performance mode` 可在 `low_latency`、`balanced`、`quality` 間切換，會調整語音分段長度；低延遲更快，高準確會等較長片段。
若要使用本機 LibreTranslate，請把 `Local translate URL` 設為例如 `http://127.0.0.1:5000/translate`。
`Glossary JSON` 可指定遊戲術語表，例如 `{"Dragon Pit":"龍坑","mid lane":"中路"}`，翻譯後會套用固定替換。
翻譯失敗時字幕會保留原文，避免單次 API 或本機翻譯服務錯誤讓字幕消失。
`Speech threshold` 可設定語音靈敏度，較高會忽略更多背景聲。
勾選 `Show original` 可在字幕同時顯示原文與譯文。
取消勾選 `Speak translations` 可只顯示字幕、不播放翻譯語音。
先按 `Mute/unmute` 靜音；按住 `Push to talk` (hold it to unmute TTS output) 時才把我方翻譯語音送出，放開後恢復靜音。
`TTS rate` 與 `TTS volume` 可調整本機 Windows TTS 語速與音量。
`TTS voice` 可填 Windows 語音名稱的一部分，例如 `Microsoft Jenny`；按 `List` 可列出已安裝 Windows 語音，留空使用系統預設聲音。
`TTS test` 會使用目前選定的 `TTS provider`，並播放到 `TTS output`。
開啟 `Record logs` 時，`Log dir` 可選擇對話紀錄保存資料夾。
`Overlay opacity` 可設定字幕透明度，範圍 0.2 到 1.0。
`Overlay font size` 可設定字幕字體大小，範圍 12 到 48。
`Overlay hold seconds` 可設定字幕停留秒數，範圍 1 到 60。
取消勾選 `Show overlay` 可一鍵隱藏字幕窗。
按 `Copy subtitles` 可複製目前字幕內容。
按 `Fix speaker audio`、`Fix mic output`、`Fix subtitles` 可快速開啟聲音設定、VB-CABLE 下載頁或重新顯示字幕窗。

## 使用

開發模式：

```powershell
py -3.10 -m pip install -r requirements.txt
py -3.10 -m realtime_audio_translator
```

發布版安裝後直接從開始選單啟動。

第一次啟動會建立：

```text
%USERPROFILE%\.realtime-audio
```

其中包含 `config.json`、`commands.json`、`models`、`logs`、`cache/audio`。
可在主視窗按 `Open app folder` 開啟 `%USERPROFILE%\.realtime-audio`。

## 音訊路由

1. 在會議軟體中，把麥克風選成 `CABLE Output (VB-Audio Virtual Cable)`。
2. 在本工具中，把 TTS 輸出選成 `CABLE Input`。
3. 喇叭裝置選你正在聽對方聲音的輸出裝置。
4. 麥克風裝置選你的實體麥克風。

## 模型

工具會優先使用 `%USERPROFILE%\.realtime-audio\models`，開發模式也會讀取目前資料夾的 `_models`。推薦模式在 RTX 4060 Laptop 4GB VRAM 這類硬體上預設選 `large-v3-turbo`；CPU 或低 VRAM 則選 `medium`。

## 打包

```powershell
.\scripts\build.ps1
```

產物位於 `dist\RealtimeAudioTranslator`。

若要產生安裝精靈：

```powershell
.\scripts\package.ps1
```

缺少 Inno Setup 時，腳本會提示安裝 `iscc.exe`，不會自動修改系統。精簡版只輸出單一 `RealtimeAudioTranslatorSetup.exe`。

## 限制

- 第一版只支援 Windows。
- 接近即時代表約 1.5 到 3 秒延遲，取決於模型、GPU、API 與網路。
- 對話紀錄預設關閉；需要保存 Markdown/JSONL 紀錄時，請手動勾選 `Record logs`。
- 可在主視窗按 `Open logs` 開啟目前紀錄資料夾，或按 `Clear cache` / `Clear logs` 清除暫存音訊與對話紀錄。
- 主視窗會顯示目前是本機 ASR 或雲端 API，以及 API 使用是否可能產生費用。
- Google TTS、Google 翻譯、OpenAI 翻譯與 OpenAI TTS 都需要網路與有效憑證；TTS provider 選 `local` 時使用 Windows 內建語音。
- 開發模式會優先載入本資料夾 `_xxl_data`；發布版使用 `%USERPROFILE%\.realtime-audio\runtime\faster-whisper-xxl.exe` 備援辨識。
