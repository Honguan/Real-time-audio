import json
import sys
import tempfile
import threading
import time
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from realtime_audio_translator.archive_install import write_install_manifest
from realtime_audio_translator.audio import AudioSegment, DeviceResolutionError, SegmentationPolicy, SegmentWorker, UtteranceSegmenter, _pyaudio_output_for_device, audio_segment_active, device_identity, find_device, format_device_label, segmentation_policy, virtual_mic_recaptures_tts
from realtime_audio_translator.asr import AudioTranscriber, add_runtime_dll_directory, add_xxl_data
from realtime_audio_translator.engine import RealtimeEngine, audio_devices_overlap, direction_label, drain_queue, overlay_text_from_config, safe_target_language
from tests.helpers import write_wav


class AudioTests(unittest.TestCase):
    def test_capture_wav_reports_sounddevice_input_overflow(self):
        import realtime_audio_translator.audio as audio_module

        class SoundDevice:
            ignore_errors = None

            def query_devices(self, index):
                return {"default_samplerate": 48000, "max_input_channels": 1}

            def rec(self, *args, **kwargs):
                return object()

            def wait(self, ignore_errors):
                self.ignore_errors = ignore_errors
                return type("Status", (), {"input_overflow": True})()

        sounddevice = SoundDevice()
        with tempfile.TemporaryDirectory() as tmp, patch.object(audio_module, "_sd", return_value=sounddevice):
            with self.assertRaisesRegex(audio_module.CaptureOverflowError, "input overflow"):
                audio_module.capture_wav(Path(tmp) / "clip.wav", 1, 0.1)

        self.assertEqual(audio_module.capture_error_code(audio_module.CaptureOverflowError("overflow")), "audio_overflow")
        self.assertIs(sounddevice.ignore_errors, False)

    def test_capture_audio_returns_mono_pcm_without_writing_a_file(self):
        import numpy as np
        import realtime_audio_translator.audio as audio_module

        class SoundDevice:
            def query_devices(self, index):
                return {"default_samplerate": 48000, "max_input_channels": 2}

            def rec(self, *args, **kwargs):
                return np.array([[1000, 3000], [-3000, -1000]], dtype=np.int16)

            def wait(self, ignore_errors):
                return type("Status", (), {"input_overflow": False})()

        with tempfile.TemporaryDirectory() as tmp, patch.object(audio_module, "_sd", return_value=SoundDevice()):
            segment = audio_module.capture_audio(1, 0)

            self.assertEqual(segment.sample_rate, 48000)
            self.assertEqual(np.frombuffer(segment.pcm, dtype=np.int16).tolist(), [2000, -2000])
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_sounddevice_frame_stream_keeps_one_open_stream(self):
        import numpy as np
        import realtime_audio_translator.audio as audio_module

        reads = 0
        opened = []

        class Stream:
            def __init__(self, **kwargs):
                opened.append(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, frames):
                nonlocal reads
                reads += 1
                return np.full((frames, 2), reads * 1000, dtype=np.int16), False

        sounddevice = type("SoundDevice", (), {
            "query_devices": staticmethod(lambda index: {"default_samplerate": 48000, "max_input_channels": 2}),
            "InputStream": Stream,
        })()
        cancel = threading.Event()
        with patch.object(audio_module, "_sd", return_value=sounddevice):
            stream = audio_module.audio_frame_stream(1, False, cancel, 0.05)
            first = next(stream)
            second = next(stream)
            cancel.set()
            self.assertEqual(list(stream), [])

        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0]["blocksize"], 2400)
        self.assertEqual(first.duration_seconds, 0.05)
        self.assertNotEqual(first.pcm, second.pcm)

    def test_loopback_frame_stream_reports_input_overflow(self):
        import realtime_audio_translator.audio as audio_module

        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, *_args, **_kwargs):
                raise OSError(-9981, "Input overflowed")

        class Audio:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def get_device_count(self):
                return 1

            def get_device_info_by_index(self, index):
                return {"name": "Speakers", "hostApi": 0, "maxOutputChannels": 2, "defaultSampleRate": 48000.0}

            def get_host_api_info_by_index(self, index):
                return {"name": "Windows WASAPI"}

            def get_wasapi_loopback_analogue_by_dict(self, output):
                return {"index": 2, "defaultSampleRate": 48000.0, "maxInputChannels": 2}

            def open(self, **_kwargs):
                return Stream()

        pyaudio = type("PyAudioModule", (), {"PyAudio": Audio, "paInt16": 8})()
        output = {"name": "Speakers", "hostapi": "Windows WASAPI", "output_channels": 2, "default_samplerate": 48000.0}
        with patch.dict(sys.modules, {"pyaudiowpatch": pyaudio}):
            with self.assertRaises(audio_module.CaptureOverflowError):
                next(audio_module._loopback_frame_stream(output, threading.Event(), 0.05))

    def test_segment_worker_recovers_from_temporary_capture_errors(self):
        import realtime_audio_translator.audio as audio_module

        events = []
        attempts = 0
        with tempfile.TemporaryDirectory() as tmp:
            worker = SegmentWorker(1, False, 0.01, segmentation_policy("balanced"), health_callback=events.append)

            def capture(*_args):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise OSError("device busy")
                worker._stopped = True
                return [AudioSegment(b"\0\0", 16000)]

            with patch.object(audio_module, "CAPTURE_RETRY_DELAYS", (0, 0, 0)), patch.object(audio_module, "audio_frame_stream", side_effect=capture):
                worker.run("me")

        self.assertEqual(attempts, 3)
        self.assertEqual([event.state for event in events], ["capturing", "degraded", "recovering", "degraded", "recovering", "capturing"])
        self.assertEqual(events[1].error_code, "audio_io_error")
        self.assertIsInstance(events[1].error, OSError)

    def test_segment_worker_reports_permanent_portaudio_failure_without_raising(self):
        import realtime_audio_translator.audio as audio_module

        class PortAudioError(Exception):
            pass

        events = []
        error = PortAudioError("device unavailable", -9985, (1, -1, "removed"))
        with tempfile.TemporaryDirectory() as tmp:
            worker = SegmentWorker(1, False, 0.01, segmentation_policy("balanced"), health_callback=events.append)
            with patch.object(audio_module, "CAPTURE_RETRY_DELAYS", (0, 0, 0)), patch.object(audio_module, "audio_frame_stream", side_effect=error) as capture:
                worker.run("speaker")

        self.assertEqual(capture.call_count, 4)
        self.assertTrue(worker._stopped)
        self.assertEqual(worker.health.state, "failed")
        self.assertEqual(worker.health.error_code, "portaudio_-9985")
        self.assertEqual(worker.health.attempt, 4)
        self.assertIs(worker.health.error, error)
        self.assertIsNotNone(worker.health.failure_timestamp)

    def test_segment_worker_does_not_retry_fatal_capture_error(self):
        import realtime_audio_translator.audio as audio_module

        error = RuntimeError("invalid capture configuration")
        with tempfile.TemporaryDirectory() as tmp:
            worker = SegmentWorker(1, False, 0.01, segmentation_policy("balanced"))
            with patch.object(audio_module, "audio_frame_stream", side_effect=error) as capture:
                worker.run("me")

        self.assertEqual(capture.call_count, 1)
        self.assertEqual(worker.health.state, "failed")
        self.assertEqual(worker.health.error_code, "capture_fatal")

    def test_one_hour_of_segments_keeps_only_bounded_memory_and_writes_no_wavs(self):
        import realtime_audio_translator.audio as audio_module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = SegmentationPolicy(0.05, 0.05, 0.05, 0, 0.20)
            worker = SegmentWorker(1, False, 0.01, policy)
            frame = AudioSegment((12000).to_bytes(2, "little", signed=True) * 800, 16000)

            def capture(*_args):
                yield from (frame for _ in range(72000))

            with patch.object(audio_module, "audio_frame_stream", side_effect=capture):
                worker.run("me")

            pending = []
            while not worker.queue.empty():
                pending.append(worker.queue.get_nowait())

            self.assertEqual(worker.queue.maxsize, 3)
            self.assertEqual(len(pending), 3)
            self.assertGreater(worker.dropped_segments, 10000)
            self.assertEqual(worker.max_queue_depth, 3)
            self.assertEqual(list(root.glob("*.wav")), [])

    def test_segment_worker_keeps_capture_and_enqueue_timestamps_until_dequeue(self):
        worker = SegmentWorker(1, False, 0.01, segmentation_policy("balanced"))
        captured = AudioSegment(b"\0\0", 16000)
        worker._enqueue(captured, {"capture_started_at": 10.0, "capture_completed_at": 12.0})

        self.assertEqual(worker.queue.get_nowait(), captured)
        timing = worker.take_timing(captured)
        self.assertEqual(timing["capture_started_at"], 10.0)
        self.assertGreaterEqual(timing["enqueued_at"], timing["capture_completed_at"])

    def test_segment_worker_flushes_active_utterance_at_stream_end(self):
        import realtime_audio_translator.audio as audio_module

        worker = SegmentWorker(1, False, 0.01, segmentation_policy("balanced"))
        frame = AudioSegment((12000).to_bytes(2, "little", signed=True) * 800, 16000)
        with patch.object(audio_module, "audio_frame_stream", return_value=iter([frame])):
            worker.run("me")

        self.assertEqual(worker.queue.qsize(), 1)

    def test_segment_worker_cancels_capture_without_queuing_a_file(self):
        import realtime_audio_translator.audio as audio_module

        entered = threading.Event()

        def capture(device, loopback, cancel_event, frame_seconds):
            entered.set()
            cancel_event.wait()
            raise InterruptedError

        cancel = threading.Event()
        worker = SegmentWorker(1, False, 0.01, segmentation_policy("balanced"), cancel)
        original_capture = audio_module.audio_frame_stream
        audio_module.audio_frame_stream = capture
        thread = threading.Thread(target=worker.run, args=("me",))
        try:
            thread.start()
            self.assertTrue(entered.wait(1))
            worker.stop()
            thread.join(1)
        finally:
            audio_module.audio_frame_stream = original_capture

        self.assertFalse(thread.is_alive())
        self.assertTrue(worker.queue.empty())

    def test_segment_worker_deletes_capture_cancelled_before_enqueue(self):
        import realtime_audio_translator.audio as audio_module

        with tempfile.TemporaryDirectory() as tmp:
            worker = SegmentWorker(1, False, 0.01, segmentation_policy("balanced"))

            def capture(*_args):
                worker.stop()
                return [AudioSegment(b"\0\0", 16000)]

            with patch.object(audio_module, "audio_frame_stream", side_effect=capture):
                worker.run("me")

            self.assertEqual(list(Path(tmp).glob("*.wav")), [])

    def test_segment_worker_deletes_partial_interrupted_capture(self):
        import realtime_audio_translator.audio as audio_module

        with tempfile.TemporaryDirectory() as tmp:
            worker = SegmentWorker(1, False, 0.01, segmentation_policy("balanced"))

            def capture(*_args):
                raise InterruptedError

            with patch.object(audio_module, "audio_frame_stream", side_effect=capture):
                worker.run("me")

            self.assertEqual(list(Path(tmp).glob("*.wav")), [])

    def test_segment_worker_stop_discards_pending_segments(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = SegmentWorker(1, False, 0.01, segmentation_policy("balanced"))
            worker.queue.put_nowait(AudioSegment(b"\0\0", 16000))

            worker.stop()

            self.assertTrue(worker.queue.empty())

    def test_segment_worker_drops_oldest_when_queue_is_full(self):
        worker = SegmentWorker(1, False, 0.01, segmentation_policy("balanced"))
        for index in range(worker.queue.maxsize):
            worker.queue.put_nowait(AudioSegment(index.to_bytes(2, "little"), 16000))

        worker._enqueue(AudioSegment(b"\x03\0", 16000))

        self.assertEqual(worker.queue.qsize(), worker.queue.maxsize)
        self.assertEqual(int.from_bytes(worker.queue.get_nowait().pcm, "little"), 1)
        self.assertEqual(worker.dropped_segments, 1)

    def test_segment_worker_does_not_enqueue_after_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = AudioSegment(b"\0\0", 16000)
            worker = SegmentWorker(1, False, 0.01, segmentation_policy("balanced"))
            worker._queue_lock.acquire()
            enqueue = threading.Thread(target=worker._enqueue, args=(fresh,))
            stop = threading.Thread(target=worker.stop)
            try:
                enqueue.start()
                stop.start()
                deadline = time.monotonic() + 1
                while not worker._stopped and time.monotonic() < deadline:
                    time.sleep(0.001)
            finally:
                worker._queue_lock.release()
            enqueue.join(1)
            stop.join(1)

            self.assertFalse(enqueue.is_alive())
            self.assertFalse(stop.is_alive())
            self.assertTrue(worker.queue.empty())

    @staticmethod
    def _device(index, name="USB Audio", hostapi="Windows WASAPI", samplerate=48000.0):
        return {
            "index": index,
            "name": name,
            "hostapi": hostapi,
            "input_channels": 2,
            "output_channels": 2,
            "default_samplerate": samplerate,
        }

    def test_find_device_empty_identity_uses_system_default_by_direction(self):
        import realtime_audio_translator.audio as audio_module

        class SoundDevice:
            def __init__(self):
                self.kinds = []

            def query_devices(self, *, kind):
                self.kinds.append(kind)
                return {"index": 7 if kind == "output" else 11}

        sounddevice = SoundDevice()
        with patch.object(audio_module, "_sd", return_value=sounddevice):
            self.assertEqual(find_device("", want_output=True), 7)
            self.assertEqual(find_device("", want_output=False), 11)

        self.assertEqual(sounddevice.kinds, ["output", "input"])

    def test_find_device_restores_same_name_usb_devices_by_index(self):
        devices = [self._device(7, "USB Headset"), self._device(9, "USB Headset")]
        identities = [device_identity(device) for device in devices]

        self.assertNotEqual(format_device_label(devices[0]), format_device_label(devices[1]))
        self.assertEqual(find_device(identities[0], want_output=True, devices=devices), 7)
        self.assertEqual(find_device(identities[1], want_output=True, devices=devices), 9)

    def test_find_device_restores_unique_signature_after_index_changes(self):
        identity = device_identity(self._device(7, "USB Headset"))
        devices = [self._device(8, "USB Headset", samplerate=44100.0)]

        self.assertEqual(find_device(identity, want_output=True, devices=devices), 8)

    def test_find_device_reports_ambiguous_duplicate_signature_after_index_changes(self):
        identity = device_identity(self._device(7, "USB Headset"))
        devices = [self._device(8, "USB Headset"), self._device(9, "USB Headset")]

        with self.assertRaisesRegex(DeviceResolutionError, "多個相同端點"):
            find_device(identity, want_output=True, devices=devices)

    def test_find_device_reports_missing_saved_device(self):
        identity = device_identity(self._device(7, "USB Headset"))

        with self.assertRaisesRegex(DeviceResolutionError, "找不到已儲存"):
            find_device(identity, want_output=True, devices=[self._device(8, "Other Headset")])

    def test_loopback_maps_across_audio_apis_by_unique_descriptor(self):
        class PyAudio:
            devices = [
                {"index": 20, "name": "其他輸出", "hostApi": 0, "maxOutputChannels": 2, "defaultSampleRate": 48000.0},
                {"index": 41, "name": "USB Headset", "hostApi": 0, "maxOutputChannels": 2, "defaultSampleRate": 48000.0},
            ]

            def get_device_count(self):
                return len(self.devices)

            def get_device_info_by_index(self, index):
                return self.devices[index]

            def get_host_api_info_by_index(self, _index):
                return {"name": "Windows WASAPI"}

        self.assertEqual(_pyaudio_output_for_device(PyAudio(), self._device(7, "USB Headset"))["index"], 41)

    def test_loopback_rejects_ambiguous_cross_api_mapping(self):
        class PyAudio:
            devices = [
                {"index": 20, "name": "USB Headset", "hostApi": 0, "maxOutputChannels": 2, "defaultSampleRate": 48000.0},
                {"index": 41, "name": "USB Headset", "hostApi": 0, "maxOutputChannels": 2, "defaultSampleRate": 48000.0},
            ]

            def get_device_count(self):
                return len(self.devices)

            def get_device_info_by_index(self, index):
                return self.devices[index]

            def get_host_api_info_by_index(self, _index):
                return {"name": "Windows WASAPI"}

        with self.assertRaisesRegex(DeviceResolutionError, "多個相同端點"):
            _pyaudio_output_for_device(PyAudio(), self._device(7, "USB Headset"))

    def test_audio_devices_overlap_requires_exact_identity(self):
        identity = device_identity(self._device(7, "CABLE Input"))

        self.assertTrue(audio_devices_overlap(identity, identity))
        self.assertFalse(audio_devices_overlap(identity, device_identity(self._device(8, "CABLE Input"))))

    def test_direction_label_is_user_facing(self):
        self.assertEqual(direction_label("speaker"), "喇叭")
        self.assertEqual(direction_label("me"), "麥克風")

    def test_virtual_mic_recaptures_tts_requires_exact_identity(self):
        identity = device_identity(self._device(7, "CABLE Input"))

        self.assertTrue(virtual_mic_recaptures_tts(identity, identity))
        self.assertFalse(virtual_mic_recaptures_tts(identity, device_identity(self._device(8, "CABLE Input"))))

    def test_audio_segment_active_uses_rms_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            quiet = Path(tmp) / "quiet.wav"
            loud = Path(tmp) / "loud.wav"
            write_wav(quiet, 0)
            write_wav(loud, 12000)

            self.assertFalse(audio_segment_active(quiet, 0.01))
            self.assertTrue(audio_segment_active(loud, 0.01))
            self.assertTrue(audio_segment_active(quiet, 0))

    def test_audio_segment_active_reads_pcm_without_a_file(self):
        quiet = AudioSegment((0).to_bytes(2, "little", signed=True) * 1600, 16000)
        loud = AudioSegment((12000).to_bytes(2, "little", signed=True) * 1600, 16000)

        self.assertFalse(audio_segment_active(quiet, 0.01))
        self.assertTrue(audio_segment_active(loud, 0.01))

    def test_streaming_corpus_avoids_fixed_boundary_word_loss_and_duplicates(self):
        corpus = json.loads((Path(__file__).parent / "fixtures" / "utterance_boundaries.json").read_text(encoding="utf-8"))
        policy = SegmentationPolicy(0.05, 0.10, 0.15, 0.10, 2.0)
        frame_samples = 800

        def word_errors(reference, hypothesis):
            row = list(range(len(hypothesis) + 1))
            for left_index, left in enumerate(reference, 1):
                next_row = [left_index]
                for right_index, right in enumerate(hypothesis, 1):
                    next_row.append(min(next_row[-1] + 1, row[right_index] + 1, row[right_index - 1] + (left != right)))
                row = next_row
            return row[-1]

        for case in corpus:
            fixed = case["fixed_window_frames"]
            baseline_dropped = {
                case["frames"][index]
                for index in range(fixed, len(case["frames"]), fixed)
                if case["frames"][index] and case["frames"][index] == case["frames"][index - 1]
            }
            segmenter = UtteranceSegmenter(policy, 0.01)
            utterances = []
            for index, word in enumerate(case["frames"]):
                sample = word * 2000
                frame = AudioSegment(sample.to_bytes(2, "little", signed=True) * frame_samples, 16000, {
                    "capture_started_at": index * policy.frame_seconds,
                    "capture_completed_at": (index + 1) * policy.frame_seconds,
                })
                utterance = segmenter.push(frame)
                if utterance:
                    utterances.append(utterance)
            trailing = segmenter.flush()
            if trailing:
                utterances.append(trailing)

            observed = []
            for utterance in utterances:
                utterance_words = []
                for offset in range(0, len(utterance.pcm), frame_samples * 2):
                    word = int.from_bytes(utterance.pcm[offset:offset + 2], "little", signed=True) // 2000
                    if word and (not utterance_words or utterance_words[-1] != word):
                        utterance_words.append(word)
                observed.extend(utterance_words)

            reference = case["reference_words"]
            baseline_words = [reference[word - 1] for word in case["expected_words"] if word not in baseline_dropped]
            streaming_words = [reference[word - 1] for word in observed]
            reference_counts = Counter(reference)
            streaming_counts = Counter(streaming_words)
            dropped_words = sum(max(0, count - streaming_counts[word]) for word, count in reference_counts.items())
            duplicated_words = sum(max(0, count - reference_counts[word]) for word, count in streaming_counts.items())
            baseline_word_errors = word_errors(reference, baseline_words)
            streaming_word_errors = word_errors(reference, streaming_words)

            self.assertEqual(len(case["reference_words"]), len(case["expected_words"]), case["name"])
            self.assertGreater(baseline_word_errors, streaming_word_errors, case["name"])
            self.assertEqual(observed, case["expected_words"], case["name"])
            self.assertEqual((streaming_word_errors, dropped_words, duplicated_words), (0, 0, 0), case["name"])
            self.assertLessEqual(utterances[0].duration_seconds, 3.0)

    def test_forced_utterance_split_marks_only_the_overlapped_followup(self):
        policy = SegmentationPolicy(0.05, 0.05, 0.10, 0.10, 0.20)
        segmenter = UtteranceSegmenter(policy, 0.01)
        frame = AudioSegment((12000).to_bytes(2, "little", signed=True) * 800, 16000)

        utterances = [utterance for _ in range(6) if (utterance := segmenter.push(frame))]

        self.assertEqual(len(utterances), 2)
        self.assertNotIn("overlap_seconds", utterances[0].timing)
        self.assertEqual(utterances[1].timing["overlap_seconds"], 0.10)

    def test_stream_end_does_not_emit_overlap_only_tail(self):
        policy = SegmentationPolicy(0.05, 0.05, 0.10, 0.10, 0.20)
        segmenter = UtteranceSegmenter(policy, 0.01)
        frame = AudioSegment((12000).to_bytes(2, "little", signed=True) * 800, 16000)

        emitted = [utterance for _ in range(4) if (utterance := segmenter.push(frame))]

        self.assertEqual(len(emitted), 1)
        self.assertIsNone(segmenter.flush())

    def test_stream_end_does_not_emit_overlap_with_only_new_silence(self):
        policy = SegmentationPolicy(0.05, 0.05, 0.10, 0.10, 0.20)
        segmenter = UtteranceSegmenter(policy, 0.01)
        speech = AudioSegment((12000).to_bytes(2, "little", signed=True) * 800, 16000)
        silence = AudioSegment((0).to_bytes(2, "little", signed=True) * 800, 16000)

        emitted = [utterance for _ in range(4) if (utterance := segmenter.push(speech))]
        segmenter.push(silence)

        self.assertEqual(len(emitted), 1)
        self.assertIsNone(segmenter.flush())

    def test_hangover_preroll_does_not_copy_previous_speech(self):
        policy = SegmentationPolicy(0.05, 0.20, 0.10, 0, 1.0)
        segmenter = UtteranceSegmenter(policy, 0.01)

        def frame(sample):
            return AudioSegment(sample.to_bytes(2, "little", signed=True) * 800, 16000)

        utterances = []
        for sample in (4000, 4000, 0, 0, 8000, 8000, 0, 0):
            if utterance := segmenter.push(frame(sample)):
                utterances.append(utterance)

        second_samples = {int.from_bytes(utterances[1].pcm[offset:offset + 2], "little", signed=True) for offset in range(0, len(utterances[1].pcm), 1600)}
        self.assertNotIn(4000, second_samples)
        self.assertIn(8000, second_samples)

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
            result = transcriber._transcribe_with_exe(Path("clip.wav"), "en")
            self.assertEqual((result.text, result.language), ("hello world", "en"))
        finally:
            asr_module.subprocess.run = original_run

    def test_whisper_model_records_language_probability_and_confidence(self):
        transcriber = AudioTranscriber.__new__(AudioTranscriber)
        transcriber._inference_lock = threading.Lock()

        class Model:
            def transcribe(self, *_args, **_kwargs):
                info = type("Info", (), {"language": "en", "language_probability": 0.91})()
                segments = [
                    type("Segment", (), {"text": " hello ", "avg_logprob": 0.0})(),
                    type("Segment", (), {"text": " world ", "avg_logprob": -1.0})(),
                ]
                return segments, info

        transcriber.model = Model()
        result = transcriber.transcribe(Path("clip.wav"), "auto")

        self.assertEqual(result.text, "hello world")
        self.assertEqual(result.language, "en")
        self.assertEqual(result.language_probability, 0.91)
        self.assertAlmostEqual(result.confidence, (1.0 + 0.36787944117144233) / 2)

    def test_whisper_model_receives_resampled_float32_pcm(self):
        transcriber = AudioTranscriber.__new__(AudioTranscriber)
        transcriber._inference_lock = threading.Lock()
        received = []

        class Model:
            def transcribe(self, audio, **_kwargs):
                received.append(audio)
                return [], type("Info", (), {"language": "en", "language_probability": 1.0})()

        transcriber.model = Model()
        transcriber.transcribe(AudioSegment((12000).to_bytes(2, "little", signed=True) * 4800, 48000), "auto")

        self.assertEqual(received[0].dtype.name, "float32")
        self.assertEqual(len(received[0]), 1600)

    def test_whisper_exe_cleans_temporary_pcm_wav(self):
        import realtime_audio_translator.asr as asr_module

        transcriber = AudioTranscriber.__new__(AudioTranscriber)
        transcriber.exe_path = Path("fw.exe")
        transcriber.model_name = "medium"
        transcriber.model_dir = Path("models")
        temp_roots = []

        def fake_run(command, **_kwargs):
            wav = Path(command[1])
            temp_roots.append(wav.parent)
            self.assertTrue(wav.exists())
            output_dir = Path(command[command.index("--output_dir") + 1])
            (output_dir / "segment.json").write_text('{"text":"hello"}', encoding="utf-8")
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with patch.object(asr_module.subprocess, "run", side_effect=fake_run):
            result = transcriber._transcribe_with_exe(AudioSegment(b"\0\0" * 1600, 16000), "en")

        self.assertEqual(result.text, "hello")
        self.assertFalse(temp_roots[0].exists())

    def test_shared_whisper_model_serializes_inference_and_returns_isolated_results(self):
        transcriber = AudioTranscriber.__new__(AudioTranscriber)
        transcriber._inference_lock = threading.Lock()
        active = 0
        max_active = 0

        class Model:
            def transcribe(self, path, **kwargs):
                nonlocal active, max_active
                active += 1
                max_active = max(max_active, active)
                time.sleep(0.005)
                active -= 1
                language = Path(path).stem
                return [type("Segment", (), {"text": f" {language} ", "avg_logprob": 0.0})()], type("Info", (), {"language": language, "language_probability": 0.9})()

        transcriber.model = Model()
        results = []
        threads = [threading.Thread(target=lambda language=language: results.append(transcriber.transcribe(Path(f"{language}.wav"), "auto"))) for language in ("en", "ja") * 10]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(max_active, 1)
        self.assertEqual(sorted((result.text, result.language) for result in results), sorted((language, language) for language in ("en", "ja") * 10))

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

    def test_audio_transcriber_rejects_unverified_runtime_before_loading_dlls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            (runtime / "_xxl_data").mkdir(parents=True)
            executable = runtime / "faster-whisper-xxl.exe"
            executable.write_bytes(b"working")
            (runtime / "ffmpeg.exe").write_bytes(b"ffmpeg!")
            write_install_manifest(runtime)
            executable.write_bytes(b"altered")

            with self.assertRaisesRegex(RuntimeError, "完整性驗證"):
                AudioTranscriber(root, "medium", root / "models", config={"runtime_dir": str(runtime)})

    def test_whisper_model_stores_detected_language(self):
        transcriber = AudioTranscriber.__new__(AudioTranscriber)
        transcriber._inference_lock = threading.Lock()
        transcriber.model_name = "medium"
        transcriber.model_dir = Path("models")

        class Segment:
            text = " hello "

        class Model:
            def transcribe(self, *args, **kwargs):
                return [Segment()], type("Info", (), {"language": "ja"})()

        transcriber.model = Model()

        result = transcriber.transcribe(Path("clip.wav"), "auto")
        self.assertEqual((result.text, result.language), ("hello", "ja"))

    def test_runtime_controls_link_cuda12_dependency(self):
        gui_source = (Path(__file__).parents[1] / "realtime_audio_translator" / "gui.py").read_text(encoding="utf-8")

        self.assertIn('text="一鍵安裝 runtime"', gui_source)
        self.assertIn('text="備用 runtime 來源"', gui_source)
        self.assertIn("UPSTREAM_RUNTIME_RELEASE_URL", gui_source)
        self.assertIn('subprocess.run([str(runtime_dir(config) / "ffmpeg.exe"), "-version"]', gui_source)
        self.assertIn('config["last_ffmpeg_failed"]', gui_source)
        self.assertIn("status = runtime_status(runtime, verify_hashes=True)", gui_source)
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
