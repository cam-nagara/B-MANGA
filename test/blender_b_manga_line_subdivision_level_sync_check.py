"""Generated-line subdivision is independent from user Subsurf levels."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, inner_lines, outline_setup, subdivision_lod  # noqa: E402


def _inner_count(obj: bpy.types.Object) -> int:
    mod = obj.modifiers.get(core.GN_MODIFIER_NAME)
    assert mod is not None and mod.node_group is not None
    socket_id = inner_lines._find_socket_id(mod.node_group, "線の分割数")
    assert socket_id is not None
    return int(mod[socket_id])


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        bpy.ops.mesh.primitive_cube_add(size=2.0)
        obj = bpy.context.object
        obj.data.materials.append(bpy.data.materials.new("Surface"))
        settings = obj.bmanga_line_settings
        settings.weld_mesh_for_outline = False
        settings.auto_subdivision_for_midpoint = True
        settings.inner_edge_smooth_factor = -1.0

        manual = obj.modifiers.new("ユーザー細分化", "SUBSURF")
        manual.levels = 2
        manual.render_levels = 4
        manual.show_viewport = False
        before = (
            manual.levels,
            manual.render_levels,
            manual.show_viewport,
            list(obj.modifiers).index(manual),
        )

        assert inner_lines.apply_inner_lines(
            obj,
            angle=math.radians(10.0),
            thickness=0.03,
            material=outline_setup.get_line_material(obj, "inner"),
            midpoint_factor=-1.0,
        )
        subdivision_lod.sync_generated_line_subdivision(obj)
        assert _inner_count(obj) == 3
        subdivision_lod.sync_generated_line_subdivision(obj, for_render=True)
        assert _inner_count(obj) == 3
        assert subdivision_lod.sync_viewport_levels_to_render(obj) == 0
        assert subdivision_lod.reset_viewport_levels_to_zero(obj) == 0
        after = (
            manual.levels,
            manual.render_levels,
            manual.show_viewport,
            list(obj.modifiers).index(manual),
        )
        assert after == before, (before, after)

        settings.auto_subdivision_for_midpoint = False
        subdivision_lod.sync_generated_line_subdivision(obj)
        assert _inner_count(obj) == 1
        assert (manual.levels, manual.render_levels, manual.show_viewport) == (2, 4, False)
        print("[PASS] generated line subdivision ignores user Subsurf levels", flush=True)
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
