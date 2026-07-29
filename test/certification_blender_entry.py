"""同期Blender検査を実行し、正常return後だけrunner sentinelを出す。"""

from __future__ import annotations

import json
import os
import runpy
import sys
import traceback
from pathlib import Path


def _emit(event: str, **details: object) -> None:
    payload = {"event": event, **details}
    print(
        "BMANGA_CERT_EVENT " + json.dumps(payload, ensure_ascii=False, sort_keys=True),
        flush=True,
    )


def main() -> None:
    target = Path(os.environ["BMANGA_CERT_TARGET"]).resolve()
    sentinel = os.environ["BMANGA_CERT_SENTINEL"]
    _emit("case_started", target=str(target))
    try:
        runpy.run_path(str(target), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        _emit("case_failed", error="SystemExit", message=str(code))
        sys.stdout.flush()
        sys.stderr.flush()
        numeric = int(code) if isinstance(code, int) and code != 0 else 1
        os._exit(numeric)
    except BaseException as exc:
        _emit("case_failed", error=type(exc).__name__, message=str(exc))
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
    _emit("case_completed", target=str(target))
    print(sentinel, flush=True)


if __name__ == "__main__":
    main()
