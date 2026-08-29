# Issue #6 audio hot-path benchmark

Environment: Windows, Python 3.10.6, 2-second 48 kHz mono int16 segment, 250 iterations.

Scope: capture-complete to the 16 kHz float32 ASR input. The previous path writes WAV, reopens it for VAD, then reopens it for ASR conversion. The new path performs VAD and ASR conversion directly on PCM memory.

| Path | p50 | p95 | Disk writes per segment |
| --- | ---: | ---: | ---: |
| Previous WAV path | 11.915 ms | 16.685 ms | 1 |
| In-memory PCM path | 0.886 ms | 1.022 ms | 0 |

The one-hour regression test simulates 1,800 two-second segments and verifies the bounded queue retains only three memory segments while creating no WAV files.
