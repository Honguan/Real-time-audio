from dataclasses import dataclass


CLOUD_PROVIDERS = {"google", "openai"}


@dataclass(frozen=True)
class ConfidenceSnapshot:
    source_language: str
    target_language: str
    provider: str
    tts_provider: str
    cloud_enabled: bool
    cost_risk: bool
    asr_latency_seconds: float | None = None
    translation_latency_seconds: float | None = None
    tts_latency_seconds: float | None = None
    language_confidence: float | None = None
    asr_confidence: float | None = None
    translation_confidence: float | None = None


def build_confidence_snapshot(
    config: dict,
    source_language: str,
    target_language: str,
    asr_latency_seconds: float | None = None,
    translation_latency_seconds: float | None = None,
    tts_latency_seconds: float | None = None,
    language_confidence: float | None = None,
    asr_confidence: float | None = None,
    translation_confidence: float | None = None,
) -> ConfidenceSnapshot:
    provider = str(config.get("provider", "local"))
    tts_provider = str(config.get("tts_provider", "local"))
    cloud_enabled = provider in CLOUD_PROVIDERS or tts_provider in CLOUD_PROVIDERS
    return ConfidenceSnapshot(
        source_language=source_language,
        target_language=target_language,
        provider=provider,
        tts_provider=tts_provider,
        cloud_enabled=cloud_enabled,
        cost_risk=cloud_enabled,
        asr_latency_seconds=asr_latency_seconds,
        translation_latency_seconds=translation_latency_seconds,
        tts_latency_seconds=tts_latency_seconds,
        language_confidence=language_confidence,
        asr_confidence=asr_confidence,
        translation_confidence=translation_confidence,
    )


def format_confidence_status(snapshot: ConfidenceSnapshot, advanced: bool = False) -> str:
    mode = "雲端 API 模式" if snapshot.cloud_enabled else "本機免費模式"
    total = sum(value for value in (snapshot.asr_latency_seconds, snapshot.translation_latency_seconds, snapshot.tts_latency_seconds) if value is not None)
    parts = [mode, f"延遲 {total:.2f} 秒", f"翻譯服務 {_provider_label(snapshot.provider)}", f"費用 {'可能' if snapshot.cost_risk else '否'}"]
    if not advanced:
        return "; ".join(parts)

    details = list(parts)
    if snapshot.language_confidence is not None:
        details.append(f"偵測語言 {snapshot.source_language} {_percent(snapshot.language_confidence)}")
    if snapshot.asr_confidence is not None:
        details.append(f"ASR 信心 {_percent(snapshot.asr_confidence)}")
    if snapshot.translation_confidence is not None:
        details.append(f"翻譯信心 {_percent(snapshot.translation_confidence)}")
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


def _percent(value: float) -> str:
    return f"{round(value * 100)}%"


def _provider_label(provider: str) -> str:
    return {"local": "本機", "google": "Google", "openai": "OpenAI"}.get(provider, provider)
