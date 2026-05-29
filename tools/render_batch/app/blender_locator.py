"""Blender 実行ファイルの探索。"""

from __future__ import annotations

import glob
import os


def candidates() -> list[str]:
    paths = [
        r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
    ]
    # 5.x を広めに拾う（バージョン統一前提だが将来差分に備える）。
    paths += sorted(
        glob.glob(r"C:\Program Files\Blender Foundation\Blender *\blender.exe"),
        reverse=True,
    )
    return paths


def find(preferred: str = "") -> str:
    """使える blender.exe を返す。preferred が実在すればそれを優先。"""
    if preferred and os.path.isfile(preferred):
        return preferred
    for path in candidates():
        if os.path.isfile(path):
            return path
    return preferred or ""
