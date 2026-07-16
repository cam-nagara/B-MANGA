"""Blender実機用: 縦中横 (たてちゅうよこ) の統合検証.

2026-07-17 対応。Meldexパリティ監査で判明した「BMangaTextEntry.tatechuyoko_ranges /
typography/tatechuyoko.py の apply_tatechuyoko が存在するが呼び出し元ゼロで描画に
反映されない」問題への対応:

  1. typography/layout.py の typeset_vertical へ縦中横処理を統合 (後処理方式は
     行送りが崩れるため使わない)。新引数 tatechuyoko_ranges (start, length) の
     一覧を受け取り、範囲内の文字を 1 文字分のセルへ横並び圧縮する。
  2. typography/metrics.py へ auto_tatechuyoko_ranges() を追加。半角英数字 +
     '!' '?' の 2〜4 文字連続ランを自動検出する (1 文字・5 文字以上は対象外)。
  3. core/text_entry.py へ BMangaTextEntry.tatechuyoko_auto (既定 True) を追加。
  4. typography/layout.py の typeset() ラッパーで、縦書き時のみ手動 ranges +
     自動検出 (手動優先) を合成して typeset_vertical へ渡す。横書きでは無効。
  5. io/schema.py に tatechuyokoAuto の保存・復元を追加 (旧データは True 扱い)。

検証項目:
  1. "その12月!?です" (auto ON) → "12" と "!?" がそれぞれ1セル横並びに配置される
  2. "no1234567" (7文字以上のラン) は変換されない (縦積みのまま)
  3. auto OFF → 変換されない。手動 ranges 指定 → 指定範囲だけ変換される
  4. 2文字はフルサイズ (scale 1.0)、4文字は半分サイズ (scale 0.5)
  5. 横書きでは ranges があっても何も起きない
  6. 列末で入り切らない場合、範囲全体が次列先頭へ折り返す
  7. ルビ付き親文字と縦中横の共存 (クラッシュしないこと)
  8. schema ラウンドトリップ (tatechuyokoAuto の保存・復元・旧データ互換=True)
  9. Pillow 実描画 PNG を _verify/2026-07-17_tatechuyoko/ へ出力 (目視用)

実行 (--factory-startup 必須):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python test\\blender_text_tatechuyoko_check.py
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_tatechuyoko"
OUT_DIR = ROOT / "_verify" / "2026-07-17_tatechuyoko"

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


def _new_entry(work_dir: Path):
    result = bpy.ops.bmanga.work_new(filepath=str(work_dir))
    assert result == {"FINISHED"}, result
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    entry = page.texts.add()
    entry.id = "text_tatechuyoko"
    entry.x_mm = 0.0
    entry.y_mm = 0.0
    entry.width_mm = 60.0
    entry.height_mm = 60.0
    entry.writing_mode = "vertical"
    entry.font_size_q = 20.0
    page.active_text_index = 0
    return work, page, entry


def _by_index(placements):
    return {g.index: g for g in placements}


def _run_check() -> None:
    mod = None
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_tatechuyoko_"))
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()

        layout = sys.modules[f"{PACKAGE}.typography.layout"]
        metrics = sys.modules[f"{PACKAGE}.typography.metrics"]
        text_ruby = sys.modules[f"{PACKAGE}.typography.ruby"]
        text_style = sys.modules[f"{PACKAGE}.utils.text_style"]
        schema = sys.modules[f"{PACKAGE}.io.schema"]
        text_real_object = sys.modules[f"{PACKAGE}.utils.text_real_object"]
        from importlib import import_module

        geom = sys.modules.get(f"{PACKAGE}.utils.geom") or import_module(f"{PACKAGE}.utils.geom")

        work, page, entry = _new_entry(temp_root / "TextTatechuyoko.bmanga")
        em_mm = geom.q_to_mm(entry.font_size_q)

        # --- 1. 自動検出 (metrics.auto_tatechuyoko_ranges) ---
        text1 = "その12月!?です"
        auto_ranges = metrics.auto_tatechuyoko_ranges(text1)
        _check(auto_ranges == [(2, 2), (5, 2)], f"自動検出範囲が想定と違います: {auto_ranges!r}")

        # --- 2. 5文字以上のランは対象外 (縦積みのまま) ---
        text2 = "no1234567"
        auto_ranges2 = metrics.auto_tatechuyoko_ranges(text2)
        _check(auto_ranges2 == [], f"長いランが誤って縦中横候補になっています: {auto_ranges2!r}")
        result2 = layout.typeset_vertical(text2, 0.0, 0.0, 60.0, 200.0, font_size_pt=geom.q_to_pt(20.0))
        _check(not result2.overflow, "長いラン検証テキストがあふれました (テスト前提の崩れ)")
        by2 = _by_index(result2.placements)
        ys2 = sorted({round(g.y_mm, 6) for g in by2.values()})
        _check(len(ys2) == len(text2), f"5文字以上のランが縦積みではなく圧縮されています: {ys2!r}")
        sizes2 = {round(g.size_pt, 6) for g in by2.values()}
        _check(
            sizes2 == {round(geom.q_to_pt(20.0), 6)},
            f"5文字以上のランの文字サイズが縮小されています: {sizes2!r}",
        )

        # --- 1 (続き). typeset() 経由 (entry.tatechuyoko_auto 既定 True) で実際に配置へ反映 ---
        entry.body = text1
        _check(bool(entry.tatechuyoko_auto), "tatechuyoko_auto の既定値が True ではありません")
        result1 = layout.typeset(entry, 0.0, 0.0, 60.0, 60.0)
        _check(not result1.overflow, "検証テキストがあふれました (テスト前提の崩れ)")
        by1 = _by_index(result1.placements)
        g_1 = by1[2]  # '1'
        g_2 = by1[3]  # '2'
        g_bang = by1[5]  # '!'
        g_q = by1[6]  # '?'
        g_month = by1[4]  # '月'
        g_de = by1[7]  # 'で'
        _check(abs(g_1.y_mm - g_2.y_mm) < 1e-9, "'12' が同じ y (横並び) に配置されていません")
        _check(g_1.x_mm != g_2.x_mm, "'12' の x が並んでいません (横並びになっていません)")
        _check(abs(g_bang.y_mm - g_q.y_mm) < 1e-9, "'!?' が同じ y (横並び) に配置されていません")
        _check(g_bang.x_mm != g_q.x_mm, "'!?' の x が並んでいません (横並びになっていません)")
        char_pitch_mm = em_mm  # letter_spacing=0 既定
        _check(
            abs(g_month.y_mm - (g_1.y_mm - char_pitch_mm)) < 1e-6,
            f"'12' セルの直後の文字の y が1ピッチ分下がっていません: 月 y={g_month.y_mm!r} 12 y={g_1.y_mm!r}",
        )
        _check(
            abs(g_de.y_mm - (g_bang.y_mm - char_pitch_mm)) < 1e-6,
            f"'!?' セルの直後の文字の y が1ピッチ分下がっていません: で y={g_de.y_mm!r} !? y={g_bang.y_mm!r}",
        )
        _check(g_1.rotation_deg == 0.0 and g_2.rotation_deg == 0.0, "縦中横セルの回転が0以外です")
        _check(
            g_1.offset_x_mm == 0.0 and g_1.offset_y_mm == 0.0,
            "縦中横セルの字面ずらしが0以外です",
        )

        # --- 4. 2文字フルサイズ (scale 1.0) / 4文字半分サイズ (scale 0.5) ---
        base_pt = geom.q_to_pt(20.0)
        result_2ch = layout.typeset_vertical(
            "ab", 0.0, 0.0, 60.0, 60.0, font_size_pt=base_pt, tatechuyoko_ranges=[(0, 2)]
        )
        sizes_2ch = {round(g.size_pt, 6) for g in result_2ch.placements}
        _check(sizes_2ch == {round(base_pt, 6)}, f"2文字セルがフルサイズになっていません: {sizes_2ch!r}")
        result_4ch = layout.typeset_vertical(
            "ab12", 0.0, 0.0, 60.0, 60.0, font_size_pt=base_pt, tatechuyoko_ranges=[(0, 4)]
        )
        sizes_4ch = {round(g.size_pt, 6) for g in result_4ch.placements}
        _check(
            sizes_4ch == {round(base_pt * 0.5, 6)},
            f"4文字セルが半分サイズになっていません: {sizes_4ch!r}",
        )

        # --- 3. auto OFF → 変換されない。手動 ranges 指定 → 指定範囲だけ変換される ---
        entry.body = "12ab34"
        entry.tatechuyoko_auto = False
        entry.tatechuyoko_ranges.clear()
        result_off = layout.typeset(entry, 0.0, 0.0, 60.0, 60.0)
        by_off = _by_index(result_off.placements)
        ys_off = sorted({round(g.y_mm, 6) for g in by_off.values()})
        _check(
            len(ys_off) == len(entry.body),
            f"auto OFF なのに縦中横変換が起きています (全文字が別の y のはず): {ys_off!r}",
        )

        span = entry.tatechuyoko_ranges.add()
        span.start = 0
        span.length = 2
        result_manual = layout.typeset(entry, 0.0, 0.0, 60.0, 60.0)
        by_manual = _by_index(result_manual.placements)
        g_m1 = by_manual[0]
        g_m2 = by_manual[1]
        _check(abs(g_m1.y_mm - g_m2.y_mm) < 1e-9, "手動 ranges 指定 (0,2) が横並びになっていません")
        # 手動範囲外の "ab" (index 2,3) と "34" (index 4,5) は auto OFF のため変換されないまま
        g_a = by_manual[2]
        g_b = by_manual[3]
        g_3 = by_manual[4]
        g_4 = by_manual[5]
        _check(
            abs(g_a.y_mm - g_b.y_mm) > 1e-6,
            "auto OFF のはずの 'ab' が縦中横変換されています (手動範囲外へ漏れています)",
        )
        _check(
            abs(g_3.y_mm - g_4.y_mm) > 1e-6,
            "auto OFF のはずの '34' が縦中横変換されています (手動範囲外へ漏れています)",
        )
        entry.tatechuyoko_auto = True
        entry.tatechuyoko_ranges.clear()

        # --- 5. 横書きでは ranges があっても何も起きない ---
        entry.body = "12ab"
        entry.writing_mode = "horizontal"
        span_h = entry.tatechuyoko_ranges.add()
        span_h.start = 0
        span_h.length = 2
        result_h = layout.typeset(entry, 0.0, 0.0, 60.0, 60.0)
        sizes_h = {round(g.size_pt, 6) for g in result_h.placements}
        _check(
            sizes_h == {round(base_pt, 6)},
            f"横書きなのに縦中横でサイズが縮小されています: {sizes_h!r}",
        )
        xs_h = sorted({round(g.x_mm, 6) for g in result_h.placements})
        _check(
            len(xs_h) == len(entry.body),
            f"横書きなのに縦中横で横並びの重なりが起きています: {xs_h!r}",
        )
        entry.tatechuyoko_ranges.clear()
        entry.writing_mode = "vertical"

        # --- 6. 列末で入り切らない場合、範囲全体が次列先頭へ折り返す ---
        region_h2 = em_mm * 2.0  # 1列2文字分の高さ
        result_wrap = layout.typeset_vertical(
            "aa12", 0.0, 0.0, 60.0, region_h2, font_size_pt=base_pt, tatechuyoko_ranges=[(2, 2)]
        )
        _check(not result_wrap.overflow, "折返し検証テキストがあふれ扱いになっています")
        by_wrap = _by_index(result_wrap.placements)
        g_a0 = by_wrap[0]
        g_w1 = by_wrap[2]
        g_w2 = by_wrap[3]
        _check(
            g_w1.x_mm < g_a0.x_mm - 1e-6,
            f"縦中横セルが次の列へ折り返されていません: セルx={g_w1.x_mm!r} 前列x={g_a0.x_mm!r}",
        )
        _check(
            abs(g_w1.y_mm - (region_h2 - em_mm)) < 1e-6,
            f"折り返し後の縦中横セルが列の先頭にありません: y={g_w1.y_mm!r}",
        )
        _check(abs(g_w1.y_mm - g_w2.y_mm) < 1e-9, "折り返し後の縦中横セルが横並びになっていません")

        # --- 7. ルビ付き親文字と縦中横の共存 (クラッシュしないこと) ---
        entry.body = "月12日"
        entry.tatechuyoko_auto = True
        entry.tatechuyoko_ranges.clear()
        text_style.clear_ruby_spans(entry)
        text_style.apply_ruby_span(entry, 1, 3, "いちに", "group")
        try:
            result_ruby = layout.typeset(entry, 0.0, 0.0, 60.0, 60.0)
            ruby_placements = text_ruby.compute_for_entry(result_ruby.placements, entry)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            _check(False, f"ルビ+縦中横の共存でクラッシュしました: {exc!r}")
        else:
            _check(isinstance(ruby_placements, list), "ルビ配置が list で返っていません")
            _check(len(ruby_placements) > 0, "ルビ+縦中横の共存でルビ配置が空になっています")
        text_style.clear_ruby_spans(entry)

        # --- 8. schema ラウンドトリップ ---
        entry.body = "その12月です"
        entry.tatechuyoko_auto = False
        data = schema.text_entry_to_dict(entry)
        _check(data.get("tatechuyokoAuto") is False, f"tatechuyokoAuto の保存値が False ではありません: {data.get('tatechuyokoAuto')!r}")
        clone = page.texts.add()
        schema.text_entry_from_dict(clone, data)
        _check(bool(clone.tatechuyoko_auto) is False, "tatechuyokoAuto の復元が False になっていません")
        page.texts.remove(len(page.texts) - 1)

        # 旧データ (キー無し) は True 扱い
        legacy_data = dict(data)
        del legacy_data["tatechuyokoAuto"]
        clone2 = page.texts.add()
        clone2.tatechuyoko_auto = False
        schema.text_entry_from_dict(clone2, legacy_data)
        _check(bool(clone2.tatechuyoko_auto) is True, "旧データ (tatechuyokoAuto 未保存) が True 扱いになっていません")
        page.texts.remove(len(page.texts) - 1)

        entry.tatechuyoko_auto = True

        # --- 9. Pillow 実描画 PNG 出力 (目視用) ---
        entry.body = "12月と3日で!?"
        entry.writing_mode = "vertical"
        entry.width_mm = 30.0
        entry.height_mm = 60.0
        entry.tatechuyoko_auto = True
        entry.tatechuyoko_ranges.clear()
        text_style.clear_ruby_spans(entry)
        rendered = text_real_object._render_entry_to_pillow(entry)
        _check(rendered is not None, "Pillow 実描画に失敗しました (バンドル済みwheelの読込に失敗)")
        if rendered is not None:
            image = rendered[0]
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            image.save(OUT_DIR / "tatechuyoko_sample.png")
            from PIL import Image as PILImage

            white = PILImage.new("RGBA", image.size, (255, 255, 255, 255))
            white.alpha_composite(image)
            white.convert("RGB").save(OUT_DIR / "tatechuyoko_sample_white.png")
            alpha = image.getchannel("A")
            _check(sum(alpha.getdata()) > 0, "Pillow 実描画に可視ピクセルがありません")

        if FAILURES:
            for f in FAILURES:
                print(f"FAIL: {f}", flush=True)
            raise AssertionError(f"{len(FAILURES)} 件の検証失敗があります")
        print("BMANGA_TEXT_TATECHUYOKO_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:  # noqa: BLE001
                pass
        try:
            bpy.ops.wm.read_factory_settings(use_empty=True)
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(temp_root, ignore_errors=True)


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
