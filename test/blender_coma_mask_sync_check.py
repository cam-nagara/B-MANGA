"""Blender 実機用: コマ平面 (coma_plane) Mesh によるコマ形状連動 + 新規
レイヤーの parent 解決の回帰テスト.

検証項目:
1. work 作成 → ``coma_plane_<page>_<coma>`` Mesh Object がコマ Collection
   直下に生成され、 background_color のデフォルトが opaque 白 (1,1,1,1) に
   なっている。
2. ``coma.rect_*_mm`` を変更すると、 update callback 経由で coma_plane Mesh
   geometry / location が即時追従する (operator を介さず PropertyGroup
   操作だけで)。
3. ``coma.background_color`` を変更すると Material の Emission Color と
   diffuse_color が追従する。
4. polygon shape でも頂点が追従する。
5. ``utils.active_target.focus_active_coma`` 呼び出し後に
   ``resolve_active_target`` が ``("coma", "<page_id>:<coma_id>", page)`` を返す。
6. paper_bg Material (``BName_PaperBackground``) と ``__papers__`` Collection
   visibility が coma_plane 操作前後で不変。
7. 旧 ``__masks__`` Collection / ``page_mask_*`` / ``coma_mask_*`` は生成
   されない (purge 済)。
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


def _mesh_extents_local_m(obj: bpy.types.Object) -> tuple[float, float, float, float]:
    xs = [float(v.co.x) for v in obj.data.vertices]
    ys = [float(v.co.y) for v in obj.data.vertices]
    return min(xs), min(ys), max(xs), max(ys)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_coma_plane_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()

        result = bpy.ops.bname.work_new(filepath=str(temp_root / "ComaPlane.bname"))
        assert result == {"FINISHED"}, result

        from bname_dev.core.work import get_work
        from bname_dev.utils import active_target as _at
        from bname_dev.utils import coma_plane as cp
        from bname_dev.utils import paper_bg_object as pbg

        scene = bpy.context.scene
        work = get_work(bpy.context)
        assert work is not None
        page = work.pages[0]
        assert len(page.comas) >= 1
        coma = page.comas[0]

        # 1. background_color のデフォルトが opaque 白
        assert _approx(coma.background_color[3], 1.0), tuple(coma.background_color)
        assert _approx(coma.background_color[0], 1.0)
        assert _approx(coma.background_color[1], 1.0)
        assert _approx(coma.background_color[2], 1.0)

        # 1b. coma_plane Object/Mesh が生成されている
        plane_obj = cp.find_coma_plane_object(page.id, coma.id)
        assert plane_obj is not None, "coma_plane Object should exist after work_new"
        assert plane_obj.data is not None
        # コマ Collection 直下にいるか
        coma_coll_name = None
        for c in plane_obj.users_collection:
            coma_coll_name = c.name
            break
        assert coma_coll_name is not None, "coma_plane should be linked to a collection"

        # ---- paper_bg Material と __papers__ Collection の事前スナップショット ----
        paper_mat = bpy.data.materials.get(pbg.PAPER_BG_MATERIAL_NAME)
        assert paper_mat is not None
        paper_mat_ptr = paper_mat.as_pointer()
        papers_coll = bpy.data.collections.get(pbg.PAPERS_COLLECTION_NAME)
        assert papers_coll is not None
        papers_layer_hidden_before = None
        for layer_coll in scene.view_layers[0].layer_collection.children:
            if layer_coll.collection is papers_coll:
                papers_layer_hidden_before = bool(layer_coll.hide_viewport)
                break

        # 2. coma.rect_*_mm 変更 → update callback 経由で coma_plane が追従
        coma.shape_type = "rect"
        coma.rect_x_mm = 10.0
        coma.rect_y_mm = 20.0
        coma.rect_width_mm = 80.0
        coma.rect_height_mm = 90.0

        plane_obj_after = cp.find_coma_plane_object(page.id, coma.id)
        assert plane_obj_after is plane_obj  # identity 維持
        x0, y0, x1, y1 = _mesh_extents_local_m(plane_obj_after)
        assert _approx(x0, 0.0) and _approx(y0, 0.0)
        assert _approx(x1, 0.080), x1
        assert _approx(y1, 0.090), y1

        # rect_x_mm/rect_y_mm は obj.location で表現 (page offset = 0 なので等価)
        # page offset を考慮した world 位置
        from bname_dev.utils import page_grid as _pg

        page_ox_mm, page_oy_mm = _pg.page_total_offset_mm(work, scene, 0)
        from bname_dev.utils.geom import mm_to_m

        assert _approx(plane_obj_after.location.x, mm_to_m(page_ox_mm + 10.0)), plane_obj_after.location.x
        assert _approx(plane_obj_after.location.y, mm_to_m(page_oy_mm + 20.0)), plane_obj_after.location.y

        # 3. background_color 変更 → Material 追従
        coma.background_color = (0.2, 0.6, 0.8, 1.0)
        mat_name = f"{cp.COMA_PLANE_MATERIAL_PREFIX}{page.id}_{coma.id}"
        mat = bpy.data.materials.get(mat_name)
        assert mat is not None
        # diffuse_color が追従
        diff = tuple(float(c) for c in mat.diffuse_color)
        assert _approx(diff[0], 0.2) and _approx(diff[1], 0.6) and _approx(diff[2], 0.8), diff
        # Emission ノードも追従
        em = next((n for n in mat.node_tree.nodes if n.type == "EMISSION"), None)
        assert em is not None
        em_color = tuple(float(c) for c in em.inputs["Color"].default_value)
        assert _approx(em_color[0], 0.2) and _approx(em_color[1], 0.6) and _approx(em_color[2], 0.8), em_color

        # 4. polygon shape も追従
        coma.shape_type = "polygon"
        coma.vertices.clear()
        for x, y in [(0.0, 0.0), (30.0, 0.0), (30.0, 50.0), (15.0, 60.0), (0.0, 50.0)]:
            v = coma.vertices.add()
            v.x_mm = x
            v.y_mm = y
        # CollectionProperty.add() は update callback を発火しないことがあるため、
        # 最後に rect_x_mm を 1 度トリガして同期させる (ユーザー実機操作と同等)
        coma.rect_x_mm = float(coma.rect_x_mm)
        plane_obj_poly = cp.find_coma_plane_object(page.id, coma.id)
        assert plane_obj_poly is plane_obj
        assert len(plane_obj_poly.data.vertices) == 5, len(plane_obj_poly.data.vertices)

        # 5. focus_active_coma → resolve_active_target が coma を返す
        page.active_coma_index = -1
        scene.bname_current_coma_id = ""
        kind, _key, _page = _at.resolve_active_target(bpy.context)
        assert kind == "page", kind
        _at.focus_active_coma(scene, work, 0, 0)
        kind2, key2, _page2 = _at.resolve_active_target(bpy.context)
        assert kind2 == "coma", kind2
        assert key2 == f"{page.id}:{coma.id}", key2

        # 6. paper_bg Material と __papers__ visibility が不変
        paper_mat_after = bpy.data.materials.get(pbg.PAPER_BG_MATERIAL_NAME)
        assert paper_mat_after is not None and paper_mat_after.as_pointer() == paper_mat_ptr
        for layer_coll in scene.view_layers[0].layer_collection.children:
            if layer_coll.collection is papers_coll:
                assert bool(layer_coll.hide_viewport) == papers_layer_hidden_before
                break

        # 7. 旧 __masks__ Collection / page_mask_* / coma_mask_* が無いこと
        assert bpy.data.collections.get("__masks__") is None
        for obj in bpy.data.objects:
            assert not obj.name.startswith("page_mask_"), obj.name
            assert not obj.name.startswith("coma_mask_"), obj.name

        # ---- 8. 新規コマ追加で coma_plane が即時生成されること ----
        from bname_dev.operators import coma_op
        from pathlib import Path as _P

        new_entry = coma_op.create_rect_coma(
            work,
            page,
            _P(work.work_dir),
            x_mm=120.0,
            y_mm=130.0,
            width_mm=40.0,
            height_mm=50.0,
            title="新規コマ",
        )
        new_plane = cp.find_coma_plane_object(page.id, new_entry.id)
        assert new_plane is not None, "新規コマの coma_plane が即時生成されるべき"
        # 新規コマの location も page offset 込み
        assert _approx(new_plane.location.x, mm_to_m(page_ox_mm + 120.0))
        assert _approx(new_plane.location.y, mm_to_m(page_oy_mm + 130.0))

        # ---- 9. ページオフセット変化で coma_plane location が追従 ----
        # apply_page_collection_transforms (= 末尾で update_coma_plane_locations
        # を呼ぶよう Fix #1 で改修済) が new_entry の coma_plane の location も
        # page_grid offset に追従させることを直接確認
        from bname_dev.utils import page_grid as _pg

        # page.manual_offset_x_mm を変えて page offset を強制シフト
        old_loc_x = float(new_plane.location.x)
        page.offset_x_mm = float(getattr(page, "offset_x_mm", 0.0)) + 50.0
        _pg.apply_page_collection_transforms(bpy.context, work)
        new_loc_x = float(new_plane.location.x)
        assert _approx(new_loc_x - old_loc_x, mm_to_m(50.0)), (old_loc_x, new_loc_x)

        # ---- 10. コマ削除で coma_plane が即時掃除 ----
        # page.comas からの削除と remove_coma_plane の連携を直接テスト
        plane_name = f"{cp.COMA_PLANE_NAME_PREFIX}{page.id}_{new_entry.id}"
        assert bpy.data.objects.get(plane_name) is not None
        cp.remove_coma_plane(page.id, new_entry.id)
        assert bpy.data.objects.get(plane_name) is None, "remove_coma_plane で Object 消滅"
        # Material も users==0 で削除されるはず
        mat_name = f"{cp.COMA_PLANE_MATERIAL_PREFIX}{page.id}_{new_entry.id}"
        assert bpy.data.materials.get(mat_name) is None, "remove_coma_plane で Material 消滅"
    finally:
        if mod is not None:
            mod.unregister()

    print("BNAME_COMA_PLANE_OK")


if __name__ == "__main__":
    main()
