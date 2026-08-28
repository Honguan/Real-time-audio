import tempfile
import unittest
from pathlib import Path
from realtime_audio_translator.config import DEFAULT_CONFIG, _has_reparse_point, clear_cache, clear_logs, ensure_app_dirs, ensure_glossary_file, load_config, log_files_to_clear, save_audio_devices, save_config
from realtime_audio_translator.ai_orchestrator import plan_session
from realtime_audio_translator.ai_auto_tuner import apply_tuning, recommend_tuning
from realtime_audio_translator.ai_confidence import build_confidence_snapshot, format_confidence_status
from realtime_audio_translator.scenarios import SCENARIO_CHOICES, apply_scenario, scenario_key, scenario_label


class AiTests(unittest.TestCase):
    def test_scenarios_apply_expected_existing_settings(self):
        self.assertEqual(SCENARIO_CHOICES, ("game_voice", "discord_chat", "meeting", "customer_service", "subtitle_only", "speak_translate", "two_way"))
        base = DEFAULT_CONFIG.copy()

        game = apply_scenario(base, "game_voice")
        meeting = apply_scenario(base, "meeting")
        discord = apply_scenario(base, "discord_chat")
        subtitle = apply_scenario(base, "subtitle_only")
        customer = apply_scenario(base, "customer_service")
        speak = apply_scenario(base, "speak_translate")
        two_way = apply_scenario(base, "two_way")

        self.assertEqual(game["performance_mode"], "low_latency")
        self.assertEqual(game["segment_seconds"], 1.5)
        self.assertTrue(game["speaker_enabled"])
        self.assertTrue(game["microphone_enabled"])
        self.assertTrue(meeting["record_logs"])
        self.assertEqual(meeting["performance_mode"], "quality")
        self.assertTrue(discord["tts_enabled"])
        self.assertFalse(subtitle["tts_enabled"])
        self.assertFalse(subtitle["microphone_enabled"])
        self.assertEqual(customer["performance_mode"], "quality")
        self.assertTrue(customer["record_logs"])
        self.assertFalse(speak["speaker_enabled"])
        self.assertTrue(speak["microphone_enabled"])
        self.assertTrue(speak["virtual_mic_enabled"])
        self.assertTrue(two_way["speaker_enabled"])
        self.assertTrue(two_way["microphone_enabled"])
        self.assertTrue(two_way["tts_enabled"])
        self.assertTrue(two_way["virtual_mic_enabled"])
        self.assertEqual(base["performance_mode"], DEFAULT_CONFIG["performance_mode"])
        self.assertEqual(scenario_label("discord_chat"), "Discord 聊天")
        self.assertEqual(scenario_key("Discord 聊天"), "discord_chat")
        self.assertEqual(scenario_key("discord_chat"), "discord_chat")
        self.assertEqual(scenario_label("custom"), "custom")

    def test_scenario_rejects_identical_fixed_languages(self):
        config = DEFAULT_CONFIG.copy()
        config["source_language"] = "ko"
        config["target_language"] = "ko"

        with self.assertRaisesRegex(ValueError, "來源與目標語言不可相同"):
            apply_scenario(config, "meeting")

    def test_ai_orchestrator_combines_scenario_tuning_and_diagnostics_without_enabling_cloud(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(root / "runtime")
            config["runtime_path"] = str(root / "runtime")
            config["models_path"] = str(root / "models")
            config["provider"] = "local"
            config["tts_provider"] = "local"
            config["scenario"] = "game_voice"
            config["performance_mode"] = "quality"
            config["device"] = "cuda"
            config["model"] = "large-v3-turbo"

            decision = plan_session(config, root, cuda_devices=0, vram_gb=0)

        self.assertEqual(decision.config["scenario"], "game_voice")
        self.assertEqual(decision.config["performance_mode"], "low_latency")
        self.assertEqual(decision.config["device"], "cpu")
        self.assertEqual(decision.config["model"], "medium")
        self.assertEqual(decision.config["provider"], "local")
        self.assertEqual(decision.config["tts_provider"], "local")
        self.assertIn("use_cpu_medium", [item.code for item in decision.recommendations])
        self.assertIn("runtime_missing", [item.code for item in decision.issues])
        self.assertIn("本機免費模式", decision.summary)

    def test_ai_orchestrator_does_not_enable_logs_without_consent(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = DEFAULT_CONFIG.copy()
            config["scenario"] = "meeting"
            config["record_logs"] = False

            decision = plan_session(config, Path(tmp), cuda_devices=1, vram_gb=6)

        self.assertFalse(decision.config["record_logs"])

    def test_auto_tuner_recommends_cpu_medium_without_cuda(self):
        config = DEFAULT_CONFIG.copy()
        config["device"] = "cuda"
        config["model"] = "large-v3-turbo"

        recommendations = recommend_tuning(config, cuda_devices=0, vram_gb=0)
        tuned = apply_tuning(config, recommendations)

        self.assertIn("use_cpu_medium", [item.code for item in recommendations])
        self.assertEqual(tuned["device"], "cpu")
        self.assertEqual(tuned["model"], "medium")
        self.assertEqual(config["device"], "cuda")
        config["model"] = "small"
        tuned = apply_tuning(config, recommend_tuning(config, cuda_devices=0, vram_gb=0))
        self.assertEqual(tuned["device"], "cpu")
        self.assertEqual(tuned["model"], "small")

    def test_auto_tuner_reduces_latency_settings(self):
        config = DEFAULT_CONFIG.copy()
        config["performance_mode"] = "quality"
        config["segment_seconds"] = 3.0

        recommendations = recommend_tuning(config, cuda_devices=1, vram_gb=6, latency_seconds=4.2)
        tuned = apply_tuning(config, recommendations)

        self.assertIn("reduce_latency", [item.code for item in recommendations])
        self.assertEqual(tuned["performance_mode"], "low_latency")
        self.assertEqual(tuned["segment_seconds"], 1.5)
        self.assertEqual(tuned["speech_threshold"], 0.02)

        config["model"] = "large-v3-turbo"
        tuned = apply_tuning(config, recommend_tuning(config, cuda_devices=1, vram_gb=6, latency_seconds=4.2))

        self.assertEqual(tuned["model"], "medium")

    def test_auto_tuner_shortens_segments_for_fast_speech(self):
        config = DEFAULT_CONFIG.copy()
        config["segment_seconds"] = 3.0
        config["last_speech_units_per_second"] = 3.5

        recommendations = recommend_tuning(config, cuda_devices=1, vram_gb=8)
        tuned = apply_tuning(config, recommendations)

        self.assertIn("fast_speech_segments", [item.code for item in recommendations])
        self.assertEqual(tuned["performance_mode"], "low_latency")
        self.assertEqual(tuned["segment_seconds"], 1.5)

    def test_auto_tuner_recommends_medium_for_low_vram(self):
        config = DEFAULT_CONFIG.copy()
        config["model"] = "large-v2"

        recommendations = recommend_tuning(config, cuda_devices=1, vram_gb=3)
        tuned = apply_tuning(config, recommendations)

        self.assertIn("low_vram_medium", [item.code for item in recommendations])
        self.assertEqual(tuned["model"], "medium")
        config["model"] = "small"
        self.assertNotIn("low_vram_medium", [item.code for item in recommend_tuning(config, cuda_devices=1, vram_gb=3)])

    def test_auto_tuner_uses_local_tts_when_cloud_tts_is_slow(self):
        config = DEFAULT_CONFIG.copy()
        config["tts_provider"] = "openai"
        config["tts_engine"] = "openai"
        config["last_tts_latency_seconds"] = 2.4

        recommendations = recommend_tuning(config, cuda_devices=1, vram_gb=8)
        tuned = apply_tuning(config, recommendations)

        self.assertIn("use_local_tts", [item.code for item in recommendations])
        self.assertEqual(tuned["tts_provider"], "local")
        self.assertEqual(tuned["tts_engine"], "system")

    def test_auto_tuner_speeds_up_local_tts_when_playback_is_slow(self):
        config = DEFAULT_CONFIG.copy()
        config["tts_provider"] = "local"
        config["tts_rate"] = 0
        config["last_tts_latency_seconds"] = 2.4

        recommendations = recommend_tuning(config, cuda_devices=1, vram_gb=8)
        tuned = apply_tuning(config, recommendations)

        self.assertIn("speed_up_local_tts", [item.code for item in recommendations])
        self.assertIn("加快本機 TTS", [item.title for item in recommendations])
        self.assertEqual(tuned["tts_rate"], 2)

    def test_auto_tuner_shows_original_when_translation_confidence_is_low(self):
        config = DEFAULT_CONFIG.copy()
        config["show_original_text"] = False
        config["last_translation_confidence"] = 0.3

        recommendations = recommend_tuning(config, cuda_devices=1, vram_gb=8)
        tuned = apply_tuning(config, recommendations)

        self.assertIn("show_original_on_low_confidence", [item.code for item in recommendations])
        self.assertIn("翻譯信心低時顯示原文", [item.title for item in recommendations])
        self.assertTrue(tuned["show_original_text"])
        self.assertIn("formal_style_on_low_confidence", [item.code for item in recommendations])
        self.assertEqual(tuned["translation_style"], "formal")

    def test_auto_tuner_locks_high_confidence_detected_language(self):
        config = DEFAULT_CONFIG.copy()
        config["source_language"] = "auto"
        config["target_language"] = "zh"
        config["last_detected_language"] = "en"
        config["last_language_confidence"] = 0.92

        recommendations = recommend_tuning(config, cuda_devices=1, vram_gb=8)
        tuned = apply_tuning(config, recommendations)

        self.assertIn("lock_detected_language", [item.code for item in recommendations])
        self.assertIn("鎖定穩定偵測語言", [item.title for item in recommendations])
        self.assertEqual(tuned["source_language"], "en")

    def test_auto_tuner_does_not_lock_source_to_target_language(self):
        config = DEFAULT_CONFIG.copy()
        config["source_language"] = "auto"
        config["target_language"] = "en"
        config["last_detected_language"] = "en"
        config["last_language_confidence"] = 0.92

        recommendations = recommend_tuning(config, cuda_devices=1, vram_gb=8)
        tuned = apply_tuning(config, recommendations)

        self.assertNotIn("lock_detected_language", [item.code for item in recommendations])
        self.assertEqual(tuned["source_language"], "auto")
        self.assertEqual(tuned["target_language"], "en")

    def test_confidence_status_reports_local_mode_latency_and_provider(self):
        config = DEFAULT_CONFIG.copy()
        snapshot = build_confidence_snapshot(config, "en", "zh", asr_latency_seconds=0.82, translation_latency_seconds=0.11)
        status = format_confidence_status(snapshot)

        self.assertFalse(snapshot.cloud_enabled)
        self.assertFalse(snapshot.cost_risk)
        self.assertIn("本機免費模式", status)
        self.assertIn("延遲 0.93 秒", status)
        self.assertIn("翻譯服務 本機", status)

    def test_confidence_status_reports_cloud_cost_and_advanced_details(self):
        config = DEFAULT_CONFIG.copy()
        config["provider"] = "openai"
        config["tts_provider"] = "google"
        snapshot = build_confidence_snapshot(
            config,
            "en",
            "zh",
            asr_latency_seconds=0.82,
            translation_latency_seconds=0.11,
            tts_latency_seconds=0.24,
            language_confidence=0.92,
            asr_confidence=0.8,
            translation_confidence=0.7,
        )
        status = format_confidence_status(snapshot, advanced=True)

        self.assertTrue(snapshot.cloud_enabled)
        self.assertTrue(snapshot.cost_risk)
        self.assertIn("雲端 API 模式", status)
        self.assertIn("費用 可能", status)
        self.assertIn("偵測語言 en 92%", status)
        self.assertIn("ASR 延遲 820ms", status)
        self.assertIn("翻譯延遲 110ms", status)
        self.assertIn("TTS 延遲 240ms", status)
        self.assertIn("TTS 服務 Google", status)


if __name__ == "__main__":
    unittest.main()
