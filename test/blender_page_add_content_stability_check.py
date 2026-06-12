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


def _find_page_index(work, page_id: str) -> int:
    for index, page in enumerate(work.pages):
        if str(getattr(page, "id", "") or "") == page_id:
            return index
    raise AssertionError(f"ページが見つかりません: {page_id}")


def _snapshot_balloon(context, work, page_id: str, balloon_id: str):
    """ページ編集シーン上で、フキダシのデータ値とページ内の実体中心を採取する."""
    from bname_dev.utils import balloon_curve_object, page_grid

    index = _find_page_index(work, page_id)
    page = work.pages[index]
    entry = next(
        (e for e in page.balloons if str(getattr(e, "id", "") or "") == balloon_id),
        None,
    )
    assert entry is not None, f"フキダシのデータがありません: {balloon_id}"
    obj = balloon_curve_object.find_balloon_object(balloon_id)
    assert obj is not None, f"フキダシの実体がありません: {balloon_id}"
    ox_mm, oy_mm = page_grid.page_total_offset_mm(work, context.scene, index)
    return (
        float(entry.x_mm),
        float(entry.y_mm),
        float(entry.width_mm),
        float(entry.height_mm),
        float(obj.location.x) * 1000.0 - ox_mm,
        float(obj.location.y) * 1000.0 - oy_mm,
    )


def _assert_balloon_stable(current, expected, label: str) -> None:
    names = ("x", "y", "width", "height", "object center x", "object center y")
    for name, actual, want in zip(names, current, expected, strict=True):
        _assert_close(actual, want, f"{label} {name}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_page_add_stability_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "PageAddStability.bname"))
        assert result == {"FINISHED"}, result

        from bname_dev.core.work import get_work
        from bname_dev.utils import page_file_scene

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page_a_id = str(getattr(work.pages[0], "id", "") or "")

        # v0.6.279 以降、フキダシ等の実体はページ用 blend のみが持つ。
        # ページを開いてフキダシを作り、ページ操作のたびに開き直して
        # ページ内位置が変わっていないことを検証する。
        def _open_page(page_id: str):
            work_now = get_work(bpy.context)
            index = _find_page_index(work_now, page_id)
            result = bpy.ops.bname.open_page_file("EXEC_DEFAULT", index=index)
            assert result == {"FINISHED"}, f"ページを開けません: {page_id} {result}"
            assert page_file_scene.is_page_edit_scene(bpy.context.scene)
            return bpy.context, get_work(bpy.context)

        def _close_page() -> None:
            result = bpy.ops.bname.exit_page_file("EXEC_DEFAULT")
            assert "FINISHED" in result, f"ページ一覧へ戻れません: {result}"

        def _build(page_id: str, *, shape: str, x: float, y: float, w: float, h: float):
            from bname_dev.operators import balloon_op
            from bname_dev.utils import balloon_curve_object
            from bname_dev.utils.layer_hierarchy import page_stack_key

            ctx, work_now = _open_page(page_id)
            page = work_now.pages[_find_page_index(work_now, page_id)]
            entry = balloon_op._create_balloon_entry(
                ctx,
                page,
                shape=shape,
                x=x,
                y=y,
                w=w,
                h=h,
                parent_kind="page",
                parent_key=page_stack_key(page),
            )
            assert entry is not None
            balloon_id = str(entry.id)
            obj = balloon_curve_object.ensure_balloon_curve_object(
                scene=ctx.scene, entry=entry, page=page
            )
            assert obj is not None
            ctx.view_layer.update()
            expected = _snapshot_balloon(ctx, work_now, page_id, balloon_id)
            _close_page()
            # 基準そのものが設定値どおりであることを確認しておく
            _assert_close(expected[0], x, "基準値 x")
            _assert_close(expected[1], y, "基準値 y")
            _assert_close(expected[4], x + w * 0.5, "基準値 中心x")
            _assert_close(expected[5], y + h * 0.5, "基準値 中心y")
            return balloon_id, expected

        def _verify(page_id: str, balloon_id: str, expected, label: str) -> None:
            ctx, work_now = _open_page(page_id)
            current = _snapshot_balloon(ctx, work_now, page_id, balloon_id)
            _assert_balloon_stable(current, expected, label)
            _close_page()

        balloon_a, expected_a = _build(
            page_a_id, shape="ellipse", x=52.0, y=64.0, w=38.0, h=24.0
        )

        for end_number in (2, 3, 4, 5):
            work = get_work(bpy.context)
            work.work_info.page_number_end = end_number
            bpy.context.view_layer.update()
            _verify(page_a_id, balloon_a, expected_a, f"ページ数 {end_number}")

        work = get_work(bpy.context)
        if len(work.pages) != 5:
            raise AssertionError(f"ページ数が反映されていません: {len(work.pages)}")

        page_b_id = str(getattr(work.pages[1], "id", "") or "")
        balloon_b, expected_b = _build(
            page_b_id, shape="rect", x=24.0, y=36.0, w=28.0, h=18.0
        )

        work = get_work(bpy.context)
        work.active_page_index = _find_page_index(work, page_a_id)
        move_result = bpy.ops.bname.page_move(direction=1)
        assert move_result == {"FINISHED"}, move_result
        bpy.context.view_layer.update()
        _verify(page_a_id, balloon_a, expected_a, "ページ並べ替え 元の1ページ目")
        _verify(page_b_id, balloon_b, expected_b, "ページ並べ替え 元の2ページ目")

        print("BNAME_PAGE_ADD_CONTENT_STABILITY_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
