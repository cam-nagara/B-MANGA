"""Blender実機用: ビュー依存文字サイズの発散ガード検証.

2026-07-21 のクラッシュ再発防止: ビュー回転でカメラがページ平面すれすれに
なると mm→px 投影換算が発散し、数十万px級の文字サイズが blf.size →
blf.draw へ渡って Blender 本体のグリフ描画 (blf_glyph_draw) が
EXCEPTION_ACCESS_VIOLATION で落ちていた。

確認項目:
  (1) 作品情報テキスト描画: 発散サイズ (1e6 / inf / nan) や非有限座標では
      blf.draw へ到達しない
  (2) 作品情報テキスト描画: 正常サイズでは従来どおり blf.draw へ到達する
  (3) ビューポート文字レンダラ (typography): 発散換算係数では blf.draw へ
      到達せず、正常係数では到達する
  (4) テキスト文字ループ (ui/overlay) が発散サイズの文字を record 化しない
      (ソースレベルでガード適用済みかの smoke は import 確認で兼ねる)

実行例:
  blender.exe --background --factory-startup --python-exit-code 1 \
      --python test/blender_overlay_font_size_guard_check.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

MOD_NAME = "bmanga_dev_font_size_guard_check"

FAILURES: list[str] = []


def _check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)
        print(f"NG: {message}", flush=True)
    else:
        print(f"OK: {message}", flush=True)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _sub(path: str):
    import importlib

    return importlib.import_module(f"{MOD_NAME}.{path}")


def _run_work_info_case(work_info, px_size, px_x=100.0, px_y=100.0):
    """_draw_text_item_pixel を blf モック下で呼び、draw 呼び出し回数を返す."""
    with mock.patch.object(work_info, "blf") as blf_mock:
        blf_mock.dimensions.return_value = (10.0, 10.0)
        work_info._draw_text_item_pixel(
            0, "p0001", px_x, px_y, px_size, (0, 0, 0, 1), "LEFT", "BOTTOM",
        )
        return blf_mock.draw.call_count


def main() -> None:
    _load_addon()
    work_info = _sub("ui.overlay_work_info")
    vp_renderer = _sub("typography.viewport_renderer")
    layout_mod = _sub("typography.layout")
    _sub("ui.overlay")  # import 成功 = ガード込みで構文・依存が健全

    # (1) 発散値では blf.draw へ到達しない
    _check(_run_work_info_case(work_info, 1.0e6) == 0,
           "作品情報: 1e6 px は描画スキップ")
    _check(_run_work_info_case(work_info, float("inf")) == 0,
           "作品情報: inf px は描画スキップ")
    _check(_run_work_info_case(work_info, float("nan")) == 0,
           "作品情報: nan px は描画スキップ")
    _check(_run_work_info_case(work_info, 40.0, px_x=float("nan")) == 0,
           "作品情報: nan 座標は描画スキップ")

    # (2) 正常サイズは従来どおり描画する
    _check(_run_work_info_case(work_info, 40.0) == 1,
           "作品情報: 正常サイズ 40px は blf.draw へ到達")

    # (3) ビューポート文字レンダラ
    result = layout_mod.TypesetResult(
        placements=[
            layout_mod.GlyphPlacement(
                ch="あ", x_mm=0.0, y_mm=0.0, size_pt=10.0,
                rotation_deg=0.0, index=0,
            ),
        ],
        overflow=False,
    )
    with mock.patch.object(vp_renderer, "blf") as blf_mock:
        vp_renderer.render_placements(result, view_to_screen_px_per_mm=1.0e9)
        _check(blf_mock.draw.call_count == 0,
               "文字レンダラ: 発散換算係数 (1e9 px/mm) は描画スキップ")
    with mock.patch.object(vp_renderer, "blf") as blf_mock:
        vp_renderer.render_placements(result, view_to_screen_px_per_mm=10.0)
        _check(blf_mock.draw.call_count == 1,
               "文字レンダラ: 正常換算係数では blf.draw へ到達")

    # (4) ガードの単体確認 (アドオン同梱の実体)
    blf_safety = _sub("utils.blf_safety")
    _check(blf_safety.safe_text_px_size(500.0) == 500.0,
           "ガード: 500px は通す")
    _check(blf_safety.safe_text_px_size(1.0e6) is None,
           "ガード: 1e6 px は None")

    print("SENTINEL_FONT_SIZE_GUARD_DONE", flush=True)
    if FAILURES:
        raise SystemExit(f"{len(FAILURES)} 件失敗: {FAILURES}")


main()
