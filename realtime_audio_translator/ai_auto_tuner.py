from dataclasses import dataclass


@dataclass(frozen=True)
class TuningRecommendation:
    code: str
    title: str
    detail: str
    changes: dict


def recommend_tuning(config: dict, cuda_devices: int, vram_gb: int, latency_seconds: float | None = None) -> list[TuningRecommendation]:
    recommendations: list[TuningRecommendation] = []
    model = config.get("model", "")
    if cuda_devices < 1 and config.get("device") != "cpu":
        recommendations.append(TuningRecommendation(
            "use_cpu_medium",
            "切換 CPU 與 medium 模型",
            "未偵測到 CUDA GPU，使用 CUDA 設定可能無法啟動或延遲很高",
            {"device": "cpu", "model": "medium", "compute_type": "int8"},
        ))
    if cuda_devices >= 1 and vram_gb < 4 and model != "medium":
        recommendations.append(TuningRecommendation(
            "low_vram_medium",
            "低 VRAM 使用 medium 模型",
            f"偵測到 VRAM 約 {vram_gb} GB，較大模型可能延遲過高",
            {"model": "medium"},
        ))
    if latency_seconds is not None and latency_seconds > 3.0:
        recommendations.append(TuningRecommendation(
            "reduce_latency",
            "降低字幕延遲",
            f"目前延遲約 {latency_seconds:.1f} 秒，建議使用低延遲分段",
            {"performance_mode": "low_latency", "segment_seconds": 1.5, "speech_threshold": 0.02},
        ))
    try:
        tts_latency = float(config.get("last_tts_latency_seconds") or 0)
    except Exception:
        tts_latency = 0
    if tts_latency > 2.0 and config.get("tts_provider") != "local":
        recommendations.append(TuningRecommendation(
            "use_local_tts",
            "切換本機 TTS",
            f"最近 TTS 延遲約 {tts_latency:.1f} 秒，雲端語音可能拖慢輸出",
            {"tts_provider": "local", "tts_engine": "system"},
        ))
    if config.get("scenario") == "game_voice" and config.get("performance_mode") != "low_latency":
        recommendations.append(TuningRecommendation(
            "game_low_latency",
            "遊戲場景使用低延遲模式",
            "遊戲語音通常需要較短字幕延遲",
            {"performance_mode": "low_latency", "segment_seconds": 1.5},
        ))
    return recommendations


def apply_tuning(config: dict, recommendations: list[TuningRecommendation]) -> dict:
    updated = config.copy()
    for recommendation in recommendations:
        updated.update(recommendation.changes)
    return updated
