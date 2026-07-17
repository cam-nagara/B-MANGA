"""Blender UI実機用: コマ⇔テキストの並べ替えがビューポート描画の前後関係へ反映されるか.

2026-07-18 ユーザー指示の視覚検証 (UIあり実機で実行すること):
  A. Meldex取込直後: テキストはコマより前面に描画される (文字が見える)
  B. テキスト行を「最背面」へ → コマ用紙面に隠れて文字が見えなくなる
  C. コマ行を「最背面」へ → テキストが再び前面に描画される

実行例:
  blender.exe --factory-startup --python test/blender_layer_stack_cross_kind_visual_check.py

スクリーンショットは _verify/2026-07-18_layer_stack_coma_text_order/ に保存する
(AI目視・手動確認用)。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_cross_kind_visual"
OUT_DIR = ROOT / "_verify" / "2026-07-18_layer_stack_coma_text_order"
SUMMARY = OUT_DIR / "visual_result.json"
STAGE = OUT_DIR / "stage.txt"


def _mark(stage: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STAGE.write_text(stage, encoding="utf-8")


def _fail(stage: str, exc: BaseException) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "ok": False,
        "stage": stage,
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc(),
    }
    SUMMARY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("CROSS_KIND_VISUAL_CHECK_ERROR", json.dumps(data, ensure_ascii=False), flush=True)
    os._exit(1)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module.register()
    return module


def _payload():
    return {
        "contract": "meldex-bmanga-scenario",
        "version": 1,
        "source": {"documentId": "visual-cross-kind"},
        "pages": [
            {"rows": [
                {"rowId": "r1", "type": "会話", "body": "重なり確認\n前面テスト", "rubies": []},
            ]},
        ],
    }


def _first_view3d():
    for window in bpy.context.window_manager.windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            space = area.spaces.active
            rv3d = getattr(space, "region_3d", None)
            if region is not None and rv3d is not None:
                return window, screen, area, region, space, rv3d
    raise AssertionError("3Dビューが見つかりません")


def _redraw(iterations: int = 4) -> None:
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=iterations)
    except Exception:  # noqa: BLE001
        pass


def _capture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _redraw(4)
    result = bpy.ops.screen.screenshot("EXEC_DEFAULT", filepath=str(path), check_existing=False)
    if "FINISHED" not in result:
        raise AssertionError(f"スクリーンショット保存に失敗しました: {result}")


def _text_screen_rect(region, rv3d, text_obj):
    from bpy_extras import view3d_utils
    from mathutils import Vector

    xs: list[float] = []
    ys: list[float] = []
    for corner in text_obj.bound_box:
        world = text_obj.matrix_world @ Vector(corner)
        projected = view3d_utils.location_3d_to_region_2d(region, rv3d, world)
        if projected is None:
            continue
        xs.append(float(projected.x))
        ys.append(float(projected.y))
    assert len(xs) >= 4, "テキストが3Dビュー内に映っていません"
    return min(xs), min(ys), max(xs), max(ys)


def _dark_pixels_in_rect(path: Path, region, rect) -> int:
    from PIL import Image

    image = Image.open(path).convert("RGB")
    width, height = image.size
    x0, y0, x1, y1 = rect
    sx0 = max(0, int(region.x + max(0.0, x0)))
    sx1 = min(width, int(region.x + min(float(region.width), x1)))
    # スクリーンショットは上原点、regionは下原点
    sy0 = max(0, int(height - (region.y + min(float(region.height), y1))))
    sy1 = min(height, int(height - (region.y + max(0.0, y0))))
    assert sx1 > sx0 and sy1 > sy0, (sx0, sy0, sx1, sy1)
    return sum(
        1
        for red, green, blue in image.crop((sx0, sy0, sx1, sy1)).getdata()
        if max(red, green, blue) < 96
    )


def _assert_text_stays_page_child(label: str, context, text) -> None:
    """順序ボタン移動がD&D誤検知でコマへ親変更されない回帰確認 (2026-07-18)."""
    row = next(
        (
            (it.parent_key, int(it.depth))
            for it in context.scene.bmanga_layer_stack
            if it.kind == "text"
        ),
        None,
    )
    assert str(text.parent_kind) == "page" and ":" not in str(text.parent_key), (
        f"{label}: テキストがページ直下でなくなりました data="
        f"({text.parent_kind},{text.parent_key}) row={row}"
    )


def _move_row(context, layer_stack_utils, kind: str, key: str, direction: str) -> None:
    stack = layer_stack_utils.sync_layer_stack(context)
    idx = next(
        i for i, item in enumerate(stack) if item.kind == kind and item.key == key
    )
    context.scene.bmanga_active_layer_stack_index = idx
    moved = layer_stack_utils.move_stack_item(context, idx, direction=direction)
    if not moved:
        rows = [
            (i, it.kind, it.key, it.parent_key, int(it.depth))
            for i, it in enumerate(context.scene.bmanga_layer_stack)
        ]
        raise AssertionError(
            f"{kind}:{key} を {direction} へ移動できません rows={rows}"
        )
    layer_stack_utils.sync_layer_stack(context)


def _row_index(stack, kind: str, key: str) -> int:
    return next(i for i, it in enumerate(stack) if it.kind == kind and it.key == key)


def _run() -> None:
    stage = "setup"
    try:
        _mark(stage)
        _load_addon()
        work_dir = Path(tempfile.mkdtemp(prefix="bmanga_cross_visual_")) / "visual.bmanga"
        result = bpy.ops.bmanga.work_new(filepath=str(work_dir))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        from bmanga_dev_cross_kind_visual import preferences
        from bmanga_dev_cross_kind_visual.core.work import get_work
        from bmanga_dev_cross_kind_visual.io import (
            balloon_presets,
            meldex_scenario_import,
            text_presets,
        )
        from bmanga_dev_cross_kind_visual.utils import (
            layer_stack as layer_stack_utils,
            page_detail,
            text_real_object,
        )

        preferences.get_preferences = lambda _context=None: SimpleNamespace(
            meldex_apply_text_presentation=False
        )
        balloon_presets.list_all_presets = lambda _path: []
        # 完全一致 + linked_balloon_preset 空 → フキダシ無しのテキスト単体行になる
        text_presets.list_all_presets = lambda _path: [
            SimpleNamespace(
                name="会話",
                data={
                    "font": r"C:\Windows\Fonts\msgothic.ttc",
                    "font_size_pt": 24.0,
                    "writing_mode": "vertical",
                    "line_height": 1.6,
                    "linked_balloon_preset": "",
                },
            ),
        ]

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        page = work.pages[0]
        page_detail.ensure_page_detail(work, page)
        assert len(page.comas) >= 1, "基本枠コマがありません"
        coma = page.comas[0]
        coma_key = f"{page.id}:{coma.coma_id or coma.id}"

        stage = "import"
        _mark(stage)
        import_result = meldex_scenario_import.import_payload(context, work, _payload())
        assert import_result["created"] == 1, import_result
        text = next(t for t in page.texts if t.meldex_source_row_id == "r1")
        assert text.parent_kind == "page", text.parent_kind

        # テキストをコマ中央へ重ねる (コマ枠線から離れた内側)
        cx = float(coma.rect_x_mm) + float(coma.rect_width_mm) * 0.5
        cy = float(coma.rect_y_mm) + float(coma.rect_height_mm) * 0.5
        text.x_mm = cx - float(text.width_mm) * 0.5
        text.y_mm = cy - float(text.height_mm) * 0.5
        text_obj = text_real_object.ensure_text_real_object(scene=scene, entry=text, page=page)
        assert text_obj is not None
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        text_key = f"{page.id}:{text.id}"

        stage = "framing"
        _mark(stage)
        window, screen, area, region, space, rv3d = _first_view3d()
        from mathutils import Quaternion, Vector

        center = text_obj.matrix_world.translation.copy()
        half = max(float(text_obj.dimensions.x), float(text_obj.dimensions.y))
        with context.temp_override(
            window=window, screen=screen, area=area, region=region, region_data=rv3d
        ):
            rv3d.view_perspective = "ORTHO"
            rv3d.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))  # 真上から
            rv3d.view_location = Vector((center.x, center.y, 0.0))
            rv3d.view_distance = max(0.15, half * 4.0)
            _redraw(6)

            report: dict[str, object] = {"ok": True}

            # --- A. 取込直後: テキストがコマより前面 (文字が見える) -----------
            stage = "capture_a"
            _mark(stage)
            rect = _text_screen_rect(region, rv3d, text_obj)
            shot_a = OUT_DIR / "visual_A_text_front.png"
            _capture(shot_a)
            report["A_text_front_dark_px"] = _dark_pixels_in_rect(shot_a, region, rect)
            assert int(report["A_text_front_dark_px"]) >= 300, report

            # --- B. テキスト行を最背面へ → コマ用紙面に隠れること -------------
            stage = "move_text_back"
            _mark(stage)
            _move_row(context, layer_stack_utils, "text", text_key, "BACK")
            _assert_text_stays_page_child("テキスト最背面直後", context, text)
            stack = scene.bmanga_layer_stack
            assert _row_index(stack, "text", text_key) > _row_index(stack, "coma", coma_key), (
                "テキスト行がコマ行より背面になっていません"
            )
            shot_b = OUT_DIR / "visual_B_text_behind.png"
            _capture(shot_b)
            _assert_text_stays_page_child("スクリーンショットB後", context, text)
            report["B_text_behind_dark_px"] = _dark_pixels_in_rect(shot_b, region, rect)
            assert int(report["B_text_behind_dark_px"]) < 50, report

            # --- C. コマ行を最背面へ → テキストが再び前面に描画されること -----
            stage = "move_coma_back"
            _mark(stage)
            _move_row(context, layer_stack_utils, "coma", coma_key, "BACK")
            stack = scene.bmanga_layer_stack
            assert _row_index(stack, "coma", coma_key) > _row_index(stack, "text", text_key), (
                "コマ行がテキスト行より背面になっていません"
            )
            shot_c = OUT_DIR / "visual_C_coma_behind.png"
            _capture(shot_c)
            report["C_coma_behind_dark_px"] = _dark_pixels_in_rect(shot_c, region, rect)
            assert int(report["C_coma_behind_dark_px"]) >= 300, report

        SUMMARY.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"CROSS_KIND_VISUAL_CHECK_PASS {report}", flush=True)
        os._exit(0)
    except BaseException as exc:  # noqa: BLE001 - UI実機はステージ付きで失敗を記録する
        _fail(stage, exc)


def _fail_safe():
    _fail("timeout", TimeoutError("UI実機テストが時間内に完了しませんでした"))


def main() -> None:
    bpy.app.timers.register(_run, first_interval=1.0)
    bpy.app.timers.register(_fail_safe, first_interval=180.0)


main()
