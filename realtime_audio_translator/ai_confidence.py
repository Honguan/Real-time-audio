from dataclasses import dataclass

from .localization import translate


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


def format_quality_status(snapshot: QualitySnapshot, advanced: bool = False, language: str = "zh-TW") -> str:
    t = lambda message, **values: translate(language, message, **values)
    mode = t("雲端 API 模式" if snapshot.cloud_enabled else "本機免費模式")
    total = sum(value for value in (snapshot.asr_latency_seconds, snapshot.translation_latency_seconds, snapshot.tts_latency_seconds) if value is not None)
    parts = [mode, t("延遲 {total:.2f} 秒", total=total), t("翻譯服務 {provider}", provider=_provider_label(snapshot.provider, language)), t("費用 {risk}", risk=t("可能" if snapshot.cost_risk else "否"))]
    if not advanced:
        return "; ".join(parts)

    details = list(parts)
    if snapshot.language_model_score is not None:
        details.append(t("偵測語言 {source} 模型分數 {score:.2f}（未校準）", source=snapshot.source_language, score=snapshot.language_model_score))
    else:
        details.append(t("語言模型分數 無法取得"))
    if snapshot.asr_model_score is not None:
        details.append(t("ASR 模型分數 {score:.2f}（平均 log probability，未校準）", score=snapshot.asr_model_score))
    else:
        details.append(t("ASR 模型分數 無法取得"))
    if snapshot.provider_quality_signal is None:
        details.append(t("翻譯品質訊號 無法取得"))
    else:
        details.append(t("翻譯品質訊號 {signal:.2f}（供應商訊號）", signal=snapshot.provider_quality_signal))
    if snapshot.translation_heuristic_warning:
        details.append(t("翻譯提示 可用") if language == "en" else f"翻譯提示 {snapshot.translation_heuristic_warning}")
    if snapshot.asr_latency_seconds is not None:
        details.append(t("ASR 延遲 {latency}", latency=_milliseconds(snapshot.asr_latency_seconds)))
    if snapshot.translation_latency_seconds is not None:
        details.append(t("翻譯延遲 {latency}", latency=_milliseconds(snapshot.translation_latency_seconds)))
    if snapshot.tts_latency_seconds is not None:
        details.append(t("TTS 延遲 {latency}", latency=_milliseconds(snapshot.tts_latency_seconds)))
    details.append(t("TTS 服務 {provider}", provider=_provider_label(snapshot.tts_provider, language)))
    return "; ".join(details)


def _milliseconds(seconds: float) -> str:
    return f"{round(seconds * 1000)}ms"


def _provider_label(provider: str, language: str = "zh-TW") -> str:
    label = {"local": "本機", "google": "Google", "openai": "OpenAI"}.get(provider, provider)
    return translate(language, label) if label == "本機" else label
