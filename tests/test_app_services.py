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
        engine = Mock(running=True)
        engine.start.side_effect = lambda: release.wait(1)

        self.assertTrue(controller.start_engine(engine))
        self.assertFalse(controller.start_engine(Mock()))
        release.set()

    def test_named_task_rejects_duplicates_and_cancel_ignores_late_result(self):
        release = threading.Event()
        done = Mock()
        states = []

        def post(_kind, callback, value):
            callback(value)

        controller = AppController(post, states.append)
        self.assertTrue(controller.submit(lambda: release.wait(1), done, name="api"))
        self.assertFalse(controller.submit(lambda: None, done, name="api"))

        started = time.perf_counter()
        self.assertEqual(controller.cancel("api"), 1)
        self.assertLess(time.perf_counter() - started, 0.2)
        self.assertFalse(controller.submit(lambda: None, done, name="api"))
        release.set()
        time.sleep(0.05)

        done.assert_not_called()
        self.assertEqual([state.status for state in states], ["started", "cancelled"])
        self.assertFalse(controller.busy)

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
