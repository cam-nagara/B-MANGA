"""Blender 実行ファイルの探索。"""

from __future__ import annotations

import os


def candidates() -> list[str]:
    paths = [
        r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    ]
    return paths


def find(preferred: str = "") -> str:
    """使える blender.exe を返す。preferred が実在すればそれを優先。"""
    if preferred and os.path.isfile(preferred):
        return preferred
    for path in candidates():
        if os.path.isfile(path):
            return path
    return preferred or ""
