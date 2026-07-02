最快使用

1. 解壓 RealtimeAudioTranslator-<tag>-win-x64.zip。
2. 執行 RealtimeAudioTranslator.exe。
3. 如果提示缺 runtime，到 https://github.com/Purfview/whisper-standalone-win/releases 下載 Faster-Whisper-XXL Windows runtime，並下載 cuBLAS.and.cuDNN_CUDA12_win_v3.7z，兩個都解壓到：
   %USERPROFILE%\.realtime-audio\runtime\cuda12
4. 如果提示缺模型，可在 App 內下載模型，或把模型 zip 解壓到：
   %USERPROFILE%\.realtime-audio\models
5. 在 Discord / 遊戲語音中，把麥克風選成 CABLE Output；本工具的 TTS output 選 CABLE Input。
