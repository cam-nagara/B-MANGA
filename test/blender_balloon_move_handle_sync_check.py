"""フキダシ移動時に表示実体と操作枠がずれないことを確認."""

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
        "bname_dev_balloon_move_sync",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_balloon_move_sync"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _find_owned_object(prop_name: str, owner_id: str):
    for obj in bpy.data.objects:
        if str(obj.get(prop_name, "") or "") == owner_id:
            return obj
    return None


def _assert_local_identity(obj, label: str) -> None:
    loc = tuple(round(float(v), 6) for v in obj.location)
    rot = tuple(round(float(v), 6) for v in obj.rotation_euler)
    scale = tuple(round(float(v), 6) for v in obj.scale)
    if loc != (0.0, 0.0, 0.0):
        raise AssertionError(f"{label}: 表示実体の位置が戻っていません: {loc}")
    if rot != (0.0, 0.0, 0.0):
        raise AssertionError(f"{label}: 表示実体の回転が戻っていません: {rot}")
    if scale != (1.0, 1.0, 1.0):
        raise AssertionError(f"{label}: 表示実体の拡大縮小が戻っていません: {scale}")


def _assert_quad_shift(before: dict[str, tuple[float, float]], after: dict[str, tuple[float, float]], dx: float, dy: float) -> None:
    for corner, point in before.items():
        shifted = after.get(corner)
        if shifted is None:
            raise AssertionError(f"操作枠の角が欠落しています: {corner}")
        if abs((shifted[0] - point[0]) - dx) > 1.0e-4 or abs((shifted[1] - point[1]) - dy) > 1.0e-4:
            raise AssertionError(f"操作枠が移動量に追従していません: {corner} {point} -> {shifted}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_balloon_move_sync_"))
    mod = None
    try:
        mod = _load_addon()
        bpy.context.scene.bname_overview_mode = True
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "MoveSync.bname"))
        if "FINISHED" not in result:
            raise AssertionError("作品作成に失敗しました")

        from bname_dev_balloon_move_sync.operators import balloon_op, object_tool_selection
        from bname_dev_balloon_move_sync.utils import (
            balloon_curve_object,
            balloon_fill_mesh,
            balloon_line_mesh,
            free_transform,
            object_selection,
            object_state_sync,
            page_grid,
        )

        scene = bpy.context.scene
        work = scene.bname_work
        page = work.pages[0]
        balloon = page.balloons.add()
        balloon.id = "balloon_move_sync"
        balloon.title = "移動同期"
        balloon.shape = "rect"
        balloon.x_mm = 20.0
        balloon.y_mm = 30.0
        balloon.width_mm = 42.0
        balloon.height_mm = 24.0
        balloon.parent_kind = "page"
        balloon.parent_key = page.id
        balloon.line_style = "solid"
        balloon.line_width_mm = 0.5
        balloon.fill_color = (1.0, 1.0, 1.0, 1.0)

        body = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=balloon, page=page)
        if body is None:
            raise AssertionError("フキダシ本体を作成できません")

        fill = _find_owned_object(balloon_fill_mesh.PROP_BALLOON_FILL_MESH_OWNER_ID, balloon.id)
        line = _find_owned_object(balloon_line_mesh.PROP_BALLOON_LINE_MESH_OWNER_ID, balloon.id)
        if fill is None or line is None:
            raise AssertionError("フキダシの表示実体が作成されていません")

        for label, obj in (("塗り", fill), ("線", line)):
            obj.location = (0.021, -0.013, 0.004)
            obj.rotation_euler = (0.0, 0.0, 0.2)
            obj.scale = (1.4, 0.7, 1.0)
            obj.hide_select = False
            obj.select_set(True)
            if not object_state_sync.sync_from_blender_object(scene, obj):
                raise AssertionError(f"{label}: 表示実体の誤移動を検出できません")
            _assert_local_identity(obj, label)
            if obj.parent is not body:
                raise AssertionError(f"{label}: 表示実体がフキダシ本体へ戻っていません")
            if obj.select_get():
                raise AssertionError(f"{label}: 表示実体が選択されたままです")

        balloon.free_transform_enabled = True
        balloon.free_transform_top_right = (35.0, 25.0)
        if balloon_op._balloon_hit_part(balloon, 80.0, 60.0) != "body":
            raise AssertionError("自由変形後の見えているフキダシ部分を移動対象として拾えていません")

        key = object_selection.balloon_key(page, balloon)
        before_rect = object_tool_selection.selection_bounds_for_key(bpy.context, key)
        before_quad = free_transform.entry_quad(balloon, before_rect)
        if before_quad is None:
            raise AssertionError("移動前の操作枠を取得できません")
        balloon_op._move_balloon_with_texts(page, balloon, balloon.x_mm + 18.0, balloon.y_mm + 11.0)
        after_rect = object_tool_selection.selection_bounds_for_key(bpy.context, key)
        after_quad = free_transform.entry_quad(balloon, after_rect)
        if after_quad is None:
            raise AssertionError("移動後の操作枠を取得できません")
        _assert_quad_shift(before_quad, after_quad, 18.0, 11.0)
        ox_mm, oy_mm = page_grid.page_total_offset_mm(work, scene, 0)
        if abs(float(body.location.x) * 1000.0 - (ox_mm + balloon.x_mm + balloon.width_mm * 0.5)) > 1.0e-4:
            raise AssertionError("フキダシ本体が移動後の位置へ追従していません")
        if abs(float(body.location.y) * 1000.0 - (oy_mm + balloon.y_mm + balloon.height_mm * 0.5)) > 1.0e-4:
            raise AssertionError("フキダシ本体が移動後の位置へ追従していません")

        body.location.x += 0.017
        body.location.y -= 0.009
        if not object_state_sync.sync_from_blender_object(scene, body):
            raise AssertionError("Blender標準操作によるフキダシ本体移動を取り込めません")
        latest_rect = object_tool_selection.selection_bounds_for_key(bpy.context, key)
        ox_mm, oy_mm = page_grid.page_total_offset_mm(work, scene, 0)
        if abs(latest_rect.x - (ox_mm + balloon.x_mm)) > 1.0e-4 or abs(latest_rect.y - (oy_mm + balloon.y_mm)) > 1.0e-4:
            raise AssertionError("取り込み後の操作枠が作品データと一致していません")

        print("BNAME_BALLOON_MOVE_HANDLE_SYNC_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
