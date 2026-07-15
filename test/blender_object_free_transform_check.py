"""Ctrl+角ハンドルの自由変形とテキスト固定サイズを確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_free_transform_check",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_free_transform_check"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _mesh_xy_bounds(obj) -> tuple[float, float, float, float]:
    xs = [float(v.co.x) * 1000.0 for v in obj.data.vertices]
    ys = [float(v.co.y) * 1000.0 for v in obj.data.vertices]
    return min(xs), min(ys), max(xs), max(ys)


def _curve_xy_bounds(obj) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for spline in obj.data.splines:
        for point in getattr(spline, "bezier_points", []) or []:
            xs.append(float(point.co.x) * 1000.0)
            ys.append(float(point.co.y) * 1000.0)
    return min(xs), min(ys), max(xs), max(ys)


def _apply_free_drag(context, key: str, dx: float, dy: float) -> None:
    from bmanga_dev_free_transform_check.operators import object_tool_op
    from bmanga_dev_free_transform_check.utils import free_transform

    class _DummyObjectTool:
        def _panel_child_snapshots(self, _page, _panel):
            return []

    op = _DummyObjectTool()
    action = free_transform.action_for_part(free_transform.TOP_RIGHT)
    op._drag_action = action
    op._snapshots = object_tool_op.BMANGA_OT_object_tool._make_snapshots(
        op,
        context,
        [key],
        primary_key=key,
        action=action,
    )
    if not op._snapshots:
        raise AssertionError(f"snapshot が作成されませんでした: {key}")
    object_tool_op.BMANGA_OT_object_tool._apply_snapshots(op, context, dx, dy)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_free_transform_"))
    mod = None
    try:
        mod = _load_addon()
        bpy.context.scene.bmanga_overview_mode = True
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "FreeTransform.bmanga"))
        if "FINISHED" not in result:
            raise AssertionError("作品作成に失敗しました")
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        if "FINISHED" not in result:
            raise AssertionError("ページファイルを開けません")

        from bmanga_dev_free_transform_check.operators import effect_line_op, object_tool_selection
        from bmanga_dev_free_transform_check.utils import (
            balloon_curve_object,
            effect_line_object,
            free_transform,
            object_selection,
            text_real_object,
        )

        scene = bpy.context.scene
        work = scene.bmanga_work
        page = work.pages[0]

        text = page.texts.add()
        text.id = "text_free"
        text.body = "自由変形"
        text.x_mm = 12.0
        text.y_mm = 16.0
        text.width_mm = 40.0
        text.height_mm = 20.0
        text.parent_key = page.id
        text_obj = text_real_object.ensure_text_real_object(scene=scene, entry=text, page=page)
        before_text = _mesh_xy_bounds(text_obj)
        _apply_free_drag(bpy.context, object_selection.text_key(page, text), 9.0, 6.0)
        after_text = _mesh_xy_bounds(text_obj)
        if tuple(round(v, 3) for v in text.free_transform_top_right) != (0.0, 0.0):
            raise AssertionError("テキストのハンドルドラッグで自由変形量が変わっています")
        if tuple(round(v, 3) for v in after_text) != tuple(round(v, 3) for v in before_text):
            raise AssertionError("テキストのハンドルドラッグで実体メッシュが変形しています")

        balloon = page.balloons.add()
        balloon.id = "balloon_free"
        balloon.shape = "rect"
        balloon.x_mm = 20.0
        balloon.y_mm = 30.0
        balloon.width_mm = 42.0
        balloon.height_mm = 24.0
        balloon.line_style = "solid"
        balloon.parent_key = page.id
        balloon_obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=balloon, page=page)
        before_balloon = _curve_xy_bounds(balloon_obj)
        _apply_free_drag(bpy.context, object_selection.balloon_key(page, balloon), 11.0, 7.0)
        after_balloon = _curve_xy_bounds(balloon_obj)
        if tuple(round(v, 3) for v in balloon.free_transform_top_right) != (11.0, 7.0):
            raise AssertionError("フキダシの自由変形量が保存されていません")
        if after_balloon[2] <= before_balloon[2] + 10.0 or after_balloon[3] <= before_balloon[3] + 6.0:
            raise AssertionError("フキダシ実体カーブに自由変形が反映されていません")

        effect_obj, effect_layer = effect_line_op._create_effect_layer(
            bpy.context,
            bounds=(10.0, 15.0, 46.0, 28.0),
            parent_key=str(page.id),
        )
        if effect_obj is None or effect_layer is None:
            raise AssertionError("効果線を作成できません")
        effect_id = str(getattr(scene, "bmanga_active_effect_layer_name", "") or "")
        effect_obj, effect_layer = object_tool_selection.find_effect_layer(effect_id)
        if effect_obj is None or effect_layer is None:
            raise AssertionError("作成後の効果線を安定IDから再取得できません")
        display = effect_line_object.find_effect_display_object(effect_obj)
        if display is None or len(display.data.vertices) == 0:
            raise AssertionError("効果線の表示メッシュが作成されていません")
        before_effect = _mesh_xy_bounds(display)
        effect_key = object_selection.effect_key(effect_layer)
        _apply_free_drag(bpy.context, effect_key, 13.0, 8.0)
        _kind, _page_id, effect_id = object_selection.parse_key(effect_key)
        # 効果線の同期はコントローラー自体を再生成するため、ドラッグ前の
        # StructRNA参照を保持せず、安定IDから現在の実体を取り直す。
        effect_obj, effect_layer = object_tool_selection.find_effect_layer(effect_id)
        if effect_obj is None or effect_layer is None:
            raise AssertionError("自由変形後の効果線を安定IDから再取得できません")
        display = effect_line_object.find_effect_display_object(effect_obj)
        after_effect = _mesh_xy_bounds(display)
        payload = free_transform.effect_payload_for_layer(effect_obj, effect_layer)
        offset = payload["offsets"][free_transform.TOP_RIGHT]
        if tuple(round(v, 3) for v in offset) != (13.0, 8.0):
            raise AssertionError("効果線の自由変形量が保存されていません")
        if after_effect[2] <= before_effect[2] + 1.0:
            raise AssertionError("効果線表示メッシュに自由変形が反映されていません")

        print("BMANGA_OBJECT_FREE_TRANSFORM_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
