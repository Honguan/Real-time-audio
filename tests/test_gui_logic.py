import unittest
from pathlib import Path
from unittest.mock import Mock
from realtime_audio_translator.config import DEFAULT_CONFIG, _has_reparse_point, clear_cache, clear_logs, ensure_app_dirs, ensure_glossary_file, load_config, log_files_to_clear, save_audio_devices, save_config
from realtime_audio_translator.diagnostics import DiagnosticIssue, collect_diagnostics
from realtime_audio_translator.engine import RealtimeEngine, audio_devices_overlap, direction_label, drain_queue, overlay_text_from_config, safe_target_language
from realtime_audio_translator.gui import LANGUAGE_CHOICES, PERFORMANCE_CHOICES, PROVIDER_CHOICES, TARGET_LANGUAGE_CHOICES, TTS_PROVIDER_CHOICES, TranslatorApp, diagnostic_action_label, diagnostic_actions, first_diagnostic_action, first_run_setup_action, first_run_wizard_needed, format_overlay_line, language_lock_value, latency_seconds_value, main_status_summary, mode_notice, overlay_clipboard_text, overlay_font_size_value, overlay_hold_seconds_value, overlay_opacity_value, overlay_visibility_action, performance_segment_seconds, record_logs_requires_confirmation, setup_guide_actions, status_message_is_error, subtitle_updates_allowed, swap_language_values, troubleshooting_action, visible_button_texts, visible_setting_keys


class GuiLogicTests(unittest.TestCase):
    def test_conversation_logs_are_off_by_default(self):
        self.assertFalse(DEFAULT_CONFIG["record_logs"])
        self.assertEqual(DEFAULT_CONFIG["log_dir"], str(Path.home() / ".realtime-audio" / "logs"))
        self.assertEqual(DEFAULT_CONFIG["tts_rate"], 0)
        self.assertEqual(DEFAULT_CONFIG["tts_volume"], 100)
        self.assertEqual(DEFAULT_CONFIG["tts_voice_name"], "")
        self.assertTrue(DEFAULT_CONFIG["show_original_text"])
        self.assertTrue(DEFAULT_CONFIG["show_translated_text"])
        self.assertEqual(DEFAULT_CONFIG["last_asr_confidence"], "")

    def test_advanced_settings_expose_openai_tts_options(self):
        settings = visible_setting_keys(True)

        self.assertIn("openai_model", settings)
        self.assertIn("openai_tts_model", settings)
        self.assertIn("openai_tts_voice", settings)

    def test_record_logs_toggle_saves_immediately(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('record_logs_widget = ttk.Checkbutton(frame, text="儲存對話紀錄", variable=self.record_logs, command=self._save)', gui_source)
        self.assertIn('speaker_tts_widget = ttk.Checkbutton(frame, text="播放對方翻譯", variable=self.speaker_tts_enabled, command=self._save)', gui_source)
        self.assertIn('start_muted_widget = ttk.Checkbutton(frame, text="啟動時先靜音", variable=self.start_muted, command=self._save)', gui_source)
        self.assertIn('overlay_topmost_widget = ttk.Checkbutton(frame, text="字幕最上層", variable=self.overlay_topmost, command=self._apply_overlay)', gui_source)
        self.assertIn('language_labels_widget = ttk.Checkbutton(frame, text="顯示語言", variable=self.show_language_labels, command=self._save)', gui_source)
        self.assertIn('speaker_capture_widget = ttk.Checkbutton(frame, text="擷取喇叭", variable=self.speaker_enabled, command=self._save)', gui_source)
        self.assertIn("self.advanced_mode_widgets = [runtime_buttons_widget, overlay_topmost_widget, language_labels_widget, original_text_widget, translated_text_widget, speaker_capture_widget, microphone_capture_widget, record_logs_widget, speaker_tts_widget, start_muted_widget]", gui_source)
        self.assertIn("for widget in self.advanced_mode_widgets:", gui_source)
        self.assertIn('translated_text_widget = ttk.Checkbutton(frame, text="顯示譯文", variable=self.show_translated_text, command=self._save)', gui_source)

    def test_open_logs_button_opens_configured_log_dir(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("開啟紀錄", self._open_logs)', gui_source)
        self.assertIn('("匯出字幕", self._export_subtitles)', gui_source)
        self.assertIn('def _open_logs(self) -> None:', gui_source)
        self.assertIn('def _export_subtitles(self) -> None:', gui_source)
        self.assertIn("export_jsonl_to_srt", gui_source)
        self.assertIn("export_jsonl_to_txt", gui_source)
        self.assertIn("append_app_log", gui_source)
        self.assertIn("save_audio_devices", gui_source)
        self.assertIn('subprocess.Popen(["explorer", str(path)])', gui_source)

    def test_open_app_folder_button_opens_app_dir(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("開啟程式資料夾", self._open_app_dir)', gui_source)
        self.assertIn('def _open_app_dir(self) -> None:', gui_source)
        self.assertIn('path = APP_DIR', gui_source)

    def test_google_json_picker_saves_immediately(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('self.vars["google_service_account_json"].set(path)\n            self._save()', gui_source)

    def test_device_model_voice_choices_save_immediately(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('elif key in AUDIO_DEVICE_KEYS or key in ("model", "device", "compute_type", "tts_voice_name"):', gui_source)
        self.assertIn('asr_devices = command_choices(commands, "device") or ["cuda", "cpu"]', gui_source)
        self.assertIn('compute_types = command_choices(commands, "compute_type") or ["auto", "int8", "float16", "float32"]', gui_source)

    def test_push_to_talk_button_holds_unmute(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('self.start_muted = tk.BooleanVar(value=bool(self.config.get("start_muted", False)))', gui_source)
        self.assertIn('ttk.Checkbutton(frame, text="啟動時先靜音", variable=self.start_muted, command=self._save)', gui_source)
        self.assertIn('config["start_muted"] = self.start_muted.get()', gui_source)
        self.assertIn('ptt_button = ttk.Button(buttons, text="按住說話")', gui_source)
        self.assertIn('ptt_button.bind("<ButtonPress-1>", lambda _event: self._push_to_talk(True))', gui_source)
        self.assertIn('ptt_button.bind("<ButtonRelease-1>", lambda _event: self._push_to_talk(False))', gui_source)
        self.assertIn('self.engine.set_muted(False)', gui_source)

    def test_subtitle_test_button_updates_overlay(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("測試字幕", self._test_subtitles)', gui_source)
        self.assertIn('def _test_subtitles(self) -> None:', gui_source)
        self.assertIn('self.overlay.update_lines("字幕測試", "字幕測試")', gui_source)

    def test_overlay_quick_toggle_switches_visibility(self):
        import realtime_audio_translator.gui as gui_module

        self.assertTrue(hasattr(gui_module, "toggle_overlay_visibility"))
        self.assertFalse(gui_module.toggle_overlay_visibility(True))
        self.assertTrue(gui_module.toggle_overlay_visibility(False))

        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('("切換字幕", self._toggle_subtitles)', gui_source)
        self.assertIn("self.overlay_visible.set(toggle_overlay_visibility(self.overlay_visible.get()))", gui_source)

    def test_speech_quick_toggle_switches_tts_output(self):
        import realtime_audio_translator.gui as gui_module

        self.assertTrue(hasattr(gui_module, "toggle_speech_enabled"))
        self.assertFalse(gui_module.toggle_speech_enabled(True))
        self.assertTrue(gui_module.toggle_speech_enabled(False))

        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('("切換語音", self._toggle_speech)', gui_source)
        self.assertIn("self.tts_enabled.set(toggle_speech_enabled(self.tts_enabled.get()))", gui_source)
        self.assertIn("輸出到虛擬麥克風", gui_source)
        self.assertIn('config["virtual_mic_enabled"] = self.virtual_mic_enabled.get()', gui_source)
        self.assertIn('self.virtual_mic_enabled.set(bool(updated.get("virtual_mic_enabled", self.virtual_mic_enabled.get())))', gui_source)

    def test_audio_source_quick_toggles_switch_capture_sources(self):
        import realtime_audio_translator.gui as gui_module

        self.assertTrue(DEFAULT_CONFIG["speaker_enabled"])
        self.assertTrue(DEFAULT_CONFIG["microphone_enabled"])
        self.assertFalse(gui_module.toggle_source_enabled(True))
        self.assertTrue(gui_module.toggle_source_enabled(False))

        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('("切換喇叭", self._toggle_speaker)', gui_source)
        self.assertIn('("切換麥克風", self._toggle_microphone)', gui_source)

    def test_mic_test_button_reports_input_level(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("測試麥克風", self._test_mic)', gui_source)
        self.assertIn('def _test_mic(self) -> None:', gui_source)
        self.assertIn('self.status.set(f"麥克風音量 {level:.4f}")', gui_source)
        self.assertIn('config["last_mic_quiet"] = level < float(self.vars["speech_threshold"].get())', gui_source)

    def test_speaker_test_button_uses_loopback_capture(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("測試喇叭", self._test_speaker)', gui_source)
        self.assertIn('def _test_speaker(self) -> None:', gui_source)
        self.assertIn('capture_wav(path, device, 0.5, loopback=True)', gui_source)
        self.assertIn('self.status.set("喇叭已偵測到聲音" if active else "喇叭目前沒有偵測到聲音")', gui_source)
        self.assertIn('config["last_speaker_quiet"] = not active', gui_source)

    def test_tts_test_button_uses_configured_output(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("測試 TTS", self._test_tts)', gui_source)
        self.assertIn('("測試虛擬麥克風", self._test_virtual_mic)', gui_source)
        self.assertIn('def _test_tts(self) -> None:', gui_source)
        self.assertIn('def _test_virtual_mic(self) -> None:', gui_source)
        self.assertIn('config["last_virtual_mic_failed"] = not active', gui_source)
        self.assertIn('config["last_virtual_mic_failed"] = True', gui_source)
        self.assertIn('provider = config.get("tts_provider", "local")', gui_source)
        self.assertIn('tts.speak_local("翻譯語音輸出測試", device)', gui_source)
        self.assertIn('audio = tts.synthesize_openai_linear16("翻譯語音輸出測試")', gui_source)
        self.assertIn('audio = tts.synthesize_google_linear16("翻譯語音輸出測試", config["target_language"])', gui_source)
        self.assertIn('cable_output = find_device("CABLE Output", want_output=False)', gui_source)
        self.assertIn('target=capture_wav, args=(path, cable_output, 2.0)', gui_source)
        self.assertIn('active = audio_segment_active(path, float(config["speech_threshold"]))', gui_source)

    def test_setup_guide_button_shows_first_run_steps(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("設定精靈", self._show_setup_guide)', gui_source)
        self.assertIn('def _show_setup_guide(self) -> None:', gui_source)
        self.assertIn("一鍵診斷", gui_source)
        self.assertIn("手動匯入 runtime", gui_source)
        self.assertIn("下載模型", gui_source)
        self.assertIn("套用場景", gui_source)
        self.assertIn("自動優化", gui_source)
        self.assertIn("喇叭來源", gui_source)
        self.assertIn("麥克風來源", gui_source)
        self.assertIn("TTS 輸出", gui_source)
        self.assertIn("CABLE Output", gui_source)
        self.assertIn("選場景會自動套用", gui_source)
        self.assertIn("自動優化", gui_source)
        self.assertIn("測試麥克風", gui_source)
        self.assertIn("測試虛擬麥克風", gui_source)

    def test_first_run_wizard_opens_for_audio_setup_issues(self):
        issues = [DiagnosticIssue("microphone_device_missing", "warning", "找不到麥克風", "", "", "audio_settings")]
        virtual_mic = [DiagnosticIssue("virtual_mic_input_missing", "warning", "找不到 VB-CABLE 麥克風", "", "", "audio_settings")]
        info_only = [DiagnosticIssue("local_translate_url_missing", "info", "本機翻譯 URL 未設定", "", "", "local_translation")]

        self.assertTrue(first_run_wizard_needed(issues))
        self.assertTrue(first_run_wizard_needed(virtual_mic))
        self.assertFalse(first_run_wizard_needed(info_only))

    def test_first_run_setup_action_shows_guide_once_when_no_blocking_issues(self):
        info_only = [DiagnosticIssue("local_translate_url_missing", "info", "本機翻譯 URL 未設定", "", "", "local_translation")]
        blocking = [DiagnosticIssue("runtime_missing", "error", "找不到 runtime", "", "", "open_runtime")]

        self.assertEqual(first_run_setup_action(blocking, False), "diagnostics")
        self.assertEqual(first_run_setup_action(info_only, False), "guide")
        self.assertEqual(first_run_setup_action(info_only, True), "")

    def test_first_diagnostic_action_prefers_runtime_then_model_then_audio(self):
        issues = [
            DiagnosticIssue("microphone_device_missing", "warning", "找不到麥克風", "", "", "audio_settings"),
            DiagnosticIssue("model_missing", "error", "找不到模型", "", "", "download_model"),
            DiagnosticIssue("runtime_missing", "error", "找不到 runtime", "", "", "open_runtime"),
        ]

        self.assertEqual(first_diagnostic_action(issues), "open_runtime")
        self.assertEqual(first_diagnostic_action(issues[:2]), "download_model")
        self.assertEqual(first_diagnostic_action([]), "")

    def test_diagnostic_actions_keep_priority_and_skip_duplicates(self):
        issues = [
            DiagnosticIssue("custom_problem", "warning", "自訂問題", "", "", "custom_fix"),
            DiagnosticIssue("model_missing", "error", "找不到模型", "", "", "download_model"),
            DiagnosticIssue("runtime_missing", "error", "找不到 runtime", "", "", "open_runtime"),
            DiagnosticIssue("runtime_missing_2", "error", "找不到 runtime 2", "", "", "open_runtime"),
        ]

        self.assertEqual(diagnostic_actions(issues), ["open_runtime", "download_model", "custom_fix"])

    def test_setup_guide_actions_cover_first_run_flow(self):
        self.assertEqual(
            setup_guide_actions(),
            ("一鍵診斷", "套用場景", "自動優化", "測試喇叭", "測試麥克風", "測試虛擬麥克風", "測試 TTS"),
        )

    def test_push_to_talk_restores_previous_mute_state(self):
        app = TranslatorApp.__new__(TranslatorApp)

        class Engine:
            def __init__(self):
                self.muted = False
                self.calls = []

            def set_muted(self, muted):
                self.muted = muted
                self.calls.append(muted)

        app.engine = Engine()

        app._push_to_talk(True)
        app._push_to_talk(False)

        self.assertEqual(app.engine.calls, [False, False])

    def test_quit_button_stops_engine_and_closes_window(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('("離開", self._quit)', gui_source)
        self.assertIn('self.protocol("WM_DELETE_WINDOW", self._quit)', gui_source)

        app = TranslatorApp.__new__(TranslatorApp)
        calls = []

        class Engine:
            def stop(self):
                calls.append("stop")

        app.engine = Engine()
        app.destroy = lambda: calls.append("destroy")

        app._quit()

        self.assertEqual(calls, ["stop", "destroy"])

    def test_gui_exposes_scenarios_and_diagnostics(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("場景", "scenario")', gui_source)
        self.assertIn("SCENARIO_CHOICES", gui_source)
        self.assertIn("tuple(scenario_label(key) for key in SCENARIO_CHOICES)", gui_source)
        self.assertIn('config["scenario"] = scenario_key(config["scenario"])', gui_source)
        self.assertIn('("套用場景", self._apply_scenario)', gui_source)
        self.assertIn('("自動優化", self._optimize_settings)', gui_source)
        self.assertIn('("一鍵診斷", self._run_diagnostics)', gui_source)
        self.assertIn('("下載離線翻譯模型", self._download_translation_models)', gui_source)
        self.assertIn("download_translation_models", gui_source)
        self.assertIn("def _show_first_run_wizard", gui_source)
        self.assertIn("first_run_setup_action", gui_source)
        self.assertIn("self._optimize_settings()", gui_source)
        self.assertIn('name == "scenario" else self._save()', gui_source)
        self.assertIn('self.vars["setup_guide_shown"].set("True")', gui_source)
        self.assertIn("def _show_diagnostics", gui_source)
        self.assertIn("def _show_text_dialog", gui_source)
        self.assertIn("ttk.Scrollbar", gui_source)
        self.assertIn('text="關閉"', gui_source)
        self.assertIn("def _run_diagnostic_action", gui_source)
        self.assertIn("self._download_runtime()", gui_source)
        self.assertIn("collect_diagnostics", gui_source)

        self.assertIn("問題名稱", gui_source)
        self.assertIn("可能原因", gui_source)
        self.assertIn("自動檢查結果", gui_source)
        self.assertIn("建議修復步驟", gui_source)
        self.assertIn("一鍵修復按鈕", gui_source)
        self.assertIn("進階日誌", gui_source)
        self.assertIn("SEVERITY_LABELS", gui_source)
        self.assertIn("錯誤", gui_source)
        self.assertIn("app.log", gui_source)
        self.assertIn("plan_session", gui_source)
        self.assertIn('config["last_cuda_devices"] = devices', gui_source)
        self.assertIn('config["last_vram_gb"] = vram_gb', gui_source)
        self.assertIn("def _auto_optimize_before_start", gui_source)
        self.assertIn("self._auto_optimize_before_start()", gui_source)
        self.assertIn('("檢查更新", self._check_updates)', gui_source)
        self.assertIn("latest_release_tag", gui_source)

    def test_gui_can_download_and_install_runtime(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('text="一鍵安裝 runtime", command=self._download_runtime', gui_source)
        self.assertIn("download_runtime", gui_source)

    def test_auto_optimize_before_start_applies_recommended_config_only_when_enabled(self):
        app = TranslatorApp.__new__(TranslatorApp)
        app.config = {"ai_auto_optimize": True}
        config = {
            "ai_auto_optimize": True,
            "device": "cuda",
            "model": "large-v3-turbo",
            "source_language": "zh",
            "target_language": "en",
            "virtual_mic_enabled": False,
            "last_latency_seconds": "4.2",
            "performance_mode": "quality",
            "segment_seconds": 3.0,
        }
        calls = []
        app._config_from_vars = lambda: config
        app._cuda_hardware = lambda current: (1, 6)
        app._load_config_into_widgets = lambda config: calls.append(("load", config))
        app._save = lambda: calls.append(("save", None))

        app._auto_optimize_before_start()

        self.assertEqual(calls[0][0], "load")
        self.assertEqual(calls[0][1]["performance_mode"], "low_latency")
        self.assertEqual(calls[0][1]["segment_seconds"], 1.5)
        self.assertFalse(calls[0][1]["virtual_mic_enabled"])
        self.assertEqual(calls[1], ("save", None))
        calls.clear()
        app.config = {"ai_auto_optimize": False}

        app._auto_optimize_before_start()

        self.assertEqual(calls, [])

    def test_latency_seconds_value_accepts_bad_values(self):
        self.assertEqual(latency_seconds_value("4.2"), 4.2)
        self.assertIsNone(latency_seconds_value(""))

    def test_status_message_error_detection(self):
        self.assertTrue(status_message_is_error("找不到 runtime：faster-whisper-xxl.exe"))
        self.assertTrue(status_message_is_error("對方：翻譯失敗：timeout"))
        self.assertTrue(status_message_is_error("沒有可用音訊裝置"))
        self.assertFalse(status_message_is_error("執行中"))

    def test_diagnostic_action_label_shows_user_button_names(self):
        self.assertEqual(diagnostic_action_label("open_runtime"), "開啟 runtime 資料夾 / 下載 runtime")
        self.assertEqual(diagnostic_action_label("download_model"), "下載模型")
        self.assertEqual(diagnostic_action_label("open_logs"), "開啟紀錄")
        self.assertEqual(diagnostic_action_label("unknown"), "unknown")

    def test_performance_mode_controls_segment_seconds(self):
        self.assertEqual(PERFORMANCE_CHOICES, ("low_latency", "balanced", "quality", "offline_light"))
        self.assertLess(performance_segment_seconds("low_latency"), performance_segment_seconds("quality"))
        self.assertEqual(performance_segment_seconds("offline_light"), 2.5)
        self.assertEqual(performance_segment_seconds("bad"), performance_segment_seconds("balanced"))

    def test_simple_mode_hides_advanced_settings(self):
        simple = visible_setting_keys(False)
        advanced = visible_setting_keys(True)
        self.assertIn("source_language", simple)
        self.assertIn("scenario", simple)
        self.assertIn("speaker_device", simple)
        self.assertIn("microphone_device", simple)
        self.assertIn("tts_output_device", simple)
        self.assertNotIn("performance_mode", simple)
        self.assertNotIn("local_translate_url", simple)
        self.assertNotIn("model", simple)
        self.assertNotIn("speaker_tts_output_device", simple)
        self.assertNotIn("runtime_dir", simple)
        self.assertNotIn("provider", simple)
        self.assertNotIn("tts_provider", simple)
        self.assertNotIn("translation_style", simple)
        self.assertIn("performance_mode", advanced)
        self.assertIn("model", advanced)
        self.assertIn("runtime_dir", advanced)
        self.assertIn("provider", advanced)
        self.assertIn("tts_provider", advanced)
        self.assertIn("translation_style", advanced)
        self.assertNotIn("google_service_account_json", simple)
        self.assertIn("google_service_account_json", advanced)

    def test_simple_mode_hides_advanced_buttons(self):
        buttons = [
            "設定精靈",
            "重新整理",
            "套用場景",
            "自動優化",
            "下載模型",
            "一鍵診斷",
            "測試 API",
            "開啟程式資料夾",
            "測試虛擬麥克風",
            "測試喇叭",
            "測試 TTS",
            "測試麥克風",
            "測試字幕",
            "開始",
            "停止",
            "修復本機翻譯",
            "清除快取",
            "開啟紀錄",
            "清除紀錄",
            "清除本機資料",
            "按住說話",
        ]

        simple = visible_button_texts(buttons, False)
        advanced = visible_button_texts(buttons, True)

        self.assertEqual(simple, ["設定精靈", "一鍵診斷", "測試虛擬麥克風", "測試麥克風", "開始", "停止"])
        self.assertNotIn("重新整理", simple)
        self.assertNotIn("套用場景", simple)
        self.assertNotIn("自動優化", simple)
        self.assertNotIn("下載模型", simple)
        self.assertNotIn("測試 API", simple)
        self.assertNotIn("測試喇叭", simple)
        self.assertNotIn("測試 TTS", simple)
        self.assertNotIn("測試字幕", simple)
        self.assertNotIn("開啟程式資料夾", simple)
        self.assertNotIn("修復本機翻譯", simple)
        self.assertNotIn("清除快取", simple)
        self.assertNotIn("開啟紀錄", simple)
        self.assertNotIn("清除紀錄", simple)
        self.assertNotIn("清除本機資料", simple)
        self.assertNotIn("按住說話", simple)
        self.assertEqual(advanced, buttons)

    def test_gui_can_add_glossary_term(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('("新增術語", self._add_glossary_term)', gui_source)
        self.assertIn('("修正上次翻譯", self._fix_last_translation)', gui_source)
        self.assertIn("last_source_text", gui_source)
        self.assertIn("simpledialog.askstring", gui_source)
        self.assertIn("是否將這個修正加入術語表？", gui_source)
        self.assertIn("add_glossary_term", gui_source)

    def test_format_overlay_line_can_show_language(self):
        self.assertEqual(format_overlay_line("hello", "en", True), "en: hello")
        self.assertEqual(format_overlay_line("hello", "en", False), "hello")

    def test_overlay_clipboard_text_joins_visible_lines(self):
        self.assertEqual(overlay_clipboard_text("speaker", "mine"), "speaker\nmine")
        self.assertEqual(overlay_clipboard_text("", "mine"), "mine")
        self.assertEqual(overlay_clipboard_text("speaker", ""), "speaker")

    def test_overlay_opacity_value_is_bounded(self):
        self.assertEqual(overlay_opacity_value("0.7"), 0.7)
        self.assertEqual(overlay_opacity_value("bad"), 0.86)
        self.assertEqual(overlay_opacity_value("0.1"), 0.2)
        self.assertEqual(overlay_opacity_value("2"), 1.0)

    def test_overlay_font_size_value_is_bounded(self):
        self.assertEqual(overlay_font_size_value("24"), 24)
        self.assertEqual(overlay_font_size_value("bad"), 18)
        self.assertEqual(overlay_font_size_value("8"), 12)
        self.assertEqual(overlay_font_size_value("80"), 48)

    def test_overlay_hold_seconds_value_is_bounded(self):
        self.assertEqual(overlay_hold_seconds_value("5"), 5.0)
        self.assertEqual(overlay_hold_seconds_value("bad"), 8.0)
        self.assertEqual(overlay_hold_seconds_value("0"), 1.0)
        self.assertEqual(overlay_hold_seconds_value("99"), 60.0)

    def test_overlay_visibility_action(self):
        self.assertEqual(overlay_visibility_action(True), "show")
        self.assertEqual(overlay_visibility_action(False), "hide")

    def test_subtitle_updates_allowed_respects_pause(self):
        self.assertTrue(subtitle_updates_allowed(False))
        self.assertFalse(subtitle_updates_allowed(True))

    def test_swap_language_values(self):
        self.assertEqual(swap_language_values("zh", "en"), ("en", "zh"))
        self.assertEqual(swap_language_values("auto", "zh"), ("auto", "zh"))

        app = TranslatorApp.__new__(TranslatorApp)
        source = Mock()
        source.get.return_value = "auto"
        app.vars = {"source_language": source, "target_language": Mock()}
        app.status = Mock()
        app._save = Mock()

        app._swap_languages()

        app.status.set.assert_called_once_with("自動偵測來源語言時無法交換；請先選擇固定來源語言")
        app._save.assert_not_called()

    def test_safe_target_language_rejects_auto(self):
        self.assertEqual(safe_target_language("ja", "zh"), "ja")
        self.assertEqual(safe_target_language("auto", "zh"), "zh")

    def test_language_lock_uses_last_detected_language_only_from_auto(self):
        self.assertEqual(language_lock_value("auto", "en"), "en")
        self.assertEqual(language_lock_value("auto", "en", "en"), "auto")
        self.assertEqual(language_lock_value("zh", "en"), "zh")
        self.assertEqual(language_lock_value("auto", ""), "auto")
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('("鎖定語言", self._lock_language)', gui_source)

    def test_language_choices_cover_mvp_languages(self):
        self.assertEqual(LANGUAGE_CHOICES, ("auto", "zh", "en", "ja", "ko"))
        self.assertEqual(TARGET_LANGUAGE_CHOICES, ("zh", "en", "ja", "ko"))
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('values = LANGUAGE_CHOICES if key == "source_language" else TARGET_LANGUAGE_CHOICES', gui_source)

    def test_troubleshooting_actions_cover_common_setup_issues(self):
        self.assertEqual(troubleshooting_action("speaker_audio"), ("open", "ms-settings:sound"))
        self.assertEqual(troubleshooting_action("mic_output"), ("open", "https://vb-audio.com/Cable/"))
        self.assertEqual(troubleshooting_action("subtitles"), ("overlay", "show"))
        self.assertEqual(troubleshooting_action("local_translation"), ("open", "https://github.com/LibreTranslate/LibreTranslate"))

    def test_provider_choices_are_fixed(self):
        self.assertEqual(PROVIDER_CHOICES, ("local", "google", "openai"))
        self.assertEqual(TTS_PROVIDER_CHOICES, ("local", "google", "openai"))

    def test_mode_notice_discloses_cloud_api_cost_risk(self):
        import realtime_audio_translator.gui as gui_module

        self.assertTrue(gui_module.cloud_activation_requires_confirmation("local", "local", "google", "local"))
        self.assertTrue(gui_module.cloud_activation_requires_confirmation("local", "google", "local", "openai"))
        self.assertFalse(gui_module.cloud_activation_requires_confirmation("local", "local", "local", "local"))
        self.assertFalse(gui_module.cloud_activation_requires_confirmation("google", "local", "local", "local"))

        cloud_notice = mode_notice("google", "openai")
        self.assertIn("目前模式：雲端 API 模式", cloud_notice)
        self.assertIn("目前供應商：Google, OpenAI", cloud_notice)
        self.assertIn("可能傳送到第三方服務", cloud_notice)
        self.assertIn("可能依 API 供應商產生費用", cloud_notice)

        local_notice = mode_notice("local", "local", False, "")
        self.assertIn("目前模式：本機免費模式", local_notice)
        self.assertIn("語音是否上傳：否", local_notice)
        self.assertIn("是否可能產生 API 費用：否", local_notice)
        self.assertIn("對話紀錄：關閉", local_notice)
        self.assertIn("本機翻譯 URL 未設定", local_notice)
        self.assertIn("對話紀錄：開啟", mode_notice("local", "local", True))
        self.assertTrue(record_logs_requires_confirmation(False, True))
        self.assertFalse(record_logs_requires_confirmation(True, True))
        self.assertFalse(record_logs_requires_confirmation(False, False))

        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")
        self.assertIn("messagebox.askyesno", gui_source)
        self.assertIn("cloud_activation_requires_confirmation", gui_source)
        self.assertIn('config["cloud_api_enabled"] = cloud_enabled', gui_source)
        self.assertIn("record_logs_requires_confirmation", gui_source)
        self.assertIn("啟用對話紀錄？", gui_source)

    def test_main_status_summary_shows_required_main_screen_state(self):
        config = DEFAULT_CONFIG.copy()
        config["scenario"] = "discord_chat"
        config["source_language"] = "en"
        config["target_language"] = "zh"
        config["speaker_device"] = "Speakers"
        config["microphone_device"] = "Microphone"
        config["tts_output_device"] = "CABLE Input"
        config["overlay_visible"] = True
        config["tts_enabled"] = True
        config["virtual_mic_enabled"] = False
        config["last_latency_seconds"] = "1.75"
        config["last_error"] = "找不到模型"

        summary = main_status_summary(config)

        for text in ("目前場景：Discord 聊天", "輸入音源：Speakers / Microphone", "輸出音源：CABLE Input", "來源語言：en", "目標語言：zh", "字幕：開啟", "TTS：開啟", "虛擬麥克風：關閉", "延遲：1.75s", "錯誤提示：找不到模型"):
            self.assertIn(text, summary)

    def test_readme_and_release_notes_mention_cloud_api_confirmation(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("Google 或 OpenAI", text)
            self.assertIn("可能產生費用", text)


if __name__ == "__main__":
    unittest.main()
