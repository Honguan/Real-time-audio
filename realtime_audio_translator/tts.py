import os
import subprocess
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


def play_linear16(audio: bytes, device_name: str = "CABLE Input", samplerate: int = 24000) -> None:
    import numpy as np
    import sounddevice as sd

    device = find_device(device_name, want_output=True)
    data = np.frombuffer(audio, dtype="int16")
    sd.play(data, samplerate=samplerate, device=device, blocking=True)


def speak_windows_sapi(text: str, device_name: str = "CABLE Input", rate: int = 0, volume: int = 100, voice_name: str = "") -> None:
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
$device = $env:RAT_TTS_DEVICE
if ($device) {
    foreach ($output in $voice.GetAudioOutputs()) {
        if ($output.GetDescription() -like "*$device*") {
            $voice.AudioOutput = $output
            break
        }
    }
}
[void]$voice.Speak($env:RAT_TTS_TEXT)
"""
    env = os.environ.copy()
    env["RAT_TTS_TEXT"] = text
    env["RAT_TTS_DEVICE"] = device_name
    env["RAT_TTS_RATE"] = str(max(-10, min(10, int(rate))))
    env["RAT_TTS_VOLUME"] = str(max(0, min(100, int(volume))))
    env["RAT_TTS_VOICE"] = voice_name
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
        env=env,
        creationflags=creationflags,
    )
