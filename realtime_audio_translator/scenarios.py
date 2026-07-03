SCENARIO_CHOICES = ("game_voice", "discord_chat", "meeting", "subtitle_only", "two_way")

SCENARIO_PRESETS = {
    "game_voice": {
        "performance_mode": "low_latency",
        "segment_seconds": 1.5,
        "tts_enabled": False,
        "speaker_enabled": True,
        "microphone_enabled": True,
        "show_original_text": True,
        "show_translated_text": True,
        "record_logs": False,
    },
    "discord_chat": {
        "performance_mode": "balanced",
        "segment_seconds": 2.0,
        "tts_enabled": True,
        "speaker_enabled": True,
        "microphone_enabled": True,
        "show_original_text": True,
        "show_translated_text": True,
        "record_logs": False,
    },
    "meeting": {
        "performance_mode": "quality",
        "segment_seconds": 3.0,
        "tts_enabled": False,
        "speaker_enabled": True,
        "microphone_enabled": True,
        "show_original_text": True,
        "show_translated_text": True,
        "record_logs": True,
    },
    "subtitle_only": {
        "performance_mode": "balanced",
        "segment_seconds": 2.0,
        "tts_enabled": False,
        "speaker_enabled": True,
        "microphone_enabled": False,
        "show_original_text": True,
        "show_translated_text": True,
        "record_logs": False,
    },
    "two_way": {
        "performance_mode": "balanced",
        "segment_seconds": 2.0,
        "tts_enabled": True,
        "speaker_enabled": True,
        "microphone_enabled": True,
        "show_original_text": True,
        "show_translated_text": True,
        "record_logs": False,
    },
}


def apply_scenario(config: dict, scenario_key: str) -> dict:
    updated = config.copy()
    updated["scenario"] = scenario_key if scenario_key in SCENARIO_PRESETS else "discord_chat"
    updated.update(SCENARIO_PRESETS.get(updated["scenario"], SCENARIO_PRESETS["discord_chat"]))
    return updated
