"""Blender実機用: 隣接ルビのはみ出し衝突が解決されることの検証.

2026-07-17 修正分: ルビ字間が既定 (0) のとき、隣接スパンのルビ同士の
重なり解決が実質無効だった:

  - 圧縮下限 (min_ext) がベタ組の延べ幅そのもので、圧縮余地が常に 0
  - eff_ls が負の字間を表現できず 0 でクランプされ、配置計算が
    圧縮前の延べ幅で再計算していた (Phase 3 の再計算問題)

修正後の仕様 (typography/ruby.py):
  - 配置計算は重なり解決後の延べ幅 (ext) をそのまま使う
  - 字間圧縮で吸収し切れない衝突は、はみ出している側のルビサイズを
    縮小して収める (下限 60%)
  - 隣に ルビ無しの文字がある場合のはみ出し (JIS で許容) は従来どおり

実行 (--factory-startup 必須):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python test\\blender_ruby_adjacent_overflow_check.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_ruby_adjacent"

FAILURES: list[str] = []


def _check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)
        print(f"NG: {message}", flush=True)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


class _Span:
    def __init__(self, start, length, ruby_text, style="group"):
        self.start = start
        self.length = length
        self.ruby_text = ruby_text
        self.style = style
        self.segments = []


def _run_check() -> None:
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        layout = sys.modules[f"{PACKAGE}.typography.layout"]
        ruby = sys.modules[f"{PACKAGE}.typography.ruby"]

        em_pt = 20.0
        em_mm = em_pt * 25.4 / 72.0

        def vertical_parents(count):
            return [
                layout.GlyphPlacement(
                    ch="親",
                    x_mm=50.0,
                    y_mm=100.0 - em_mm * (i + 1),
                    size_pt=em_pt,
                    rotation_deg=0.0,
                    index=i,
                )
                for i in range(count)
            ]

        def assert_no_overlap(placements, label):
            # ルビ文字のセル (y, y+em) が互いに重ならないこと (縦書き)
            cells = sorted(
                ((p.y_mm, p.y_mm + p.size_pt * 25.4 / 72.0) for p in placements),
                key=lambda c: c[0],
            )
            for (lo1, hi1), (lo2, _hi2) in zip(cells, cells[1:]):
                _check(
                    hi1 <= lo2 + 0.05,
                    f"{label}: ルビ文字が重なっています (上端 {lo2:.2f} < 下端 {hi1:.2f})",
                )

        # --- 1. 隣接スパン両方にルビ (東京/とうきょう + 都庁/とちょう 相当) ---
        parents = vertical_parents(4)
        spans = [_Span(0, 2, "とうきょう"), _Span(2, 2, "とちょう")]
        placements = ruby.compute_ruby_placements(
            parents, spans, ruby_size_ratio=0.5, ruby_letter_spacing=0.0,
            writing_mode="vertical",
        )
        _check(len(placements) == 9, f"ルビ文字数が9ではありません: {len(placements)}")
        assert_no_overlap(placements, "隣接スパン")

        # 縮小してもサイズ下限 (60%) を守ること
        min_size = min(p.size_pt for p in placements)
        _check(
            min_size >= em_pt * 0.5 * 0.6 - 1e-6,
            f"ルビサイズが下限60%を割っています: {min_size}",
        )

        # --- 2. 隣がルビ無しなら従来どおりはみ出しを許容する (過剰修正の防止) ---
        parents2 = vertical_parents(4)  # 「全集中の」相当: 0-2 にだけルビ
        spans2 = [_Span(0, 3, "ぜんしゅうちゅう")]
        placements2 = ruby.compute_ruby_placements(
            parents2, spans2, ruby_size_ratio=0.5, ruby_letter_spacing=0.0,
            writing_mode="vertical",
        )
        _check(len(placements2) == 8, f"ルビ文字数が8ではありません: {len(placements2)}")
        ruby_em2 = {round(p.size_pt, 4) for p in placements2}
        _check(
            ruby_em2 == {round(em_pt * 0.5, 4)},
            f"衝突が無いのにルビサイズが変わっています: {ruby_em2}",
        )
        top = max(p.y_mm + p.size_pt * 25.4 / 72.0 for p in placements2)
        bottom = min(p.y_mm for p in placements2)
        _check(
            (top - bottom) > em_mm * 3.0 + 0.1,
            "親3文字より長いルビが圧縮されてしまっています (はみ出し許容の退行)",
        )

        # --- 3. 横書きでも隣接スパンが重ならない ---
        parents3 = [
            layout.GlyphPlacement(
                ch="親", x_mm=10.0 + em_mm * i, y_mm=50.0,
                size_pt=em_pt, rotation_deg=0.0, index=i,
            )
            for i in range(4)
        ]
        placements3 = ruby.compute_ruby_placements(
            parents3, [_Span(0, 2, "とうきょう"), _Span(2, 2, "とちょう")],
            ruby_size_ratio=0.5, ruby_letter_spacing=0.0,
            writing_mode="horizontal",
        )
        cells3 = sorted(
            ((p.x_mm, p.x_mm + p.size_pt * 25.4 / 72.0) for p in placements3),
            key=lambda c: c[0],
        )
        for (lo1, hi1), (lo2, _hi2) in zip(cells3, cells3[1:]):
            _check(
                hi1 <= lo2 + 0.05,
                f"横書き: ルビ文字が重なっています ({lo2:.2f} < {hi1:.2f})",
            )

        if FAILURES:
            for f in FAILURES:
                print(f"FAIL: {f}", flush=True)
            raise AssertionError(f"{len(FAILURES)} 件の検証失敗があります")
        print("BMANGA_RUBY_ADJACENT_OVERFLOW_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:  # noqa: BLE001
                pass


def _main() -> None:
    try:
        _run_check()
        sys.stdout.flush()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)
    os._exit(0)


if __name__ == "__main__":
    _main()
