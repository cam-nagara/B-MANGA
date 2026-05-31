"""Blender実機用: ページ追加で既存ページのレイヤー位置が変わらないことを確認。"""

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


def _balloon_world_center_mm(context, work, page_index: int, entry, obj) -> tuple[float, float]:
    from bname_dev.utils import page_grid

    ox_mm, oy_mm = page_grid.page_total_offset_mm(work, context.scene, page_index)
    return (
        float(obj.location.x) * 1000.0 - ox_mm,
        float(obj.location.y) * 1000.0 - oy_mm,
    )


def _assert_balloon_stable(context, work, page_index: int, entry, obj, expected, label: str) -> None:
    x, y, width, height, center_x, center_y = expected
    _assert_close(entry.x_mm, x, f"{label} x")
    _assert_close(entry.y_mm, y, f"{label} y")
    _assert_close(entry.width_mm, width, f"{label} width")
    _assert_close(entry.height_mm, height, f"{label} height")
    actual_center = _balloon_world_center_mm(context, work, page_index, entry, obj)
    _assert_close(actual_center[0], center_x, f"{label} object center x")
    _assert_close(actual_center[1], center_y, f"{label} object center y")


def _find_page_index(work, page_id: str) -> int:
    for index, page in enumerate(work.pages):
        if str(getattr(page, "id", "") or "") == page_id:
            return index
    raise AssertionError(f"ページが見つかりません: {page_id}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_page_add_stability_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "PageAddStability.bname"))
        assert result == {"FINISHED"}, result

        from bname_dev.core.work import get_work
        from bname_dev.operators import balloon_op
        from bname_dev.utils import balloon_curve_object
        from bname_dev.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page_a = work.pages[0]
        page_a_id = str(getattr(page_a, "id", "") or "")
        entry_a = balloon_op._create_balloon_entry(
            context,
            page_a,
            shape="ellipse",
            x=52.0,
            y=64.0,
            w=38.0,
            h=24.0,
            parent_kind="page",
            parent_key=page_stack_key(page_a),
        )
        obj_a = balloon_curve_object.ensure_balloon_curve_object(
            scene=context.scene,
            entry=entry_a,
            page=page_a,
        )
        assert obj_a is not None
        context.view_layer.update()
        expected_a = (
            float(entry_a.x_mm),
            float(entry_a.y_mm),
            float(entry_a.width_mm),
            float(entry_a.height_mm),
            float(entry_a.x_mm) + float(entry_a.width_mm) * 0.5,
            float(entry_a.y_mm) + float(entry_a.height_mm) * 0.5,
        )

        for end_number in (2, 3, 4, 5):
            work.work_info.page_number_end = end_number
            context.view_layer.update()
            page_a_index = _find_page_index(work, page_a_id)
            _assert_balloon_stable(
                context,
                work,
                page_a_index,
                entry_a,
                obj_a,
                expected_a,
                f"ページ数 {end_number}",
            )

        if len(work.pages) != 5:
            raise AssertionError(f"ページ数が反映されていません: {len(work.pages)}")
        if int(getattr(work, "active_page_index", -1)) != 0:
            raise AssertionError("ページ追加後に選択ページが変わっています")

        page_b = work.pages[1]
        page_b_id = str(getattr(page_b, "id", "") or "")
        entry_b = balloon_op._create_balloon_entry(
            context,
            page_b,
            shape="rect",
            x=24.0,
            y=36.0,
            w=28.0,
            h=18.0,
            parent_kind="page",
            parent_key=page_stack_key(page_b),
        )
        obj_b = balloon_curve_object.ensure_balloon_curve_object(
            scene=context.scene,
            entry=entry_b,
            page=page_b,
        )
        assert obj_b is not None
        context.view_layer.update()
        expected_b = (
            float(entry_b.x_mm),
            float(entry_b.y_mm),
            float(entry_b.width_mm),
            float(entry_b.height_mm),
            float(entry_b.x_mm) + float(entry_b.width_mm) * 0.5,
            float(entry_b.y_mm) + float(entry_b.height_mm) * 0.5,
        )

        work.active_page_index = _find_page_index(work, page_a_id)
        move_result = bpy.ops.bname.page_move(direction=1)
        assert move_result == {"FINISHED"}, move_result
        context.view_layer.update()
        _assert_balloon_stable(
            context,
            work,
            _find_page_index(work, page_a_id),
            entry_a,
            obj_a,
            expected_a,
            "ページ並べ替え 元の1ページ目",
        )
        _assert_balloon_stable(
            context,
            work,
            _find_page_index(work, page_b_id),
            entry_b,
            obj_b,
            expected_b,
            "ページ並べ替え 元の2ページ目",
        )

        print("BNAME_PAGE_ADD_CONTENT_STABILITY_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
