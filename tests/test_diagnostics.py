import os
import tempfile
import unittest
from pathlib import Path
from realtime_audio_translator.config import DEFAULT_CONFIG, _has_reparse_point, clear_cache, clear_logs, ensure_app_dirs, ensure_glossary_file, load_config, log_files_to_clear, save_audio_devices, save_config
from realtime_audio_translator.diagnostics import DiagnosticIssue, collect_diagnostics
from realtime_audio_translator.gui import LANGUAGE_CHOICES, PERFORMANCE_CHOICES, PROVIDER_CHOICES, TARGET_LANGUAGE_CHOICES, TTS_PROVIDER_CHOICES, TranslatorApp, diagnostic_action_label, diagnostic_actions, first_diagnostic_action, first_run_setup_action, first_run_wizard_needed, format_overlay_line, language_lock_value, latency_seconds_value, main_status_summary, mode_notice, overlay_clipboard_text, overlay_font_size_value, overlay_hold_seconds_value, overlay_opacity_value, overlay_visibility_action, performance_segment_seconds, record_logs_requires_confirmation, setup_guide_actions, status_message_is_error, subtitle_updates_allowed, swap_language_values, troubleshooting_action, visible_button_texts, visible_setting_keys
from realtime_audio_translator.models import cuda_hardware_from_check_output, list_models, model_available, model_download_command, model_install_message, models_dir, recommend_model


class DiagnosticsTests(unittest.TestCase):
    def test_default_mode_uses_free_local_providers(self):
        self.assertEqual(DEFAULT_CONFIG["app_language"], "zh-TW")
        self.assertEqual(DEFAULT_CONFIG["ui_mode"], "simple")
        self.assertEqual(DEFAULT_CONFIG["asr_engine"], "faster-whisper-xxl")
        self.assertEqual(DEFAULT_CONFIG["asr_model"], "small")
        self.assertEqual(DEFAULT_CONFIG["model"], "small")
        self.assertEqual(DEFAULT_CONFIG["translation_engine"], "local")
        self.assertEqual(DEFAULT_CONFIG["translation_style"], "plain")
        self.assertEqual(DEFAULT_CONFIG["tts_engine"], "system")
        self.assertEqual(DEFAULT_CONFIG["runtime_path"], str(Path.home() / ".realtime-audio" / "runtime" / "cuda12"))
        self.assertEqual(DEFAULT_CONFIG["models_path"], str(Path.home() / ".realtime-audio" / "models"))
        self.assertFalse(DEFAULT_CONFIG["save_conversation_history"])
        self.assertFalse(DEFAULT_CONFIG["cloud_api_enabled"])
        self.assertTrue(DEFAULT_CONFIG["subtitle_always_on_top"])
        self.assertFalse(DEFAULT_CONFIG["virtual_mic_enabled"])
        self.assertFalse(DEFAULT_CONFIG["start_muted"])
        self.assertEqual(DEFAULT_CONFIG["provider"], "local")
        self.assertEqual(DEFAULT_CONFIG["tts_provider"], "local")
        self.assertFalse(DEFAULT_CONFIG["advanced_mode"])
        self.assertEqual(DEFAULT_CONFIG["scenario"], "discord_chat")
        self.assertTrue(DEFAULT_CONFIG["ai_auto_optimize"])
        self.assertTrue(DEFAULT_CONFIG["ai_self_diagnosis"])
        self.assertFalse(DEFAULT_CONFIG["setup_guide_shown"])
        self.assertEqual(DEFAULT_CONFIG["performance_mode"], "balanced")
        notice = mode_notice(DEFAULT_CONFIG["provider"], DEFAULT_CONFIG["tts_provider"])
        self.assertIn("目前模式：本機免費模式", notice)
        self.assertIn("語音是否上傳：否", notice)
        self.assertIn("是否可能產生 API 費用：否", notice)

    def test_diagnostics_report_runtime_model_feedback_and_provider_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(root / "runtime")
            config["model"] = "missing-model"
            config["provider"] = "google"
            config["speaker_device"] = "CABLE Input [Windows WASAPI]"
            config["tts_output_device"] = "CABLE Input"

            issues = collect_diagnostics(config, root)
            codes = [issue.code for issue in issues]

        self.assertIn("runtime_missing", codes)
        self.assertIn("model_missing", codes)
        self.assertIn("feedback_risk", codes)
        self.assertIn("cloud_credentials_missing", codes)
        runtime_issue = next(issue for issue in issues if issue.code == "runtime_missing")
        cloud_issue = next(issue for issue in issues if issue.code == "cloud_credentials_missing")
        self.assertIn("RealtimeAudioTranslator-runtime-cuda12-core-<version>.7z", runtime_issue.fix)
        self.assertIn("RealtimeAudioTranslator-runtime-cuda12-dlls-<version>.zip", runtime_issue.fix)
        self.assertIn("專案 ID", cloud_issue.detail)
        self.assertIn("兩個都", runtime_issue.fix)
        self.assertTrue(all(isinstance(issue.title, str) for issue in issues))
        self.assertTrue(all(issue.action for issue in issues))

    def test_diagnostics_report_recent_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(Path(tmp) / "runtime")
            config["last_error"] = "對方：翻譯失敗：timeout"

            issues = collect_diagnostics(config, Path(tmp))

        issue = next(item for item in issues if item.code == "recent_error")
        self.assertEqual(issue.detail, "對方：翻譯失敗：timeout")
        self.assertEqual(issue.action, "open_logs")

    def test_diagnostics_warn_when_speaker_tts_output_matches_speaker_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(Path(tmp) / "runtime")
            config["speaker_device"] = "Speakers [Windows WASAPI]"
            config["tts_output_device"] = "CABLE Input"
            config["speaker_tts_enabled"] = True
            config["speaker_tts_output_device"] = "Speakers"

            issues = collect_diagnostics(config, Path(tmp))

        issue = next(item for item in issues if item.code == "feedback_risk")
        self.assertIn("對方翻譯播放輸出", issue.fix)

    def test_diagnostics_warn_when_virtual_mic_output_is_not_cable_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["tts_enabled"] = True
            config["tts_output_device"] = "Speakers"

            issues = collect_diagnostics(config, root)
            config["virtual_mic_enabled"] = True
            enabled_issues = collect_diagnostics(config, root)

        self.assertNotIn("virtual_mic_route", [item.code for item in issues])
        issue = next(item for item in enabled_issues if item.code == "virtual_mic_route")
        self.assertEqual(issue.action, "audio_settings")
        self.assertIn("CABLE Input", issue.fix)
        self.assertIn("CABLE Output", issue.fix)

    def test_diagnostics_warn_when_virtual_mic_cable_input_device_missing(self):
        import realtime_audio_translator.diagnostics as diagnostics_module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (model / "model.bin").write_text("model", encoding="utf-8")
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["virtual_mic_enabled"] = True
            config["tts_output_device"] = "CABLE Input"
            original_find_device = diagnostics_module.find_device
            diagnostics_module.find_device = lambda _name, want_output: None
            try:
                issues = collect_diagnostics(config, root)
            finally:
                diagnostics_module.find_device = original_find_device

        issue = next(item for item in issues if item.code == "virtual_mic_device_missing")
        self.assertEqual(issue.action, "audio_settings")
        self.assertIn("VB-CABLE", issue.title)
        self.assertIn("CABLE Input", issue.fix)

    def test_diagnostics_warn_when_virtual_mic_cable_output_input_missing(self):
        import realtime_audio_translator.diagnostics as diagnostics_module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (model / "model.bin").write_text("model", encoding="utf-8")
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["virtual_mic_enabled"] = True
            config["tts_output_device"] = "CABLE Input"
            original_find_device = diagnostics_module.find_device

            def fake_find_device(name, want_output):
                if name == "CABLE Input" and want_output:
                    return 1
                return None

            diagnostics_module.find_device = fake_find_device
            try:
                issues = collect_diagnostics(config, root)
            finally:
                diagnostics_module.find_device = original_find_device

        issue = next(item for item in issues if item.code == "virtual_mic_input_missing")
        self.assertEqual(issue.action, "audio_settings")
        self.assertIn("CABLE Output", issue.fix)
        self.assertIn("Discord", issue.detail)

    def test_diagnostics_do_not_crash_when_audio_device_query_fails(self):
        import realtime_audio_translator.diagnostics as diagnostics_module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (model / "model.bin").write_text("model", encoding="utf-8")
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["speaker_device"] = "Speakers"
            config["microphone_device"] = "Microphone"
            original_find_device = diagnostics_module.find_device
            diagnostics_module.find_device = lambda _name, want_output: (_ for _ in ()).throw(RuntimeError("audio backend unavailable"))
            try:
                issues = collect_diagnostics(config, root)
            finally:
                diagnostics_module.find_device = original_find_device

        codes = [item.code for item in issues]
        self.assertIn("speaker_device_missing", codes)
        self.assertIn("microphone_device_missing", codes)

    def test_diagnostics_warn_when_microphone_captures_virtual_mic_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["microphone_device"] = "CABLE Output (VB-Audio Virtual Cable) [Windows WASAPI]"
            config["tts_output_device"] = "CABLE Input (VB-Audio Virtual Cable) [Windows WASAPI]"
            config["virtual_mic_enabled"] = True

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "microphone_feedback_risk")
        self.assertEqual(issue.action, "audio_settings")
        self.assertIn("實體麥克風", issue.fix)

    def test_diagnostics_report_high_subtitle_latency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_latency_seconds"] = 4.2

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "subtitle_latency_high")
        self.assertEqual(issue.action, "optimize_settings")
        self.assertIn("4.2", issue.detail)
        self.assertIn("自動優化", issue.fix)

    def test_local_provider_without_translate_url_is_info_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["provider"] = "local"
            config["local_translate_url"] = ""
            config["models_path"] = str(root / "translation-models")
            (model / "model.bin").write_text("model", encoding="utf-8")

            issues = collect_diagnostics(config, root)

        local_issue = next(issue for issue in issues if issue.code == "local_translate_url_missing")
        offline_issue = next(issue for issue in issues if issue.code == "offline_translation_model_missing")
        self.assertEqual(local_issue.severity, "info")
        self.assertEqual(offline_issue.action, "download_translation_models")
        self.assertNotIn("runtime_missing", [issue.code for issue in issues])
        self.assertNotIn("model_missing", [issue.code for issue in issues])

    def test_diagnostics_uses_configured_models_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            configured_models = root / "custom-models"
            model = configured_models / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (model / "model.bin").write_text("model", encoding="utf-8")
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["models_path"] = str(configured_models)
            config["model"] = "medium"

            issues = collect_diagnostics(config, root)

        self.assertEqual(models_dir(config), configured_models)
        self.assertNotIn("model_missing", [issue.code for issue in issues])

    def test_models_dir_expands_windows_environment_variables(self):
        self.assertEqual(models_dir({"models_path": r"%USERPROFILE%\models"}), Path(os.environ["USERPROFILE"]) / "models")

    def test_diagnostics_report_empty_translation_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_translation_empty"] = True

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "translation_empty")
        self.assertEqual(issue.severity, "warning")
        self.assertEqual(issue.action, "local_translation")

    def test_diagnostics_report_low_translation_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_translation_confidence"] = 0.3

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "translation_confidence_low")
        self.assertEqual(issue.action, "local_translation")
        self.assertIn("修正上次翻譯", issue.fix)

    def test_diagnostics_report_low_asr_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_asr_confidence"] = 0.4

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "asr_confidence_low")
        self.assertEqual(issue.action, "audio_settings")
        self.assertEqual(issue.title, "語音辨識信心偏低")
        self.assertIn("語音辨識信心約 40%", issue.detail)
        self.assertIn("較大模型", issue.fix)

    def test_diagnostics_report_tts_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_tts_failed"] = True

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "tts_no_sound")
        self.assertEqual(issue.severity, "warning")
        self.assertEqual(issue.action, "audio_settings")

    def test_diagnostics_report_high_tts_latency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_tts_latency_seconds"] = 2.4

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "tts_latency_high")
        self.assertEqual(issue.action, "audio_settings")
        self.assertIn("本機 TTS", issue.fix)

    def test_diagnostics_report_virtual_mic_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["virtual_mic_enabled"] = True
            config["last_virtual_mic_failed"] = True

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "virtual_mic_no_output")
        self.assertIn("Discord", issue.title)
        self.assertIn("CABLE Output", issue.fix)

    def test_diagnostics_report_quiet_audio_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_mic_quiet"] = True
            config["last_speaker_quiet"] = True

            issues = collect_diagnostics(config, root)

        codes = [item.code for item in issues]
        self.assertIn("microphone_no_sound", codes)
        self.assertIn("speaker_no_sound", codes)

    def test_diagnostics_report_gpu_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "large-v3-turbo"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["device"] = "cuda"
            config["model"] = "large-v3-turbo"
            config["last_cuda_devices"] = 0
            config["last_vram_gb"] = 0

            no_gpu = collect_diagnostics(config, root)
            config["last_cuda_devices"] = 1
            config["last_vram_gb"] = 3
            low_vram = collect_diagnostics(config, root)
            config["model"] = "small"
            small_model = collect_diagnostics(config, root)

        self.assertIn("gpu_unavailable", [item.code for item in no_gpu])
        self.assertIn("gpu_low_vram", [item.code for item in low_vram])
        self.assertNotIn("gpu_low_vram", [item.code for item in small_model])
        no_gpu_auto = next(item for item in no_gpu if item.code == "auto_tune_recommended")
        self.assertIn("切換 CPU 與 medium 模型", no_gpu_auto.detail)
        low_vram_auto = next(item for item in low_vram if item.code == "auto_tune_recommended")
        self.assertIn("低 VRAM 使用 medium 模型", low_vram_auto.detail)

    def test_diagnostics_report_asr_runtime_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_asr_failed"] = True

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "asr_runtime_failed")
        self.assertEqual(issue.action, "open_runtime")

    def test_diagnostics_report_ffmpeg_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_ffmpeg_failed"] = True

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "ffmpeg_failed")
        self.assertEqual(issue.action, "open_runtime")

    def test_diagnostics_include_auto_tune_recommendations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["scenario"] = "game_voice"
            config["performance_mode"] = "quality"
            config["ai_auto_optimize"] = True

            issues = collect_diagnostics(config, root)

        auto_issue = next(issue for issue in issues if issue.code == "auto_tune_recommended")
        self.assertEqual(auto_issue.severity, "info")
        self.assertIn("遊戲場景使用低延遲模式", auto_issue.detail)

    def test_diagnostics_suggest_locking_language_when_auto_detection_is_uncertain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["source_language"] = "auto"
            config["last_detected_language"] = "en"
            config["last_language_confidence"] = 0.42

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "language_lock_recommended")
        self.assertEqual(issue.severity, "info")
        self.assertEqual(issue.action, "language_settings")


if __name__ == "__main__":
    unittest.main()
