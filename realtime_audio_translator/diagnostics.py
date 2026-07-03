import os
from dataclasses import dataclass
from pathlib import Path

from .ai_auto_tuner import recommend_tuning
from .audio import device_name_from_label, find_device
from .config import APP_DIR
from .models import model_available
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
    return model_available(model, repo_root / "_models", APP_DIR / "models") or model_available(model, repo_root / "models", APP_DIR / "models")


def collect_diagnostics(config: dict, repo_root: Path) -> list[DiagnosticIssue]:
    issues: list[DiagnosticIssue] = []
    status = runtime_status(runtime_dir(config))
    if not status["ready"]:
        issues.append(DiagnosticIssue(
            "runtime_missing",
            "error",
            "找不到語音辨識 runtime",
            f"缺少：{', '.join(status['missing'])}",
            f"下載 runtime core 與 CUDA12 DLL，解壓到 {status['path']}",
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
            "按 Download model，或把模型 zip 解壓到 models 資料夾",
            "download_model",
        ))
    if config.get("speaker_enabled", True) and config.get("tts_enabled", True) and _devices_overlap(config.get("speaker_device", ""), config.get("tts_output_device", "")):
        issues.append(DiagnosticIssue(
            "feedback_risk",
            "warning",
            "可能發生音訊回授",
            "喇叭來源與 TTS output 看起來是同一個裝置",
            "把 TTS output 設為 CABLE Input，喇叭來源設為實際播放對方聲音的裝置",
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
            "目前 local provider 只會套用 glossary 並保留原文",
            "啟動 LibreTranslate 後填入 http://127.0.0.1:5000/translate，或改用雲端 provider",
            "local_translation",
        ))
    if config.get("ai_auto_optimize", True):
        latency = config.get("last_latency_seconds")
        try:
            latency = float(latency) if latency not in (None, "") else None
        except Exception:
            latency = None
        tuning = recommend_tuning(config, cuda_devices=1, vram_gb=4, latency_seconds=latency)
        if tuning:
            issues.append(DiagnosticIssue(
                "auto_tune_recommended",
                "info",
                "可套用自動優化建議",
                "；".join(item.title for item in tuning),
                "按 Optimize settings 套用建議設定",
                "optimize_settings",
            ))
    return issues
