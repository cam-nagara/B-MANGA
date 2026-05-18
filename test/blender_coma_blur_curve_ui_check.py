"""Blender実機(UI)用: ぼかしカーブ編集が表示素材ノードを直接触らない確認."""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_coma_blur_ui",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_coma_blur_ui"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _create_brush_coma():
    scene = bpy.context.scene
    work = scene.bname_work
    work.loaded = True
    page = work.pages.add()
    page.id = "p0001"
    page.title = "1ページ"
    coma = page.comas.add()
    coma.id = "c01"
    coma.coma_id = "c01"
    coma.title = "コマ1"
    coma.rect_width_mm = 90.0
    coma.rect_height_mm = 70.0
    coma.border.style = "brush"
    coma.border.width_mm = 3.5
    coma.border.blur_amount = 1.0
    coma.border.blur_dither = True
    return scene, work, page, coma


def _select_coma_in_stack(context) -> int:
    from bname_dev_coma_blur_ui.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    assert stack is not None
    for index, item in enumerate(stack):
        if str(getattr(item, "kind", "") or "") == "coma":
            assert layer_stack_utils.select_stack_index(context, index)
            return index
    raise AssertionError("レイヤー一覧にコマが見つかりません")


def _ui_override():
    window = bpy.context.window
    if window is None:
        return None
    screen = window.screen
    area = next((candidate for candidate in screen.areas if candidate.type == "VIEW_3D"), None)
    if area is None:
        area = screen.areas[0] if screen.areas else None
    if area is None:
        return None
    region = next((candidate for candidate in area.regions if candidate.type == "WINDOW"), None)
    if region is None:
        return None
    return {"window": window, "screen": screen, "area": area, "region": region}


def _run_ui_check(scene, work, page, coma) -> None:
    from bname_dev_coma_blur_ui.utils import coma_blur_curve, coma_border_object

    obj = coma_border_object.ensure_coma_border_object(scene, work, page, coma)
    assert obj is not None, "輪郭ぼかしの表示オブジェクトが作成されていません"
    mat = obj.data.materials[0] if getattr(obj.data, "materials", None) else None
    live_node = coma_blur_curve.find_curve_node(mat)
    assert live_node is not None, "表示用のぼかしカーブがありません"

    index = _select_coma_in_stack(bpy.context)
    override = _ui_override()
    assert override is not None, "UIコンテキストが見つかりません"
    with bpy.context.temp_override(**override):
        result = bpy.ops.bname.layer_stack_detail("INVOKE_DEFAULT", index=index)
        assert "RUNNING_MODAL" in result or "FINISHED" in result, result
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=8)

    ui_node = coma_blur_curve.ensure_ui_curve_node(coma.border)
    assert ui_node is not None, "ぼかしカーブ編集UIが作成されていません"
    assert ui_node.id_data is not live_node.id_data, "ぼかしカーブ編集UIが表示素材を直接編集しています"
    coma_blur_curve.apply_points_to_node(ui_node, ((0.0, 0.0), (0.22, 0.9), (1.0, 1.0)))
    assert coma_blur_curve.sync_ui_curve_to_border(coma.border), "ぼかしカーブ編集が反映されていません"
    assert "0.2200,0.9000" in coma.border.blur_curve_points, coma.border.blur_curve_points
    with bpy.context.temp_override(**override):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=8)
    print("BNAME_COMA_BLUR_CURVE_UI_CHECK_OK")
    sys.stdout.flush()


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    scene, work, page, coma = _create_brush_coma()

    attempts = {"count": 0}

    def _timer():
        attempts["count"] += 1
        if bpy.context.window is None and attempts["count"] < 30:
            return 0.1
        try:
            _run_ui_check(scene, work, page, coma)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            os._exit(1)
        os._exit(0)
        return None

    bpy.app.timers.register(_timer, first_interval=0.1)


if __name__ == "__main__":
    main()
