"""Blender実機用: 枠線カット後もレイヤーリストのコマ並びを維持する。"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_coma_knife_layer_order",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_coma_knife_layer_order"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_rect_coma(entry, coma_id: str, x_mm: float, z_order: int) -> None:
    entry.id = coma_id
    entry.coma_id = coma_id
    entry.title = ""
    entry.shape_type = "rect"
    entry.rect_x_mm = x_mm
    entry.rect_y_mm = 20.0
    entry.rect_width_mm = 60.0
    entry.rect_height_mm = 80.0
    entry.z_order = z_order


def _stack_coma_order(page) -> list[str]:
    page_id = str(getattr(page, "id", "") or "")
    prefix = f"{page_id}:"
    out: list[str] = []
    for item in bpy.context.scene.bmanga_layer_stack:
        if str(getattr(item, "kind", "") or "") != "coma":
            continue
        key = str(getattr(item, "key", "") or "")
        if key.startswith(prefix):
            out.append(key[len(prefix):])
    return out


def _prepare_case(root: Path):
    from bmanga_dev_coma_knife_layer_order.io import coma_io, page_io
    from bmanga_dev_coma_knife_layer_order.utils import layer_stack

    result = bpy.ops.bmanga.work_new(filepath=str(root / "layer_order.bmanga"))
    if "FINISHED" not in result:
        raise AssertionError(f"作品作成に失敗しました: {result}")
    work = bpy.context.scene.bmanga_work
    work.paper.read_direction = "right"
    page = work.pages[0]
    while len(page.comas) > 0:
        page.comas.remove(len(page.comas) - 1)

    # ユーザーがレイヤーリストで c03, c01, c02 の順へ並べ替えた状態を再現する。
    setup = [
        ("c01", 20.0, 1),
        ("c02", 100.0, 0),
        ("c03", 180.0, 2),
    ]
    work_dir = Path(work.work_dir)
    for coma_id, x_mm, z_order in setup:
        entry = page.comas.add()
        _set_rect_coma(entry, coma_id, x_mm, z_order)
        coma_io.save_coma_meta(work_dir, page.id, entry)
    page.coma_count = len(page.comas)
    page.active_coma_index = 0
    page.stack_expanded = True
    page_io.save_page_json(work_dir, page)
    layer_stack.sync_layer_stack_after_data_change(bpy.context, align_coma_order=True)
    order = _stack_coma_order(page)
    if order != ["c03", "c01", "c02"]:
        raise AssertionError(f"前提のレイヤー順が作れませんでした: {order}")
    return work, page


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_knife_layer_order_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        work, page = _prepare_case(temp_root)

        from bmanga_dev_coma_knife_layer_order.operators import coma_knife_cut_op

        target = page.comas[0]
        cut_x = float(target.rect_x_mm) + float(target.rect_width_mm) * 0.5
        ok = coma_knife_cut_op._apply_cut_to_coma(
            work,
            page,
            0,
            Path(work.work_dir),
            (cut_x, float(target.rect_y_mm) - 5.0),
            (cut_x, float(target.rect_y_mm) + float(target.rect_height_mm) + 5.0),
        )
        if not ok:
            raise AssertionError("枠線カットに失敗しました")
        coma_knife_cut_op._sync_layer_stack_after_cut(bpy.context)
        order = _stack_coma_order(page)
        if "c04" not in order:
            raise AssertionError(f"追加コマがレイヤーリストにありません: {order}")
        existing_order = [coma_id for coma_id in order if coma_id in {"c03", "c01", "c02"}]
        if existing_order != ["c03", "c01", "c02"]:
            raise AssertionError(f"既存コマのレイヤー順が変わりました: {order}")
        print("BMANGA_COMA_KNIFE_CUT_LAYER_ORDER_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
