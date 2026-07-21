"""utils/blf_safety.py の境界値テスト (Blender 非依存).

2026-07-21 のビュー回転クラッシュ (投影発散した巨大文字サイズが
blf.size → blf.draw へ渡り EXCEPTION_ACCESS_VIOLATION) の再発防止。
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path


def _load_blf_safety():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "bmanga_blf_safety", root / "utils" / "blf_safety.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_safe_text_px_size_normal_values():
    mod = _load_blf_safety()
    assert mod.safe_text_px_size(14.0) == 14.0
    assert mod.safe_text_px_size(1) == 1.0
    assert mod.safe_text_px_size(0.5) == 0.5
    assert mod.safe_text_px_size(mod.MAX_TEXT_PX_SIZE) == mod.MAX_TEXT_PX_SIZE


def test_safe_text_px_size_rejects_divergence():
    mod = _load_blf_safety()
    # 投影発散で実際に発生し得る巨大値・非有限値はすべて skip (None)
    assert mod.safe_text_px_size(mod.MAX_TEXT_PX_SIZE + 1.0) is None
    assert mod.safe_text_px_size(1.0e6) is None
    assert mod.safe_text_px_size(float("inf")) is None
    assert mod.safe_text_px_size(float("-inf")) is None
    assert mod.safe_text_px_size(float("nan")) is None


def test_safe_text_px_size_rejects_non_positive_and_non_numeric():
    mod = _load_blf_safety()
    assert mod.safe_text_px_size(0.0) is None
    assert mod.safe_text_px_size(-5.0) is None
    assert mod.safe_text_px_size(None) is None
    assert mod.safe_text_px_size("abc") is None


def test_finite_xy():
    mod = _load_blf_safety()
    assert mod.finite_xy(0.0, 0.0) is True
    assert mod.finite_xy(-100.5, 99999.0) is True
    assert mod.finite_xy(float("inf"), 0.0) is False
    assert mod.finite_xy(0.0, float("nan")) is False
    assert mod.finite_xy(None, 0.0) is False
    assert math.isfinite(mod.MAX_TEXT_PX_SIZE)
