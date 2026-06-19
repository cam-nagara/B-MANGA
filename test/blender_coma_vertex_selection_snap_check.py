"""Blender 実機(背景)用: コマ頂点の複数選択・スナップ確認。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_coma_vertex",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_coma_vertex"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_poly(coma, points):
    coma.shape_type = "polygon"
    coma.vertices.clear()
    for x_mm, y_mm in points:
        vertex = coma.vertices.add()
        vertex.x_mm = float(x_mm)
        vertex.y_mm = float(y_mm)


def _poly(coma):
    return [(round(float(v.x_mm), 3), round(float(v.y_mm), 3)) for v in coma.vertices]


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = _load_addon()
    try:
        from bmanga_dev_coma_vertex.operators import coma_edge_move_op
        from bmanga_dev_coma_vertex.utils import coma_vertex_edit, edge_selection

        context = bpy.context
        work = context.scene.bmanga_work
        work.loaded = True
        page = work.pages.add()
        page.id = "p0001"
        page.title = "1ページ"
        coma = page.comas.add()
        coma.coma_id = "c01"
        _set_poly(coma, [(0, 0), (20, 0), (20, 20), (0, 20)])

        selected = edge_selection.set_vertex_selection(
            context,
            page_index=0,
            coma_index=0,
            vertex_index=0,
            mode="single",
        )
        assert selected == {0}, selected
        selected = edge_selection.set_vertex_selection(
            context,
            page_index=0,
            coma_index=0,
            vertex_index=3,
            mode="add",
        )
        assert selected == {0, 3}, selected
        assert edge_selection.selected_vertices(context, page_index=0, coma_index=0) == {0, 3}

        snap_dx, snap_dy = coma_vertex_edit.snap_vertex_delta_to_guides(
            [(0, 0), (20, 0), (20, 20), (0, 20)],
            {0},
            0,
            19.2,
            5.0,
            [],
            snap_tolerance_mm=coma_edge_move_op.DRAG_SNAP_TOL_MM,
            direction_snap_tolerance_mm=coma_edge_move_op.VERTEX_DIRECTION_SNAP_TOL_MM,
        )
        assert abs(snap_dx - 20.0) < 0.001 and abs(snap_dy - 5.0) < 0.001, (snap_dx, snap_dy)

        fake = SimpleNamespace(
            _work=work,
            _area=None,
            _region=SimpleNamespace(x=0, y=0),
            _rv3d=None,
            _selection={"type": "vertex", "page": 0, "coma": 0, "vertex": 0, "vertices": [0, 3]},
            _drag_start_world=(0.0, 0.0),
            _original_geometry=None,
            _drag_moved=False,
        )
        fake._to_window = lambda event: (event.mouse_x, event.mouse_y)
        coma_edge_move_op.BMANGA_OT_coma_edge_move._capture_original_geometry(fake)
        original_region_to_world = coma_edge_move_op._region_to_world_mm
        try:
            coma_edge_move_op._region_to_world_mm = lambda _region, _rv3d, mx, my: (float(mx), float(my))
            event = SimpleNamespace(mouse_x=2, mouse_y=3)
            coma_edge_move_op.BMANGA_OT_coma_edge_move._apply_drag(fake, event)
        finally:
            coma_edge_move_op._region_to_world_mm = original_region_to_world
        assert _poly(coma) == [(2.0, 3.0), (20.0, 0.0), (20.0, 20.0), (2.0, 23.0)], _poly(coma)

        other = page.comas.add()
        other.coma_id = "c02"
        _set_poly(other, [(-18, 0), (-2, 0), (-2, 6), (-18, 6)])
        extended = coma_vertex_edit.find_extended_vertex_adjacent_edges(
            work,
            0,
            0,
            0,
            0,
            [(0, 0), (20, 0), (20, 20), (0, 20)],
            page_offset_fn=coma_edge_move_op._page_offset,
            all_edges_world_fn=coma_edge_move_op._all_coma_edges_world,
            gap_tolerance_mm=coma_edge_move_op.ADJACENCY_GAP_TOLERANCE_MM,
        )
        assert (0, 1, 0) in extended or (0, 1, 2) in extended, extended

        print("BMANGA_COMA_VERTEX_SELECTION_SNAP_CHECK_OK")
    finally:
        mod.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


main()
