"""Blender実機用: 内側へ向いたフキダシしっぽを凹みとして描画する回帰確認。"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_ENV = os.environ.get("BMANGA_BALLOON_TAIL_INWARD_VISUAL_OUT", "")
OUT_PATH = Path(OUT_ENV) if OUT_ENV else ROOT / ".codex" / "visual" / "balloon_tail_reference_sample" / "bmanga_tail_inward_check.png"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_tail_inward_check",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_tail_inward_check"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _mesh_bounds_xy(obj) -> tuple[float, float, float, float]:
    xs = [float(v.co.x) for v in obj.data.vertices]
    ys = [float(v.co.y) for v in obj.data.vertices]
    return min(xs), min(ys), max(xs), max(ys)


def _point_in_tri(px: float, py: float, a, b, c) -> bool:
    v0x = c.x - a.x
    v0y = c.y - a.y
    v1x = b.x - a.x
    v1y = b.y - a.y
    v2x = px - a.x
    v2y = py - a.y
    dot00 = v0x * v0x + v0y * v0y
    dot01 = v0x * v1x + v0y * v1y
    dot02 = v0x * v2x + v0y * v2y
    dot11 = v1x * v1x + v1y * v1y
    dot12 = v1x * v2x + v1y * v2y
    denom = dot00 * dot11 - dot01 * dot01
    if abs(denom) <= 1.0e-14:
        return False
    inv = 1.0 / denom
    u = (dot11 * dot02 - dot01 * dot12) * inv
    v = (dot00 * dot12 - dot01 * dot02) * inv
    return u >= -1.0e-6 and v >= -1.0e-6 and (u + v) <= 1.0 + 1.0e-6


def _mesh_covers_point(obj, point_xy: tuple[float, float]) -> bool:
    px, py = point_xy
    verts = obj.data.vertices
    for poly in obj.data.polygons:
        indices = list(poly.vertices)
        if len(indices) != 3:
            continue
        a, b, c = (verts[i].co for i in indices)
        if _point_in_tri(px, py, a, b, c):
            return True
    return False


def _create_balloon(context, page, parent_key):
    from bmanga_dev_tail_inward_check.operators import balloon_op
    from bmanga_dev_tail_inward_check.utils import balloon_curve_object

    entry = balloon_op._create_balloon_entry(
        context,
        page,
        shape="ellipse",
        x=40.0,
        y=42.0,
        w=84.0,
        h=102.0,
        parent_kind="page",
        parent_key=parent_key,
    )
    entry.id = "tail_inward_check"
    entry.line_style = "solid"
    entry.line_width_mm = 0.82
    entry.fill_color = (1.0, 1.0, 1.0, 1.0)
    entry.line_color = (0.0, 0.0, 0.0, 1.0)

    inward = entry.tails.add()
    inward.type = "straight"
    inward.root_width_mm = 20.0
    inward.tip_width_mm = 0.0
    inward.custom_points_enabled = True
    inward.start_x_mm = -12.0
    inward.start_y_mm = 35.0
    inward.end_x_mm = 38.0
    inward.end_y_mm = 36.0

    outward = entry.tails.add()
    outward.type = "straight"
    outward.root_width_mm = 20.0
    outward.tip_width_mm = 0.0
    outward.custom_points_enabled = True
    outward.start_x_mm = 66.0
    outward.start_y_mm = 31.0
    outward.end_x_mm = 96.0
    outward.end_y_mm = 12.0

    obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    assert obj is not None
    return entry


def _hide_page_helpers() -> None:
    for obj in bpy.data.objects:
        name = str(getattr(obj, "name", "") or "").lower()
        if "paper" in name or "guide" in name or "safe" in name:
            obj.hide_viewport = True
            obj.hide_render = True


def main() -> None:
    mod = None
    try:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        temp_root = Path(tempfile.mkdtemp(prefix="bmanga_tail_inward_check_"))
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "TailInwardCheck.bmanga"))
        assert "FINISHED" in result, result

        from bmanga_dev_tail_inward_check.core.work import get_work
        from bmanga_dev_tail_inward_check.utils import balloon_line_mesh
        from bmanga_dev_tail_inward_check.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        _hide_page_helpers()
        entry = _create_balloon(context, page, page_stack_key(page))
        context.view_layer.update()

        tail_line = bpy.data.objects.get(f"{balloon_line_mesh.BALLOON_TAIL_MAIN_LINE_MESH_NAME_PREFIX}{entry.id}")
        if tail_line is not None:
            raise AssertionError("接合済みしっぽで分離線が残っています")
        line_obj = bpy.data.objects.get(f"{balloon_line_mesh.BALLOON_LINE_MESH_NAME_PREFIX}{entry.id}")
        fill_obj = bpy.data.objects.get(f"balloon_fill_mesh_{entry.id}")
        if line_obj is None or fill_obj is None:
            raise AssertionError("フキダシの主線または塗りがありません")

        line_min_x, _line_min_y, line_max_x, _line_max_y = _mesh_bounds_xy(line_obj)
        fill_min_x, _fill_min_y, fill_max_x, _fill_max_y = _mesh_bounds_xy(fill_obj)
        if line_min_x < -0.052 or fill_min_x < -0.052:
            raise AssertionError(f"内向きしっぽが外へ突き出しています: line={line_min_x:.4f}, fill={fill_min_x:.4f}")
        if line_max_x < 0.052 or fill_max_x < 0.050:
            raise AssertionError(f"外向きしっぽが外形に反映されていません: line={line_max_x:.4f}, fill={fill_max_x:.4f}")
        if _mesh_covers_point(fill_obj, (-0.050, 0.012)):
            raise AssertionError("内向きしっぽの外側へ塗りがはみ出しています")
        print(f"BMANGA_BALLOON_TAIL_INWARD_JOIN_OK {OUT_PATH}", flush=True)
    finally:
        if mod is not None:
            mod.unregister()


if __name__ == "__main__":
    main()
