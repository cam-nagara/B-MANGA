"""B-MANGA Line: saved inner lines are extracted from evaluated geometry."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, inner_line_cache, inner_lines, outline_setup  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _evaluated_polygon_count(obj: bpy.types.Object) -> int:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return len(mesh.polygons)
    finally:
        evaluated.to_mesh_clear()


def main() -> None:
    b_manga_line.register()
    try:
        _clear_scene()
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
        obj = bpy.context.object
        obj.name = "BML_inner_cache_subdivision_cube"
        subsurf = obj.modifiers.new("BML_test_simple_subdivision", "SUBSURF")
        subsurf.subdivision_type = "SIMPLE"
        subsurf.levels = 1
        subsurf.render_levels = 1

        material = outline_setup.get_line_material(obj, "inner")
        assert inner_lines.apply_inner_lines(
            obj,
            angle=math.radians(30.0),
            thickness=0.01,
            material=material,
            midpoint_factor=0.5,
            midpoint_angle=math.radians(30.0),
            midpoint_jitter_percent=0.0,
        )

        cache_name = str(obj.get(inner_line_cache.CACHE_OBJECT_PROP, "") or "")
        cache = bpy.data.objects.get(cache_name)
        assert cache is not None, "保存済み稜谷線オブジェクトが作られていません"
        assert cache.data is not None and len(cache.data.edges) > 12, (
            "サブディビジョン後の辺から稜谷線が保存されていません",
            len(cache.data.edges) if cache and cache.data else None,
        )

        mod = obj.modifiers.get(core.GN_MODIFIER_NAME)
        assert mod is not None and inner_line_cache.is_cached_modifier(mod), (
            "稜谷線表示が保存済み線ノードになっていません"
        )
        mod.show_viewport = False
        surface_polygons = _evaluated_polygon_count(obj)
        mod.show_viewport = True
        line_polygons = _evaluated_polygon_count(obj)
        assert line_polygons > surface_polygons, (
            "保存済み稜谷線が表示メッシュへ反映されていません",
            surface_polygons,
            line_polygons,
        )

        before_edges = len(cache.data.edges)
        assert inner_lines.update_parameters(
            obj,
            angle=math.radians(80.0),
            thickness=0.02,
            midpoint_factor=-0.25,
        )
        assert len(cache.data.edges) == before_edges, (
            "表示設定更新だけで保存済み稜谷線が作り直されています"
        )

        print("[PASS] inner-line cache uses evaluated geometry")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
