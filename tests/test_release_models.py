import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from realtime_audio_translator.models import MODEL_INVALID, MODEL_MARKER, cuda_hardware_from_check_output, download_model, list_models, model_available, model_download_command, model_install_message, model_status, models_dir, recommend_model
from tests.helpers import write_model


class ReleaseModelsTests(unittest.TestCase):
    def test_model_recommendation_prefers_turbo_on_small_cuda_vram(self):
        self.assertEqual(recommend_model(cuda_devices=1, vram_gb=4, prefer_quality=False), "large-v3-turbo")
        self.assertEqual(recommend_model(cuda_devices=0, vram_gb=0, prefer_quality=False), "medium")

    def test_cuda_check_output_reports_devices_and_vram(self):
        devices, vram_gb = cuda_hardware_from_check_output("CUDA device 0: RTX 3060, total memory: 6144 MB")

        self.assertEqual(devices, 1)
        self.assertEqual(vram_gb, 6)

    def test_model_download_command_uses_app_model_dir(self):
        command = model_download_command(Path("fw.exe"), Path("probe.wav"), "medium", Path("models"))
        self.assertEqual(command[0], "fw.exe")
        self.assertIn("--model_dir", command)
        self.assertIn("models", command)

    def test_list_models_keeps_known_download_choices(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_models = Path(tmp) / "models"
            (app_models / "faster-whisper-medium").mkdir(parents=True)
            (app_models / "whisper-small").mkdir(parents=True)

            models = list_models(Path(tmp) / "missing", app_models)

            self.assertIn("small", models)
            self.assertIn("medium", models)
            self.assertIn("large-v3-turbo", models)

    def test_model_available_accepts_downloaded_model_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_models = Path(tmp) / "models"
            write_model(app_models / "faster-whisper-medium")
            write_model(app_models / "whisper-small")
            (app_models / "whisper-empty").mkdir()

            self.assertTrue(model_available("medium", Path(tmp) / "missing", app_models))
            self.assertTrue(model_available("small", Path(tmp) / "missing", app_models))
            self.assertFalse(model_available("", Path(tmp) / "missing", app_models))
            self.assertFalse(model_available("empty", Path(tmp) / "missing", app_models))
            self.assertFalse(model_available("large-v3-turbo", Path(tmp) / "missing", app_models))

    def test_model_available_expands_environment_model_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "whisper-small"
            write_model(model)
            with patch.dict(os.environ, {"RTA_TEST_MODEL": str(model)}):
                self.assertTrue(model_available(r"%RTA_TEST_MODEL%", Path(tmp) / "missing", Path(tmp) / "models"))

    def test_model_validation_rejects_arbitrary_partial_and_nested_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_models = Path(tmp) / "models"
            for name, filename in (("empty", None), ("arbitrary", "notes.txt"), ("partial", "model.bin.partial")):
                folder = app_models / name
                folder.mkdir(parents=True)
                if filename:
                    (folder / filename).write_text("partial", encoding="utf-8")
                self.assertEqual(model_status(name, Path(tmp) / "missing", app_models), MODEL_INVALID)
            nested = app_models / "nested"
            write_model(nested / "faster-whisper-nested")
            self.assertEqual(model_status("nested", Path(tmp) / "missing", app_models), MODEL_INVALID)

    def test_successful_download_writes_installed_marker_and_detects_later_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / "faster-whisper-xxl.exe"
            exe.write_text("exe", encoding="utf-8")
            models = root / "models"

            def run(*_args, **_kwargs):
                write_model(models / "faster-whisper-medium")
                return SimpleNamespace(returncode=0)

            with patch("realtime_audio_translator.models.subprocess.run", side_effect=run):
                self.assertEqual(download_model(exe, "medium", models), 0)

            installed = models / "faster-whisper-medium"
            self.assertTrue((installed / MODEL_MARKER).is_file())
            self.assertTrue(model_available("medium", root / "missing", models))
            (installed / "model.bin").write_bytes(b"")
            self.assertFalse(model_available("medium", root / "missing", models))

    def test_model_install_message_shows_model_folder(self):
        message = model_install_message("medium", Path(r"C:\Users\me\.realtime-audio\models"))

        self.assertIn("medium", message)
        self.assertIn(r"C:\Users\me\.realtime-audio\models", message)
        self.assertIn("下載模型", message)

    def test_start_checks_model_before_engine(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn("status = runtime_status(runtime_dir(self.config), verify_hashes=True)", gui_source)
        self.assertIn('if not status["ready"]:', gui_source)
        self.assertIn('append_app_log(APP_DIR, "runtime_missing", missing=status["missing"])', gui_source)
        self.assertIn('messagebox.showerror("找不到 runtime", runtime_install_message(runtime_dir(self.config)))', gui_source)
        self.assertIn('self._set_last_error(error)', gui_source)
        self.assertIn("app_models = models_dir(self.config)", gui_source)
        self.assertIn('current_model_status = model_status(self.config["model"], self.repo_root / "_models", app_models)', gui_source)
        self.assertIn("if current_model_status != MODEL_READY:", gui_source)
        self.assertIn('messagebox.showerror(title, model_install_message(self.config["model"], app_models))', gui_source)
        self.assertIn('self._set_last_error("")', gui_source)
        self.assertIn('self._post_ui("overlay", speaker, mine, engine=engine)', gui_source)
        self.assertIn('self._post_ui("status", message, engine=engine)', gui_source)

    def test_runtime_status_uses_configured_model_folder(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('model_available(config["model"], self.repo_root / "_models", models_dir(config))', gui_source)

    def test_package_script_builds_release_zip_with_readme(self):
        script = Path("scripts/package.ps1").read_text(encoding="utf-8")
        self.assertIn("RealtimeAudioTranslator-$Version-win-x64.zip", script)
        self.assertIn("RealtimeAudioTranslator-runtime-cuda12-$Version.zip", script)
        self.assertIn("RealtimeAudioTranslator-runtime-cuda12-core-$Version.7z", script)
        self.assertIn("RealtimeAudioTranslator-runtime-cuda12-dlls-$Version.zip", script)
        self.assertIn("RuntimeCoreFormat", script)
        self.assertIn("README.md", script)
        self.assertIn("RELEASE_NOTES.md", script)

    def test_package_script_writes_sha256sums(self):
        script = Path("scripts/package.ps1").read_text(encoding="utf-8")

        self.assertIn("SHA256SUMS.txt", script)
        self.assertIn("System.Security.Cryptography.SHA256", script)
        self.assertNotIn("Get-FileHash", script)

    def test_github_release_workflow_uploads_zip_assets(self):
        workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

        self.assertIn("tags:", workflow)
        self.assertIn("v*", workflow)
        self.assertIn("workflow_dispatch", workflow)
        self.assertIn("build_runtime", workflow)
        self.assertIn("require_runtime_asset", workflow)
        self.assertIn("github.event_name == 'push' || inputs.build_runtime == 'true'", workflow)
        self.assertIn("python -m pip install -r requirements.txt", workflow)
        self.assertIn(".\\scripts\\test.ps1", workflow)
        self.assertIn("releases?per_page=20", workflow)
        self.assertNotIn("/releases/latest", workflow)
        self.assertIn("Sort-Object updated_at -Descending", workflow)
        self.assertIn("Faster-Whisper-XXL_.*_windows", workflow)
        self.assertIn("cuBLAS.and.cuDNN_CUDA12_win_v3.7z", workflow)
        self.assertIn("cublas64_12.dll", workflow)
        self.assertIn("cublasLt64_12.dll", workflow)
        self.assertIn("cudnn64_9.dll", workflow)
        self.assertNotIn("-Filter *.dll", workflow)
        self.assertIn("& ./scripts/package_app_zip.ps1 -Version $version", workflow)
        self.assertIn("& ./scripts/package_runtime_zip.ps1 -Version $version -RuntimeSource \"downloaded-runtime\" -SplitRuntime -RuntimeCoreFormat 7z", workflow)
        self.assertIn("& ./scripts/make_checksums.ps1", workflow)
        self.assertNotIn("@args", workflow)
        self.assertNotIn("@packageArgs", workflow)
        self.assertIn("softprops/action-gh-release", workflow)
        self.assertIn("gh release edit", workflow)
        self.assertIn("tag_name:", workflow)
        self.assertIn("inputs.version || github.ref_name", workflow)
        self.assertIn("dist-release/*.zip", workflow)
        self.assertIn("dist-release/*.7z", workflow)
        self.assertIn("dist-release/SHA256SUMS.txt", workflow)

    def test_ci_workflow_gates_push_and_pull_requests_with_shared_tests(self):
        ci_path = Path(".github/workflows/ci.yml")
        script_path = Path("scripts/test.ps1")

        self.assertTrue(ci_path.is_file())
        self.assertTrue(script_path.is_file())
        self.assertFalse(Path(".github/workflows/test.yml").exists())

        ci = ci_path.read_text(encoding="utf-8")
        script = script_path.read_text(encoding="utf-8")
        self.assertIn("push:", ci)
        self.assertIn("pull_request:", ci)
        self.assertGreaterEqual(ci.count("- master"), 2)
        self.assertIn('cache: "pip"', ci)
        self.assertIn(".\\scripts\\test.ps1", ci)
        self.assertIn("& $Python -m unittest discover -s tests", script)
        self.assertIn("& $Python -m compileall -q realtime_audio_translator tests", script)
        self.assertIn("$LASTEXITCODE", script)

    def test_release_workflow_packages_basic_offline_languages(self):
        workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

        for pair in ('@("zh", "en")', '@("en", "zh")', '@("ja", "en")', '@("en", "ja")', '@("ko", "en")', '@("en", "ko")'):
            self.assertIn(pair, workflow)

    def test_release_notes_include_public_download_instructions(self):
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        self.assertIn("最快使用", notes)
        self.assertIn("RealtimeAudioTranslator.exe", notes)
        self.assertIn("RealtimeAudioTranslator-runtime-cuda12-core-<tag>.7z", notes)
        self.assertIn("RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.zip", notes)
        self.assertIn("%USERPROFILE%\\.realtime-audio\\runtime\\cuda12", notes)
        self.assertIn("%USERPROFILE%\\.realtime-audio\\models", notes)
        self.assertIn("VB-CABLE", notes)
        self.assertIn("GitHub Releases", notes)
        self.assertIn("https://github.com/Purfview/whisper-standalone-win/releases", notes)
        self.assertIn("cuBLAS.and.cuDNN_CUDA12_win_v3.7z", notes)
        self.assertIn("本機翻譯 URL", notes)
        self.assertIn("兩個 runtime 壓縮檔", notes)

    def test_quick_start_doc_exists_for_app_zip(self):
        quick_start = Path("docs/README_QUICK_START_zh-TW.txt").read_text(encoding="utf-8")

        self.assertIn("RealtimeAudioTranslator.exe", quick_start)
        self.assertIn("RealtimeAudioTranslator-runtime-cuda12-core-<tag>.7z", quick_start)
        self.assertIn("RealtimeAudioTranslator-runtime-cuda12-dlls-<tag>.zip", quick_start)
        self.assertIn("%USERPROFILE%\\.realtime-audio\\runtime\\cuda12", quick_start)
        self.assertIn("%USERPROFILE%\\.realtime-audio\\models", quick_start)
        self.assertIn("場景", quick_start)
        self.assertIn("一鍵診斷", quick_start)
        self.assertIn("CABLE Output", quick_start)
        self.assertIn("CABLE Input", quick_start)
        self.assertIn("本機翻譯 URL", quick_start)
        self.assertIn("測試 TTS", quick_start)
        self.assertNotIn("TTS test", quick_start)

    def test_readme_and_release_notes_cover_required_faq(self):
        required = (
            "沒有字幕",
            "聽不到對方聲音",
            "對方聽不到翻譯語音",
            "找不到 runtime",
            "找不到模型",
            "Discord 沒有收到虛擬麥克風聲音",
            "字幕延遲太高",
            "GPU 無法使用",
        )

        for path in (Path("README.md"), Path("docs/RELEASE_NOTES.md")):
            text = path.read_text(encoding="utf-8")
            self.assertIn("兩個 runtime 壓縮檔", text)
            self.assertIn("手動匯入 runtime", text)
            self.assertIn("解壓到同一個暫存資料夾", text)
            self.assertNotIn("`Device`", text)
            self.assertIn("ASR 裝置", text)
            for item in required:
                self.assertIn(item, text)

    def test_readme_mentions_push_to_talk(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        self.assertIn("虛擬麥克風啟動時靜音", readme)
        self.assertIn("Push to talk", readme)
        self.assertIn("按住說話", readme)
        self.assertIn("只暫時取消虛擬麥克風靜音", readme)
        self.assertNotIn("hold it to unmute TTS output", readme)
        self.assertIn("虛擬麥克風啟動時靜音", notes)
        self.assertIn("Push to talk", notes)
        self.assertIn("按住說話", notes)

    def test_readme_and_release_notes_mention_virtual_mic_output_switch(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("輸出到虛擬麥克風", text)

    def test_readme_and_release_notes_explain_uncalibrated_quality_signals(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("未校準", text)
            self.assertIn("品質訊號", text)
            self.assertIn("本機/雲端", text)
            self.assertIn("費用", text)

    def test_readme_and_release_notes_mention_ai_orchestrator(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("AI 決策中樞", text)
            self.assertIn("自動優化", text)
            self.assertIn("預覽", text)
            self.assertIn("確認後", text)
            self.assertIn("可持久化建議", text)

    def test_readme_and_release_notes_mention_all_scenarios(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("字幕-only", text)
            self.assertIn("雙向翻譯", text)
            self.assertIn("客服對話", text)
            self.assertIn("自己說話翻譯", text)

    def test_readme_and_release_notes_mention_offline_light_mode(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("offline_light", text)
            self.assertIn("離線省資源", text)

    def test_readme_and_release_notes_mention_argos_offline_translate(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("Argos Translate", text)
            self.assertIn("離線模型", text)

    def test_readme_and_release_notes_mention_language_lock_hint(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("語言判斷", text)
            self.assertIn("來源語言", text)

    def test_readme_and_release_notes_mention_check_updates(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("檢查更新", text)
            self.assertIn("GitHub Releases", text)

    def test_readme_mentions_open_logs(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("開啟紀錄", readme)
        self.assertIn("app.log", readme)
        self.assertIn("開啟紀錄資料夾", readme)

    def test_readme_and_release_notes_mention_clear_local_data(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("清除快取", text)
            self.assertIn("清除紀錄", text)
            self.assertIn("清除本機資料", text)
            self.assertIn("翻譯快取", text)

    def test_readme_and_release_notes_mention_record_log_consent(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("對話紀錄預設關閉", text)
            self.assertIn("開啟前會詢問", text)
            self.assertIn("存在本機", text)
            self.assertIn("原文", text)
            self.assertIn("譯文", text)
            self.assertIn("7 天", text)
            self.assertIn("100 MB", text)
            self.assertIn("背景", text)
            self.assertIn("logs\\conversations", text)
            self.assertIn("logs\\app.log", text)

    def test_readme_mentions_open_app_folder(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("開啟程式資料夾", readme)
        self.assertIn("%USERPROFILE%\\.realtime-audio", readme)
        self.assertIn("settings.json", readme)
        self.assertIn("audio_devices.json", readme)
        self.assertIn("config\\glossary.json", readme)
        self.assertIn("models\\whisper-small", readme)
        self.assertIn("models\\translation", readme)
        self.assertIn("models\\tts", readme)

    def test_readme_and_release_notes_mention_subtitle_export(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("匯出字幕", text)
            self.assertIn("%USERPROFILE%\\.realtime-audio\\exports\\subtitles", text)

    def test_readme_and_release_notes_mention_add_glossary_term(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        notes = Path("docs/RELEASE_NOTES.md").read_text(encoding="utf-8")

        for text in (readme, notes):
            self.assertIn("新增術語", text)
            self.assertIn("修正上次翻譯", text)
            self.assertIn("術語", text)

    def test_readme_mentions_tts_test_provider(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("測試 TTS", readme)
        self.assertIn("測試虛擬麥克風", readme)
        self.assertIn("TTS 服務", readme)
        self.assertIn("OpenAI 模型", readme)
        self.assertIn("OpenAI TTS 聲音", readme)

    def test_readme_mentions_overlay_language_and_topmost(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("顯示語言", readme)
        self.assertIn("字幕最上層", readme)

    def test_readme_mentions_release_checksums(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("SHA256SUMS.txt", readme)
        self.assertIn("GitHub Releases", readme)
        self.assertIn("RealtimeAudioTranslator.exe", readme)
        self.assertIn("%USERPROFILE%\\.realtime-audio\\runtime\\cuda12", readme)
        self.assertIn("%USERPROFILE%\\.realtime-audio\\models", readme)


if __name__ == "__main__":
    unittest.main()
