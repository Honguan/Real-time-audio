SCENARIO_CHOICES = ("game_voice", "discord_chat", "meeting", "customer_support", "subtitle_only", "mic_translate", "two_way")

SCENARIO_LABELS = {
    "game_voice": "遊戲語音",
    "discord_chat": "Discord 聊天",
    "meeting": "遠端會議",
    "customer_support": "客服對話",
    "subtitle_only": "只顯示字幕",
    "mic_translate": "自己說話翻譯輸出",
    "two_way": "雙向完整翻譯",
}

SCENARIO_PRESETS = {
    "game_voice": {
        "performance_mode": "low_latency",
        "segment_seconds": 1.5,
        "tts_enabled": False,
        "speaker_enabled": True,
        "microphone_enabled": True,
        "virtual_mic_enabled": False,
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
        "virtual_mic_enabled": True,
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
        "virtual_mic_enabled": False,
        "show_original_text": True,
        "show_translated_text": True,
        "record_logs": True,
    },
    "customer_support": {
        "performance_mode": "quality",
        "translation_style": "formal",
        "segment_seconds": 3.0,
        "tts_enabled": True,
        "speaker_enabled": True,
        "microphone_enabled": True,
        "virtual_mic_enabled": True,
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
        "virtual_mic_enabled": False,
        "show_original_text": True,
        "show_translated_text": True,
        "record_logs": False,
    },
    "mic_translate": {
        "performance_mode": "balanced",
        "segment_seconds": 2.0,
        "tts_enabled": True,
        "speaker_enabled": False,
        "microphone_enabled": True,
        "virtual_mic_enabled": True,
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
        "virtual_mic_enabled": True,
        "show_original_text": True,
        "show_translated_text": True,
        "record_logs": False,
    },
}


def scenario_label(scenario_key: str) -> str:
    return SCENARIO_LABELS.get(scenario_key, scenario_key)


def apply_scenario(config: dict, scenario_key: str) -> dict:
    updated = config.copy()
    updated["scenario"] = scenario_key if scenario_key in SCENARIO_PRESETS else "discord_chat"
    updated["translation_style"] = "plain"
    updated.update(SCENARIO_PRESETS.get(updated["scenario"], SCENARIO_PRESETS["discord_chat"]))
    return updated
