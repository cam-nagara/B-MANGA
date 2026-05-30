"""Blender実機用: 枠線カット後のコマ番号が読む順に割り当たることを確認。"""

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
        "bname_dev_coma_knife_order",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_coma_knife_order"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _center(panel) -> tuple[float, float]:
    if str(getattr(panel, "shape_type", "") or "") == "rect":
        return (
            float(panel.rect_x_mm) + float(panel.rect_width_mm) * 0.5,
            float(panel.rect_y_mm) + float(panel.rect_height_mm) * 0.5,
        )
    points = [(float(v.x_mm), float(v.y_mm)) for v in panel.vertices]
    if not points:
        return 0.0, 0.0
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _coma_by_id(page, coma_id: str):
    for entry in page.comas:
        if str(getattr(entry, "coma_id", "") or "") == coma_id:
            return entry
    return None


def _prepare_case(root: Path, name: str, read_direction: str):
    result = bpy.ops.bname.work_new(filepath=str(root / f"{name}.bname"))
    if "FINISHED" not in result:
        raise AssertionError(f"作品作成に失敗しました: {result}")
    work = bpy.context.scene.bname_work
    work.paper.read_direction = read_direction
    work.coma_gap.vertical_mm = 0.0
    work.coma_gap.horizontal_mm = 0.0
    page = work.pages[0]
    while len(page.comas) > 1:
        page.comas.remove(len(page.comas) - 1)
    if len(page.comas) == 0:
        entry = page.comas.add()
        entry.id = "c01"
        entry.coma_id = "c01"
    entry = page.comas[0]
    entry.id = "c01"
    entry.coma_id = "c01"
    entry.title = ""
    entry.shape_type = "rect"
    entry.rect_x_mm = 0.0
    entry.rect_y_mm = 0.0
    entry.rect_width_mm = 200.0
    entry.rect_height_mm = 120.0
    entry.z_order = 0
    page.active_coma_index = 0
    return work, page


def _cut_case(name: str, read_direction: str, point_a, point_b, expectation: str, root: Path) -> None:
    from bname_dev_coma_knife_order.operators import coma_knife_cut_op

    work, page = _prepare_case(root, name, read_direction)
    ok = coma_knife_cut_op._apply_cut_to_coma(
        work,
        page,
        0,
        Path(work.work_dir),
        point_a,
        point_b,
    )
    if not ok:
        raise AssertionError(f"枠線カットに失敗しました: {name}")
    if len(page.comas) != 2:
        raise AssertionError(f"コマ数が2つになっていません: {name} count={len(page.comas)}")
    first = _coma_by_id(page, "c01")
    second = _coma_by_id(page, "c02")
    if first is None or second is None:
        ids = [str(getattr(entry, "coma_id", "") or "") for entry in page.comas]
        raise AssertionError(f"c01/c02 が揃っていません: {name} ids={ids}")
    first_x, first_y = _center(first)
    second_x, second_y = _center(second)
    if expectation == "c01_right" and not (first_x > second_x):
        raise AssertionError(f"右側が c01 ではありません: {name} c01={first_x} c02={second_x}")
    if expectation == "c01_left" and not (first_x < second_x):
        raise AssertionError(f"左側が c01 ではありません: {name} c01={first_x} c02={second_x}")
    if expectation == "c01_top" and not (first_y > second_y):
        raise AssertionError(f"上側が c01 ではありません: {name} c01={first_y} c02={second_y}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_coma_knife_order_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        _cut_case("left_vertical_up", "left", (100.0, -20.0), (100.0, 140.0), "c01_right", temp_root)
        _cut_case("left_vertical_down", "left", (100.0, 140.0), (100.0, -20.0), "c01_right", temp_root)
        _cut_case("right_vertical_up", "right", (100.0, -20.0), (100.0, 140.0), "c01_left", temp_root)
        _cut_case("right_vertical_down", "right", (100.0, 140.0), (100.0, -20.0), "c01_left", temp_root)
        _cut_case("left_horizontal", "left", (-20.0, 60.0), (220.0, 60.0), "c01_top", temp_root)
        _cut_case("right_horizontal", "right", (-20.0, 60.0), (220.0, 60.0), "c01_top", temp_root)
        print("BNAME_COMA_KNIFE_CUT_READING_ORDER_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
