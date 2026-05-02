"""Blender 実機用: コマ形状変更時の mask Mesh 自動追従と、 viewport コマ
クリックによる新規レイヤーの parent 解決の回帰テスト.

検証項目:
1. ``mask_object.update_coma_mask_geometry`` が既存 mask Mesh の頂点 / Object
   location を coma 形状 (rect_*_mm) に追従させる (副作用ゼロ — 副 collection
   は触らない)。 mask Object/Mesh が未生成なら何もしない (False を返す)。
2. ``utils.active_target.focus_active_coma`` を呼ぶと、 ``page.active_coma_index``
   と ``scene.bname_current_coma_id`` が更新され、
   ``resolve_active_target`` が ``("coma", "<page_id>:<coma_id>", page)`` を返す。
3. paper_bg Material (``BName_PaperBackground``) と __papers__ Collection が
   mask 更新後も触られないこと (paper_color / paint 描画への副作用が無いこと)。
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


def _mesh_extents_local_m(mask_obj: bpy.types.Object) -> tuple[float, float, float, float]:
    xs = [float(v.co.x) for v in mask_obj.data.vertices]
    ys = [float(v.co.y) for v in mask_obj.data.vertices]
    return min(xs), min(ys), max(xs), max(ys)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_mask_sync_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()

        result = bpy.ops.bname.work_new(filepath=str(temp_root / "MaskSync.bname"))
        assert result == {"FINISHED"}, result

        from bname_dev.core.work import get_work
        from bname_dev.utils import active_target as _at
        from bname_dev.utils import mask_object as mo

        scene = bpy.context.scene
        work = get_work(bpy.context)
        assert work is not None
        page = work.pages[0]
        assert len(page.comas) >= 1
        coma = page.comas[0]
        coma.shape_type = "rect"
        coma.rect_x_mm = 10.0
        coma.rect_y_mm = 20.0
        coma.rect_width_mm = 50.0
        coma.rect_height_mm = 60.0

        # ---- 0. mask 未生成時は False (副作用ゼロ) ----
        # 一旦 mask が無い状態を確認
        mask_name_pre = f"{mo.COMA_MASK_NAME_PREFIX}{page.id}_{coma.id}"
        existing = bpy.data.objects.get(mask_name_pre)
        if existing is not None:
            try:
                bpy.data.objects.remove(existing, do_unlink=True)
            except Exception:
                pass
        assert mo.update_coma_mask_geometry(page, coma) is False, (
            "mask Object 未生成時は False を返すべき"
        )

        # ---- 1. 初期 ensure ----
        mask_obj = mo.ensure_coma_mask_object(scene, page, coma)
        assert mask_obj is not None
        x0, y0, x1, y1 = _mesh_extents_local_m(mask_obj)
        assert _approx(x0, 0.0) and _approx(y0, 0.0)
        assert _approx(x1, 0.050) and _approx(y1, 0.060)
        assert _approx(mask_obj.location.x, 0.010)
        assert _approx(mask_obj.location.y, 0.020)

        # ---- paper_bg Material と __papers__ Collection の事前スナップショット ----
        from bname_dev.utils import paper_bg_object as pbg

        paper_mat = bpy.data.materials.get(pbg.PAPER_BG_MATERIAL_NAME)
        assert paper_mat is not None
        paper_mat_ptr = paper_mat.as_pointer()
        papers_coll = bpy.data.collections.get(pbg.PAPERS_COLLECTION_NAME)
        assert papers_coll is not None
        papers_coll_ptr = papers_coll.as_pointer()
        papers_layer_coll_hidden_before = None
        for layer_coll in scene.view_layers[0].layer_collection.children:
            if layer_coll.collection is papers_coll:
                papers_layer_coll_hidden_before = bool(layer_coll.hide_viewport)
                break

        # ---- 2. update_coma_mask_geometry: rect 拡張 ----
        coma.rect_x_mm = 5.0
        coma.rect_y_mm = 15.0
        coma.rect_width_mm = 80.0
        coma.rect_height_mm = 90.0
        assert mo.update_coma_mask_geometry(page, coma) is True
        x0, y0, x1, y1 = _mesh_extents_local_m(mask_obj)
        assert _approx(x1, 0.080) and _approx(y1, 0.090), (x1, y1)
        assert _approx(mask_obj.location.x, 0.005)
        assert _approx(mask_obj.location.y, 0.015)

        # paper_bg Material が触られていないこと (識別子 / diffuse_color)
        paper_mat_after = bpy.data.materials.get(pbg.PAPER_BG_MATERIAL_NAME)
        assert paper_mat_after is not None and paper_mat_after.as_pointer() == paper_mat_ptr
        # __papers__ Collection の hide_viewport も触られていないこと
        for layer_coll in scene.view_layers[0].layer_collection.children:
            if layer_coll.collection is papers_coll:
                assert bool(layer_coll.hide_viewport) == papers_layer_coll_hidden_before, (
                    "update_coma_mask_geometry が __papers__ の visibility を触ってはいけない"
                )
                break
        assert bpy.data.collections.get(pbg.PAPERS_COLLECTION_NAME).as_pointer() == papers_coll_ptr

        # ---- 3. update_masks_for_pages ----
        coma.rect_width_mm = 40.0
        n = mo.update_masks_for_pages(work, {0})
        assert n >= 1, n
        x0, y0, x1, y1 = _mesh_extents_local_m(mask_obj)
        assert _approx(x1, 0.040), x1

        # ---- 4. focus_active_coma → resolve_active_target が coma を返す ----
        # まず page.active_coma_index を -1 にして「コマ未選択」状態を作る
        page.active_coma_index = -1
        scene.bname_current_coma_id = ""
        kind, key, _page = _at.resolve_active_target(bpy.context)
        # コマ未選択時: page か "" になる
        assert kind == "page", kind

        # focus_active_coma 呼び出し → coma が active になる
        _at.focus_active_coma(scene, work, 0, 0)
        assert int(page.active_coma_index) == 0
        assert str(scene.bname_current_coma_id) == coma.id
        kind2, key2, _page2 = _at.resolve_active_target(bpy.context)
        assert kind2 == "coma", kind2
        assert key2 == f"{page.id}:{coma.id}", key2

        # ---- 5. polygon shape も追従 ----
        coma.shape_type = "polygon"
        coma.vertices.clear()
        for x, y in [(0.0, 0.0), (30.0, 0.0), (30.0, 50.0), (15.0, 60.0), (0.0, 50.0)]:
            v = coma.vertices.add()
            v.x_mm = x
            v.y_mm = y
        assert mo.update_coma_mask_geometry(page, coma) is True
        # polygon mask の location は (0,0)、 mesh 頂点が world 座標
        assert _approx(mask_obj.location.x, 0.0)
        assert _approx(mask_obj.location.y, 0.0)
        assert len(mask_obj.data.vertices) == 5
    finally:
        if mod is not None:
            mod.unregister()

    print("BNAME_COMA_MASK_SYNC_OK")


if __name__ == "__main__":
    main()
