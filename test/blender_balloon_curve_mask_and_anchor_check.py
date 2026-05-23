"""Blender 実機用: コマ内フキダシのマスク混入と制御点過多を検証。"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_balloon_curve_mask_anchor",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_balloon_curve_mask_anchor"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _evaluated_bounds(obj) -> tuple[float, float, float]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        coords = [vertex.co.copy() for vertex in mesh.vertices]
        assert coords, "表示結果の頂点がありません"
        min_x = min(co.x for co in coords)
        max_x = max(co.x for co in coords)
        min_y = min(co.y for co in coords)
        max_y = max(co.y for co in coords)
        min_z = min(co.z for co in coords)
        max_z = max(co.z for co in coords)
        return max_x - min_x, max_y - min_y, max_z - min_z
    finally:
        evaluated.to_mesh_clear()


def _material_names(obj) -> set[str]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return {str(mat.name) for mat in getattr(mesh, "materials", []) if mat is not None}
    finally:
        evaluated.to_mesh_clear()


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_balloon_curve_mask_anchor_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "BalloonMaskAnchor.bname"))
        assert "FINISHED" in result, result

        from bname_dev_balloon_curve_mask_anchor.core.work import get_work
        from bname_dev_balloon_curve_mask_anchor.utils import balloon_curve_object
        from bname_dev_balloon_curve_mask_anchor.utils import coma_plane
        from bname_dev_balloon_curve_mask_anchor.utils import mask_apply
        from bname_dev_balloon_curve_mask_anchor.utils.layer_hierarchy import coma_stack_key

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        coma = page.comas[0]
        coma.shape_type = "rect"
        coma.rect_x_mm = 20.0
        coma.rect_y_mm = 35.0
        coma.rect_width_mm = 120.0
        coma.rect_height_mm = 150.0
        coma.background_color = (1.0, 1.0, 1.0, 1.0)
        parent_key = coma_stack_key(page, coma)
        coma_plane.ensure_coma_plane(scene, work, page, coma)
        coma_plane.ensure_coma_mask(scene, work, page, coma)

        entry = page.balloons.add()
        entry.id = "balloon_mask_anchor"
        entry.title = "フキダシ"
        entry.shape = "cloud"
        entry.x_mm = 58.0
        entry.y_mm = 80.0
        entry.width_mm = 45.0
        entry.height_mm = 36.0
        entry.parent_kind = "coma"
        entry.parent_key = parent_key
        entry.fill_color = (0.8, 1.0, 0.85, 1.0)
        entry.fill_opacity = 100.0
        entry.opacity = 100.0
        entry.line_width_mm = 1.2

        obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
        assert obj is not None and obj.type == "CURVE", "フキダシ実体がカーブではありません"
        mask_apply.apply_mask_to_layer_object(obj)
        bpy.context.view_layer.update()

        body_points = len(obj.data.splines[0].bezier_points)
        assert body_points <= 32, f"雲フキダシの制御点が細かすぎます: {body_points}"
        assert body_points >= 6, f"雲フキダシの制御点が不足しています: {body_points}"
        width_m, height_m, depth_m = _evaluated_bounds(obj)
        assert width_m < 0.07, f"コマ形状がフキダシ表示に混入しています: width={width_m}"
        assert height_m < 0.06, f"コマ形状がフキダシ表示に混入しています: height={height_m}"
        assert depth_m < 0.01, f"コママスクの厚みがフキダシ表示に混入しています: depth={depth_m}"
        leaked = {name for name in _material_names(obj) if "ComaPlane" in name}
        assert not leaked, f"コマ用素材がフキダシ表示に混入しています: {sorted(leaked)}"

        entry.shape = "ellipse"
        balloon_curve_object.ensure_balloon_curve_object(
            scene=scene,
            entry=entry,
            page=page,
            force_regenerate=True,
        )
        assert len(obj.data.splines[0].bezier_points) == 4, "楕円フキダシが4点ベジェになっていません"
        print("BNAME_BALLOON_CURVE_MASK_ANCHOR_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
