# Realtime Audio Translator

Windows 即時雙向語音翻譯工具。它擷取目前喇叭與麥克風聲音，使用本資料夾的 Whisper 環境做近即時辨識，透過 OpenAI 或 Google 翻譯文字，並用 Google 或 OpenAI TTS 將我方翻譯語音播放到 VB-CABLE。

## 需求

- Windows 10/11
- VB-Audio Virtual Cable
- Google Cloud 服務帳戶 JSON，並啟用 Cloud Translation 與 Text-to-Speech
- 可選：`OPENAI_API_KEY`，用於 OpenAI 翻譯或 OpenAI TTS
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
Provider 選 `local` 時不呼叫雲端翻譯，字幕會保留原文，適合離線檢查 ASR 與字幕流程。
勾選 `Show original` 可在字幕同時顯示原文與譯文。
`Overlay opacity` 可設定字幕透明度，範圍 0.2 到 1.0。
`Overlay font size` 可設定字幕字體大小，範圍 12 到 48。
`Overlay hold seconds` 可設定字幕停留秒數，範圍 1 到 60。
取消勾選 `Show overlay` 可一鍵隱藏字幕窗。

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
- 可在主視窗按 `Clear cache` 或 `Clear logs` 清除暫存音訊與對話紀錄。
- 主視窗會顯示目前是本機 ASR 或雲端 API，以及 API 使用是否可能產生費用。
- Google TTS、Google 翻譯、OpenAI 翻譯與 OpenAI TTS 都需要網路與有效憑證。
- 開發模式會優先載入本資料夾 `_xxl_data`；發布版使用 `%USERPROFILE%\.realtime-audio\runtime\faster-whisper-xxl.exe` 備援辨識。
