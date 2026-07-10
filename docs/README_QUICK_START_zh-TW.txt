RealtimeAudioTranslator 快速開始

1. 從 GitHub Releases 下載 `RealtimeAudioTranslator-<tag>-win-x64.zip`。
2. 解壓縮後直接執行 `RealtimeAudioTranslator.exe`，不需要另外安裝 Python。
3. 第一次開啟若提示缺 runtime，下載 `RealtimeAudioTranslator-runtime-cuda12-core-<tag>.7z` 與
   `RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.zip`，兩個都解壓到：
   `%USERPROFILE%\.realtime-audio\runtime\cuda12`
   若檔案總管無法開啟 core `.7z`，請使用 7-Zip 解壓。
4. 若模型無法由 App 下載，下載模型 zip，解壓到：
   `%USERPROFILE%\.realtime-audio\models`
5. 在主視窗選「場景」，選擇後會自動套用場景預設。
6. 按「一鍵診斷」檢查 runtime、模型、音訊裝置、VB-CABLE 與 API 設定。
7. Discord 或遊戲語音的麥克風選 `CABLE Output`；App 的「TTS 輸出」選 `CABLE Input`。
8. 進階模式按「下載離線翻譯模型」可下載目前語言的雙向 Argos Translate 模型；模型放在：
   `%USERPROFILE%\.realtime-audio\models\translation`
   無法下載時，下載透過英文中繼支援中文、英文、日文、韓文的 `RealtimeAudioTranslator-models-translation-<tag>.zip`，解壓到
   `%USERPROFILE%\.realtime-audio\models`，保留 `translation` 資料夾；其他語言配對請切換語言後下載。
9. 若使用 LibreTranslate，請在進階模式的「本機翻譯 URL」填入端點，例如：
   `http://127.0.0.1:5000/translate`
10. 簡單模式先按「測試麥克風」與「測試虛擬麥克風」；進階模式可再按「測試字幕」/「測試喇叭」/「測試 TTS」。
11. 確認無誤後按「開始」開始即時字幕與翻譯。
