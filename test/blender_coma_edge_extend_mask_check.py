"""Blender 実機用: 枠線辺ドラッグ / 三角ハンドル拡張で coma mask Mesh が
コマ rect に追従して再生成されるかの回帰テスト.

期待: ``rect_x_mm/rect_y_mm/rect_width_mm/rect_height_mm`` を変えて
``_save_changes`` (またはその shim 経由) が呼ばれると、
``coma_mask_mesh_<page>_<coma>`` の頂点 4 点が新 rect を表すように更新される。
"""

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


def _approx(a: float, b: float, tol: float = 1e-4) -> bool:
    return abs(float(a) - float(b)) < tol


def _mask_xy_extents_m(mask_obj: bpy.types.Object) -> tuple[float, float, float, float]:
    """rect 形状コマの mask Mesh について、 ローカル頂点の min/max XY (m) を返す."""
    xs = [float(v.co.x) for v in mask_obj.data.vertices]
    ys = [float(v.co.y) for v in mask_obj.data.vertices]
    return min(xs), min(ys), max(xs), max(ys)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_coma_edge_mask_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()

        result = bpy.ops.bname.work_new(filepath=str(temp_root / "EdgeMask.bname"))
        assert result == {"FINISHED"}, result

        from bname_dev.core.work import get_work
        from bname_dev.utils import mask_object as mo
        from bname_dev.operators import coma_edge_move_op

        work = get_work(bpy.context)
        assert work is not None
        page = work.pages[0]
        assert len(page.comas) >= 1, "work_new should create at least one coma"
        coma = page.comas[0]

        # 確実に rect 形状にしておく
        coma.shape_type = "rect"
        coma.rect_x_mm = 10.0
        coma.rect_y_mm = 20.0
        coma.rect_width_mm = 50.0
        coma.rect_height_mm = 60.0

        # 初期 mask Mesh を ensure
        mask_obj = mo.ensure_coma_mask_object(bpy.context.scene, page, coma)
        assert mask_obj is not None
        x0, y0, x1, y1 = _mask_xy_extents_m(mask_obj)
        # rect 形状の mask Mesh は (0,0)〜(width_m,height_m) の頂点を持ち、
        # Object 自体が rect_x_mm,rect_y_mm にオフセットされる
        assert _approx(x0, 0.0) and _approx(y0, 0.0)
        assert _approx(x1, 0.050) and _approx(y1, 0.060)
        assert _approx(mask_obj.location.x, 0.010)
        assert _approx(mask_obj.location.y, 0.020)

        # ===== 三角ハンドル拡張をシミュレート =====
        # コマ rect を直接拡張 (drag 終了後と等価) → _refresh_coma_masks_for_pages
        # を呼び出して mask Mesh が追従するかを確認
        coma.rect_width_mm = 80.0
        coma.rect_height_mm = 90.0
        coma.rect_x_mm = 5.0
        coma.rect_y_mm = 15.0

        n = coma_edge_move_op._refresh_coma_masks_for_pages(work, {0})
        assert n >= 1, f"refresh count should be >= 1, got {n}"

        mask_obj_after = bpy.data.objects.get(mask_obj.name)
        assert mask_obj_after is mask_obj, "mask Object identity should be preserved"
        x0, y0, x1, y1 = _mask_xy_extents_m(mask_obj_after)
        assert _approx(x0, 0.0) and _approx(y0, 0.0), (x0, y0)
        assert _approx(x1, 0.080), x1
        assert _approx(y1, 0.090), y1
        assert _approx(mask_obj_after.location.x, 0.005), mask_obj_after.location.x
        assert _approx(mask_obj_after.location.y, 0.015), mask_obj_after.location.y

        # ===== もう一度縮小 (gap を空ける ▲ 操作のシミュレート) =====
        coma.rect_width_mm = 30.0
        coma.rect_height_mm = 40.0
        coma_edge_move_op._refresh_coma_masks_for_pages(work, {0})
        x0, y0, x1, y1 = _mask_xy_extents_m(mask_obj)
        assert _approx(x1, 0.030), x1
        assert _approx(y1, 0.040), y1

    finally:
        if mod is not None:
            mod.unregister()

    print("BNAME_COMA_EDGE_EXTEND_MASK_OK")


if __name__ == "__main__":
    main()
