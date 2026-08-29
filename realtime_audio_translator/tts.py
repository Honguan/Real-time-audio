import os
import subprocess
import tempfile
import time
import wave
from pathlib import Path

from .audio import find_device


def write_linear16_wav(path: Path, audio: bytes, samplerate: int = 24000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(samplerate)
        handle.writeframes(audio)
    return path


def play_linear16(audio: bytes, device_name: str = "", samplerate: int = 24000, cancel_event=None, volume: int = 100) -> None:
    import numpy as np
    import sounddevice as sd

    if cancel_event is not None and cancel_event.is_set():
        return
    device = find_device(device_name, want_output=True)
    data = np.frombuffer(audio, dtype="int16")
    volume = max(0, min(100, int(volume)))
    if volume != 100:
        data = (data.astype("int32") * volume // 100).clip(-32768, 32767).astype("int16")
    block_frames = max(1, samplerate // 20)
    with sd.OutputStream(samplerate=samplerate, device=device, channels=1, dtype="int16") as stream:
        for offset in range(0, len(data), block_frames):
            if cancel_event is not None and cancel_event.is_set():
                stream.abort()
                return
            stream.write(data[offset:offset + block_frames])


def _play_wav(path: Path, device_name: str, cancel_event=None) -> None:
    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise RuntimeError("本機 TTS 輸出不是 16-bit PCM")
        play_linear16(handle.readframes(handle.getnframes()), device_name, handle.getframerate(), cancel_event)


def list_windows_sapi_voices() -> list[str]:
    script = r"""
$voice = New-Object -ComObject SAPI.SpVoice
foreach ($candidate in $voice.GetVoices()) {
    $candidate.GetDescription()
}
"""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        creationflags=creationflags,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def speak_windows_sapi(text: str, device_name: str = "", rate: int = 0, volume: int = 100, voice_name: str = "", cancel_event=None) -> dict[str, float]:
    script = r"""
$voice = New-Object -ComObject SAPI.SpVoice
$voice.Rate = [int]$env:RAT_TTS_RATE
$voice.Volume = [int]$env:RAT_TTS_VOLUME
$voiceName = $env:RAT_TTS_VOICE
if ($voiceName) {
    foreach ($candidate in $voice.GetVoices()) {
        if ($candidate.GetDescription() -like "*$voiceName*") {
            $voice.Voice = $candidate
            break
        }
    }
}
$wavPath = $env:RAT_TTS_WAV
if ($wavPath) {
    $stream = New-Object -ComObject SAPI.SpFileStream
    $stream.Open($wavPath, 3, $false)
    $voice.AudioOutputStream = $stream
}
[void]$voice.Speak($env:RAT_TTS_TEXT)
if ($wavPath) {
    $stream.Close()
}
"""
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = Path(tmp) / "sapi.wav"
        env = os.environ.copy()
        env["RAT_TTS_TEXT"] = text
        env["RAT_TTS_WAV"] = str(wav_path) if device_name else ""
        env["RAT_TTS_RATE"] = str(max(-10, min(10, int(rate))))
        env["RAT_TTS_VOLUME"] = str(max(0, min(100, int(volume))))
        env["RAT_TTS_VOICE"] = voice_name
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
        stage_started = time.perf_counter()
        stage_started_at = time.time()
        if cancel_event is None:
            subprocess.run(command, check=True, env=env, creationflags=creationflags)
        else:
            process = subprocess.Popen(command, env=env, creationflags=creationflags)
            while process.poll() is None:
                if cancel_event.wait(0.05):
                    process.terminate()
                    try:
                        process.wait(1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    return {}
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, command)
        if device_name:
            synthesis_completed_at = time.time()
            synthesis_seconds = time.perf_counter() - stage_started
            playback_started = time.perf_counter()
            playback_started_at = time.time()
            _play_wav(wav_path, device_name, cancel_event)
            return {
                "tts_synthesis_started_at": stage_started_at,
                "tts_synthesis_completed_at": synthesis_completed_at,
                "tts_playback_started_at": playback_started_at,
                "tts_playback_completed_at": time.time(),
                "last_tts_synthesis_seconds": synthesis_seconds,
                "last_tts_playback_seconds": time.perf_counter() - playback_started,
            }
        return {
            "tts_synthesis_started_at": stage_started_at,
            "tts_synthesis_completed_at": stage_started_at,
            "tts_playback_started_at": stage_started_at,
            "tts_playback_completed_at": time.time(),
            "last_tts_synthesis_seconds": 0.0,
            "last_tts_playback_seconds": time.perf_counter() - stage_started,
        }
