import json
import queue
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from realtime_audio_translator.engine import RealtimeEngine, audio_devices_overlap, direction_label, drain_queue, overlay_text_from_config, safe_target_language
from realtime_audio_translator.logbook import ConversationLog
from realtime_audio_translator.subtitle_export import export_jsonl_to_srt, export_jsonl_to_txt, srt_timestamp


class SubtitleExportTests(unittest.TestCase):
    def test_conversation_log_writes_markdown_and_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ConversationLog(Path(tmp), "session")
            log.append("speaker", "en", "zh-TW", "hello", "你好", "google")
            row = json.loads((Path(tmp) / "session.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["session_id"], "session")
            self.assertEqual(row["translated_text"], "你好")
            md = (Path(tmp) / "session.md").read_text(encoding="utf-8")
            self.assertIn("created:", md)
            self.assertIn("speaker", md)
            self.assertIn("provider: google", md)
            self.assertIn("你好", md)

    def test_conversation_log_auto_session_ids_do_not_collide_within_same_second(self):
        class Clock:
            calls = 0

            @classmethod
            def now(cls, _tz=None):
                cls.calls += 1
                return datetime(2026, 7, 1, 12, 0, 0, cls.calls)

        with tempfile.TemporaryDirectory() as tmp:
            with patch("realtime_audio_translator.logbook.datetime", Clock):
                first = ConversationLog(Path(tmp))
                second = ConversationLog(Path(tmp))

            self.assertNotEqual(first.session_id, second.session_id)
            self.assertNotEqual(first.jsonl_path, second.jsonl_path)

    def test_conversation_log_can_write_latency(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ConversationLog(Path(tmp), "session")
            log.append("speaker", "en", "zh-TW", "hello", "hi", "google", latency_seconds=1.25)
            row = json.loads((Path(tmp) / "session.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["latency_seconds"], 1.25)

    def test_conversation_log_writes_pipeline_performance_and_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ConversationLog(Path(tmp), "session")
            log.append("speaker", "en", "zh-TW", "hello", "hi", "google", performance={"last_asr_latency_seconds": 0.4}, timestamps={"capture_started_at": 10.0, "subtitle_published_at": 11.2})
            row = json.loads((Path(tmp) / "session.jsonl").read_text(encoding="utf-8").splitlines()[0])

            self.assertEqual(row["performance"]["last_asr_latency_seconds"], 0.4)
            self.assertEqual(row["timestamps"]["subtitle_published_at"], 11.2)

    def test_jsonl_log_exports_to_srt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jsonl = root / "session.jsonl"
            jsonl.write_text(
                "\n".join(
                    [
                        json.dumps({"direction": "speaker", "text": "hello", "translated_text": "你好"}, ensure_ascii=False),
                        json.dumps({"direction": "microphone", "text": "謝謝", "translated_text": "thanks"}, ensure_ascii=False),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            srt = export_jsonl_to_srt(jsonl, root / "exports" / "subtitles")
            txt = export_jsonl_to_txt(jsonl, root / "exports" / "subtitles")

            self.assertEqual(srt, root / "exports" / "subtitles" / "session.srt")
            text = srt.read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:03,000", text)
            self.assertIn("speaker: 你好", text)
            self.assertIn("00:00:03,000 --> 00:00:06,000", text)
            self.assertIn("microphone: thanks", text)
            self.assertEqual(txt, root / "exports" / "subtitles" / "session.txt")
            self.assertEqual(txt.read_text(encoding="utf-8"), "speaker: 你好\nmicrophone: thanks\n")
            self.assertEqual(srt_timestamp(3.25), "00:00:03,250")

    def test_pause_discards_stale_audio_segments(self):
        segments = queue.Queue()
        segments.put("old-1.wav")
        segments.put("old-2.wav")

        self.assertEqual(drain_queue(segments), 2)
        self.assertTrue(segments.empty())


if __name__ == "__main__":
    unittest.main()
