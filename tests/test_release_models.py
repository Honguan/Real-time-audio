import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import requests
from realtime_audio_translator.models import MODEL_INVALID, MODEL_MARKER, ModelDownloadCancelled, cuda_hardware_from_check_output, download_model, list_models, model_available, model_install_message, model_status, models_dir, recommend_model, verify_model_integrity
from tests.helpers import write_model


def git_blob_digest(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


class DownloadResponse:
    def __init__(self, data=b"", status_code=200, json_data=None, fail=False):
        self.data = data
        self.status_code = status_code
        self.json_data = json_data
        self.fail = fail

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self.json_data

    def iter_content(self, chunk_size):
        if self.fail:
            split = max(1, len(self.data) // 2)
            yield self.data[:split]
            raise requests.ConnectionError("interrupted")
        yield self.data


class DownloadSession:
    def __init__(self, fail_once=""):
        self.files = {
            "config.json": json.dumps({"model_type": "whisper"}).encode(),
            "model.bin": b"model-weights",
            "tokenizer.json": json.dumps({"version": "1.0"}).encode(),
            "vocabulary.txt": b"token",
        }
        self.fail_once = fail_once
        self.ranges = []

    def get(self, url, params=None, headers=None, stream=False, timeout=None):
        if "/api/models/" in url:
            siblings = []
            for name, data in self.files.items():
                entry = {"rfilename": name, "size": len(data), "blobId": git_blob_digest(data)}
                if name == "model.bin":
                    entry["lfs"] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                siblings.append(entry)
            return DownloadResponse(json_data={"id": "Systran/faster-whisper-medium", "sha": "a" * 40, "siblings": siblings})
        name = url.rsplit("/", 1)[-1]
        data = self.files[name]
        start = int((headers or {}).get("Range", "bytes=0-")[6:-1] or 0)
        if start:
            self.ranges.append((name, start))
        fail = name == self.fail_once
        if fail:
            self.fail_once = ""
        return DownloadResponse(data[start:], 206 if start else 200, fail=fail)


class ReleaseModelsTests(unittest.TestCase):
    def test_model_recommendation_prefers_turbo_on_small_cuda_vram(self):
        self.assertEqual(recommend_model(cuda_devices=1, vram_gb=4, prefer_quality=False), "large-v3-turbo")
        self.assertEqual(recommend_model(cuda_devices=0, vram_gb=0, prefer_quality=False), "medium")

    def test_cuda_check_output_reports_devices_and_vram(self):
        devices, vram_gb = cuda_hardware_from_check_output("CUDA device 0: RTX 3060, total memory: 6144 MB")

        self.assertEqual(devices, 1)
        self.assertEqual(vram_gb, 6)

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
            models = root / "models"
            progress = []

            installed = download_model("medium", models, progress.append, session=DownloadSession())

            self.assertTrue((installed / MODEL_MARKER).is_file())
            marker = json.loads((installed / MODEL_MARKER).read_text(encoding="utf-8"))
            self.assertEqual((marker["model"], marker["revision"]), ("medium", "a" * 40))
            self.assertTrue(verify_model_integrity(installed, verify_hashes=True))
            self.assertTrue(any("100.0%" in message and "MB/s" in message for message in progress))
            self.assertFalse((models / "probe.wav").exists())
            self.assertTrue(model_available("medium", root / "missing", models))
            (installed / "model.bin").write_bytes(b"")
            self.assertFalse(model_available("medium", root / "missing", models))
            self.assertFalse(verify_model_integrity(installed, verify_hashes=True))

    def test_model_integrity_rejects_manifest_without_required_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp)
            (model / MODEL_MARKER).write_text(json.dumps({"version": 2, "files": []}), encoding="utf-8")

            self.assertFalse(verify_model_integrity(model, verify_hashes=True))

    def test_interrupted_download_resumes_partial_and_installs_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp) / "models"
            session = DownloadSession(fail_once="model.bin")

            with self.assertRaises(requests.ConnectionError):
                download_model("medium", models, session=session)

            self.assertFalse((models / "faster-whisper-medium").exists())
            staging = models / ".faster-whisper-medium.partial"
            self.assertTrue((staging / "model.bin.partial").exists())
            (staging / "obsolete.partial").write_bytes(b"old revision")
            installed = download_model("medium", models, session=session)

            self.assertTrue(installed.is_dir())
            self.assertTrue(any(name == "model.bin" and offset > 0 for name, offset in session.ranges))
            self.assertFalse(staging.exists())
            self.assertFalse((installed / "obsolete.partial").exists())

    def test_model_download_checks_space_and_supports_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp) / "models"
            with patch("realtime_audio_translator.models.shutil.disk_usage", return_value=type("Usage", (), {"free": 0})()):
                with self.assertRaisesRegex(RuntimeError, "空間不足"):
                    download_model("medium", models, session=DownloadSession())

            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(ModelDownloadCancelled):
                download_model("medium", models, cancel_event=cancel, session=DownloadSession())
            self.assertFalse((models / "faster-whisper-medium").exists())

    def test_model_download_ui_exposes_progress_cancellation_and_no_probe_audio(self):
        models_source = Path("realtime_audio_translator/models.py").read_text(encoding="utf-8")
        gui_source = Path("realtime_audio_translator/gui.py").read_text(encoding="utf-8")

        self.assertNotIn("probe.wav", models_source)
        self.assertNotIn("model_download_command", models_source)
        self.assertIn("MB/s", models_source)
        self.assertIn('(\"取消模型下載\", self._cancel_model_download)', gui_source)
        self.assertIn("已下載部分可供稍後續傳", gui_source)

    def test_model_install_message_shows_model_folder(self):
        message = model_install_message("medium", Path(r"C:\Users\me\.realtime-audio\models"))

        self.assertIn("medium", message)
        self.assertIn(r"C:\Users\me\.realtime-audio\models", message)
        self.assertIn("下載模型", message)

    def test_start_checks_model_before_engine(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('status = runtime_status(runtime_dir(self.config), self.config.get("device", "auto"), self.config.get("compute_type", "auto"), verify_hashes=True)', gui_source)
        self.assertIn('if not status["ready"]:', gui_source)
        self.assertIn('append_app_log(APP_DIR, "runtime_missing", missing=status["missing"])', gui_source)
        self.assertIn('messagebox.showerror("runtime 不可用", error + "\\n\\n" + runtime_install_message(runtime_dir(self.config), self.config.get("device", "auto")))', gui_source)
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
        self.assertIn("ref: ${{ inputs.version || github.ref }}", workflow)
        self.assertIn("python-version: \"3.10.11\"", workflow)
        self.assertIn("--require-hashes -r requirements-release.txt", workflow)
        self.assertIn("--no-deps --no-build-isolation .", workflow)
        self.assertIn(".\\scripts\\test.ps1", workflow)
        self.assertNotIn("releases?per_page=20", workflow)
        self.assertNotIn("argospm-index/main", workflow)
        self.assertIn("Get-Content release-lock.json", workflow)
        self.assertIn("Get-FileHash", workflow)
        self.assertIn("cublas64_12.dll", workflow)
        self.assertIn("cublasLt64_12.dll", workflow)
        self.assertIn("cudnn64_9.dll", workflow)
        self.assertGreaterEqual(workflow.count("$LASTEXITCODE -ne 0"), 2)
        self.assertIn('Test-Path "runtime-download\\.complete"', workflow)
        self.assertNotIn("-Filter *.dll", workflow)
        self.assertIn("& ./scripts/build.ps1 -SkipInstall", workflow)
        self.assertIn("& ./scripts/package_app_zip.ps1 -Version $version -SkipBuild", workflow)
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
        self.assertIn("dist-release/RELEASE_MANIFEST.json", workflow)
        self.assertIn("dist-release/SHA256SUMS.txt", workflow)

    def test_release_inputs_are_versioned_and_hash_locked(self):
        lock = json.loads(Path("release-lock.json").read_text(encoding="utf-8"))
        requirements = Path(lock["python"]["requirements"])
        workflows = "\n".join(path.read_text(encoding="utf-8") for path in Path(".github/workflows").glob("*.yml"))

        self.assertEqual(lock["python"]["version"], "3.10.11")
        self.assertEqual(lock["python"]["index_url"], "https://pypi.org/simple")
        self.assertEqual(hashlib.sha256(requirements.read_bytes()).hexdigest(), lock["python"]["requirements_sha256"])
        self.assertEqual(len(lock["runtime"]), 2)
        self.assertEqual(len(lock["translation_models"]["packages"]), 6)
        self.assertRegex(lock["translation_models"]["index_revision"], r"^[0-9a-f]{40}$")
        for action in lock["github_actions"]:
            self.assertRegex(action["commit"], r"^[0-9a-f]{40}$")
            self.assertIn(f'{action["name"]}@{action["commit"]}', workflows)
        for asset in lock["runtime"] + lock["translation_models"]["packages"]:
            self.assertGreater(asset["size"], 0)
            self.assertRegex(asset["sha256"], r"^[0-9a-f]{64}$")
            self.assertNotIn("latest", asset["url"])
        self.assertIn('".json"', Path("scripts/make_checksums.ps1").read_text(encoding="utf-8"))

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
        self.assertIn('python-version: ["3.10", "3.13"]', ci)
        self.assertIn("python-version: ${{ matrix.python-version }}", ci)
        self.assertIn("python -m pip install -e .", ci)
        self.assertIn("cache-dependency-path: pyproject.toml", ci)
        self.assertIn(".\\scripts\\test.ps1", ci)
        self.assertIn("& $Python -m unittest discover -s tests", script)
        self.assertIn("& $Python -m compileall -q realtime_audio_translator tests", script)
        self.assertIn("$LASTEXITCODE", script)

    def test_standard_package_metadata_defines_supported_python_and_gui_entry_point(self):
        metadata = Path("pyproject.toml").read_text(encoding="utf-8")
        build = Path("scripts/build.ps1").read_text(encoding="utf-8")

        self.assertIn('requires-python = ">=3.10,<3.14"', metadata)
        self.assertIn('[project.gui-scripts]', metadata)
        self.assertIn('realtime-audio-translator = "realtime_audio_translator.gui:main"', metadata)
        self.assertIn('python -m pip install -e ".[build]"', build)
        self.assertNotIn("py -3.10", build)
        self.assertFalse(Path("requirements.txt").exists())

    def test_release_workflow_packages_basic_offline_languages(self):
        lock = json.loads(Path("release-lock.json").read_text(encoding="utf-8"))
        pairs = {(item["from"], item["to"]) for item in lock["translation_models"]["packages"]}

        self.assertEqual(pairs, {("zh", "en"), ("en", "zh"), ("ja", "en"), ("en", "ja"), ("ko", "en"), ("en", "ko")})

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
