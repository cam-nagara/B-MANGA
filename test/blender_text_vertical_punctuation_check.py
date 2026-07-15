"""Blender実機用: 縦書きの約物 (記号類) 位置・回転対応の検証.

2026-07-16 対応。縦書きテキストで記号類の位置が横書きのままだった問題:

  1. 括弧類 (「」『』（）【】等) が回転されず横書き字形のまま表示される
     → metrics._VERTICAL_ROTATE_CHARS へ括弧類を追加し 90 度回転
  2. 句読点 (、。，．) が全角ボディの左下 (横書き位置) のまま表示される
     → 描画時に右上へ 0.5em ずらす (GlyphPlacement.offset_x_mm/offset_y_mm)
  3. 小書き仮名 (っゃゅょ等) が縦書き字形の右上寄せにならない
     → 描画時に右上へ 0.1em ずらす
  4. 明示改行 (\n) 直後の行頭に禁則文字 (ー…、等) が来ると「ぶら下げ」が
     誤発動して前の列末尾へ張り付く → 自動折返し時のみぶら下げる
  5. ぶら下げで配置された文字の回転・ずらしが消える → 保持する
  6. Pillow 書き出しの回転方向が blf (負=時計回り) と逆だった
     → Image.rotate へ同符号で渡し、全角ボディ中心を回転軸に補正

検証は layout の配置データと、Pillow 実描画の字面 (ink) 重心の両方で行う。

実行 (--factory-startup 必須):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python test\\blender_text_vertical_punctuation_check.py
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_text_vpunct"

FAILURES: list[str] = []


def _check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)
        print(f"NG: {message}", flush=True)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _placement_map(result):
    by_char: dict[str, list] = {}
    for g in result.placements:
        by_char.setdefault(g.ch, []).append(g)
    return by_char


def _run_check() -> None:
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()

        layout = sys.modules[f"{PACKAGE}.typography.layout"]
        metrics = sys.modules[f"{PACKAGE}.typography.metrics"]
        export_renderer = sys.modules[f"{PACKAGE}.typography.export_renderer"]
        tatechuyoko = sys.modules[f"{PACKAGE}.typography.tatechuyoko"]
        text_style = sys.modules[f"{PACKAGE}.utils.text_style"]

        font_size_pt = 12.0
        em_mm = font_size_pt * 25.4 / 72.0

        # --- 1. 回転対象: 括弧類・ダッシュ類は -90、通常文字・句読点は 0 ---
        for ch in "「」『』（）〈〉《》【】〔〕［］｛｝ー―…‥～〜－":
            _check(metrics.needs_vertical_rotation(ch), f"縦書き回転対象のはずの文字が回転されません: {ch!r}")
        for ch in "あ漢！？・、。っー゙ゃ"[:8]:
            if ch in "ー":
                continue
            _check(
                not metrics.needs_vertical_rotation(ch) or ch in "ー",
                f"回転してはいけない文字が回転対象です: {ch!r}",
            )

        # --- 2. 字面ずらし量: 句読点 0.5em / 小書き仮名 0.1em / 通常 0 ---
        for ch in "、。，．":
            _check(
                metrics.vertical_draw_offset_em(ch) == (0.5, 0.5),
                f"句読点の縦書きずらし量が (0.5, 0.5) ではありません: {ch!r}",
            )
        for ch in "ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮ":
            _check(
                metrics.vertical_draw_offset_em(ch) == (0.1, 0.1),
                f"小書き仮名の縦書きずらし量が (0.1, 0.1) ではありません: {ch!r}",
            )
        for ch in "あ漢！？・「ー":
            _check(
                metrics.vertical_draw_offset_em(ch) == (0.0, 0.0),
                f"ずらし不要の文字にずらし量が付いています: {ch!r}",
            )

        # --- 3. typeset_vertical が回転・ずらしを配置データへ反映する ---
        result = layout.typeset_vertical(
            "「あ、い。」っ", 0.0, 0.0, 60.0, 60.0, font_size_pt=font_size_pt
        )
        by_char = _placement_map(result)
        _check(not result.overflow, "検証テキストが領域からあふれました (テスト前提の崩れ)")
        for ch in ("「", "」"):
            g = by_char[ch][0]
            _check(g.rotation_deg == -90.0, f"縦書きで {ch!r} が回転されていません: {g.rotation_deg!r}")
        for ch in ("、", "。"):
            g = by_char[ch][0]
            _check(
                abs(g.offset_x_mm - em_mm * 0.5) < 1e-9 and abs(g.offset_y_mm - em_mm * 0.5) < 1e-9,
                f"縦書きで {ch!r} の右上ずらしが配置に反映されていません: ({g.offset_x_mm!r}, {g.offset_y_mm!r})",
            )
        g = by_char["っ"][0]
        _check(
            abs(g.offset_x_mm - em_mm * 0.1) < 1e-9 and abs(g.offset_y_mm - em_mm * 0.1) < 1e-9,
            f"縦書きで小書き仮名のずらしが配置に反映されていません: ({g.offset_x_mm!r}, {g.offset_y_mm!r})",
        )
        for ch in ("あ", "い"):
            g = by_char[ch][0]
            _check(
                g.rotation_deg == 0.0 and g.offset_x_mm == 0.0 and g.offset_y_mm == 0.0,
                f"通常文字に回転・ずらしが付いています: {ch!r}",
            )

        # --- 4. 横書きは回転・ずらしなし (従来互換) ---
        result_h = layout.typeset_horizontal(
            "「あ、い。」っ", 0.0, 0.0, 60.0, 60.0, font_size_pt=font_size_pt
        )
        for g in result_h.placements:
            _check(
                g.rotation_deg == 0.0 and g.offset_x_mm == 0.0 and g.offset_y_mm == 0.0,
                f"横書きに回転・ずらしが付いています: {g.ch!r}",
            )

        # --- 5. 明示改行 (\n) 直後の禁則文字はぶら下げず新しい列の先頭に置く ---
        region_h = em_mm * 4.0  # 1列4文字
        result_nl = layout.typeset_vertical(
            "あい\nー…", 0.0, 0.0, 60.0, region_h, font_size_pt=font_size_pt
        )
        by_char_nl = _placement_map(result_nl)
        g_a = by_char_nl["あ"][0]
        g_dash = by_char_nl["ー"][0]
        g_dots = by_char_nl["…"][0]
        _check(
            g_dash.x_mm < g_a.x_mm - 1e-9,
            f"\\n直後の 'ー' が新しい列に置かれていません (前の列へぶら下がっています): "
            f"x_mm={g_dash.x_mm!r} (前列 x_mm={g_a.x_mm!r})",
        )
        _check(
            abs(g_dash.y_mm - (region_h - em_mm)) < 1e-9,
            f"\\n直後の 'ー' が列の先頭 (上端) に置かれていません: y_mm={g_dash.y_mm!r}",
        )
        _check(
            abs(g_dots.y_mm - (region_h - em_mm * 2.0)) < 1e-9 and abs(g_dots.x_mm - g_dash.x_mm) < 1e-9,
            f"\\n直後の行の2文字目 '…' の位置が想定と違います: ({g_dots.x_mm!r}, {g_dots.y_mm!r})",
        )

        # --- 6. 自動折返しのぶら下げは従来どおり機能し、回転・ずらしを保持する ---
        region_h2 = em_mm * 2.0  # 1列2文字
        result_wrap = layout.typeset_vertical(
            "ああー", 0.0, 0.0, 60.0, region_h2, font_size_pt=font_size_pt
        )
        by_char_wrap = _placement_map(result_wrap)
        g_a2 = by_char_wrap["あ"][1]
        g_hang = by_char_wrap["ー"][0]
        _check(
            abs(g_hang.x_mm - g_a2.x_mm) < 1e-9 and g_hang.y_mm < g_a2.y_mm - 1e-9,
            f"自動折返しの 'ー' が前の列末尾へぶら下がっていません: ({g_hang.x_mm!r}, {g_hang.y_mm!r})",
        )
        _check(
            g_hang.rotation_deg == -90.0,
            f"ぶら下げ配置で 'ー' の回転が失われています: {g_hang.rotation_deg!r}",
        )
        result_wrap2 = layout.typeset_vertical(
            "ああ、", 0.0, 0.0, 60.0, region_h2, font_size_pt=font_size_pt
        )
        g_hang2 = _placement_map(result_wrap2)["、"][0]
        _check(
            abs(g_hang2.offset_x_mm - em_mm * 0.5) < 1e-9 and abs(g_hang2.offset_y_mm - em_mm * 0.5) < 1e-9,
            f"ぶら下げ配置で '、' の右上ずらしが失われています: ({g_hang2.offset_x_mm!r}, {g_hang2.offset_y_mm!r})",
        )

        # --- 7. 縦中横は新フィールドと共存する (回帰スモーク) ---
        class _Span:
            start = 0
            length = 2

        tate = tatechuyoko.apply_tatechuyoko(result.placements, [_Span()])
        _check(len(tate) == len(result.placements), "apply_tatechuyoko が配置数を変えました")

        # --- 8. Pillow 実描画の字面 (ink) 位置検証 ---
        _check(export_renderer.has_pillow(), "Pillow が利用できません (バンドル済みwheelの読込に失敗)")
        font_path = text_style.resolve_font_path("")
        _check(bool(font_path), "日本語フォントが解決できません")
        if export_renderer.has_pillow() and font_path:
            from PIL import Image

            px_per_mm = 20.0
            size_px = int(font_size_pt * px_per_mm * 25.4 / 72.0)

            def ink_stats(ch: str):
                """1文字だけ縦書き配置し、セル中心からの ink 重心 (em) と縦横比を返す."""
                pad_mm = em_mm * 2.0
                region = em_mm * 1.0
                res = layout.typeset_vertical(
                    ch, pad_mm, pad_mm, region, region, font_size_pt=font_size_pt
                )
                if len(res.placements) != 1:
                    return None
                g0 = res.placements[0]
                img_size = int((region + pad_mm * 2.0) * px_per_mm)
                image = Image.new("RGBA", (img_size, img_size), (0, 0, 0, 0))
                export_renderer.render_to_image(
                    res, image, font_path=font_path, px_per_mm=px_per_mm, color=(0, 0, 0, 255)
                )
                bbox = image.getchannel("A").getbbox()
                if bbox is None:
                    return None
                cell_left = g0.x_mm * px_per_mm
                cell_top = img_size - (g0.y_mm * px_per_mm + size_px)
                cx = (bbox[0] + bbox[2]) / 2.0
                cy = (bbox[1] + bbox[3]) / 2.0
                dx_em = (cx - (cell_left + size_px / 2.0)) / size_px
                dy_em = ((cell_top + size_px / 2.0) - cy) / size_px  # 上+
                w_em = (bbox[2] - bbox[0]) / size_px
                h_em = (bbox[3] - bbox[1]) / size_px
                return dx_em, dy_em, w_em, h_em

            for ch in ("、", "。"):
                stats = ink_stats(ch)
                _check(stats is not None, f"{ch!r} の ink が取得できません")
                if stats:
                    dx, dy, _w, _h = stats
                    _check(
                        dx > 0.15 and dy > 0.15,
                        f"縦書きの {ch!r} が右上に描画されていません: dx_em={dx:.3f} dy_em={dy:.3f}",
                    )

            # 始め括弧は字面が「囲む文字側」= 縦書きでは下半分に付き、外側 (上)
            # が二分アキになる (JLREQ)。終わり括弧はその逆で上半分に付く。
            stats = ink_stats("「")
            if stats:
                dx, dy, _w, _h = stats
                _check(
                    dy < -0.1 and abs(dx) < 0.25,
                    f"縦書きの '「' が縦組み字形 (下寄りのコーナー) になっていません: dx_em={dx:.3f} dy_em={dy:.3f}",
                )
            stats = ink_stats("」")
            if stats:
                dx, dy, _w, _h = stats
                _check(
                    dy > 0.1 and abs(dx) < 0.25,
                    f"縦書きの '」' が縦組み字形 (上寄りのコーナー) になっていません: dx_em={dx:.3f} dy_em={dy:.3f}",
                )
            stats = ink_stats("ー")
            if stats:
                dx, _dy, w, h = stats
                _check(
                    h > w and abs(dx) < 0.2,
                    f"縦書きの 'ー' が縦棒として列中央に描画されていません: dx_em={dx:.3f} w_em={w:.3f} h_em={h:.3f}",
                )
            stats = ink_stats("（")
            if stats:
                _dx, dy, w, h = stats
                _check(
                    w > h and dy < -0.1,
                    f"縦書きの '（' が下開き (字面下寄り) の縦組み字形になっていません: dy_em={dy:.3f} w_em={w:.3f} h_em={h:.3f}",
                )
            stats = ink_stats("っ")
            if stats:
                dx, dy, _w, _h = stats
                _check(
                    dx > 0.02 and dy > -0.05,
                    f"縦書きの 'っ' が右上寄りに描画されていません: dx_em={dx:.3f} dy_em={dy:.3f}",
                )
            stats = ink_stats("あ")
            if stats:
                dx, dy, _w, _h = stats
                _check(
                    abs(dx) < 0.15 and abs(dy) < 0.2,
                    f"通常文字 'あ' の描画位置が動いてしまっています: dx_em={dx:.3f} dy_em={dy:.3f}",
                )

        if FAILURES:
            for f in FAILURES:
                print(f"FAIL: {f}", flush=True)
            raise AssertionError(f"{len(FAILURES)} 件の検証失敗があります")
        print("BMANGA_TEXT_VPUNCT_OK", flush=True)
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
        try:
            bpy.ops.wm.quit_blender()
        except Exception:  # noqa: BLE001
            pass
        sys.exit(1)
    try:
        bpy.ops.wm.quit_blender()
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    _main()
