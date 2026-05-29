"""連続実行アプリの起動エントリ。

    python tools/render_batch/run_app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.gui import main  # noqa: E402

if __name__ == "__main__":
    main()
