# Test layers

- `test_*.py`: default offline suite. Network, GUI, audio devices, and providers use mocks or fakes.
- `test_runtime.py`: integration checks using temporary files and subprocesses; still safe for normal CI.
- `test_smoke.py`: source-tree smoke checks.
- Real Windows audio hardware and packaged-app smoke checks are manual release checks and must not be added to the default CI suite.

Put reusable test data builders in `helpers.py`; keep behavior-specific fakes beside the test that uses them.
