RealtimeAudioTranslator 快速開始

1. 從 GitHub Releases 下載已簽章的 `RealtimeAudioTranslator-<tag>-setup.exe`，確認 Publisher 後執行。
2. 安裝程式會偵測 GPU／VRAM；CPU 模式只下載 runtime 核心，CUDA 模式才下載 CUDA DLL。下載都直接來自上游並驗證 SHA-256。
3. 需要免安裝版時，下載 `RealtimeAudioTranslator-<tag>-win-x64.zip`，解壓後執行 `RealtimeAudioTranslator.exe`；
   再從 Faster-Whisper-XXL 上游下載 runtime，使用「手動匯入 runtime」安裝到 `%USERPROFILE%\.realtime-audio\runtime\cuda12`。
4. 模型由 App 直接從上游下載到 `%USERPROFILE%\.realtime-audio\models`；本專案 Release 不封裝模型。
5. 在主視窗選「場景」，選擇後會自動套用場景預設。
6. 按「一鍵診斷」檢查 runtime、模型、音訊裝置、VB-CABLE 與 API 設定。
7. Discord 或遊戲語音的麥克風選 `CABLE Output`；App 的「TTS 輸出」選 `CABLE Input`。
8. 進階模式按「下載離線翻譯模型」可下載目前語言的雙向 Argos Translate 模型；模型放在：
   `%USERPROFILE%\.realtime-audio\models\translation`
   模型套件的完整再散布條款未明，因此只由 App 直接從 Argos 上游取得。
9. 若使用 LibreTranslate，請在進階模式的「本機翻譯 URL」填入端點，例如：
   `http://127.0.0.1:5000/translate`
10. 簡單模式先按「測試麥克風」與「測試虛擬麥克風」；進階模式可再按「測試字幕」/「測試喇叭」/「測試 TTS」。

解除安裝會分別詢問是否移除 runtime、模型、設定與紀錄／快取，預設全部保留；不會刪除未列入的其他使用者資料。
11. 確認無誤後按「開始」開始即時字幕與翻譯。
