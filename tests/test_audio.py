import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from realtime_audio_translator.audio import audio_segment_active, device_name_from_label, find_device, loopback_device_for_output, virtual_mic_recaptures_tts
from realtime_audio_translator.asr import AudioTranscriber, add_runtime_dll_directory, add_xxl_data
from realtime_audio_translator.engine import RealtimeEngine, audio_devices_overlap, direction_label, drain_queue, overlay_text_from_config, safe_target_language
from tests.helpers import write_wav


class AudioTests(unittest.TestCase):
    def test_device_label_strips_hostapi_suffix(self):
        self.assertEqual(device_name_from_label("CABLE Input (VB-Audio Virtual Cable) [Windows WASAPI]"), "CABLE Input (VB-Audio Virtual Cable)")

    def test_find_device_ignores_empty_label(self):
        devices = [{"index": 7, "name": "Speakers", "input_channels": 0, "output_channels": 2, "hostapi": "WASAPI"}]
        with patch("realtime_audio_translator.audio.list_audio_devices", return_value=devices):
            self.assertIsNone(find_device("", want_output=True))
            self.assertEqual(find_device("Speakers", want_output=True), 7)

    def test_loopback_device_matches_selected_output_name(self):
        loopbacks = [
            {"index": 7, "name": "Headphones [Loopback]"},
            {"index": 9, "name": "Speakers (USB Audio) [Loopback]"},
        ]

        self.assertEqual(loopback_device_for_output(loopbacks, "Speakers (USB Audio) [Windows WASAPI]")["index"], 9)
        self.assertIsNone(loopback_device_for_output(loopbacks, "Missing speakers"))

    def test_audio_devices_overlap_matches_short_and_full_names(self):
        self.assertTrue(audio_devices_overlap("CABLE Input", "CABLE Input (VB-Audio Virtual Cable) [Windows WASAPI]"))
        self.assertFalse(audio_devices_overlap("Speakers", "CABLE Input"))

    def test_direction_label_is_user_facing(self):
        self.assertEqual(direction_label("speaker"), "喇叭")
        self.assertEqual(direction_label("me"), "麥克風")

    def test_virtual_mic_recaptures_tts_matches_vb_cable_pair(self):
        self.assertTrue(virtual_mic_recaptures_tts("CABLE Output (VB-Audio Virtual Cable)", "CABLE Input (VB-Audio Virtual Cable)"))
        self.assertFalse(virtual_mic_recaptures_tts("Microphone", "CABLE Input"))

    def test_audio_segment_active_uses_rms_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            quiet = Path(tmp) / "quiet.wav"
            loud = Path(tmp) / "loud.wav"
            write_wav(quiet, 0)
            write_wav(loud, 12000)

            self.assertFalse(audio_segment_active(quiet, 0.01))
            self.assertTrue(audio_segment_active(loud, 0.01))
            self.assertTrue(audio_segment_active(quiet, 0))

    def test_whisper_auto_language_omits_language_flag(self):
        import realtime_audio_translator.asr as asr_module

        calls = []
        original_run = asr_module.subprocess.run
        asr_module.subprocess.run = lambda command, **kwargs: calls.append(command) or type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "out"
                out.mkdir()
                transcriber = AudioTranscriber.__new__(AudioTranscriber)
                transcriber.exe_path = Path("fw.exe")
                transcriber.model_name = "medium"
                transcriber.model_dir = Path("models")
                transcriber._transcribe_with_exe(out / "clip.wav", "auto")
        finally:
            asr_module.subprocess.run = original_run

        self.assertNotIn("--language", calls[0])

    def test_whisper_exe_reads_plain_text_from_json_output(self):
        import realtime_audio_translator.asr as asr_module

        original_run = asr_module.subprocess.run

        def fake_run(command, **_kwargs):
            output_dir = Path(command[command.index("--output_dir") + 1])
            (output_dir / "clip.json").write_text('{"language":"en","text":" hello world "}', encoding="utf-8")
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        asr_module.subprocess.run = fake_run
        try:
            transcriber = AudioTranscriber.__new__(AudioTranscriber)
            transcriber.exe_path = Path("fw.exe")
            transcriber.model_name = "medium"
            transcriber.model_dir = Path("models")
            self.assertEqual(transcriber._transcribe_with_exe(Path("clip.wav"), "en"), "hello world")
        finally:
            asr_module.subprocess.run = original_run

    def test_whisper_model_records_language_probability_and_confidence(self):
        transcriber = AudioTranscriber.__new__(AudioTranscriber)

        class Model:
            def transcribe(self, *_args, **_kwargs):
                info = type("Info", (), {"language": "en", "language_probability": 0.91})()
                segments = [
                    type("Segment", (), {"text": " hello ", "avg_logprob": 0.0})(),
                    type("Segment", (), {"text": " world ", "avg_logprob": -1.0})(),
                ]
                return segments, info

        transcriber.model = Model()
        text = transcriber.transcribe(Path("clip.wav"), "auto")

        self.assertEqual(text, "hello world")
        self.assertEqual(transcriber.last_language, "en")
        self.assertEqual(transcriber.last_language_probability, 0.91)
        self.assertAlmostEqual(transcriber.last_confidence, (1.0 + 0.36787944117144233) / 2)

    def test_add_xxl_data_prefers_runtime_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_data = root / "repo" / "_xxl_data"
            runtime_data = root / "runtime" / "_xxl_data"
            repo_data.mkdir(parents=True)
            runtime_data.mkdir(parents=True)
            original_path = sys.path[:]
            try:
                add_xxl_data(root / "repo", root / "runtime")
                self.assertEqual(sys.path[0], str(root / "runtime" / "_xxl_data"))
                self.assertIn(str(root / "repo" / "_xxl_data"), sys.path)
            finally:
                sys.path[:] = original_path

    def test_add_runtime_dll_directory_keeps_handle(self):
        import realtime_audio_translator.asr as asr_module

        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "runtime"
            runtime.mkdir()
            calls = []
            original_add = getattr(asr_module.os, "add_dll_directory", None)
            original_handles = asr_module.DLL_DIRECTORIES[:]
            asr_module.os.add_dll_directory = lambda path: calls.append(path) or "handle"
            try:
                asr_module.DLL_DIRECTORIES.clear()
                add_runtime_dll_directory(runtime)
                self.assertEqual(calls, [str(runtime)])
                self.assertEqual(asr_module.DLL_DIRECTORIES, ["handle"])
            finally:
                if original_add is None:
                    delattr(asr_module.os, "add_dll_directory")
                else:
                    asr_module.os.add_dll_directory = original_add
                asr_module.DLL_DIRECTORIES[:] = original_handles

    def test_whisper_model_stores_detected_language(self):
        transcriber = AudioTranscriber.__new__(AudioTranscriber)
        transcriber.model_name = "medium"
        transcriber.model_dir = Path("models")

        class Segment:
            text = " hello "

        class Model:
            def transcribe(self, *args, **kwargs):
                return [Segment()], type("Info", (), {"language": "ja"})()

        transcriber.model = Model()

        self.assertEqual(transcriber.transcribe(Path("clip.wav"), "auto"), "hello")
        self.assertEqual(transcriber.last_language, "ja")

    def test_runtime_controls_link_cuda12_dependency(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('text="一鍵安裝 runtime"', gui_source)
        self.assertIn('text="備用 runtime 來源"', gui_source)
        self.assertIn("UPSTREAM_RUNTIME_RELEASE_URL", gui_source)
        self.assertIn('subprocess.run([str(runtime_dir(config) / "ffmpeg.exe"), "-version"]', gui_source)
        self.assertIn('config["last_ffmpeg_failed"]', gui_source)
        self.assertIn("status = runtime_status(runtime)", gui_source)
        self.assertIn('if not status["ready"]:', gui_source)
        self.assertIn('messagebox.showerror("找不到 runtime", runtime_install_message(runtime))', gui_source)
        self.assertIn('self.status.set("找不到 runtime：" + ", ".join(status["missing"]))', gui_source)
        self.assertIn('subprocess.run([str(exe), "--checkcuda"], capture_output=True, text=True, timeout=5, check=False)', gui_source)
        self.assertIn("except Exception:\n            return 0, 0", gui_source)

    def test_import_runtime_refreshes_commands_json(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('messagebox.showerror("runtime 不完整", "缺少：" + ", ".join(status["missing"]))', gui_source)
        self.assertIn('if not status["ready"]:', gui_source)
        self.assertIn('refresh_commands(whisper_exe(target), APP_DIR / "commands.json")', gui_source)
        self.assertIn('self._refresh_lists()\n        self.status.set("runtime 已匯入；commands.json 已更新")', gui_source)
        self.assertIn('refresh_commands(exe, APP_DIR / "commands.json")', gui_source)
        self.assertIn('self._refresh_lists()\n        self.status.set("commands.json 已更新")', gui_source)
        self.assertIn('messagebox.showerror("commands.json 更新失敗", str(exc))', gui_source)
        self.assertIn('message = f"模型下載失敗：{exc}"', gui_source)


if __name__ == "__main__":
    unittest.main()
