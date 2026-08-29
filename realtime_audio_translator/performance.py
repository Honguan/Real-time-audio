import math
from collections import deque


END_TO_END_P50 = "last_end_to_end_p50_seconds"
END_TO_END_P95 = "last_end_to_end_p95_seconds"
END_TO_END_P99 = "last_end_to_end_p99_seconds"
FIRST_SUBTITLE_P50 = "last_first_subtitle_p50_seconds"
FIRST_SUBTITLE_P95 = "last_first_subtitle_p95_seconds"
FIRST_SUBTITLE_P99 = "last_first_subtitle_p99_seconds"
QUEUE_WAIT = "last_queue_wait_seconds"
ASR_LATENCY = "last_asr_latency_seconds"
TRANSLATION_LATENCY = "last_translation_latency_seconds"
TTS_SYNTHESIS = "last_tts_synthesis_seconds"
TTS_PLAYBACK = "last_tts_playback_seconds"
TTS_QUEUE_DEPTH = "last_tts_queue_depth"
REAL_TIME_FACTOR = "last_real_time_factor"
CPU_PERCENT = "last_cpu_percent"

END_TO_END_P95_BUDGET_SECONDS = 3.0
FIRST_SUBTITLE_P95_BUDGET_SECONDS = 3.0
QUEUE_WAIT_BUDGET_SECONDS = 1.0
ASR_BUDGET_SECONDS = 2.0
TRANSLATION_BUDGET_SECONDS = 2.0
TTS_STAGE_BUDGET_SECONDS = 2.0

DEFINED_METRICS = {
    END_TO_END_P50,
    END_TO_END_P95,
    END_TO_END_P99,
    FIRST_SUBTITLE_P50,
    FIRST_SUBTITLE_P95,
    FIRST_SUBTITLE_P99,
    QUEUE_WAIT,
    ASR_LATENCY,
    TRANSLATION_LATENCY,
    TTS_SYNTHESIS,
    TTS_PLAYBACK,
    TTS_QUEUE_DEPTH,
    REAL_TIME_FACTOR,
    CPU_PERCENT,
}


def metric_value(config: dict, key: str) -> float | None:
    if key not in DEFINED_METRICS:
        raise KeyError(f"undefined performance metric: {key}")
    try:
        return float(config[key]) if config.get(key) not in (None, "") else None
    except (TypeError, ValueError):
        return None


class LatencyWindow:
    def __init__(self, maxlen: int = 1000):
        self.samples = deque(maxlen=max(1, int(maxlen)))

    def add(self, seconds: float) -> None:
        self.samples.append(max(0.0, float(seconds)))

    def snapshot(self, keys=(END_TO_END_P50, END_TO_END_P95, END_TO_END_P99)) -> dict[str, float]:
        if not self.samples:
            return {}
        ordered = sorted(self.samples)

        def percentile(value: float) -> float:
            return ordered[max(0, math.ceil(value * len(ordered)) - 1)]

        return dict(zip(keys, (percentile(0.50), percentile(0.95), percentile(0.99))))
