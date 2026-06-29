# Realtime Audio Translator

Windows 即時雙向語音翻譯工具。它擷取目前喇叭與麥克風聲音，使用本資料夾的 Whisper 環境做近即時辨識，透過 OpenAI 或 Google 翻譯文字，並用 Google TTS 將我方翻譯語音播放到 VB-CABLE。

## 需求

- Windows 10/11
- Python 3.10 launcher：`py -3.10`
- VB-Audio Virtual Cable
- Google Cloud 服務帳戶 JSON，並啟用 Cloud Translation 與 Text-to-Speech
- 可選：`OPENAI_API_KEY`
- 可選打包：Inno Setup，需可執行 `iscc.exe`

## 使用

```powershell
py -3.10 -m pip install -r requirements.txt
py -3.10 -m realtime_audio_translator
```

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

工具會優先使用 `%USERPROFILE%\.realtime-audio\models`，也會讀取目前資料夾的 `_models`。推薦模式在 RTX 4060 Laptop 4GB VRAM 這類硬體上預設選 `large-v3-turbo`；CPU 或低 VRAM 則選 `medium`。

## 打包

```powershell
.\scripts\build.ps1
```

產物位於 `dist\RealtimeAudioTranslator`。

若要產生安裝精靈：

```powershell
.\scripts\package.ps1
```

缺少 Inno Setup 時，腳本會提示安裝 `iscc.exe`，不會自動修改系統。

## 限制

- 第一版只支援 Windows。
- 接近即時代表約 1.5 到 3 秒延遲，取決於模型、GPU、API 與網路。
- Google TTS 與翻譯需要網路與有效憑證。
- `faster-whisper-xxl.exe --help` 用於產生 `commands.json` 與備援；主即時路徑直接載入 `_xxl_data` 內的套件。
