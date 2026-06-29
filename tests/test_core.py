import json
import queue
import tempfile
import unittest
from pathlib import Path

from realtime_audio_translator.audio import device_name_from_label
from realtime_audio_translator.commands import parse_help_options
from realtime_audio_translator.config import DEFAULT_CONFIG, ensure_app_dirs, load_config, save_config
from realtime_audio_translator.engine import RealtimeEngine
from realtime_audio_translator.gui import format_overlay_line, swap_language_values
from realtime_audio_translator.logbook import ConversationLog
from realtime_audio_translator.models import recommend_model
from realtime_audio_translator.providers import build_google_translate_request, build_openai_translation_request


class CoreTests(unittest.TestCase):
    def test_config_round_trip_creates_expected_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_app_dirs(root)
            config = load_config(root)
            self.assertEqual(config["source_language"], DEFAULT_CONFIG["source_language"])
            config["target_language"] = "ja"
            save_config(root, config)
            self.assertEqual(load_config(root)["target_language"], "ja")
            self.assertTrue((root / "models").is_dir())
            self.assertTrue((root / "logs").is_dir())
            self.assertTrue((root / "cache" / "audio").is_dir())

    def test_parse_help_options_extracts_choices_and_flags(self):
        help_text = """
        --model MODEL, -m MODEL
        --task {transcribe,translate}
        --output_format [{json,lrc,txt,text,vtt,srt,tsv,all} ...]
        --checkcuda, -cc
        """
        options = parse_help_options(help_text)
        self.assertEqual(options["model"]["aliases"], ["-m"])
        self.assertEqual(options["task"]["choices"], ["transcribe", "translate"])
        self.assertIn("json", options["output_format"]["choices"])
        self.assertTrue(options["checkcuda"]["flag"])

    def test_provider_request_builders_do_not_embed_secrets(self):
        openai = build_openai_translation_request("hello", "zh-TW", "en")
        self.assertEqual(openai["headers"]["Authorization"], "Bearer ${OPENAI_API_KEY}")
        self.assertIn("Translate", openai["json"]["input"])

        google = build_google_translate_request("hello", "zh-TW", "en", "project-1")
        self.assertIn("/projects/project-1:translateText", google["url"])
        self.assertEqual(google["json"]["targetLanguageCode"], "zh-TW")

    def test_conversation_log_writes_markdown_and_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ConversationLog(Path(tmp), "session")
            log.append("speaker", "en", "zh-TW", "hello", "你好", "google")
            row = json.loads((Path(tmp) / "session.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["translated_text"], "你好")
            md = (Path(tmp) / "session.md").read_text(encoding="utf-8")
            self.assertIn("speaker", md)
            self.assertIn("你好", md)

    def test_conversation_log_can_write_latency(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ConversationLog(Path(tmp), "session")
            log.append("speaker", "en", "zh-TW", "hello", "hi", "google", latency_seconds=1.25)
            row = json.loads((Path(tmp) / "session.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["latency_seconds"], 1.25)

    def test_model_recommendation_prefers_turbo_on_small_cuda_vram(self):
        self.assertEqual(recommend_model(cuda_devices=1, vram_gb=4, prefer_quality=False), "large-v3-turbo")
        self.assertEqual(recommend_model(cuda_devices=0, vram_gb=0, prefer_quality=False), "medium")

    def test_device_label_strips_hostapi_suffix(self):
        self.assertEqual(device_name_from_label("CABLE Input (VB-Audio Virtual Cable) [Windows WASAPI]"), "CABLE Input (VB-Audio Virtual Cable)")

    def test_format_overlay_line_can_show_language(self):
        self.assertEqual(format_overlay_line("hello", "en", True), "en: hello")
        self.assertEqual(format_overlay_line("hello", "en", False), "hello")

    def test_swap_language_values(self):
        self.assertEqual(swap_language_values("zh", "en"), ("en", "zh"))

    def test_engine_reports_segment_latency(self):
        statuses = []
        config = DEFAULT_CONFIG.copy()
        config["record_logs"] = False
        engine = RealtimeEngine(Path("."), config, lambda speaker, mine: None, statuses.append)

        class Transcriber:
            def transcribe(self, wav, source_language):
                return "hello"

        class Translator:
            def translate(self, text, source_language, target_language):
                engine.running = False
                return "你好"

        class Worker:
            def __init__(self):
                self.queue = queue.Queue()
                self.queue.put(Path("clip.wav"))

        engine.running = True
        engine.transcriber = Transcriber()
        engine.translator = Translator()
        engine._process_segments("speaker", Worker())

        self.assertTrue(any(status.startswith("speaker latency ") for status in statuses))


if __name__ == "__main__":
    unittest.main()
