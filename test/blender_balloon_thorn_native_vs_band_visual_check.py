"""Blender実機用: トゲ(鋭角)フキダシの主線について「標準経路 (CurveToMesh+円プロファイル)」と
現行「輪郭差分の帯 方式」を、線幅を変えて並べて画像出力する確認用スクリプト。

目的: 過去の「鋭角部分やコマ枠で線幅が一定に保たれない」症状が、
標準経路 (CurveToMesh) に戻したときに実際にどう出るかを確認する。
雲側の追加検証 (blender_balloon_cloud_native_vs_capsule_visual_check.py) と対になる。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_THORN_NATIVE_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_thorn_native_vs_band_"))

LINE_WIDTHS_MM = (0.3, 0.5, 1.0, 2.0, 4.0, 7.0)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_thorn_native_check",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_thorn_native_check"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_ortho_camera(center_x_m: float, center_y_m: float, scale_m: float) -> None:
    camera_data = bpy.data.cameras.new("確認カメラ")
    camera = bpy.data.objects.new("確認カメラ", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = (center_x_m, center_y_m, 2.0)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = scale_m
    bpy.context.scene.camera = camera


def _render_to(path: Path, *, width_px: int = 1600, height_px: int = 900) -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in {item.identifier for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items} else "BLENDER_EEVEE"
    scene.render.resolution_x = width_px
    scene.render.resolution_y = height_px
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(path)
    scene.render.film_transparent = False
    bpy.ops.render.render(write_still=True)


def _force_line_method_input(obj, *, filled_line_enabled: bool) -> None:
    for modifier in getattr(obj, "modifiers", []) or []:
        if modifier.type != "NODES":
            continue
        node_group = getattr(modifier, "node_group", None)
        if node_group is None:
            continue
        target_identifier = None
        for item in getattr(node_group.interface, "items_tree", []) or []:
            if getattr(item, "in_out", "") != "INPUT":
                continue
            if str(getattr(item, "name", "") or "") == "線を面で生成":
                target_identifier = getattr(item, "identifier", None)
                break
        if target_identifier is None:
            continue
        modifier[target_identifier] = bool(filled_line_enabled)
        try:
            obj.update_tag()
        except Exception:
            pass


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_thorn_native_work_"))
    _OUT_PATH.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "ThornNativeCheck.bmanga"))
    assert "FINISHED" in result, result

    from bmanga_dev_thorn_native_check.core.work import get_work
    from bmanga_dev_thorn_native_check.operators import balloon_op
    from bmanga_dev_thorn_native_check.utils import balloon_curve_object
    from bmanga_dev_thorn_native_check.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = get_work(context)
    assert work is not None and work.loaded
    page = work.pages[0]
    parent_key = page_stack_key(page)

    spacing_mm = 60.0
    base_x_mm = 20.0
    base_y_mm = 60.0
    width_mm = 48.0
    height_mm = 48.0

    objects: list[tuple[bpy.types.Object, float]] = []
    for index, line_w_mm in enumerate(LINE_WIDTHS_MM):
        x_mm = base_x_mm + spacing_mm * index
        entry = balloon_op._create_balloon_entry(
            context,
            page,
            shape="thorn",
            x=x_mm,
            y=base_y_mm,
            w=width_mm,
            h=height_mm,
            parent_kind="page",
            parent_key=parent_key,
        )
        entry.line_style = "solid"
        entry.line_width_mm = float(line_w_mm)
        entry.line_color = (1.0, 0.15, 0.15, 1.0)
        entry.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry.fill_opacity = 100.0
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        assert obj is not None and obj.type == "CURVE"
        objects.append((obj, float(line_w_mm)))

    xs = [float(obj.location.x) for obj, _ in objects]
    ys = [float(obj.location.y) for obj, _ in objects]
    half_w = float(width_mm) * 0.0005 * 1.2
    half_h = float(height_mm) * 0.0005 * 1.2
    center_x = (min(xs) - half_w + max(xs) + half_w) * 0.5
    center_y = (min(ys) - half_h + max(ys) + half_h) * 0.5
    scale = (max(xs) + half_w) - (min(xs) - half_w) + 0.02
    _set_ortho_camera(center_x, center_y, scale)

    band_path = _OUT_PATH / "thorn_main_line__current_band.png"
    _render_to(band_path)
    print(f"[OUT] band (current): {band_path}")

    for obj, _ in objects:
        _force_line_method_input(obj, filled_line_enabled=False)
    native_path = _OUT_PATH / "thorn_main_line__native_curve_to_mesh.png"
    _render_to(native_path)
    print(f"[OUT] native (CurveToMesh): {native_path}")

    print(f"[DONE] 出力ディレクトリ: {_OUT_PATH}")


if __name__ == "__main__":
    main()
