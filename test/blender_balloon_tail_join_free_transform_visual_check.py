"""Blender実機用: フキダシしっぽの一体表示と自由変形の目視画像を生成。"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_BALLOON_TAIL_FREE_VISUAL_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_tail_free_visual_"))
OUTPUT_PATH = _OUT_PATH if _OUT_PATH.suffix.lower() == ".png" else _OUT_PATH / "balloon_tail_join_free_transform.png"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_tail_free_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_tail_free_visual"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _mesh_bounds_xy(obj) -> tuple[float, float, float, float]:
    xs = [float(v.co.x) for v in obj.data.vertices]
    ys = [float(v.co.y) for v in obj.data.vertices]
    return min(xs), min(ys), max(xs), max(ys)


def _make_pink_background(center_x: float, center_y: float, scale: float) -> None:
    half = max(0.2, float(scale))
    mesh = bpy.data.meshes.new("tail_free_visual_bg_mesh")
    mesh.from_pydata(
        [
            (center_x - half, center_y - half, -0.004),
            (center_x + half, center_y - half, -0.004),
            (center_x + half, center_y + half, -0.004),
            (center_x - half, center_y + half, -0.004),
        ],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    obj = bpy.data.objects.new("tail_free_visual_bg", mesh)
    bpy.context.scene.collection.objects.link(obj)
    mat = bpy.data.materials.new("tail_free_visual_bg_pink")
    mat.diffuse_color = (1.0, 0.38, 0.86, 1.0)
    mesh.materials.append(mat)


def _hide_page_helpers() -> None:
    for obj in bpy.data.objects:
        name = str(getattr(obj, "name", "") or "").lower()
        if "paper" in name or "guide" in name or "safe" in name:
            obj.hide_render = True
            obj.hide_viewport = True


def _hide_non_visual_objects(allowed_ids: set[str]) -> None:
    for obj in bpy.data.objects:
        name = str(getattr(obj, "name", "") or "")
        if name == "tail_free_visual_bg" or getattr(obj, "type", "") in {"CAMERA", "LIGHT"}:
            continue
        if any(item_id and item_id in name for item_id in allowed_ids):
            continue
        obj.hide_render = True
        obj.hide_viewport = True


def _world_bounds(objects) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for obj in objects:
        if obj is None:
            continue
        try:
            corners = list(getattr(obj, "bound_box", []) or [])
        except Exception:  # noqa: BLE001
            corners = []
        for corner in corners:
            world = obj.matrix_world @ Vector(corner)
            xs.append(float(world.x))
            ys.append(float(world.y))
    if not xs or not ys:
        return 0.095, 0.082, 0.20, 0.16
    return min(xs), min(ys), max(xs), max(ys)


def _set_camera_for_objects(objects) -> tuple[float, float, float]:
    min_x, min_y, max_x, max_y = _world_bounds(objects)
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5
    scale = max(max_x - min_x, max_y - min_y, 0.08) * 1.55
    cam_data = bpy.data.cameras.new("フキダシ確認カメラ")
    cam = bpy.data.objects.new("フキダシ確認カメラ", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = (center_x, center_y, 2.0)
    cam.rotation_euler = (0.0, 0.0, 0.0)
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = scale
    bpy.context.scene.camera = cam
    return center_x, center_y, scale


def _configure_render() -> None:
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:  # noqa: BLE001
        try:
            scene.render.engine = "BLENDER_EEVEE"
        except Exception:  # noqa: BLE001
            pass
    light_data = bpy.data.lights.new("フキダシ確認ライト", "AREA")
    light = bpy.data.objects.new("フキダシ確認ライト", light_data)
    bpy.context.scene.collection.objects.link(light)
    cam = scene.camera
    if cam is not None:
        light.location = (cam.location.x, cam.location.y, 1.2)
    light_data.energy = 500.0
    light_data.size = 1.0
    scene.render.resolution_x = 1200
    scene.render.resolution_y = 760
    scene.render.film_transparent = False
    if scene.world is not None:
        scene.world.color = (1.0, 0.38, 0.86)
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "Medium High Contrast"
    scene.render.filepath = str(OUTPUT_PATH)


def _create_joined_tail_balloon(context, page, parent_key):
    from bmanga_dev_tail_free_visual.operators import balloon_op
    from bmanga_dev_tail_free_visual.utils import balloon_curve_object

    entry = balloon_op._create_balloon_entry(
        context,
        page,
        shape="ellipse",
        x=26.0,
        y=42.0,
        w=62.0,
        h=72.0,
        parent_kind="page",
        parent_key=parent_key,
    )
    entry.id = "tail_join_visual"
    entry.line_style = "solid"
    entry.line_width_mm = 0.9
    entry.fill_color = (1.0, 1.0, 1.0, 1.0)
    entry.line_color = (0.0, 0.0, 0.0, 1.0)
    left = entry.tails.add()
    left.type = "straight"
    left.direction_deg = 195.0
    left.length_mm = 34.0
    left.root_width_mm = 20.0
    left.tip_width_mm = 0.0
    right = entry.tails.add()
    right.type = "straight"
    right.direction_deg = 318.0
    right.length_mm = 30.0
    right.root_width_mm = 18.0
    right.tip_width_mm = 0.0
    obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    assert obj is not None
    return entry, obj


def _create_free_transform_balloon(context, page, parent_key):
    from bmanga_dev_tail_free_visual.operators import balloon_op
    from bmanga_dev_tail_free_visual.utils import balloon_curve_object, free_transform, object_selection
    from bmanga_dev_tail_free_visual.operators import object_tool_op

    entry = balloon_op._create_balloon_entry(
        context,
        page,
        shape="rect",
        x=112.0,
        y=45.0,
        w=50.0,
        h=52.0,
        parent_kind="page",
        parent_key=parent_key,
    )
    entry.id = "free_transform_visual"
    entry.line_style = "solid"
    entry.line_width_mm = 0.9
    entry.fill_color = (1.0, 1.0, 1.0, 1.0)
    entry.line_color = (0.0, 0.0, 0.0, 1.0)
    obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    assert obj is not None

    class _DummyObjectTool:
        def _panel_child_snapshots(self, _page, _panel):
            return []

    key = object_selection.balloon_key(page, entry)
    action = free_transform.action_for_part(free_transform.TOP_RIGHT)
    tool = _DummyObjectTool()
    tool._drag_action = action
    tool._snapshots = object_tool_op.BMANGA_OT_object_tool._make_snapshots(
        tool,
        context,
        [key],
        primary_key=key,
        action=action,
    )
    object_tool_op.BMANGA_OT_object_tool._apply_snapshots(tool, context, 20.0, 18.0)
    return entry, obj


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_tail_free_visual_work_"))
    mod = None
    try:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "TailFreeVisual.bmanga"))
        assert "FINISHED" in result, result

        from bmanga_dev_tail_free_visual.core.work import get_work
        from bmanga_dev_tail_free_visual.utils import balloon_line_mesh
        from bmanga_dev_tail_free_visual.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        parent_key = page_stack_key(page)

        _hide_page_helpers()
        joined, _joined_obj = _create_joined_tail_balloon(context, page, parent_key)
        free_entry, _free_obj = _create_free_transform_balloon(context, page, parent_key)
        context.view_layer.update()

        tail_line = bpy.data.objects.get(f"{balloon_line_mesh.BALLOON_TAIL_MAIN_LINE_MESH_NAME_PREFIX}{joined.id}")
        if tail_line is not None:
            raise AssertionError("結合済みしっぽの分離線メッシュが残っています")
        line_obj = bpy.data.objects.get(f"{balloon_line_mesh.BALLOON_LINE_MESH_NAME_PREFIX}{joined.id}")
        if line_obj is None:
            raise AssertionError("しっぽ結合後の主線メッシュがありません")
        min_x, _min_y, _max_x, _max_y = _mesh_bounds_xy(line_obj)
        if min_x > -0.055:
            raise AssertionError(f"しっぽが主線メッシュの外形に入っていません: min_x={min_x:.4f}")
        if tuple(round(v, 3) for v in free_entry.free_transform_top_right) != (20.0, 18.0):
            raise AssertionError("自由変形の角移動量が保存されていません")

        free_line = bpy.data.objects.get(f"{balloon_line_mesh.BALLOON_LINE_MESH_NAME_PREFIX}{free_entry.id}")
        free_fill = bpy.data.objects.get(f"balloon_fill_mesh_{free_entry.id}")
        joined_fill = bpy.data.objects.get(f"balloon_fill_mesh_{joined.id}")
        center_x, center_y, scale = _set_camera_for_objects([line_obj, joined_fill, free_line, free_fill])
        _make_pink_background(center_x, center_y, scale)
        _hide_non_visual_objects({str(joined.id), str(free_entry.id)})
        _configure_render()
        bpy.ops.render.render(write_still=True)
        print(f"BMANGA_BALLOON_TAIL_JOIN_FREE_TRANSFORM_VISUAL_OK {OUTPUT_PATH}", flush=True)
    finally:
        if mod is not None:
            mod.unregister()


if __name__ == "__main__":
    main()
