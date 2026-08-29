import json
import os
import queue
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from realtime_audio_translator.engine import RealtimeEngine, audio_devices_overlap, direction_label, drain_queue, overlay_text_from_config, safe_target_language
from realtime_audio_translator.logbook import ConversationLog, conversation_log_usage, enforce_log_retention
from realtime_audio_translator.subtitle_export import export_jsonl_to_srt, export_jsonl_to_txt, srt_timestamp


class SubtitleExportTests(unittest.TestCase):
    def test_conversation_log_writes_markdown_and_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ConversationLog(Path(tmp), "session")
            log.append("speaker", "en", "zh-TW", "hello", "你好", "google")
            self.assertTrue(log.close())
            row = json.loads((Path(tmp) / "session.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["schema_version"], 2)
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
            self.assertTrue(first.close())
            self.assertTrue(second.close())

    def test_conversation_log_can_write_latency(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ConversationLog(Path(tmp), "session")
            log.append("speaker", "en", "zh-TW", "hello", "hi", "google", latency_seconds=1.25)
            self.assertTrue(log.close())
            row = json.loads((Path(tmp) / "session.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["latency_seconds"], 1.25)

    def test_conversation_log_writes_pipeline_performance_and_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ConversationLog(Path(tmp), "session")
            log.append(
                "speaker",
                "en",
                "zh-TW",
                "hello",
                "hi",
                "google",
                performance={"last_asr_latency_seconds": 0.4},
                timestamps={"capture_started_at": 10.0, "subtitle_published_at": 11.2},
                audio_start_seconds=30.0,
                audio_end_seconds=31.25,
            )
            self.assertTrue(log.close())
            row = json.loads((Path(tmp) / "session.jsonl").read_text(encoding="utf-8").splitlines()[0])

            self.assertEqual(row["performance"]["last_asr_latency_seconds"], 0.4)
            self.assertEqual(row["timestamps"]["subtitle_published_at"], 11.2)
            self.assertEqual((row["audio_start_seconds"], row["audio_end_seconds"]), (30.0, 31.25))

    def test_conversation_log_append_is_bounded_and_non_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = threading.Event()
            log = ConversationLog(Path(tmp), "session", queue_size=2)
            log._write = lambda _row: gate.wait(1)

            started = time.perf_counter()
            for index in range(100):
                log.append("speaker", "en", "zh", str(index), str(index), "local")
            elapsed = time.perf_counter() - started

            self.assertLess(elapsed, 0.2)
            self.assertGreater(log.dropped_records, 0)
            gate.set()
            self.assertTrue(log.close())

    def test_conversation_log_content_modes_limit_stored_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            for mode, expected in (("both", {"text", "translated_text"}), ("original", {"text"}), ("translation", {"translated_text"}), ("none", set())):
                log = ConversationLog(Path(tmp), mode, content_mode=mode)
                log.append("speaker", "en", "zh", "secret original", "secret translation", "local")
                self.assertTrue(log.close())
                row = json.loads(log.jsonl_path.read_text(encoding="utf-8").splitlines()[0])
                self.assertEqual({key for key in ("text", "translated_text") if key in row}, expected)
                markdown = log.md_path.read_text(encoding="utf-8")
                self.assertEqual("secret original" in markdown, mode in {"both", "original"})
                self.assertEqual("secret translation" in markdown, mode in {"both", "translation"})

    def test_conversation_log_retention_removes_old_and_oversized_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = ConversationLog(root, "old")
            old.append("speaker", "en", "zh", "hello", "你好", "local")
            self.assertTrue(old.close())
            old_time = time.time() - 10 * 86400
            for path in (old.jsonl_path, old.md_path):
                path.touch()
                os.utime(path, (old_time, old_time))

            enforce_log_retention(root, retention_days=7, max_bytes=1024 * 1024)

            self.assertFalse(old.jsonl_path.exists())
            large = ConversationLog(root, "large")
            large.append("speaker", "en", "zh", "x" * 2000, "y" * 2000, "local")
            self.assertTrue(large.close())
            self.assertGreater(conversation_log_usage(root), 100)

            enforce_log_retention(root, retention_days=0, max_bytes=100)

            self.assertEqual(conversation_log_usage(root), 0)

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

    def test_jsonl_log_exports_real_audio_ranges_and_orders_two_way_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jsonl = root / "session.jsonl"
            jsonl.write_text(
                "\n".join(
                    json.dumps(row, ensure_ascii=False)
                    for row in (
                        {"direction": "me", "translated_text": "later", "audio_start_seconds": 32.0},
                        {"direction": "speaker", "translated_text": "after silence", "audio_start_seconds": 30.0, "audio_end_seconds": 31.25},
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            text = export_jsonl_to_srt(jsonl, root).read_text(encoding="utf-8")

            self.assertLess(text.index("speaker: after silence"), text.index("me: later"))
            self.assertIn("00:00:30,000 --> 00:00:31,250", text)
            self.assertIn("00:00:32,000 --> 00:00:35,000", text)

    def test_jsonl_point_timestamps_use_next_cue_as_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jsonl = root / "session.jsonl"
            jsonl.write_text(
                json.dumps({"text": "one", "audio_start_seconds": 5.0}) + "\n"
                + json.dumps({"text": "two", "audio_start_seconds": 7.5}) + "\n",
                encoding="utf-8",
            )

            text = export_jsonl_to_srt(jsonl, root).read_text(encoding="utf-8")

            self.assertIn("00:00:05,000 --> 00:00:07,500", text)

    def test_pause_discards_stale_audio_segments(self):
        segments = queue.Queue()
        segments.put("old-1.wav")
        segments.put("old-2.wav")

        self.assertEqual(drain_queue(segments), 2)
        self.assertTrue(segments.empty())


if __name__ == "__main__":
    unittest.main()
