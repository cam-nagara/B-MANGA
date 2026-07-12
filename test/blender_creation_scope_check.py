"""Blender実機用: ビューポート作成位置からページ/コマ所属を解決する確認."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev"] = mod
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
    from bmanga_dev.utils import layer_hierarchy

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


def _assert_rect_on_page(context, work, page_index: int, rect, label: str) -> None:
    from bmanga_dev.utils import page_grid

    ox_mm, oy_mm = page_grid.page_total_offset_mm(work, context.scene, page_index)
    center_x = float(rect.x) + float(rect.width) * 0.5
    center_y = float(rect.y) + float(rect.height) * 0.5
    local_x = center_x - ox_mm
    local_y = center_y - oy_mm
    paper = work.paper
    if not (
        0.0 <= local_x <= float(paper.canvas_width_mm)
        and 0.0 <= local_y <= float(paper.canvas_height_mm)
    ):
        raise AssertionError(
            f"{label}: 選択枠が対象ページ外です "
            f"world=({center_x:.3f},{center_y:.3f}) "
            f"local=({local_x:.3f},{local_y:.3f}) page={page_index}"
        )


def _check_creation_scope_for_layout(context, work, page_index: int, *, start_side: str, read_direction: str) -> None:
    from bmanga_dev.operators import balloon_op, effect_line_op
    from bmanga_dev.utils import gp_layer_parenting as gp_parent
    from bmanga_dev.utils import layer_hierarchy, object_selection, page_grid
    from bmanga_dev.operators import object_tool_selection, text_op

    work.paper.start_side = start_side
    work.paper.read_direction = read_direction
    context.scene.bmanga_overview_mode = True
    page_grid.apply_page_collection_transforms(context, work)

    page = work.pages[page_index]
    panel = page.comas[0]
    page_key = layer_hierarchy.page_stack_key(page)
    coma_key = layer_hierarchy.coma_stack_key(page, panel)
    ox_mm, oy_mm = page_grid.page_total_offset_mm(work, context.scene, page_index)

    local_x, local_y = _inside_panel_point(panel)
    world_x = ox_mm + local_x
    world_y = oy_mm + local_y
    resolved = effect_line_op._creation_context_for_world_point(context, world_x, world_y)
    assert resolved is not None
    _work, resolved_page, resolved_page_index, lx, ly, parent_key = resolved
    assert resolved_page == page
    assert resolved_page_index == page_index
    assert parent_key == coma_key
    _assert_close(lx, local_x, f"{start_side}/{read_direction} effect local x")
    _assert_close(ly, local_y, f"{start_side}/{read_direction} effect local y")

    obj, layer = effect_line_op._create_effect_layer(
        context,
        (lx, ly, 12.0, 10.0),
        parent_key=parent_key,
    )
    assert obj is not None and layer is not None
    assert gp_parent.parent_key(layer) == coma_key
    world_bounds = effect_line_op.effect_layer_world_bounds(
        context,
        obj,
        layer,
        effect_line_op.effect_layer_bounds(obj, layer),
    )
    assert world_bounds is not None
    _assert_close(world_bounds[0], world_x, f"{start_side}/{read_direction} effect handle world x")
    _assert_close(world_bounds[1], world_y, f"{start_side}/{read_direction} effect handle world y")

    effect_key = object_selection.effect_key(layer)
    effect_rect = object_tool_selection.selection_bounds_for_key(context, effect_key)
    assert effect_rect is not None
    _assert_rect_on_page(context, work, page_index, effect_rect, f"{start_side}/{read_direction} 効果線")

    parent_kind, balloon_parent_key = balloon_op._parent_for_creation_point(page, local_x, local_y)
    assert parent_kind == "coma"
    assert balloon_parent_key == coma_key
    balloon = balloon_op._create_balloon_entry(
        context,
        page,
        shape="ellipse",
        x=local_x + 3.0,
        y=local_y + 3.0,
        w=18.0,
        h=12.0,
        parent_kind=parent_kind,
        parent_key=balloon_parent_key,
    )
    balloon_rect = object_tool_selection.selection_bounds_for_key(
        context,
        object_selection.balloon_key(page, balloon),
    )
    assert balloon_rect is not None
    _assert_rect_on_page(context, work, page_index, balloon_rect, f"{start_side}/{read_direction} フキダシ")

    text, missing = text_op._create_text_entry(
        context,
        page,
        body="ページずれ確認",
        x_mm=local_x + 6.0,
        y_mm=local_y + 6.0,
        width_mm=20.0,
        height_mm=14.0,
        parent_kind=parent_kind,
        parent_key=balloon_parent_key,
    )
    assert not missing
    text_rect = object_tool_selection.selection_bounds_for_key(
        context,
        object_selection.text_key(page, text),
    )
    assert text_rect is not None
    _assert_rect_on_page(context, work, page_index, text_rect, f"{start_side}/{read_direction} テキスト")

    page_local_x, page_local_y = _outside_panel_point(work, page)
    page_world_x = ox_mm + page_local_x
    page_world_y = oy_mm + page_local_y
    page_resolved = effect_line_op._creation_context_for_world_point(
        context,
        page_world_x,
        page_world_y,
    )
    assert page_resolved is not None
    assert page_resolved[2] == page_index
    assert page_resolved[5] == page_key
    _assert_close(page_resolved[3], page_local_x, f"{start_side}/{read_direction} page local x")
    _assert_close(page_resolved[4], page_local_y, f"{start_side}/{read_direction} page local y")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_creation_scope_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "CreationScope.bmanga"))
        assert result == {"FINISHED"}, result
        result = bpy.ops.bmanga.page_add()
        assert result == {"FINISHED"}, result
        result = bpy.ops.bmanga.page_add()
        assert result == {"FINISHED"}, result

        from bmanga_dev.operators import balloon_op, effect_line_op
        from bmanga_dev.utils import gp_layer_parenting as gp_parent
        from bmanga_dev.utils import layer_hierarchy, object_naming as on, page_grid

        context = bpy.context
        work = context.scene.bmanga_work
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
        world_bounds = effect_line_op.effect_layer_world_bounds(
            context,
            obj,
            layer,
            effect_line_op.effect_layer_bounds(obj, layer),
        )
        assert world_bounds is not None
        _assert_close(world_bounds[0], world_x, "effect handle world x")
        _assert_close(world_bounds[1], world_y, "effect handle world y")

        from bmanga_dev.ui import overlay_effect_line

        drawn_rects = []

        def _capture_outline(rect, *_args, **_kwargs):
            drawn_rects.append(SimpleNamespace(x=rect.x, y=rect.y, width=rect.width, height=rect.height))

        overlay_effect_line.draw_active_effect_line_bounds(
            context,
            draw_rect_fill=lambda *_args, **_kwargs: None,
            draw_rect_outline=_capture_outline,
        )
        if not drawn_rects:
            raise AssertionError("effect handle overlay was not drawn")
        _assert_close(drawn_rects[0].x, world_x - 1.0, "effect overlay rect x")
        _assert_close(drawn_rects[0].y, world_y - 1.0, "effect overlay rect y")

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

        for start_side, read_direction in (
            ("right", "left"),
            ("left", "left"),
            ("right", "right"),
            ("left", "right"),
            ("right", "down"),
            ("left", "down"),
        ):
            _check_creation_scope_for_layout(
                context,
                work,
                2,
                start_side=start_side,
                read_direction=read_direction,
            )
    finally:
        if mod is not None:
            mod.unregister()

    print("BMANGA_CREATION_SCOPE_OK")


if __name__ == "__main__":
    main()
