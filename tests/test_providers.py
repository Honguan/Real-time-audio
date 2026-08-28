import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from realtime_audio_translator.config import DEFAULT_CONFIG, _has_reparse_point, clear_cache, clear_logs, ensure_app_dirs, ensure_glossary_file, load_config, log_files_to_clear, save_audio_devices, save_config
from realtime_audio_translator.ai_memory import add_glossary_term, cache_translation, cached_translation
from realtime_audio_translator.offline_translation import install_translation_models, normalize_translation_text, translation_model_available, translation_model_pairs, translation_models_dir
from realtime_audio_translator.providers import TextToSpeech, Translator, build_google_translate_request, build_openai_translation_request, google_access_token


class ProvidersTests(unittest.TestCase):
    def test_provider_request_builders_do_not_embed_secrets(self):
        openai = build_openai_translation_request("hello", "zh-TW", "en")
        self.assertEqual(openai["headers"]["Authorization"], "Bearer ${OPENAI_API_KEY}")
        self.assertIn("Translate", openai["json"]["input"])

        contextual = build_openai_translation_request("it", "zh-TW", "en", context=[("hello", "你好")])
        self.assertIn("Recent context", contextual["json"]["input"])
        self.assertIn("hello -> 你好", contextual["json"]["input"])

        formal = build_openai_translation_request("hello", "zh-TW", "en", style="formal")
        self.assertIn("Style: formal.", formal["json"]["input"])

        glossary = build_openai_translation_request("push", "zh-TW", "en", glossary={"push": "push lane"})
        self.assertIn("Use these glossary translations first", glossary["json"]["input"])
        self.assertIn("push -> push lane", glossary["json"]["input"])

        google = build_google_translate_request("hello", "zh-TW", "en", "project-1")
        self.assertIn("/projects/project-1:translateText", google["url"])
        self.assertEqual(google["json"]["targetLanguageCode"], "zh-TW")

    def test_local_provider_fails_without_translation_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")
            translator = Translator(config)

            with patch("realtime_audio_translator.providers.translate_offline", return_value=""), patch.object(translator, "_argos_translate", return_value=""), patch("realtime_audio_translator.providers.requests.post") as post:
                with self.assertRaisesRegex(RuntimeError, "找不到 zh→en 的本機翻譯模型"):
                    translator.translate("你好", "zh", "en")

            post.assert_not_called()

    def test_local_provider_can_use_installed_argos_without_url(self):
        class Translation:
            def translate(self, text):
                return f"本機:{text}"

        class Language:
            def __init__(self, code):
                self.code = code

            def get_translation(self, target):
                return Translation()

        package = type(sys)("argostranslate")
        module = type(sys)("argostranslate.translate")
        package.translate = module
        module.get_installed_languages = lambda: [Language("en"), Language("zh")]
        original_package = sys.modules.get("argostranslate")
        original_module = sys.modules.get("argostranslate.translate")
        sys.modules["argostranslate"] = package
        sys.modules["argostranslate.translate"] = module
        try:
            with tempfile.TemporaryDirectory() as tmp:
                config = DEFAULT_CONFIG.copy()
                config["provider"] = "local"
                config["models_path"] = str(Path(tmp) / "models")
                config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")
                translator = Translator(config)

                self.assertEqual(translator.translate("hello", "en", "zh-TW"), "本機:hello")
                self.assertEqual(translator.last_confidence, 0.8)
        finally:
            if original_package is None:
                sys.modules.pop("argostranslate", None)
            else:
                sys.modules["argostranslate"] = original_package
            if original_module is None:
                sys.modules.pop("argostranslate.translate", None)
            else:
                sys.modules["argostranslate.translate"] = original_module

    def test_local_provider_uses_project_offline_translation_models_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")
            with patch("realtime_audio_translator.providers.translate_offline", return_value="離線:hello"):
                translator = Translator(config)

                self.assertEqual(translator.translate("hello", "en", "zh"), "離線:hello")
                self.assertEqual(translator.last_confidence, 0.8)

    def test_local_translation_model_assets_import_from_configured_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = DEFAULT_CONFIG.copy()
            config["models_path"] = str(Path(tmp) / "models")
            model_file = translation_models_dir(config) / "en_zh.argosmodel"
            model_file.parent.mkdir(parents=True)
            with zipfile.ZipFile(model_file, "w") as archive:
                archive.writestr("translate-en_zh/metadata.json", '{"from_code":"en","to_code":"zh"}')

            self.assertEqual(install_translation_models(config), 1)

            self.assertTrue((translation_models_dir(config) / "packages" / "translate-en_zh" / "metadata.json").is_file())

    def test_offline_translation_uses_english_pivot_for_basic_languages(self):
        self.assertEqual(
            translation_model_pairs("zh", "ja"),
            (("zh", "en"), ("en", "ja"), ("ja", "en"), ("en", "zh")),
        )

    def test_offline_translation_normalizes_sentencepiece_markers(self):
        self.assertEqual(normalize_translation_text("▁这是实时音频翻译测试."), "这是实时音频翻译测试.")
        self.assertEqual(normalize_translation_text("This is▁an▁instant▁voice test."), "This is an instant voice test.")

    def test_offline_translation_model_available_through_english_pivot(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = DEFAULT_CONFIG.copy()
            config["models_path"] = str(Path(tmp) / "models")
            packages = translation_models_dir(config) / "packages"
            for source, target in (("zh", "en"), ("en", "ja")):
                package = packages / f"translate-{source}_{target}"
                (package / "model").mkdir(parents=True)
                (package / "sentencepiece.model").write_bytes(b"test")
                (package / "metadata.json").write_text(
                    json.dumps({"from_code": source, "to_code": target}), encoding="utf-8"
                )

            self.assertTrue(translation_model_available(config, "zh", "ja"))

    def test_local_argos_translation_persists_cache_without_url(self):
        class Translation:
            def translate(self, text):
                return f"本機:{text}"

        class Language:
            def __init__(self, code):
                self.code = code

            def get_translation(self, target):
                return Translation()

        package = type(sys)("argostranslate")
        module = type(sys)("argostranslate.translate")
        package.translate = module
        module.get_installed_languages = lambda: [Language("en"), Language("zh")]
        original_package = sys.modules.get("argostranslate")
        original_module = sys.modules.get("argostranslate.translate")
        sys.modules["argostranslate"] = package
        sys.modules["argostranslate.translate"] = module
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db = Path(tmp) / "translation_cache.db"
                config = DEFAULT_CONFIG.copy()
                config["provider"] = "local"
                config["models_path"] = str(Path(tmp) / "models")
                config["translation_cache_path"] = str(db)

                self.assertEqual(Translator(config).translate("hello", "en", "zh-TW"), "本機:hello")
                self.assertEqual(cached_translation(db, "local", "en", "zh-TW", "hello"), "本機:hello")
        finally:
            if original_package is None:
                sys.modules.pop("argostranslate", None)
            else:
                sys.modules["argostranslate"] = original_package
            if original_module is None:
                sys.modules.pop("argostranslate.translate", None)
            else:
                sys.modules["argostranslate.translate"] = original_module

    def test_local_fallback_does_not_persist_original_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "translation_cache.db"
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["translation_cache_path"] = str(db)

            translator = Translator(config)
            with patch("realtime_audio_translator.providers.translate_offline", return_value=""), patch.object(translator, "_argos_translate", return_value=""):
                with self.assertRaises(RuntimeError):
                    translator.translate("hello", "auto", "zh-TW")
            self.assertIsNone(cached_translation(db, "local", "auto", "zh-TW", "hello"))

    def test_local_provider_rejects_cached_source_text_when_backend_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "translation_cache.db"
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["translation_cache_path"] = str(db)
            cache_translation(db, "local", "zh", "en", "你好", "你好")

            for memory_cached in (False, True):
                with self.subTest(memory_cached=memory_cached):
                    translator = Translator(config)
                    if memory_cached:
                        translator.cache[("local", "zh", "en", "你好")] = "你好"
                    with patch("realtime_audio_translator.providers.translate_offline", return_value=""), patch.object(translator, "_argos_translate", return_value=""):
                        with self.assertRaises(RuntimeError):
                            translator.translate("你好", "zh", "en")

    def test_translator_does_not_set_success_confidence_without_local_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")
            translator = Translator(config)

            with patch("realtime_audio_translator.providers.translate_offline", return_value=""), patch.object(translator, "_argos_translate", return_value=""):
                with self.assertRaises(RuntimeError):
                    translator.translate("hello", "auto", "zh-TW")

        self.assertIsNone(translator.last_confidence)

    def test_local_provider_can_call_libretranslate_endpoint(self):
        import realtime_audio_translator.providers as providers_module

        calls = []

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"translatedText": "你好"}

        original_post = providers_module.requests.post
        providers_module.requests.post = lambda *args, **kwargs: calls.append((args, kwargs)) or Response()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                config = DEFAULT_CONFIG.copy()
                config["provider"] = "local"
                config["local_translate_url"] = "http://127.0.0.1:5000/translate"
                config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")
                translator = Translator(config)

                self.assertEqual(translator.translate("hello", "en", "zh-TW"), "你好")
        finally:
            providers_module.requests.post = original_post

        self.assertEqual(calls[0][0][0], "http://127.0.0.1:5000/translate")
        self.assertEqual(calls[0][1]["json"]["q"], "hello")
        self.assertEqual(calls[0][1]["json"]["source"], "en")
        self.assertEqual(calls[0][1]["json"]["target"], "zh-TW")

    def test_openai_tts_requests_pcm_audio(self):
        import os
        import realtime_audio_translator.providers as providers_module

        calls = []

        class Response:
            content = b"pcm"

            def raise_for_status(self):
                return None

        original_key = os.environ.get("OPENAI_API_KEY")
        original_post = providers_module.requests.post
        os.environ["OPENAI_API_KEY"] = "test-key"
        providers_module.requests.post = lambda *args, **kwargs: calls.append((args, kwargs)) or Response()
        try:
            audio = TextToSpeech(DEFAULT_CONFIG.copy()).synthesize_openai_linear16("hello")
        finally:
            providers_module.requests.post = original_post
            if original_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = original_key

        self.assertEqual(audio, b"pcm")
        self.assertEqual(calls[0][1]["json"]["response_format"], "pcm")

    def test_openai_tts_missing_key_uses_chinese_error(self):
        original_key = os.environ.get("OPENAI_API_KEY")
        os.environ.pop("OPENAI_API_KEY", None)
        try:
            with self.assertRaisesRegex(RuntimeError, "未設定 OPENAI_API_KEY"):
                TextToSpeech(DEFAULT_CONFIG.copy()).synthesize_openai_linear16("hello")
        finally:
            if original_key is not None:
                os.environ["OPENAI_API_KEY"] = original_key

    def test_google_tts_can_request_configured_voice(self):
        import realtime_audio_translator.providers as providers_module

        calls = []

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"audioContent": "cGNt"}

        original_post = providers_module.requests.post
        original_token = providers_module.google_access_token
        providers_module.google_access_token = lambda path: "test-token"
        providers_module.requests.post = lambda *args, **kwargs: calls.append((args, kwargs)) or Response()
        try:
            config = DEFAULT_CONFIG.copy()
            config["google_tts_voice"] = "en-US-Neural2-A"
            audio = TextToSpeech(config).synthesize_google_linear16("hello", "en-US")
        finally:
            providers_module.requests.post = original_post
            providers_module.google_access_token = original_token

        self.assertEqual(audio, b"pcm")
        self.assertEqual(calls[0][1]["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(calls[0][1]["json"]["voice"]["name"], "en-US-Neural2-A")

    def test_local_tts_uses_windows_sapi(self):
        import realtime_audio_translator.providers as providers_module

        calls = []
        original_speak = providers_module.speak_windows_sapi
        providers_module.speak_windows_sapi = lambda text, device, rate=0, volume=100, voice_name="": calls.append((text, device, rate, volume, voice_name))
        try:
            config = DEFAULT_CONFIG.copy()
            config["tts_rate"] = -2
            config["tts_volume"] = 80
            config["tts_voice_name"] = "Microsoft Jenny"
            TextToSpeech(config).speak_local("hello", "CABLE Input")
        finally:
            providers_module.speak_windows_sapi = original_speak

        self.assertEqual(calls, [("hello", "CABLE Input", -2, 80, "Microsoft Jenny")])

    def test_windows_sapi_receives_voice_name(self):
        import realtime_audio_translator.tts as tts_module

        calls = []
        original_run = tts_module.subprocess.run
        tts_module.subprocess.run = lambda *args, **kwargs: calls.append((args, kwargs))
        try:
            tts_module.speak_windows_sapi("hello", "CABLE Input", voice_name="Microsoft Jenny")
        finally:
            tts_module.subprocess.run = original_run

        self.assertEqual(calls[0][1]["env"]["RAT_TTS_VOICE"], "Microsoft Jenny")

    def test_windows_sapi_strips_hostapi_from_output_device(self):
        import realtime_audio_translator.tts as tts_module

        calls = []
        original_run = tts_module.subprocess.run
        tts_module.subprocess.run = lambda *args, **kwargs: calls.append((args, kwargs))
        try:
            tts_module.speak_windows_sapi("hello", "CABLE Input (VB-Audio Virtual Cable) [Windows WASAPI]")
        finally:
            tts_module.subprocess.run = original_run

        self.assertEqual(calls[0][1]["env"]["RAT_TTS_DEVICE"], "CABLE Input (VB-Audio Virtual Cable)")

    def test_windows_sapi_lists_voice_names(self):
        import realtime_audio_translator.tts as tts_module

        class Result:
            stdout = "Microsoft Jenny Desktop\r\n\r\nMicrosoft Haruka Desktop\r\n"

        original_run = tts_module.subprocess.run
        tts_module.subprocess.run = lambda *args, **kwargs: Result()
        try:
            voices = tts_module.list_windows_sapi_voices()
        finally:
            tts_module.subprocess.run = original_run

        self.assertEqual(voices, ["Microsoft Jenny Desktop", "Microsoft Haruka Desktop"])

    def test_google_translate_auto_source_omits_source_language(self):
        request = build_google_translate_request("hello", "zh", "auto", "project")
        self.assertNotIn("sourceLanguageCode", request["json"])
        self.assertEqual(request["json"]["targetLanguageCode"], "zh")

    def test_google_access_token_missing_json_uses_chinese_error(self):
        with self.assertRaisesRegex(RuntimeError, "未設定 Google 服務帳戶 JSON"):
            google_access_token("")
        with self.assertRaisesRegex(RuntimeError, "找不到 Google 服務帳戶 JSON"):
            google_access_token(str(Path("missing-google-service-account.json")))


if __name__ == "__main__":
    unittest.main()
