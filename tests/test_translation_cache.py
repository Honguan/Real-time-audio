import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from realtime_audio_translator.config import DEFAULT_CONFIG, _has_reparse_point, clear_cache, clear_logs, ensure_app_dirs, ensure_glossary_file, load_config, log_files_to_clear, save_audio_devices, save_config
from realtime_audio_translator.ai_memory import add_glossary_term, cache_translation, cached_translation
from realtime_audio_translator.providers import HttpClient, TextToSpeech, Translator, build_google_translate_request, build_openai_translation_request, google_access_token


class TranslationCacheTests(unittest.TestCase):
    def test_translator_caches_repeated_requests(self):
        import os
        import realtime_audio_translator.providers as providers_module

        calls = []

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"output_text": "你好"}

        original_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "test-key"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                config = DEFAULT_CONFIG.copy()
                config["provider"] = "openai"
                config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")
                translator = Translator(config)
                translator.http.session.post = lambda *args, **kwargs: calls.append((args, kwargs)) or Response()
                self.assertEqual(translator.translate("hello", "en", "zh-TW").text, "你好")
                self.assertEqual(translator.translate("hello", "en", "zh-TW").text, "你好")
        finally:
            if original_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = original_key

        self.assertEqual(len(calls), 1)

    def test_translator_sends_short_term_context_to_openai(self):
        import os
        import realtime_audio_translator.providers as providers_module

        calls = []

        class Response:
            def __init__(self, text):
                self.text = text

            def raise_for_status(self):
                return None

            def json(self):
                return {"output_text": self.text}

        original_key = os.environ.get("OPENAI_API_KEY")
        responses = [Response("你好"), Response("它")]
        os.environ["OPENAI_API_KEY"] = "test-key"
        try:
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "openai"
            config["translation_cache_enabled"] = False
            translator = Translator(config)
            translator.http.session.post = lambda *args, **kwargs: calls.append((args, kwargs)) or responses.pop(0)
            translator.translate("hello", "en", "zh-TW")
            translator.translate("it", "en", "zh-TW")
        finally:
            if original_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = original_key

        self.assertIn("hello -> 你好", calls[1][1]["json"]["input"])

    def test_short_term_context_keeps_only_recent_items(self):
        translator = Translator(DEFAULT_CONFIG.copy())
        for index in range(6):
            translator._remember_context(f"source {index}", f"target {index}")

        self.assertEqual(translator.context, [
            ("source 2", "target 2"),
            ("source 3", "target 3"),
            ("source 4", "target 4"),
            ("source 5", "target 5"),
        ])

    def test_translator_sends_glossary_to_openai(self):
        import os
        import realtime_audio_translator.providers as providers_module

        calls = []

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"output_text": "push lane"}

        original_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "test-key"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                glossary = Path(tmp) / "glossary.json"
                glossary.write_text(json.dumps({"push": "push lane"}), encoding="utf-8")
                config = DEFAULT_CONFIG.copy()
                config["provider"] = "openai"
                config["translation_cache_enabled"] = False
                config["glossary_path"] = str(glossary)

                translator = Translator(config)
                translator.http.session.post = lambda *args, **kwargs: calls.append((args, kwargs)) or Response()
                translator.translate("push", "en", "zh-TW")
        finally:
            if original_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = original_key

        self.assertIn("push -> push lane", calls[0][1]["json"]["input"])

    def test_translation_memory_persists_cached_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "translation_cache.db"

            cache_translation(db, "openai", "en", "zh-TW", "hello", "你好")

            self.assertEqual(cached_translation(db, "openai", "en", "zh-TW", "hello"), "你好")
            self.assertIsNone(cached_translation(db, "google", "en", "zh-TW", "hello"))

    def test_translator_uses_persistent_translation_cache(self):
        import os
        import realtime_audio_translator.providers as providers_module

        calls = []

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"output_text": "你好"}

        original_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "test-key"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                config = DEFAULT_CONFIG.copy()
                config["provider"] = "openai"
                config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")
                http = HttpClient()
                http.session.post = lambda *args, **kwargs: calls.append((args, kwargs)) or Response()
                self.assertEqual(Translator(config, http=http).translate("hello", "en", "zh-TW").text, "你好")
                self.assertEqual(Translator(config, http=http).translate("hello", "en", "zh-TW").text, "你好")
        finally:
            if original_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = original_key

        self.assertEqual(len(calls), 1)

    def test_add_glossary_term_preserves_existing_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary = Path(tmp) / "glossary.json"
            glossary.write_text(json.dumps({"boss": "王"}), encoding="utf-8")

            add_glossary_term(glossary, "cooldown", "冷卻")

            self.assertEqual(json.loads(glossary.read_text(encoding="utf-8")), {"boss": "王", "cooldown": "冷卻"})

    def test_translator_applies_glossary_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary = Path(tmp) / "glossary.json"
            glossary.write_text(json.dumps({"Dragon Pit": "龍坑", "mid lane": "中路"}), encoding="utf-8")
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["models_path"] = str(Path(tmp) / "models")
            config["glossary_path"] = str(glossary)
            config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")

            with patch("realtime_audio_translator.providers.translate_offline", return_value="Push mid lane near Dragon Pit"):
                translated = Translator(config).translate("Push mid lane near Dragon Pit", "en", "zh-TW")

        self.assertEqual(translated.text, "Push 中路 near 龍坑")

    def test_translator_applies_longer_glossary_terms_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary = Path(tmp) / "glossary.json"
            glossary.write_text(json.dumps({"Dragon": "龍", "Dragon Pit": "龍坑"}), encoding="utf-8")
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["models_path"] = str(Path(tmp) / "models")
            config["glossary_path"] = str(glossary)
            config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")

            with patch("realtime_audio_translator.providers.translate_offline", return_value="Dragon Pit"):
                translated = Translator(config).translate("Dragon Pit", "en", "zh-TW")

        self.assertEqual(translated.text, "龍坑")

    def test_translator_ignores_empty_glossary_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary = Path(tmp) / "glossary.json"
            glossary.write_text(json.dumps({"": "BAD", "Dragon Pit": "龍坑"}), encoding="utf-8")
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["models_path"] = str(Path(tmp) / "models")
            config["glossary_path"] = str(glossary)
            config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")

            with patch("realtime_audio_translator.providers.translate_offline", return_value="Dragon Pit"):
                translated = Translator(config).translate("Dragon Pit", "en", "zh-TW")

        self.assertEqual(translated.text, "龍坑")

    def test_translator_applies_glossary_to_cached_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary = Path(tmp) / "glossary.json"
            glossary.write_text(json.dumps({"Dragon Pit": "龍坑"}), encoding="utf-8")
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["models_path"] = str(Path(tmp) / "models")
            config["glossary_path"] = str(glossary)
            config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")
            translator = Translator(config)

            with patch("realtime_audio_translator.providers.translate_offline", return_value="Dragon Pit"):
                self.assertEqual(translator.translate("Dragon Pit", "en", "zh-TW").text, "龍坑")
                self.assertEqual(translator.translate("Dragon Pit", "en", "zh-TW").text, "龍坑")

    def test_translator_ignores_invalid_glossary_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary = Path(tmp) / "glossary.json"
            glossary.write_text("{bad", encoding="utf-8")
            config = DEFAULT_CONFIG.copy()
            config["provider"] = "local"
            config["models_path"] = str(Path(tmp) / "models")
            config["glossary_path"] = str(glossary)
            config["translation_cache_path"] = str(Path(tmp) / "translation_cache.db")

            with patch("realtime_audio_translator.providers.translate_offline", return_value="translated"):
                translated = Translator(config).translate("Dragon Pit", "en", "zh-TW")

        self.assertEqual(translated.text, "translated")


if __name__ == "__main__":
    unittest.main()
