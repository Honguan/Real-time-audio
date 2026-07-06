import os
from dataclasses import dataclass
from pathlib import Path

from .ai_auto_tuner import recommend_tuning
from .audio import device_name_from_label, find_device, virtual_mic_recaptures_tts
from .config import APP_DIR
from .models import model_available, models_dir
from .runtime import runtime_dir, runtime_status


@dataclass(frozen=True)
class DiagnosticIssue:
    code: str
    severity: str
    title: str
    detail: str
    fix: str
    action: str


def _devices_overlap(left: str, right: str) -> bool:
    left_name = device_name_from_label(left).lower().strip()
    right_name = device_name_from_label(right).lower().strip()
    return bool(left_name and right_name and (left_name in right_name or right_name in left_name))


def _model_exists(config: dict, repo_root: Path) -> bool:
    model = config.get("model", "")
    app_models = models_dir(config)
    return model_available(model, repo_root / "_models", app_models) or model_available(model, repo_root / "models", app_models)


def collect_diagnostics(config: dict, repo_root: Path) -> list[DiagnosticIssue]:
    issues: list[DiagnosticIssue] = []
    status = runtime_status(runtime_dir(config))
    if not status["ready"]:
        issues.append(DiagnosticIssue(
            "runtime_missing",
            "error",
            "找不到語音辨識 runtime",
            f"缺少：{', '.join(status['missing'])}",
            f"下載 RealtimeAudioTranslator-runtime-cuda12-<version>.zip，解壓到 {status['path']}",
            "open_runtime",
        ))
    if status["ready"] and status["warnings"]:
        issues.append(DiagnosticIssue(
            "cuda_dll_missing",
            "warning",
            "CUDA12 DLL 可能不完整",
            f"建議補齊：{', '.join(status['warnings'])}",
            f"解壓 {status['cuda_package']} 到 runtime 資料夾",
            "open_runtime",
        ))
    if not _model_exists(config, repo_root):
        issues.append(DiagnosticIssue(
            "model_missing",
            "error",
            "找不到語音辨識模型",
            f"目前模型：{config.get('model', '')}",
            "按「下載模型」，或把模型 zip 解壓到 models 資料夾",
            "download_model",
        ))
    tts_overlaps_speaker = _devices_overlap(config.get("speaker_device", ""), config.get("tts_output_device", ""))
    speaker_tts_overlaps_speaker = config.get("speaker_tts_enabled", False) and _devices_overlap(config.get("speaker_device", ""), config.get("speaker_tts_output_device", ""))
    if config.get("speaker_enabled", True) and config.get("tts_enabled", True) and (tts_overlaps_speaker or speaker_tts_overlaps_speaker):
        issues.append(DiagnosticIssue(
            "feedback_risk",
            "warning",
            "可能發生音訊回授",
            "喇叭來源與翻譯語音輸出看起來是同一個裝置",
            "把 TTS output 設為 CABLE Input，Speaker TTS output 改成不同喇叭或先關閉 Speak opponent",
            "audio_settings",
        ))
    if config.get("tts_enabled", True) and config.get("virtual_mic_enabled", False) and "cable input" not in str(config.get("tts_output_device", "")).lower():
        issues.append(DiagnosticIssue(
            "virtual_mic_route",
            "warning",
            "虛擬麥克風輸出可能未設定",
            "TTS output 目前看起來不是 CABLE Input",
            "把 TTS output 設為 CABLE Input，並把 Discord 麥克風設為 CABLE Output",
            "audio_settings",
        ))
    if config.get("microphone_enabled", True) and config.get("tts_enabled", True) and config.get("virtual_mic_enabled", False) and virtual_mic_recaptures_tts(config.get("microphone_device", ""), config.get("tts_output_device", "")):
        issues.append(DiagnosticIssue(
            "microphone_feedback_risk",
            "warning",
            "麥克風可能收到翻譯語音",
            "Microphone device 看起來選到 CABLE Output，會把送給 Discord 的翻譯語音再收回來",
            "Microphone device 請選實體麥克風；Discord 的麥克風才選 CABLE Output",
            "audio_settings",
        ))
    if config.get("speaker_enabled", True) and not find_device(config.get("speaker_device", ""), want_output=True):
        issues.append(DiagnosticIssue("speaker_device_missing", "warning", "找不到喇叭來源", "未選到可用輸出裝置", "選擇正在播放 Discord 或遊戲語音的喇叭", "audio_settings"))
    if config.get("microphone_enabled", True) and not find_device(config.get("microphone_device", ""), want_output=False):
        issues.append(DiagnosticIssue("microphone_device_missing", "warning", "找不到麥克風", "未選到可用輸入裝置", "選擇你的實體麥克風", "audio_settings"))
    cloud = [name for name in (config.get("provider"), config.get("tts_provider")) if name in ("openai", "google")]
    if "openai" in cloud and not os.environ.get("OPENAI_API_KEY"):
        issues.append(DiagnosticIssue("cloud_credentials_missing", "error", "OpenAI API key 未設定", "OpenAI provider 需要 OPENAI_API_KEY", "設定環境變數 OPENAI_API_KEY，或改回 local provider", "api_settings"))
    if "google" in cloud and (not config.get("google_project_id") or not config.get("google_service_account_json")):
        issues.append(DiagnosticIssue("cloud_credentials_missing", "error", "Google 憑證未設定", "Google provider 需要 project id 與 service account JSON", "填入 Google project 與 JSON 路徑，或改回 local provider", "api_settings"))
    if config.get("provider") == "local" and not str(config.get("local_translate_url", "")).strip():
        issues.append(DiagnosticIssue(
            "local_translate_url_missing",
            "info",
            "本機翻譯 URL 未設定",
            "若未安裝 Argos Translate，local provider 只會套用 glossary 並保留原文",
            "安裝 Argos Translate 離線模型，或啟動 LibreTranslate 後填入 http://127.0.0.1:5000/translate",
            "local_translation",
        ))
    if config.get("last_translation_empty"):
        issues.append(DiagnosticIssue(
            "translation_empty",
            "warning",
            "翻譯結果空白",
            "最近一次翻譯沒有回傳文字",
            "檢查翻譯 provider、Local translate URL 或改用其他翻譯服務",
            "local_translation",
        ))
    try:
        asr_confidence = float(config.get("last_asr_confidence") or 1.0)
    except Exception:
        asr_confidence = 1.0
    if asr_confidence < 0.5:
        issues.append(DiagnosticIssue(
            "asr_confidence_low",
            "warning",
            "ASR confidence is low",
            f"Recent ASR confidence is about {round(asr_confidence * 100)}%",
            "先跑「測試麥克風」或「測試喇叭」，降低背景噪音，或改用較大模型",
            "audio_settings",
        ))
    try:
        translation_confidence = float(config.get("last_translation_confidence") or 1.0)
    except Exception:
        translation_confidence = 1.0
    if translation_confidence < 0.5:
        issues.append(DiagnosticIssue(
            "translation_confidence_low",
            "info",
            "翻譯信心偏低",
            f"最近一次翻譯信心約 {round(translation_confidence * 100)}%",
            "可按 Fix last translation 加入術語，或設定 Local translate URL",
            "local_translation",
        ))
    if config.get("last_tts_failed"):
        issues.append(DiagnosticIssue(
            "tts_no_sound",
            "warning",
            "TTS 沒有聲音",
            "最近一次翻譯語音播放失敗",
            "檢查 TTS output、VB-CABLE 與 TTS provider 設定",
            "audio_settings",
        ))
    try:
        tts_latency = float(config.get("last_tts_latency_seconds") or 0)
    except Exception:
        tts_latency = 0
    if tts_latency > 2.0:
        issues.append(DiagnosticIssue(
            "tts_latency_high",
            "warning",
            "TTS 延遲過高",
            f"最近一次翻譯語音播放約 {tts_latency:.1f} 秒",
            "改用 local TTS、降低語音輸出頻率，或檢查 TTS output 裝置",
            "audio_settings",
        ))
    if config.get("virtual_mic_enabled", False) and config.get("last_virtual_mic_failed"):
        issues.append(DiagnosticIssue(
            "virtual_mic_no_output",
            "warning",
            "Discord 沒有收到虛擬麥克風聲音",
            "最近一次「測試虛擬麥克風」播放失敗",
            "確認 TTS output 選 CABLE Input，並把 Discord 麥克風設為 CABLE Output",
            "audio_settings",
        ))
    if config.get("last_asr_failed"):
        issues.append(DiagnosticIssue(
            "asr_runtime_failed",
            "error",
            "faster-whisper-xxl 無法呼叫",
            "最近一次啟動語音辨識失敗",
            "確認 runtime 資料夾、faster-whisper-xxl.exe、CUDA12 DLL 與模型路徑",
            "open_runtime",
        ))
    if config.get("last_ffmpeg_failed"):
        issues.append(DiagnosticIssue(
            "ffmpeg_failed",
            "error",
            "ffmpeg 無法呼叫",
            "最近一次 runtime 檢查無法執行 ffmpeg",
            "重新解壓 runtime zip，確認 ffmpeg.exe 直接放在 runtime 資料夾",
            "open_runtime",
        ))
    if config.get("last_mic_quiet"):
        issues.append(DiagnosticIssue(
            "microphone_no_sound",
            "warning",
            "麥克風沒有聲音",
            "最近一次「測試麥克風」音量低於語音門檻",
            "確認麥克風輸入裝置、系統音量與權限設定",
            "audio_settings",
        ))
    if config.get("last_speaker_quiet"):
        issues.append(DiagnosticIssue(
            "speaker_no_sound",
            "warning",
            "系統音訊沒有聲音",
            "最近一次「測試喇叭」沒有偵測到有效聲音",
            "播放 Discord 或遊戲語音後再測試，並確認喇叭來源選對裝置",
            "audio_settings",
        ))
    if config.get("source_language") == "auto":
        try:
            language_confidence = float(config.get("last_language_confidence", 1.0))
        except Exception:
            language_confidence = 1.0
        if language_confidence < 0.7:
            detected = config.get("last_detected_language") or "目前語言"
            issues.append(DiagnosticIssue(
                "language_lock_recommended",
                "info",
                "語言判斷信心偏低",
                f"最近偵測為 {detected}，信心約 {round(language_confidence * 100)}%",
                "若字幕語言跳動，請把 Source language 從 auto 改成固定語言。",
                "language_settings",
            ))
    try:
        current_latency = float(config.get("last_latency_seconds", 0))
    except Exception:
        current_latency = 0
    if current_latency > 3.0:
        issues.append(DiagnosticIssue(
            "subtitle_latency_high",
            "warning",
            "字幕延遲過高",
            f"最近一次處理延遲約 {current_latency:.1f} 秒",
            "按「自動優化」套用低延遲分段與 VAD 設定",
            "optimize_settings",
        ))
    cuda_devices = config.get("last_cuda_devices")
    tuning_cuda_devices = 1
    tuning_vram_gb = 4
    if cuda_devices not in (None, ""):
        try:
            cuda_devices = int(float(cuda_devices))
        except Exception:
            cuda_devices = 0
        try:
            vram_gb = int(float(config.get("last_vram_gb") or 0))
        except Exception:
            vram_gb = 0
        tuning_cuda_devices = cuda_devices
        tuning_vram_gb = vram_gb
        if config.get("device") != "cpu" and cuda_devices < 1:
            issues.append(DiagnosticIssue(
                "gpu_unavailable",
                "warning",
                "GPU 不支援或無法使用",
                "最近一次 CUDA 檢查沒有偵測到可用 GPU",
                "按「自動優化」切換 CPU 與較小模型，或確認 CUDA12 runtime 已解壓",
                "optimize_settings",
            ))
        if cuda_devices >= 1 and vram_gb < 4 and config.get("model") != "medium":
            issues.append(DiagnosticIssue(
                "gpu_low_vram",
                "warning",
                "GPU 記憶體不足",
                f"最近一次 CUDA 檢查 VRAM 約 {vram_gb} GB",
                "按「自動優化」改用 medium 模型",
                "optimize_settings",
            ))
    if config.get("ai_auto_optimize", True):
        latency = config.get("last_latency_seconds")
        try:
            latency = float(latency) if latency not in (None, "") else None
        except Exception:
            latency = None
        tuning = recommend_tuning(config, cuda_devices=tuning_cuda_devices, vram_gb=tuning_vram_gb, latency_seconds=latency)
        if tuning:
            issues.append(DiagnosticIssue(
                "auto_tune_recommended",
                "info",
                "可套用自動優化建議",
                "；".join(item.title for item in tuning),
                "按「自動優化」套用建議設定",
                "optimize_settings",
            ))
    return issues
