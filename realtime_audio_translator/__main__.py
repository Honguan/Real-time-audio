import argparse
import sys
from pathlib import Path


def run() -> int:
    if "--smoke-test" in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--smoke-test", action="store_true")
        parser.add_argument("--smoke-output", type=Path, required=True)
        args = parser.parse_args()
        from realtime_audio_translator.smoke import run_smoke_test, write_smoke_result
        result = run_smoke_test()
        write_smoke_result(args.smoke_output, result)
        return 0 if result["status"] == "ok" else 1
    from realtime_audio_translator.gui import main
    main()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
