from dataclasses import dataclass

from .config import validate_language_pair


@dataclass(frozen=True)
class TuningRecommendation:
    code: str
    title: str
    detail: str
    changes: dict


def recommend_tuning(config: dict, cuda_devices: int, vram_gb: int) -> list[TuningRecommendation]:
    recommendations: list[TuningRecommendation] = []
    model = config.get("model", "")
    if cuda_devices < 1 and config.get("device") != "cpu":
        changes = {"device": "cpu", "compute_type": "int8"}
        title = "切換 CPU 模式"
        if str(model).startswith("large"):
            changes["model"] = "medium"
            title = "切換 CPU 與 medium 模型"
        recommendations.append(TuningRecommendation(
            "use_cpu_medium",
            title,
            "未偵測到 CUDA GPU，使用 CUDA 設定可能無法啟動或延遲很高",
            changes,
        ))
    if cuda_devices >= 1 and vram_gb < 4 and str(model).startswith("large"):
        recommendations.append(TuningRecommendation(
            "low_vram_medium",
            "低 VRAM 使用 medium 模型",
            f"偵測到 VRAM 約 {vram_gb} GB，較大模型可能延遲過高",
            {"model": "medium"},
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
    validate_language_pair(updated)
    return updated


def format_tuning_preview(before: dict, after: dict, recommendations: list[TuningRecommendation]) -> str:
    lines = ["建議變更（確認後才會儲存）："]
    lines.extend(f"- {item.title}：{item.detail}" for item in recommendations)
    changes = [(key, before.get(key), value) for key, value in sorted(after.items()) if not key.startswith("last_") and before.get(key) != value]
    if changes:
        lines.append("")
        lines.append("設定前後：")
        lines.extend(f"- {key}：{old} → {new}" for key, old, new in changes)
    lines.append("")
    lines.append("選擇「否」會保留原設定。")
    return "\n".join(lines)
