# Realtime Audio Translator

Windows x64 即時雙向語音翻譯工具。可擷取喇叭與麥克風聲音，轉文字、翻譯、顯示字幕 overlay，並可把翻譯語音送到 VB-CABLE 給 Discord、遊戲或會議軟體使用。

專案採 [MIT License](LICENSE)；第三方來源、授權與未再散布項目記錄於 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，Release 另附 CycloneDX SBOM。

## 最快使用

1. 到 GitHub Releases 下載已簽章的 `RealtimeAudioTranslator-<tag>-setup.exe`，確認 Publisher 後執行。
2. 安裝程式會偵測 NVIDIA GPU／VRAM；CPU 模式只下載 runtime 核心，CUDA 模式才下載 CUDA DLL。Runtime 直接來自 Faster-Whisper-XXL 上游並驗證 SHA-256。
3. 若未偵測到 VB-CABLE，安裝程式只提供官方下載指引，不會靜默安裝第三方驅動程式。

需要免安裝版時，仍可下載 `RealtimeAudioTranslator-<tag>-win-x64.zip`，解壓後執行 `RealtimeAudioTranslator.exe`，再按「下載上游 runtime」並使用「手動匯入 runtime」。

把上游檔案解壓到同一個暫存資料夾，再於程式內按「手動匯入 runtime」並選擇該資料夾；程式會驗證內容後安全安裝到：

```text
%USERPROFILE%\.realtime-audio\runtime\cuda12
```

匯入來源資料夾內應直接看到 `faster-whisper-xxl.exe` 與 CUDA12 DLL。主程式已包含 Python runtime，不需要另外安裝 Python。
若檔案總管無法開啟 `.7z`，請使用 7-Zip 解壓。

## 需要下載哪些檔案

- 建議：`RealtimeAudioTranslator-<tag>-setup.exe`
- 免安裝版：`RealtimeAudioTranslator-<tag>-win-x64.zip`
- Runtime：從 Faster-Whisper-XXL 上游 Releases 下載，不隨本專案 Release 再散布
- 授權與供應鏈清單：`LICENSE`、`THIRD_PARTY_NOTICES.md`、`SBOM.cdx.json`
- 校驗用：`SHA256SUMS.txt`

解除安裝時會分別詢問是否移除 runtime、模型、設定與紀錄／快取；預設全部保留，且不會遞迴刪除 `%USERPROFILE%\.realtime-audio` 內未列入的其他資料。

模型 zip 若有提供，請解壓到：

```text
%USERPROFILE%\.realtime-audio\models
```

Whisper 模型可放在 `models\whisper-small`；完整資料夾需直接包含 `config.json`、`model.bin`、`tokenizer.json` 與 `vocabulary.*`。程式會先取得官方模型版本、大小與雜湊，顯示下載百分比／MB／速度，檢查磁碟空間，再以 `.partial` 續傳、SHA 驗證與原子安裝完成；可按「取消模型下載」，稍後重試會接續未完成部分。空資料夾、巢狀解壓或 `.partial` 不會被當成已安裝模型。翻譯模型放在 `models\translation`；TTS 模型放在 `models\tts`。

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
- 「虛擬麥克風啟動時靜音」：啟動後只靜音外送語音，搭配「按住說話」變成按住才送出我方翻譯；不影響本機播放對方翻譯。
- 「輸出到虛擬麥克風」：開啟後才會把我方翻譯語音送到「TTS 輸出」。
- 「播放對方翻譯」：開啟後才會把對方語音翻譯播放到「對方翻譯播放輸出」；留空則使用系統預設喇叭。
- 「儲存對話紀錄」：對話紀錄預設關閉，任何場景都不會自動開啟；每次程式執行開啟前會詢問同意，並顯示儲存內容、位置與期限，允許後才存在本機。寫入在有界背景佇列執行，不阻塞字幕處理。
- 「紀錄內容」/「紀錄保留天數」/「紀錄容量上限 MB」：可選原文與譯文、僅原文、僅譯文或不含對話文字；預設保留 7 天、上限 100 MB，背景寫入後會自動輪替與清理。
- 「開啟紀錄」：開啟紀錄資料夾 `%USERPROFILE%\.realtime-audio\logs\conversations`；應用程式事件另存於 `logs\app.log`。
- 「清除快取」/「清除紀錄」/「清除本機資料」：清除本機翻譯快取、暫存音訊與對話紀錄。
- 「匯出字幕」：把最新 JSONL 對話紀錄匯出成 SRT 與 TXT；新版紀錄依實際音訊起訖排序與定時，舊紀錄則沿用每段 3 秒的相容 fallback。檔案放在 `%USERPROFILE%\.realtime-audio\exports\subtitles`。
- 「開啟程式資料夾」：開啟 `%USERPROFILE%\.realtime-audio`；唯一設定檔在 `config\settings.json`，執行狀態另存於 `config\state.json`，術語表在 `config\glossary.json`，音訊裝置快照在 `config\audio_devices.json`。
- 「新增術語」：加入固定術語翻譯，例如 `cooldown` → `冷卻`。
- 「顯示語言」：在字幕前顯示語言代碼。
- 「場景」：選擇遊戲、Discord、會議、客服對話、字幕-only、自己說話翻譯或雙向翻譯預設後會自動套用；進階模式也可按「套用場景」。
- 「效能模式」：可選 `low_latency`、`balanced`、`quality` 或離線省資源 `offline_light`。
- 「自動優化」：使用 AI 決策中樞依場景與硬體提出建議；會先預覽設定前後差異，只有確認後才儲存，單次延遲、語速或 TTS 量測不會產生可持久化建議。
- 「一鍵診斷」：顯示目前缺少的 runtime、模型、音訊或 API 設定。
- 「檢查更新」：檢查 GitHub Releases 是否有新版本。
- 進階狀態列會區分未校準模型分數、供應商品質訊號與啟發式提示，並顯示本機/雲端模式與費用風險；沒有可信分數時會明確顯示無法取得。
- 診斷會將語言判斷與 ASR 模型分數標示為非校準正確率，必要時提示手動調整「來源語言」；自動調校不會依這些分數永久變更設定。

「按住說話」（Push to talk）只暫時取消虛擬麥克風靜音；「本機翻譯靜音/取消」獨立控制對方翻譯播放。

## 翻譯與 TTS

預設本機翻譯不會上傳雲端。進階模式可按「下載離線翻譯模型」，工具會直接從 Argos 來源下載目前語言配對。下載後模型放在：

```text
%USERPROFILE%\.realtime-audio\models\translation
```

Argos 套件未完整明示整個 `.argosmodel` 成品的再散布條款，因此本專案 Release 不封裝模型。若要改用 LibreTranslate，請在進階模式把「本機翻譯 URL」填成例如：

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
- 找不到 runtime：把 core `.7z` 與 DLL `.zip` 解壓到同一暫存資料夾，再使用「手動匯入 runtime」完成驗證安裝。
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

## 開發

支援 Python 3.10 至 3.13；正式版目前使用 Python 3.10 建置，CI 同時驗證 3.10 與下一個建置版本 3.13。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[build]"
python -m realtime_audio_translator
.\scripts\test.ps1
```
