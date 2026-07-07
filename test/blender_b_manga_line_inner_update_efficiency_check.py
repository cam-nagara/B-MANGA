"""B-MANGA Line: inner-line display updates do not rebuild edge chains."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, inner_line_chains, inner_lines, outline_setup  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def main() -> None:
    b_manga_line.register()
    real_update = inner_line_chains.update_chain_id_attribute
    try:
        _clear_scene()
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
        obj = bpy.context.object
        obj.name = "BML_inner_update_efficiency_cube"
        material = outline_setup.get_line_material(obj, "inner")
        counts = {"chain_updates": 0}

        def counted_update(*args, **kwargs):
            counts["chain_updates"] += 1
            return real_update(*args, **kwargs)

        inner_line_chains.update_chain_id_attribute = counted_update
        assert inner_lines.apply_inner_lines(
            obj,
            angle=math.radians(45.0),
            thickness=0.001,
            material=material,
            midpoint_factor=0.0,
            midpoint_angle=math.radians(45.0),
            midpoint_jitter_percent=0.0,
        )
        assert counts["chain_updates"] == 1, counts

        assert inner_lines.update_parameters(
            obj,
            thickness=0.002,
            material=material,
            midpoint_factor=0.5,
            midpoint_jitter_percent=20.0,
            width_curve_25=0.2,
            width_curve_50=0.5,
            width_curve_75=0.8,
        )
        assert counts["chain_updates"] == 1, (
            "線幅・カラー・中間頂点の表示更新だけで稜谷線チェーンが再計算されています",
            counts,
        )

        assert inner_lines.update_parameters(obj, use_marked_edges=False)
        assert counts["chain_updates"] == 1, (
            "同じ線種条件の再適用だけで稜谷線チェーンが再計算されています",
            counts,
        )

        assert inner_lines.update_parameters(obj, angle=math.radians(60.0))
        assert counts["chain_updates"] == 2, (
            "検出角度変更で稜谷線チェーンが再計算されていません",
            counts,
        )

        print("[PASS] inner-line display updates avoid chain rebuild")
    finally:
        inner_line_chains.update_chain_id_attribute = real_update
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
