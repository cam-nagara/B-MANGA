"""Blender実機用: ビューポート作成位置からページ/コマ所属を解決する確認."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _assert_close(actual: float, expected: float, label: str, eps: float = 1.0e-4) -> None:
    if abs(float(actual) - float(expected)) > eps:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def _inside_panel_point(panel) -> tuple[float, float]:
    return (
        float(panel.rect_x_mm) + float(panel.rect_width_mm) * 0.5,
        float(panel.rect_y_mm) + float(panel.rect_height_mm) * 0.5,
    )


def _outside_panel_point(work, page) -> tuple[float, float]:
    from bname_dev.utils import layer_hierarchy

    width = float(getattr(work.paper, "canvas_width_mm", 210.0) or 210.0)
    height = float(getattr(work.paper, "canvas_height_mm", 297.0) or 297.0)
    candidates = [
        (width * 0.05, height * 0.05),
        (width * 0.95, height * 0.05),
        (width * 0.05, height * 0.95),
        (width * 0.95, height * 0.95),
        (width * 0.5, height * 0.05),
        (width * 0.5, height * 0.95),
    ]
    for x_mm, y_mm in candidates:
        if layer_hierarchy.coma_containing_point(page, x_mm, y_mm) is None:
            return x_mm, y_mm
    raise AssertionError("page-local point outside panels was not found")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_creation_scope_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "CreationScope.bname"))
        assert result == {"FINISHED"}, result
        result = bpy.ops.bname.page_add()
        assert result == {"FINISHED"}, result

        from bname_dev.operators import balloon_op, effect_line_op
        from bname_dev.utils import gp_layer_parenting as gp_parent
        from bname_dev.utils import layer_hierarchy, object_naming as on, page_grid

        context = bpy.context
        work = context.scene.bname_work
        page = work.pages[1]
        panel = page.comas[0]
        page_key = layer_hierarchy.page_stack_key(page)
        coma_key = layer_hierarchy.coma_stack_key(page, panel)
        ox_mm, oy_mm = page_grid.page_total_offset_mm(work, context.scene, 1)

        local_x, local_y = _inside_panel_point(panel)
        world_x = ox_mm + local_x
        world_y = oy_mm + local_y
        resolved = effect_line_op._creation_context_for_world_point(context, world_x, world_y)
        assert resolved is not None
        _work, resolved_page, page_index, lx, ly, parent_key = resolved
        assert resolved_page == page
        assert page_index == 1
        assert parent_key == coma_key
        _assert_close(lx, local_x, "effect local x")
        _assert_close(ly, local_y, "effect local y")
        obj, layer = effect_line_op._create_effect_layer(
            context,
            (lx, ly, 12.0, 10.0),
            parent_key=parent_key,
        )
        assert obj is not None and layer is not None
        assert gp_parent.parent_key(layer) == coma_key
        assert str(obj.get(on.PROP_PARENT_KEY, "") or "") == coma_key

        parent_kind, balloon_parent_key = balloon_op._parent_for_creation_point(
            page,
            local_x,
            local_y,
        )
        assert parent_kind == "coma"
        assert balloon_parent_key == coma_key

        page_local_x, page_local_y = _outside_panel_point(work, page)
        page_world_x = ox_mm + page_local_x
        page_world_y = oy_mm + page_local_y
        page_resolved = effect_line_op._creation_context_for_world_point(
            context,
            page_world_x,
            page_world_y,
        )
        assert page_resolved is not None
        assert page_resolved[2] == 1
        assert page_resolved[5] == page_key
        _assert_close(page_resolved[3], page_local_x, "page local x")
        _assert_close(page_resolved[4], page_local_y, "page local y")
        parent_kind, balloon_parent_key = balloon_op._parent_for_creation_point(
            page,
            page_local_x,
            page_local_y,
        )
        assert parent_kind == "page"
        assert balloon_parent_key == page_key
    finally:
        if mod is not None:
            mod.unregister()

    print("BNAME_CREATION_SCOPE_OK")


if __name__ == "__main__":
    main()
