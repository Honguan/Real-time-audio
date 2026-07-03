from dataclasses import dataclass
from pathlib import Path

from .ai_auto_tuner import TuningRecommendation, apply_tuning, recommend_tuning
from .diagnostics import DiagnosticIssue, collect_diagnostics
from .scenarios import apply_scenario


@dataclass(frozen=True)
class SessionPlan:
    config: dict
    issues: list[DiagnosticIssue]
    recommendations: list[TuningRecommendation]
    summary: str


def plan_session(config: dict, repo_root: Path, cuda_devices: int = 0, vram_gb: float = 0, latency_seconds: float | None = None) -> SessionPlan:
    planned = apply_scenario(config, str(config.get("scenario", "discord_chat")))
    recommendations = recommend_tuning(planned, cuda_devices, vram_gb, latency_seconds)
    planned = apply_tuning(planned, recommendations)
    issues = collect_diagnostics(planned, repo_root)
    cloud = planned.get("provider") in {"google", "openai"} or planned.get("tts_provider") in {"google", "openai"}
    summary = "雲端 API 模式" if cloud else "本機免費模式"
    if recommendations:
        summary += f"; 建議 {len(recommendations)} 項"
    if issues:
        summary += f"; 診斷 {len(issues)} 項"
    return SessionPlan(planned, issues, recommendations, summary)
