"""Blender実機用: 既存テスト `blender_balloon_multiline_visual_check.py` の実績ある
シーンをそのまま使い、最後にフキダシ modifier の `線を面で生成` を全部 False へ
切り替えて再レンダする。2枚を比較することで、CurveToMesh+円プロファイル経路に
戻したときに過去の地雷 (多重線・鋭角・滑らか形状・コマ内マスク・しっぽ・前後重なり)
で破綻が出ないかを確認する。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BNAME_BALLOON_FULL_FEATURE_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bname_balloon_full_feature_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_full_feature_check",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_full_feature_check"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_camera(center_x_m, center_y_m, scale_m):
    cam_data = bpy.data.cameras.new("確認カメラ")
    cam = bpy.data.objects.new("確認カメラ", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = (center_x_m, center_y_m, 2.0)
    cam.rotation_euler = (0.0, 0.0, 0.0)
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = scale_m
    bpy.context.scene.camera = cam


def _set_camera_for_entries(objects_and_entries):
    xs, ys = [], []
    for obj, entry in objects_and_entries:
        half_w = max(0.01, float(getattr(entry, "width_mm", 0.0) or 0.0) * 0.0005)
        half_h = max(0.01, float(getattr(entry, "height_mm", 0.0) or 0.0) * 0.0005)
        xs.extend([float(obj.location.x) - half_w, float(obj.location.x) + half_w])
        ys.extend([float(obj.location.y) - half_h, float(obj.location.y) + half_h])
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = max(max(xs) - min(xs), max(ys) - min(ys)) * 1.45
    _set_camera(cx, cy, scale)


def _configure_multiline(entry, *, direction="outside"):
    entry.line_style = "double"
    entry.line_width_mm = 0.22
    entry.multi_line_count = 4
    entry.multi_line_width_mm = 0.35
    entry.multi_line_spacing_mm = 1.2
    entry.multi_line_width_scale_percent = 80.0
    entry.multi_line_direction = direction
    entry.line_color = (0.0, 0.0, 0.0, 1.0)
    entry.fill_opacity = 100.0


def _toggle_filled_line(obj, *, filled_line_enabled):
    for modifier in getattr(obj, "modifiers", []) or []:
        if modifier.type != "NODES":
            continue
        node_group = getattr(modifier, "node_group", None)
        if node_group is None:
            continue
        for item in getattr(node_group.interface, "items_tree", []) or []:
            if getattr(item, "in_out", "") != "INPUT":
                continue
            if str(getattr(item, "name", "") or "") == "線を面で生成":
                identifier = getattr(item, "identifier", None)
                if identifier:
                    modifier[identifier] = bool(filled_line_enabled)
                break
        try:
            obj.update_tag()
        except Exception:
            pass


def _do_render(scene, path):
    scene.render.filepath = str(path)
    result = bpy.ops.render.render(write_still=True)
    assert "FINISHED" in result, result


def main():
    temp_root = Path(tempfile.mkdtemp(prefix="bname_balloon_full_feature_work_"))
    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "FullFeatureCheck.bname"))
        assert "FINISHED" in result, result

        from bname_dev_full_feature_check.core.work import get_work
        from bname_dev_full_feature_check.operators import balloon_op
        from bname_dev_full_feature_check.utils import balloon_curve_object
        from bname_dev_full_feature_check.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        parent_key = page_stack_key(page)

        # --- 以下は blender_balloon_multiline_visual_check.py の main の実績シーン構築 ---
        ellipse = balloon_op._create_balloon_entry(
            context, page, shape="ellipse", x=18.0, y=38.0, w=54.0, h=34.0,
            parent_kind="page", parent_key=parent_key,
        )
        _configure_multiline(ellipse, direction="outside")
        ellipse.fill_color = (1.0, 1.0, 1.0, 1.0)

        thorn = balloon_op._create_balloon_entry(
            context, page, shape="thorn", x=88.0, y=34.0, w=70.0, h=48.0,
            parent_kind="page", parent_key=parent_key,
        )
        _configure_multiline(thorn, direction="both")
        thorn.line_width_mm = 3.0
        thorn.fill_color = (0.78, 1.0, 0.83, 1.0)
        thorn.thorn_multi_line_valley_width_mm = 0.18
        thorn.thorn_multi_line_peak_width_mm = 0.48
        thorn.thorn_multi_line_length_scale_percent = 72.0

        behind_thorn = balloon_op._create_balloon_entry(
            context, page, shape="thorn", x=120.0, y=92.0, w=54.0, h=54.0,
            parent_kind="page", parent_key=parent_key,
        )
        _configure_multiline(behind_thorn, direction="outside")
        behind_thorn.line_width_mm = 0.3
        behind_thorn.multi_line_width_mm = 0.22
        behind_thorn.multi_line_spacing_mm = 1.0
        behind_thorn.fill_color = (0.96, 0.96, 0.96, 1.0)

        cloud = balloon_op._create_balloon_entry(
            context, page, shape="cloud", x=128.0, y=98.0, w=42.0, h=42.0,
            parent_kind="page", parent_key=parent_key,
        )
        _configure_multiline(cloud, direction="outside")
        cloud.line_width_mm = 0.3
        cloud.multi_line_width_mm = 0.28
        cloud.multi_line_spacing_mm = 0.7
        cloud.fill_color = (1.0, 1.0, 1.0, 1.0)

        thick_cloud = balloon_op._create_balloon_entry(
            context, page, shape="cloud", x=172.0, y=72.0, w=38.0, h=38.0,
            parent_kind="page", parent_key=parent_key,
        )
        _configure_multiline(thick_cloud, direction="outside")
        thick_cloud.line_style = "solid"
        thick_cloud.line_width_mm = 7.0
        thick_cloud.fill_color = (1.0, 0.93, 0.98, 1.0)

        freeform = balloon_op._create_balloon_entry(
            context, page, shape="rect", x=50.0, y=98.0, w=42.0, h=24.0,
            parent_kind="page", parent_key=parent_key,
        )
        _configure_multiline(freeform, direction="inside")
        freeform.fill_color = (1.0, 0.82, 0.92, 1.0)
        obj_tmp = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=freeform, page=page)
        assert obj_tmp is not None
        obj_tmp.data.splines[0].bezier_points[1].co.x += 0.008
        obj_tmp.data.splines[0].bezier_points[2].co.y += 0.006
        balloon_op._set_balloon_rect(page, freeform, 42.0, 92.0, 62.0, 40.0)

        objects_and_entries = []
        for entry in (ellipse, thorn, behind_thorn, cloud, thick_cloud, freeform):
            obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
            assert obj is not None and obj.type == "CURVE"
            objects_and_entries.append((obj, entry))
        # --- ここまで既存テストと同じ ---

        _set_camera_for_entries(objects_and_entries)
        try:
            scene.render.engine = "BLENDER_EEVEE"
        except Exception:
            pass
        scene.world = scene.world or bpy.data.worlds.new("World")
        scene.world.color = (0.55, 0.55, 0.55)
        scene.render.resolution_x = 1600
        scene.render.resolution_y = 1200
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
        scene.render.image_settings.file_format = "PNG"

        # 1枚目: 現方式
        face_path = _OUT_PATH / "full_feature__current_face.png"
        _do_render(scene, face_path)
        print(f"[OUT] current (face): {face_path}", flush=True)

        # 2枚目: 標準経路
        for obj, _ in objects_and_entries:
            _toggle_filled_line(obj, filled_line_enabled=False)
        native_path = _OUT_PATH / "full_feature__native_curve_to_mesh.png"
        _do_render(scene, native_path)
        print(f"[OUT] native (CurveToMesh): {native_path}", flush=True)

        print(f"[DONE] 出力: {_OUT_PATH}", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
