import os
import tempfile
import unittest
from pathlib import Path
from realtime_audio_translator.archive_install import write_install_manifest
from realtime_audio_translator.audio import device_identity
from realtime_audio_translator.config import DEFAULT_CONFIG, _has_reparse_point, clear_cache, clear_logs, ensure_app_dirs, ensure_glossary_file, load_config, log_files_to_clear, save_audio_devices, save_config
from realtime_audio_translator.diagnostics import DiagnosticIssue, collect_diagnostics
from realtime_audio_translator.gui import LANGUAGE_CHOICES, PERFORMANCE_CHOICES, PROVIDER_CHOICES, TARGET_LANGUAGE_CHOICES, TTS_PROVIDER_CHOICES, TranslatorApp, diagnostic_action_label, diagnostic_actions, first_diagnostic_action, first_run_setup_action, first_run_wizard_needed, format_overlay_line, language_lock_value, latency_seconds_value, main_status_summary, mode_notice, overlay_clipboard_text, overlay_font_size_value, overlay_hold_seconds_value, overlay_opacity_value, overlay_visibility_action, performance_segment_seconds, setup_guide_actions, status_message_is_error, subtitle_updates_allowed, swap_language_values, troubleshooting_action, visible_button_texts, visible_setting_keys
from realtime_audio_translator.models import cuda_hardware_from_check_output, list_models, model_available, model_download_command, model_install_message, models_dir, recommend_model
from tests.helpers import write_model


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
        self.assertFalse(DEFAULT_CONFIG["start_virtual_mic_muted"])
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
            feedback_device = device_identity({
                "index": 1,
                "name": "虛擬音訊輸出",
                "hostapi": "Windows WASAPI",
                "input_channels": 0,
                "output_channels": 2,
                "default_samplerate": 48000.0,
            })
            config["speaker_device"] = feedback_device
            config["tts_output_device"] = feedback_device

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
            speaker_device = device_identity({
                "index": 2,
                "name": "播放輸出",
                "hostapi": "Windows WASAPI",
                "input_channels": 0,
                "output_channels": 2,
                "default_samplerate": 48000.0,
            })
            config["speaker_device"] = speaker_device
            config["tts_output_device"] = ""
            config["speaker_tts_enabled"] = True
            config["speaker_tts_output_device"] = speaker_device

            issues = collect_diagnostics(config, Path(tmp))

        issue = next(item for item in issues if item.code == "feedback_risk")
        self.assertIn("輸出端點", issue.fix)

    def test_diagnostics_warn_when_virtual_mic_output_is_not_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            write_install_manifest(runtime)
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["tts_enabled"] = True
            config["tts_output_device"] = ""

            issues = collect_diagnostics(config, root)
            config["virtual_mic_enabled"] = True
            enabled_issues = collect_diagnostics(config, root)

        self.assertNotIn("virtual_mic_route", [item.code for item in issues])
        issue = next(item for item in enabled_issues if item.code == "virtual_mic_route")
        self.assertEqual(issue.action, "audio_settings")
        self.assertIn("輸出端點", issue.fix)
        self.assertIn("輸入端點", issue.fix)

    def test_diagnostics_warn_when_virtual_mic_output_device_missing(self):
        import realtime_audio_translator.diagnostics as diagnostics_module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            write_model(model)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            write_install_manifest(runtime)
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["virtual_mic_enabled"] = True
            output_device = device_identity({
                "index": 3,
                "name": "虛擬音訊輸出",
                "hostapi": "Windows WASAPI",
                "input_channels": 0,
                "output_channels": 2,
                "default_samplerate": 48000.0,
            })
            input_device = device_identity({
                "index": 4,
                "name": "虛擬音訊輸入",
                "hostapi": "Windows WASAPI",
                "input_channels": 2,
                "output_channels": 0,
                "default_samplerate": 48000.0,
            })
            config["tts_output_device"] = output_device
            config["virtual_mic_input_device"] = input_device
            original_find_device = diagnostics_module.find_device
            diagnostics_module.find_device = lambda _identity, _want_output: None
            try:
                issues = collect_diagnostics(config, root)
            finally:
                diagnostics_module.find_device = original_find_device

        issue = next(item for item in issues if item.code == "virtual_mic_device_missing")
        self.assertEqual(issue.action, "audio_settings")
        self.assertIn("虛擬音訊輸出", issue.title)
        self.assertIn("輸出端點", issue.fix)

    def test_diagnostics_warn_when_virtual_mic_input_device_missing(self):
        import realtime_audio_translator.diagnostics as diagnostics_module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            write_model(model)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            write_install_manifest(runtime)
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["virtual_mic_enabled"] = True
            output_device = device_identity({
                "index": 5,
                "name": "虛擬音訊輸出",
                "hostapi": "Windows WASAPI",
                "input_channels": 0,
                "output_channels": 2,
                "default_samplerate": 48000.0,
            })
            input_device = device_identity({
                "index": 6,
                "name": "虛擬音訊輸入",
                "hostapi": "Windows WASAPI",
                "input_channels": 2,
                "output_channels": 0,
                "default_samplerate": 48000.0,
            })
            config["tts_output_device"] = output_device
            config["virtual_mic_input_device"] = input_device
            original_find_device = diagnostics_module.find_device

            def fake_find_device(identity, want_output):
                if identity == output_device and want_output:
                    return 1
                return None

            diagnostics_module.find_device = fake_find_device
            try:
                issues = collect_diagnostics(config, root)
            finally:
                diagnostics_module.find_device = original_find_device

        issue = next(item for item in issues if item.code == "virtual_mic_input_missing")
        self.assertEqual(issue.action, "audio_settings")
        self.assertIn("輸入端點", issue.fix)
        self.assertIn("輸入端點", issue.detail)

    def test_diagnostics_do_not_crash_when_audio_device_query_fails(self):
        import realtime_audio_translator.diagnostics as diagnostics_module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            write_model(model)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            write_install_manifest(runtime)
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
            write_install_manifest(runtime)
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            virtual_input_device = device_identity({
                "index": 7,
                "name": "虛擬音訊輸入",
                "hostapi": "Windows WASAPI",
                "input_channels": 2,
                "output_channels": 0,
                "default_samplerate": 48000.0,
            })
            tts_output_device = device_identity({
                "index": 8,
                "name": "虛擬音訊輸出",
                "hostapi": "Windows WASAPI",
                "input_channels": 0,
                "output_channels": 2,
                "default_samplerate": 48000.0,
            })
            config["microphone_device"] = virtual_input_device
            config["virtual_mic_input_device"] = virtual_input_device
            config["tts_output_device"] = tts_output_device
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
            write_install_manifest(runtime)
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_end_to_end_p95_seconds"] = 4.2

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "subtitle_latency_high")
        self.assertEqual(issue.action, "optimize_settings")
        self.assertIn("4.2", issue.detail)
        self.assertIn("自動優化", issue.fix)

    def test_diagnostics_distinguish_asr_network_and_queue_backlog(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = DEFAULT_CONFIG.copy()
            config.update({
                "last_asr_latency_seconds": 2.5,
                "last_translation_latency_seconds": 2.6,
                "last_queue_wait_seconds": 1.4,
                "last_dropped_segments": 3,
                "last_rate_limit_count": 1,
            })

            codes = {issue.code for issue in collect_diagnostics(config, Path(tmp))}

        self.assertTrue({"asr_latency_high", "translation_latency_high", "audio_queue_backlog"}.issubset(codes))

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
            write_install_manifest(runtime)
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["provider"] = "local"
            config["local_translate_url"] = ""
            config["models_path"] = str(root / "translation-models")
            write_model(model)

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
            write_model(model)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            write_install_manifest(runtime)
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["models_path"] = str(configured_models)
            config["model"] = "medium"

            issues = collect_diagnostics(config, root)

        self.assertEqual(models_dir(config), configured_models)
        self.assertNotIn("model_missing", [issue.code for issue in issues])
        self.assertNotIn("model_corrupt", [issue.code for issue in issues])

    def test_diagnostics_distinguish_incomplete_model_from_missing_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "custom-models"
            partial = models / "faster-whisper-custom"
            partial.mkdir(parents=True)
            (partial / "model.bin.partial").write_text("partial", encoding="utf-8")
            config = DEFAULT_CONFIG | {"model": "custom", "models_path": str(models), "runtime_dir": str(root / "runtime")}

            issues = collect_diagnostics(config, root)
            codes = [issue.code for issue in issues]

        self.assertIn("model_corrupt", codes)
        self.assertNotIn("model_missing", codes)
        issue = next(issue for issue in issues if issue.code == "model_corrupt")
        self.assertEqual(issue.action, "download_model")
        self.assertIn("重新下載", issue.fix)

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
            write_install_manifest(runtime)
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_translation_empty"] = True

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "translation_empty")
        self.assertEqual(issue.severity, "warning")
        self.assertEqual(issue.action, "local_translation")

    def test_diagnostics_distinguish_unavailable_translation_quality_and_heuristics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            write_install_manifest(runtime)
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_source_text"] = "hello"
            config["last_provider_quality_signal"] = None
            config["last_translation_heuristic_warning"] = "快取結果，未重新評分"

            issues = collect_diagnostics(config, root)
            config["last_provider_quality_signal"] = 0.73
            config["last_translation_heuristic_warning"] = None
            scored_issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "translation_quality_unavailable")
        self.assertEqual(issue.action, "local_translation")
        self.assertIn("未提供可信品質分數", issue.detail)
        heuristic = next(item for item in issues if item.code == "translation_heuristic_warning")
        self.assertIn("快取結果", heuristic.detail)
        self.assertTrue(any(item.code == "asr_model_score_unavailable" for item in issues))
        quality = next(item for item in scored_issues if item.code == "translation_quality_signal")
        self.assertIn("0.73", quality.detail)
        self.assertIn("不視為校準後正確率", quality.detail)

    def test_diagnostics_label_asr_model_score_as_uncalibrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            model = root / "models" / "medium"
            runtime.mkdir()
            model.mkdir(parents=True)
            (runtime / "faster-whisper-xxl.exe").write_text("exe", encoding="utf-8")
            (runtime / "ffmpeg.exe").write_text("ff", encoding="utf-8")
            (runtime / "_xxl_data").mkdir()
            write_install_manifest(runtime)
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_asr_model_score"] = -0.4

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "asr_model_score_available")
        self.assertEqual(issue.action, "audio_settings")
        self.assertEqual(issue.title, "語音辨識模型分數")
        self.assertIn("-0.40", issue.detail)
        self.assertIn("未校準模型分數", issue.detail)

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
            write_install_manifest(runtime)
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
            write_install_manifest(runtime)
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["last_tts_synthesis_seconds"] = 2.4

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "tts_latency_high")
        self.assertEqual(issue.action, "audio_settings")
        self.assertIn("TTS 供應商", issue.fix)

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
            write_install_manifest(runtime)
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["virtual_mic_enabled"] = True
            config["last_virtual_mic_failed"] = True

            issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "virtual_mic_no_output")
        self.assertIn("Discord", issue.title)
        self.assertIn("配對端點", issue.fix)

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
            write_install_manifest(runtime)
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
            write_install_manifest(runtime)
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
            write_install_manifest(runtime)
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
            write_install_manifest(runtime)
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
            write_install_manifest(runtime)
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
            write_install_manifest(runtime)
            config = DEFAULT_CONFIG.copy()
            config["runtime_dir"] = str(runtime)
            config["model"] = "medium"
            config["source_language"] = "auto"
            config["last_detected_language"] = "en"
            config["last_language_model_score"] = 0.42

            issues = collect_diagnostics(config, root)
            config["last_language_model_score"] = ""
            unavailable_issues = collect_diagnostics(config, root)

        issue = next(item for item in issues if item.code == "language_model_score_low")
        self.assertEqual(issue.severity, "info")
        self.assertEqual(issue.action, "language_settings")
        self.assertIn("不視為校準後正確率", issue.detail)
        self.assertTrue(any(item.code == "language_model_score_unavailable" for item in unavailable_issues))


if __name__ == "__main__":
    unittest.main()
