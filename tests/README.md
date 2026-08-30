# Test layers

- `test_*.py`: default offline suite. Network, GUI, audio devices, and providers use mocks or fakes.
- `test_runtime.py`: integration checks using temporary files and subprocesses; still safe for normal CI.
- `test_smoke.py`: source-tree smoke checks.
- Real Windows audio hardware remains a manual release check. CI builds an app-only installer and smoke-tests its packaged EXE; the release workflow repeats the packaged smoke test after optional Authenticode signing.

Put reusable test data builders in `helpers.py`; keep behavior-specific fakes beside the test that uses them.
