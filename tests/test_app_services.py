import queue
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from realtime_audio_translator.app_controller import AppController
from realtime_audio_translator.app_services import AudioDiagnosticsService, RuntimeService, UpdateService


class AppControllerTests(unittest.TestCase):
    def test_start_engine_rejects_duplicate_initialization(self):
        release = threading.Event()
        controller = AppController(lambda _kind, callback, value: callback(value))
        engine = Mock(running=True, stop_pending=False)
        engine.start.side_effect = lambda: release.wait(1)

        self.assertTrue(controller.start_engine(engine))
        self.assertFalse(controller.start_engine(Mock()))
        release.set()

    def test_named_task_rejects_duplicates_and_cancel_ignores_late_result(self):
        release = threading.Event()
        callbacks = queue.Queue()
        done = Mock()
        states = []
        controller = AppController(lambda _kind, callback, value: callbacks.put((callback, value)), states.append)
        try:
            self.assertTrue(controller.submit(release.wait, done, name="api"))
            callback, state = callbacks.get(timeout=1)
            callback(state)
            self.assertFalse(controller.submit(lambda: None, done, name="api"))

            started = time.perf_counter()
            self.assertEqual(controller.cancel("api"), 1)
            self.assertLess(time.perf_counter() - started, 0.2)
            self.assertTrue(controller.busy)
            self.assertEqual(controller.cancel("api"), 0)
            self.assertFalse(controller.submit(lambda: None, done, name="api"))
            callback, state = callbacks.get(timeout=1)
            callback(state)
            self.assertEqual([state.status for state in states], ["started", "cancelling"])
            release.set()
            callback, result = callbacks.get(timeout=1)
            self.assertTrue(controller.busy)
            callback(result)
            callback, state = callbacks.get(timeout=1)
            callback(state)

            done.assert_not_called()
            self.assertEqual([state.status for state in states], ["started", "cancelling", "cancelled"])
            self.assertFalse(controller.busy)
        finally:
            release.set()

    def test_busy_except_ignores_only_named_long_tasks(self):
        release = threading.Event()
        callbacks = queue.Queue()
        controller = AppController(lambda _kind, callback, value: callbacks.put((callback, value)))
        try:
            self.assertFalse(controller.busy_except(frozenset({"download"})))
            controller.submit(release.wait, Mock(), name="download")
            self.assertTrue(controller.has_task("download"))
            self.assertTrue(controller.busy)
            self.assertFalse(controller.busy_except(frozenset({"download"})))
            controller.submit(release.wait, Mock(), name="api")
            self.assertTrue(controller.busy_except(frozenset({"download"})))
            release.set()
            for _ in range(2):
                callback, result = callbacks.get(timeout=1)
                callback(result)
            self.assertFalse(controller.has_task("download"))
            self.assertFalse(controller.busy)
        finally:
            release.set()

    def test_cancel_suppresses_result_already_queued_for_ui(self):
        callbacks = queue.Queue()
        done = Mock()
        states = []
        controller = AppController(lambda _kind, callback, value: callbacks.put((callback, value)), states.append)
        self.assertTrue(controller.submit(lambda: 42, done, name="api"))
        callback, state = callbacks.get(timeout=1)
        callback(state)
        callback, result = callbacks.get(timeout=1)

        self.assertTrue(controller.busy)
        self.assertEqual(controller.cancel("api"), 1)
        self.assertTrue(controller.busy)
        self.assertEqual(controller.cancel("api"), 0)
        state_callback, state = callbacks.get(timeout=1)
        state_callback(state)
        self.assertEqual([state.status for state in states], ["started", "cancelling"])
        callback(result)
        callback, state = callbacks.get(timeout=1)
        callback(state)

        done.assert_not_called()
        self.assertEqual([state.status for state in states], ["started", "cancelling", "cancelled"])
        self.assertFalse(controller.busy)
        self.assertTrue(controller.submit(lambda: 43, done, name="api"))
        callback, state = callbacks.get(timeout=1)
        callback(state)
        callback, result = callbacks.get(timeout=1)
        callback(result)
        callback, state = callbacks.get(timeout=1)
        callback(state)
        self.assertEqual(done.call_args.args[0].value, 43)

    def test_failed_start_releases_engine_resources_before_ui_completion(self):
        for raises in (False, True):
            with self.subTest(raises=raises):
                callbacks = queue.Queue()
                controller = AppController(lambda _kind, callback, value: callbacks.put((callback, value)))
                engine = Mock(running=raises, stop_pending=False)
                failure = RuntimeError("startup failed")
                if raises:
                    engine.start.side_effect = failure
                done = Mock()

                self.assertTrue(controller.start_engine(engine, done))
                callback, result = callbacks.get(timeout=1)
                engine.stop.assert_called_once_with(report_status=False)
                callback(result)

                self.assertIsNone(controller.engine)
                self.assertFalse(controller.busy)
                self.assertIs(done.call_args.args[0].error, failure if raises else None)

    def test_capture_failure_after_start_keeps_engine_available_for_cleanup(self):
        callbacks = queue.Queue()
        controller = AppController(lambda _kind, callback, value: callbacks.put((callback, value)))
        engine = Mock(running=True, stop_pending=False)
        self.assertTrue(controller.start_engine(engine))
        callback, result = callbacks.get(timeout=1)
        engine.running = False
        callback(result)

        self.assertIs(controller.engine, engine)
        self.assertTrue(controller.stop_engine(Mock()))
        callback, result = callbacks.get(timeout=1)
        callback(result)
        engine.stop.assert_called_once_with()
        self.assertIsNone(controller.engine)

    def test_rejected_engine_start_closes_log_without_overwriting_failure_status(self):
        from realtime_audio_translator.config import DEFAULT_CONFIG
        from realtime_audio_translator.engine import RealtimeEngine

        callbacks = queue.Queue()
        controller = AppController(lambda _kind, callback, value: callbacks.put((callback, value)))
        statuses = []
        with TemporaryDirectory() as temp:
            config = {**DEFAULT_CONFIG, "record_logs": True, "log_dir": temp,
                      "source_language": "en", "target_language": "en"}
            engine = RealtimeEngine(Path(temp), config, Mock(), statuses.append)
            try:
                self.assertTrue(engine.log._thread.is_alive())
                self.assertTrue(controller.start_engine(engine))
                callback, result = callbacks.get(timeout=1)
                callback(result)

                self.assertFalse(engine.log._thread.is_alive())
                self.assertIsNone(controller.engine)
                self.assertEqual(len(statuses), 1)
                self.assertNotEqual(statuses[0], "已停止")
            finally:
                engine.stop(report_status=False)

    def test_submit_returns_typed_result_through_ui_dispatcher(self):
        posted = threading.Event()
        event = {}

        def post(kind, callback, result):
            event.update(kind=kind, callback=callback, result=result)
            posted.set()

        controller = AppController(post)
        controller.submit(lambda: 42, Mock())

        self.assertTrue(posted.wait(1))
        self.assertEqual(event["kind"], "callback")
        self.assertEqual(event["result"].value, 42)
        self.assertIsNone(event["result"].error)

    def test_stop_timeout_can_be_retried_before_queued_ui_completion(self):
        callbacks = queue.Queue()
        controller = AppController(lambda _kind, callback, value: callbacks.put((callback, value)))
        engine = Mock(stop_pending=True)
        controller.engine = engine
        self.assertTrue(controller.stop_engine(Mock()))
        first_callback, first_result = callbacks.get(timeout=1)
        self.assertIs(controller.engine, engine)
        self.assertFalse(controller.busy)
        entered = threading.Event()
        release = threading.Event()

        def finish_stop():
            entered.set()
            release.wait(1)
            engine.stop_pending = False
            return "stopped"

        engine.stop.side_effect = finish_stop
        try:
            self.assertTrue(controller.stop_engine(Mock()))
            self.assertTrue(entered.wait(1))
            first_callback(first_result)
            self.assertTrue(controller.busy)
            self.assertFalse(controller.stop_engine(Mock()))
            release.set()
            callback, result = callbacks.get(timeout=1)
            self.assertFalse(controller.busy)
            self.assertIsNone(controller.engine)
            callback(result)
        finally:
            release.set()

    def test_stop_exception_preserves_engine_for_retry(self):
        callbacks = queue.Queue()
        controller = AppController(lambda _kind, callback, value: callbacks.put((callback, value)))
        failure = OSError("disk unavailable")
        engine = Mock(stop_pending=False)
        engine.stop.side_effect = [failure, "stopped"]
        controller.engine = engine
        done = Mock()
        self.assertTrue(controller.stop_engine(done))
        callback, result = callbacks.get(timeout=1)
        callback(result)
        self.assertIs(controller.engine, engine)
        self.assertIs(done.call_args.args[0].error, failure)
        self.assertFalse(controller.busy)

        self.assertTrue(controller.stop_engine(done))
        callback, result = callbacks.get(timeout=1)
        callback(result)
        self.assertIsNone(controller.engine)

    def test_failed_start_keeps_engine_when_cleanup_times_out_or_raises(self):
        for raises in (False, True):
            with self.subTest(raises=raises):
                callbacks = queue.Queue()
                controller = AppController(lambda _kind, callback, value: callbacks.put((callback, value)))
                engine = Mock(running=False, stop_pending=not raises)
                if raises:
                    engine.stop.side_effect = OSError("cleanup failed")
                self.assertTrue(controller.start_engine(engine))
                callback, result = callbacks.get(timeout=1)
                callback(result)
                self.assertIs(controller.engine, engine)
                self.assertFalse(controller.busy)

    def test_stop_engine_does_not_block_caller(self):
        posted = threading.Event()
        release = threading.Event()
        controller = AppController(lambda *_args: posted.set())
        engine = Mock()
        engine.stop.side_effect = lambda: release.wait(1) or "stopped"
        controller.engine = engine

        started = time.perf_counter()
        controller.stop_engine(Mock())

        self.assertLess(time.perf_counter() - started, 0.2)
        release.set()
        self.assertTrue(posted.wait(1))


class RuntimeServiceTests(unittest.TestCase):
    def test_import_runtime_is_testable_without_tk(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            service = RuntimeService(root / "commands.json")
            status = {"ready": True}
            with (
                patch("realtime_audio_translator.app_services.install_runtime_from", return_value=root / "runtime") as install,
                patch("realtime_audio_translator.app_services.runtime_status", return_value=status),
                patch("realtime_audio_translator.app_services.refresh_commands") as refresh,
            ):
                result = service.import_runtime(root / "source", root / "target", "cpu", "int8")

            self.assertEqual(result.status, status)
            install.assert_called_once()
            refresh.assert_called_once()

    def test_check_reports_ffmpeg_failure(self):
        service = RuntimeService(Path("commands.json"))
        with (
            patch("realtime_audio_translator.app_services.runtime_status", return_value={"ready": True}),
            patch("realtime_audio_translator.app_services.subprocess.run", return_value=Mock(returncode=1)),
        ):
            result = service.check(Path("runtime"), "cpu", "int8")

        self.assertTrue(result.ffmpeg_failed)


class AudioDiagnosticsServiceTests(unittest.TestCase):
    def test_api_bypasses_persistent_cache_and_preserves_settings(self):
        from realtime_audio_translator.providers import Translator

        with TemporaryDirectory() as tmp:
            config = {"provider": "local", "local_translate_url": "http://localhost/translate",
                      "translation_cache_enabled": True,
                      "translation_cache_path": str(Path(tmp) / "cache.db")}
            service = AudioDiagnosticsService(Path(tmp))
            with patch.object(Translator, "_local_translate", return_value="你好"):
                Translator(config).translate("hello", "en", "zh")
            with patch.object(Translator, "_local_translate", side_effect=RuntimeError("service unavailable")):
                with self.assertRaisesRegex(RuntimeError, "service unavailable"):
                    service.test_api(config)
            with patch.object(Translator, "_local_translate", return_value="新的結果") as request:
                self.assertEqual(service.test_api(config), "新的結果")
                request.assert_called_once()
            self.assertTrue(config["translation_cache_enabled"])
            with patch.object(Translator, "_local_translate") as request:
                self.assertEqual(Translator(config).translate("hello", "en", "zh").text, "你好")
                request.assert_not_called()


    def test_virtual_microphone_propagates_capture_errors_instead_of_reading_old_wav(self):
        from realtime_audio_translator.audio import AudioSegment
        from tests.helpers import write_wav

        for after_start in (False, True):
            with self.subTest(after_start=after_start), TemporaryDirectory() as tmp:
                root = Path(tmp)
                stale = root / "virtual-mic-test.wav"
                write_wav(stale, 20000)
                previous = stale.read_bytes()
                service = AudioDiagnosticsService(root)
                error = OSError("capture disconnected")

                def frames(*args):
                    if after_start:
                        yield AudioSegment(b"\0\0" * 800, 16000)
                    raise error

                with patch("realtime_audio_translator.app_services.find_device", return_value=3), patch("realtime_audio_translator.app_services.audio_frame_stream", side_effect=frames), patch.object(service, "test_tts") as speak:
                    with self.assertRaises(OSError) as raised:
                        service.test_virtual_microphone({"virtual_mic_input_device": "cable", "speech_threshold": 0.01})
                self.assertIs(raised.exception, error)
                self.assertEqual(speak.call_count, int(after_start))
                self.assertEqual(stale.read_bytes(), previous)

    def test_virtual_microphone_uses_current_frames_and_closes_capture(self):
        from realtime_audio_translator.audio import AudioSegment

        for audible in (False, True):
            with self.subTest(audible=audible), TemporaryDirectory() as tmp:
                service = AudioDiagnosticsService(Path(tmp))
                ready, playback, closed = threading.Event(), threading.Event(), threading.Event()

                def frames(device, loopback, cancel, seconds):
                    try:
                        self.assertEqual(device, 3)
                        self.assertFalse(loopback)
                        ready.set()
                        yield AudioSegment(b"\0\0" * 800, 16000)
                        while not cancel.wait(0.001):
                            sample = 16000 if audible and playback.is_set() else 0
                            yield AudioSegment(sample.to_bytes(2, "little", signed=True) * 800, 16000)
                    finally:
                        closed.set()

                def speak(config):
                    self.assertTrue(ready.is_set())
                    playback.set()

                with patch("realtime_audio_translator.app_services.find_device", return_value=3), patch("realtime_audio_translator.app_services.audio_frame_stream", side_effect=frames), patch.object(service, "test_tts", side_effect=speak):
                    self.assertEqual(service.test_virtual_microphone({"virtual_mic_input_device": "cable", "speech_threshold": 0.01}), audible)
                self.assertTrue(closed.is_set())
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_virtual_microphone_closes_capture_when_tts_fails(self):
        from realtime_audio_translator.audio import AudioSegment

        service = AudioDiagnosticsService(Path("cache"))
        closed = threading.Event()

        def frames(device, loopback, cancel, seconds):
            try:
                yield AudioSegment(b"\0\0" * 800, 16000)
                while not cancel.wait(0.001):
                    yield AudioSegment(b"\0\0" * 800, 16000)
            finally:
                closed.set()

        with patch("realtime_audio_translator.app_services.find_device", return_value=3), patch("realtime_audio_translator.app_services.audio_frame_stream", side_effect=frames), patch.object(service, "test_tts", side_effect=RuntimeError("TTS failed")):
            with self.assertRaisesRegex(RuntimeError, "TTS failed"):
                service.test_virtual_microphone({"virtual_mic_input_device": "cable", "speech_threshold": 0.01})
        self.assertTrue(closed.is_set())

    def test_microphone_uses_shared_capture_and_reports_normalized_rms(self):
        from realtime_audio_translator.audio import AudioSegment

        service = AudioDiagnosticsService(Path("cache"))
        segment = AudioSegment((16384).to_bytes(2, "little", signed=True) * 24000, 48000)
        with patch("realtime_audio_translator.app_services.find_device", return_value=3), patch("realtime_audio_translator.app_services.capture_audio", return_value=segment) as capture:
            self.assertAlmostEqual(service.test_microphone({"microphone_device": "mic"}), 0.5)
            capture.assert_called_once_with(3, 0.5)
        with patch("realtime_audio_translator.app_services.find_device", return_value=3), patch("realtime_audio_translator.app_services.capture_audio", side_effect=OSError("input overflow")):
            with self.assertRaisesRegex(OSError, "input overflow"):
                service.test_microphone({"microphone_device": "mic"})

    def test_tone_uses_shared_pcm_output_path(self):
        service = AudioDiagnosticsService(Path("cache"))
        with patch("realtime_audio_translator.app_services.play_linear16") as play:
            service.test_tone({"tts_output_device": "cable"})
        audio, device = play.call_args.args
        self.assertEqual(device, "cable")
        self.assertEqual(len(audio), 12000)
        self.assertNotEqual(audio, b"\0" * len(audio))
        self.assertEqual(play.call_args.kwargs, {"samplerate": 24000})

    def test_diagnostics_and_speaker_test_do_not_require_tk(self):
        service = AudioDiagnosticsService(Path("cache"))
        with patch("realtime_audio_translator.app_services.collect_diagnostics", return_value=["issue"]):
            self.assertEqual(service.collect({}, Path("repo")), ["issue"])
        with (
            patch("realtime_audio_translator.app_services.find_device", return_value=3),
            patch("realtime_audio_translator.app_services.capture_wav") as capture,
            patch("realtime_audio_translator.app_services.audio_segment_active", return_value=True),
        ):
            self.assertTrue(service.test_speaker({"speaker_device": "speaker", "speech_threshold": "0.1"}))
            capture.assert_called_once_with(Path("cache") / "speaker-test.wav", 3, 0.5, loopback=True)


class UpdateServiceTests(unittest.TestCase):
    def test_check_returns_release_message(self):
        with (
            patch("realtime_audio_translator.app_services.current_version", return_value="v1.0.0"),
            patch("realtime_audio_translator.app_services.latest_release_tag", return_value="v1.1.0"),
        ):
            self.assertIn("v1.1.0", UpdateService().check(Path("app")))

    def test_controller_and_services_do_not_depend_on_tk_or_gui(self):
        root = Path(__file__).parents[1] / "realtime_audio_translator"
        source = (root / "app_controller.py").read_text(encoding="utf-8") + (root / "app_services.py").read_text(encoding="utf-8")

        self.assertNotIn("tkinter", source)
        self.assertNotIn(".gui", source)


if __name__ == "__main__":
    unittest.main()
