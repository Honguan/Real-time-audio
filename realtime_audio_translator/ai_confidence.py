from dataclasses import dataclass


CLOUD_PROVIDERS = {"google", "openai"}


@dataclass(frozen=True)
class QualitySnapshot:
    source_language: str
    target_language: str
    provider: str
    tts_provider: str
    cloud_enabled: bool
    cost_risk: bool
    asr_latency_seconds: float | None = None
    translation_latency_seconds: float | None = None
    tts_latency_seconds: float | None = None
    language_model_score: float | None = None
    asr_model_score: float | None = None
    provider_quality_signal: float | None = None
    translation_heuristic_warning: str | None = None


def build_quality_snapshot(
    config: dict,
    source_language: str,
    target_language: str,
    asr_latency_seconds: float | None = None,
    translation_latency_seconds: float | None = None,
    tts_latency_seconds: float | None = None,
    language_model_score: float | None = None,
    asr_model_score: float | None = None,
    provider_quality_signal: float | None = None,
    translation_heuristic_warning: str | None = None,
) -> QualitySnapshot:
    provider = str(config.get("provider", "local"))
    tts_provider = str(config.get("tts_provider", "local"))
    cloud_enabled = provider in CLOUD_PROVIDERS or tts_provider in CLOUD_PROVIDERS
    return QualitySnapshot(
        source_language=source_language,
        target_language=target_language,
        provider=provider,
        tts_provider=tts_provider,
        cloud_enabled=cloud_enabled,
        cost_risk=cloud_enabled,
        asr_latency_seconds=asr_latency_seconds,
        translation_latency_seconds=translation_latency_seconds,
        tts_latency_seconds=tts_latency_seconds,
        language_model_score=language_model_score,
        asr_model_score=asr_model_score,
        provider_quality_signal=provider_quality_signal,
        translation_heuristic_warning=translation_heuristic_warning,
    )


def format_quality_status(snapshot: QualitySnapshot, advanced: bool = False) -> str:
    mode = "雲端 API 模式" if snapshot.cloud_enabled else "本機免費模式"
    total = sum(value for value in (snapshot.asr_latency_seconds, snapshot.translation_latency_seconds, snapshot.tts_latency_seconds) if value is not None)
    parts = [mode, f"延遲 {total:.2f} 秒", f"翻譯服務 {_provider_label(snapshot.provider)}", f"費用 {'可能' if snapshot.cost_risk else '否'}"]
    if not advanced:
        return "; ".join(parts)

    details = list(parts)
    if snapshot.language_model_score is not None:
        details.append(f"偵測語言 {snapshot.source_language} 模型分數 {snapshot.language_model_score:.2f}（未校準）")
    else:
        details.append("語言模型分數 無法取得")
    if snapshot.asr_model_score is not None:
        details.append(f"ASR 模型分數 {snapshot.asr_model_score:.2f}（平均 log probability，未校準）")
    else:
        details.append("ASR 模型分數 無法取得")
    if snapshot.provider_quality_signal is None:
        details.append("翻譯品質訊號 無法取得")
    else:
        details.append(f"翻譯品質訊號 {snapshot.provider_quality_signal:.2f}（供應商訊號）")
    if snapshot.translation_heuristic_warning:
        details.append(f"翻譯提示 {snapshot.translation_heuristic_warning}")
    if snapshot.asr_latency_seconds is not None:
        details.append(f"ASR 延遲 {_milliseconds(snapshot.asr_latency_seconds)}")
    if snapshot.translation_latency_seconds is not None:
        details.append(f"翻譯延遲 {_milliseconds(snapshot.translation_latency_seconds)}")
    if snapshot.tts_latency_seconds is not None:
        details.append(f"TTS 延遲 {_milliseconds(snapshot.tts_latency_seconds)}")
    details.append(f"TTS 服務 {_provider_label(snapshot.tts_provider)}")
    return "; ".join(details)


def _milliseconds(seconds: float) -> str:
    return f"{round(seconds * 1000)}ms"


def _provider_label(provider: str) -> str:
    return {"local": "本機", "google": "Google", "openai": "OpenAI"}.get(provider, provider)
