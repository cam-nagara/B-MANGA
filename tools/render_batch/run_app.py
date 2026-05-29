"""連続実行アプリの起動エントリ。

    <Blender同梱python> run_app.py

（B-Name-Render パネルの「連続実行アプリを開く…」ボタン、または
 連続実行アプリ.vbs のダブルクリックから起動される）

ローカルWebサーバを起動し、ブラウザ(Edge/Chrome のアプリ窓)でUIを開く。
Blender 同梱 Python でも動くよう、標準ライブラリのみで構成している。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server import main  # noqa: E402

if __name__ == "__main__":
    main()
