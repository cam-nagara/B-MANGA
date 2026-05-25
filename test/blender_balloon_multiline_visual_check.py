"""Blender実機用: フキダシ多重線と自由変形の目視確認画像を生成。"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BNAME_BALLOON_MULTILINE_VISUAL_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bname_balloon_multiline_visual_"))
OUTPUT_PATH = _OUT_PATH if _OUT_PATH.suffix.lower() == ".png" else _OUT_PATH / "balloon_multiline_visual.png"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_balloon_multiline_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_balloon_multiline_visual"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_camera(center_x_m: float, center_y_m: float, scale_m: float) -> None:
    camera_data = bpy.data.cameras.new("多重線確認カメラ")
    camera = bpy.data.objects.new("多重線確認カメラ", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = (center_x_m, center_y_m, 2.0)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = scale_m
    bpy.context.scene.camera = camera


def _set_camera_for_entries(objects_and_entries) -> None:
    xs: list[float] = []
    ys: list[float] = []
    for obj, entry in objects_and_entries:
        half_w = max(0.01, float(getattr(entry, "width_mm", 0.0) or 0.0) * 0.0005)
        half_h = max(0.01, float(getattr(entry, "height_mm", 0.0) or 0.0) * 0.0005)
        xs.extend([float(obj.location.x) - half_w, float(obj.location.x) + half_w])
        ys.extend([float(obj.location.y) - half_h, float(obj.location.y) + half_h])
    center_x = (min(xs) + max(xs)) * 0.5
    center_y = (min(ys) + max(ys)) * 0.5
    scale = max(max(xs) - min(xs), max(ys) - min(ys)) * 1.45
    _set_camera(center_x, center_y, scale)


def _configure_multiline(entry, *, direction: str = "outside") -> None:
    entry.line_style = "double"
    entry.line_width_mm = 0.22
    entry.multi_line_count = 4
    entry.multi_line_width_mm = 0.35
    entry.multi_line_spacing_mm = 1.2
    entry.multi_line_width_scale_percent = 80.0
    entry.multi_line_direction = direction
    entry.line_color = (0.0, 0.0, 0.0, 1.0)
    entry.fill_opacity = 100.0


def _material_slot_names(obj) -> list[str]:
    data = getattr(obj, "data", None)
    return [str(getattr(mat, "name", "") or "") for mat in getattr(data, "materials", []) or []]


def _assert_balloon_material_order(obj) -> None:
    names = _material_slot_names(obj)
    assert len(names) >= 4, f"フキダシ素材スロットが不足しています: {names}"
    assert "BName_Balloon_Fill_" in names[0], f"塗りが最背面スロットではありません: {names}"
    assert "BName_Balloon_Outer_Edge_" in names[1], f"外側フチが塗りの直後ではありません: {names}"
    assert "BName_Balloon_Inner_Edge_" in names[2], f"内側フチが外側フチの直後ではありません: {names}"
    assert "BName_Balloon_Curve_" in names[3], f"主線が最前面スロットではありません: {names}"
    for index in (0, 3):
        material = obj.data.materials[index]
        assert str(getattr(material, "blend_method", "") or "") in {"OPAQUE", "HASHED"}, (
            f"不透明な塗り/主線が半透明描画になっています: {index}, "
            f"{getattr(material, 'name', '')}, {getattr(material, 'blend_method', '')}"
        )


def _assert_cloud_has_no_stale_thorn_paths(obj) -> None:
    data = getattr(obj, "data", None)
    assert data is not None
    for spline in getattr(data, "splines", []) or []:
        if str(getattr(spline, "type", "") or "") == "BEZIER":
            points = list(getattr(spline, "bezier_points", []) or [])
            if len(points) >= 6 and all(
                str(getattr(point, "handle_left_type", "") or "") == "VECTOR"
                and str(getattr(point, "handle_right_type", "") or "") == "VECTOR"
                for point in points
            ):
                raise AssertionError("雲の輪郭にトゲ直線の制御点が残っています")
            continue
        if str(getattr(spline, "type", "") or "") != "POLY":
            continue
        points = list(getattr(spline, "points", []) or [])
        if not points:
            continue
        role = float(getattr(points[0], "radius", 0.0) or 0.0)
        assert abs(role - 500.0) > 0.001, "雲の多重線にトゲ直線の主線面が混入しています"
        if role <= 50.0:
            continue
        assert bool(getattr(spline, "use_cyclic_u", False)), "雲の多重線に開いたトゲ状パスが混入しています"


def _poly_role_counts(obj) -> dict[int, int]:
    counts: dict[int, int] = {}
    data = getattr(obj, "data", None)
    if data is None:
        return counts
    for spline in getattr(data, "splines", []) or []:
        if str(getattr(spline, "type", "") or "") != "POLY":
            continue
        points = list(getattr(spline, "points", []) or [])
        if not points:
            continue
        role = int(round(float(getattr(points[0], "radius", 0.0) or 0.0)))
        counts[role] = counts.get(role, 0) + 1
    return counts


def _assert_sharp_line_fill_role(obj, *, expected: bool) -> None:
    counts = _poly_role_counts(obj)
    has_role = counts.get(500, 0) > 0
    assert has_role == expected, f"トゲ直線の主線面の有無が不正です: expected={expected}, roles={counts}"


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_balloon_multiline_visual_work_"))
    mod = None
    try:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "BalloonMultiLineVisual.bname"))
        assert "FINISHED" in result, result

        from bname_dev_balloon_multiline_visual.core.work import get_work
        from bname_dev_balloon_multiline_visual.operators import balloon_op
        from bname_dev_balloon_multiline_visual.utils import balloon_curve_object
        from bname_dev_balloon_multiline_visual.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        parent_key = page_stack_key(page)

        ellipse = balloon_op._create_balloon_entry(
            context,
            page,
            shape="ellipse",
            x=18.0,
            y=38.0,
            w=54.0,
            h=34.0,
            parent_kind="page",
            parent_key=parent_key,
        )
        _configure_multiline(ellipse, direction="outside")
        ellipse.fill_color = (1.0, 1.0, 1.0, 1.0)

        thorn = balloon_op._create_balloon_entry(
            context,
            page,
            shape="thorn",
            x=88.0,
            y=34.0,
            w=70.0,
            h=48.0,
            parent_kind="page",
            parent_key=parent_key,
        )
        _configure_multiline(thorn, direction="both")
        thorn.line_width_mm = 3.0
        thorn.fill_color = (0.78, 1.0, 0.83, 1.0)
        thorn.thorn_multi_line_valley_width_mm = 0.18
        thorn.thorn_multi_line_peak_width_mm = 0.48
        thorn.thorn_multi_line_length_scale_percent = 72.0

        behind_thorn = balloon_op._create_balloon_entry(
            context,
            page,
            shape="thorn",
            x=120.0,
            y=92.0,
            w=54.0,
            h=54.0,
            parent_kind="page",
            parent_key=parent_key,
        )
        _configure_multiline(behind_thorn, direction="outside")
        behind_thorn.line_width_mm = 0.3
        behind_thorn.multi_line_width_mm = 0.22
        behind_thorn.multi_line_spacing_mm = 1.0
        behind_thorn.fill_color = (0.96, 0.96, 0.96, 1.0)

        cloud = balloon_op._create_balloon_entry(
            context,
            page,
            shape="cloud",
            x=128.0,
            y=98.0,
            w=42.0,
            h=42.0,
            parent_kind="page",
            parent_key=parent_key,
        )
        _configure_multiline(cloud, direction="outside")
        cloud.line_width_mm = 0.3
        cloud.multi_line_width_mm = 0.28
        cloud.multi_line_spacing_mm = 0.7
        cloud.fill_color = (1.0, 1.0, 1.0, 1.0)

        switched_cloud = balloon_op._create_balloon_entry(
            context,
            page,
            shape="thorn",
            x=170.0,
            y=122.0,
            w=32.0,
            h=32.0,
            parent_kind="page",
            parent_key=parent_key,
        )
        _configure_multiline(switched_cloud, direction="outside")
        switched_cloud.multi_line_width_mm = 0.24
        switched_obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=switched_cloud, page=page)
        assert switched_obj is not None and switched_obj.type == "CURVE"
        switched_cloud.shape = "cloud"
        switched_obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=switched_cloud, page=page)
        assert switched_obj is not None and switched_obj.type == "CURVE"
        _assert_cloud_has_no_stale_thorn_paths(switched_obj)
        _assert_sharp_line_fill_role(switched_obj, expected=False)
        switched_obj.hide_render = True
        switched_obj.hide_viewport = True

        freeform = balloon_op._create_balloon_entry(
            context,
            page,
            shape="rect",
            x=50.0,
            y=98.0,
            w=42.0,
            h=24.0,
            parent_kind="page",
            parent_key=parent_key,
        )
        _configure_multiline(freeform, direction="inside")
        freeform.fill_color = (1.0, 0.82, 0.92, 1.0)
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=freeform, page=page)
        assert obj is not None
        obj.data.splines[0].bezier_points[1].co.x += 0.008
        obj.data.splines[0].bezier_points[2].co.y += 0.006
        balloon_op._set_balloon_rect(page, freeform, 42.0, 92.0, 62.0, 40.0)

        objects_and_entries = []
        for entry in (ellipse, thorn, behind_thorn, cloud, freeform):
            obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
            assert obj is not None and obj.type == "CURVE"
            _assert_balloon_material_order(obj)
            if str(getattr(entry, "shape", "") or "") == "cloud":
                _assert_cloud_has_no_stale_thorn_paths(obj)
                _assert_sharp_line_fill_role(obj, expected=False)
            if str(getattr(entry, "shape", "") or "") == "thorn":
                _assert_sharp_line_fill_role(obj, expected=True)
            objects_and_entries.append((obj, entry))

        _set_camera_for_entries(objects_and_entries)
        scene = context.scene
        try:
            scene.render.engine = "BLENDER_EEVEE"
        except Exception:
            pass
        scene.world = scene.world or bpy.data.worlds.new("World")
        scene.world.color = (0.55, 0.55, 0.55)
        scene.render.resolution_x = 1000
        scene.render.resolution_y = 760
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
        scene.render.filepath = str(OUTPUT_PATH)
        result = bpy.ops.render.render(write_still=True)
        assert "FINISHED" in result, result
        print(f"BNAME_BALLOON_MULTILINE_VISUAL_OK out={OUTPUT_PATH}", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
