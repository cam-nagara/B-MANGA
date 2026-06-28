"""Blender実機用: 枠線カット後の前後順・再採番・コマ実体階層を確認。"""

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
        "bmanga_dev_coma_knife_finalize",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_coma_knife_finalize"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_rect_coma(entry, coma_id: str, x_mm: float, y_mm: float, z_order: int) -> None:
    entry.id = coma_id
    entry.coma_id = coma_id
    entry.title = coma_id
    entry.shape_type = "rect"
    entry.rect_x_mm = x_mm
    entry.rect_y_mm = y_mm
    entry.rect_width_mm = 60.0
    entry.rect_height_mm = 60.0
    entry.z_order = z_order


def _center(coma) -> tuple[float, float]:
    if str(getattr(coma, "shape_type", "") or "") == "rect":
        return (
            float(coma.rect_x_mm) + float(coma.rect_width_mm) * 0.5,
            float(coma.rect_y_mm) + float(coma.rect_height_mm) * 0.5,
        )
    points = [(float(v.x_mm), float(v.y_mm)) for v in coma.vertices]
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _coma_by_id(page, coma_id: str):
    for coma in page.comas:
        if str(getattr(coma, "id", "") or "") == coma_id:
            return coma
    return None


def _object_by_pointer(pointer: int):
    for obj in bpy.data.objects:
        try:
            if int(obj.as_pointer()) == pointer:
                return obj
        except Exception:
            continue
    return None


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


def _add_scene_parented_layers(scene, page_id: str) -> None:
    for collection_name, layer_id in (
        ("bmanga_image_layers", "cut_image"),
        ("bmanga_image_path_layers", "cut_pattern_curve"),
        ("bmanga_fill_layers", "cut_fill"),
        ("bmanga_raster_layers", "cut_raster"),
    ):
        entry = getattr(scene, collection_name).add()
        entry.id = layer_id
        entry.parent_kind = "coma"
        entry.parent_key = f"{page_id}:c03"


def _assert_scene_parented_layers(scene, page_id: str) -> None:
    for collection_name in (
        "bmanga_image_layers",
        "bmanga_image_path_layers",
        "bmanga_fill_layers",
        "bmanga_raster_layers",
    ):
        entry = getattr(scene, collection_name)[0]
        if str(getattr(entry, "parent_key", "") or "") != f"{page_id}:c01":
            raise AssertionError(
                f"{collection_name} の親先が分割後の新しいコマIDへ追従していません: "
                f"{getattr(entry, 'parent_key', '')}"
            )


def _prepare_case(temp_root: Path):
    from bmanga_dev_coma_knife_finalize.io import coma_io, page_io

    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "knife_finalize.bmanga"))
    if "FINISHED" not in result:
        raise AssertionError(f"作品作成に失敗しました: {result}")
    work = bpy.context.scene.bmanga_work
    work.paper.read_direction = "left"
    page = work.pages[0]
    while len(page.comas) > 0:
        page.comas.remove(len(page.comas) - 1)

    entries = [
        ("c01", 20.0, 120.0, 3),
        ("c02", 20.0, 40.0, 0),
        ("c03", 120.0, 120.0, 2),
    ]
    work_dir = Path(work.work_dir)
    for coma_id, x_mm, y_mm, z_order in entries:
        entry = page.comas.add()
        _set_rect_coma(entry, coma_id, x_mm, y_mm, z_order)
        if coma_id == "c03":
            entry.border.style = "brush"
            entry.border.width_mm = 1.2
            entry.border.blur_amount = 0.8
        coma_io.save_coma_meta(work_dir, page.id, entry)
    page.coma_count = len(page.comas)
    page.active_coma_index = 2
    page_io.save_page_json(work_dir, page)
    page_io.save_pages_json(work_dir, work)
    result = bpy.ops.bmanga.open_page_file(index=0)
    if "FINISHED" not in result:
        raise AssertionError(f"ページ用blendファイルを開けません: {result}")
    return bpy.context.scene.bmanga_work, Path(bpy.context.scene.bmanga_work.work_dir)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_knife_finalize_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        work, work_dir = _prepare_case(temp_root)

        from bmanga_dev_coma_knife_finalize.operators import coma_knife_cut_op
        from bmanga_dev_coma_knife_finalize.utils import coma_plane, layer_object_sync, object_naming as on

        scene = bpy.context.scene
        page = work.pages[0]
        _add_scene_parented_layers(scene, str(getattr(page, "id", "") or ""))
        target_index = next(
            i for i, coma in enumerate(page.comas)
            if str(getattr(coma, "id", "") or "") == "c03"
        )
        target = page.comas[target_index]
        plane = coma_plane.ensure_coma_plane(scene, work, page, target)
        if plane is None:
            raise AssertionError("カット前の輪郭ぼかしコマ面が作成されていません")
        layer_object_sync.mirror_work_to_outliner(scene, work)
        old_pointer = int(plane.as_pointer())

        cut_x = float(target.rect_x_mm) + float(target.rect_width_mm) * 0.5
        ok = coma_knife_cut_op._apply_cut_to_coma(
            work,
            page,
            target_index,
            work_dir,
            (cut_x, float(target.rect_y_mm) - 5.0),
            (cut_x, float(target.rect_y_mm) + float(target.rect_height_mm) + 5.0),
        )
        if not ok:
            raise AssertionError("枠線カットに失敗しました")
        coma_knife_cut_op._finalize_cut_after_data_change(bpy.context, work, page, work_dir)
        page = work.pages[0]

        ids = [str(getattr(coma, "id", "") or "") for coma in page.comas]
        if ids != ["c01", "c02", "c03", "c04"]:
            raise AssertionError(f"カット後にコマIDが順番通りではありません: {ids}")
        _assert_scene_parented_layers(scene, str(getattr(page, "id", "") or ""))
        right_piece = _coma_by_id(page, "c01")
        left_piece = _coma_by_id(page, "c02")
        if right_piece is None or left_piece is None:
            raise AssertionError(f"分割後のコマが見つかりません: {ids}")
        if not (_center(right_piece)[0] > _center(left_piece)[0]):
            raise AssertionError("右側のコマが前面側IDになっていません")
        if not (int(right_piece.z_order) > int(left_piece.z_order)):
            raise AssertionError(
                f"右側のコマが前面になっていません: c01={right_piece.z_order} c02={left_piece.z_order}"
            )
        if int(left_piece.z_order) <= int(_coma_by_id(page, "c04").z_order):
            raise AssertionError("分割で生じた背面側コマがページ最背面へ落ちています")

        order = _stack_coma_order(page)
        if order.index("c01") >= order.index("c02"):
            raise AssertionError(f"レイヤーリストで右側コマが前面ではありません: {order}")

        retargeted_plane = _object_by_pointer(old_pointer)
        if retargeted_plane is None:
            raise AssertionError("輪郭ぼかしのコマ面が再採番時に作り直されています")
        if str(retargeted_plane.get(coma_plane.PROP_COMA_PLANE_OWNER_ID, "") or "") != f"{page.id}:c01":
            raise AssertionError(
                "輪郭ぼかしのコマ面の持ち主が新しいコマIDへ追従していません: "
                f"{retargeted_plane.name}"
            )
        parent_collections = [
            coll for coll in retargeted_plane.users_collection
            if str(coll.get(on.PROP_KIND, "") or "") == "coma"
        ]
        if not parent_collections or str(parent_collections[0].get(on.PROP_ID, "") or "") != f"{page.id}:c01":
            raise AssertionError("輪郭ぼかしのコマ面が新しいコマ階層へ入っていません")
        for coma_id in ids:
            meta_path = work_dir / page.id / coma_id / f"{coma_id}.json"
            if not meta_path.is_file():
                raise AssertionError(f"コマ用ファイル名が再採番に追従していません: {meta_path}")
        if any("__bmanga_coma_tmp__" in coll.name for coll in bpy.data.collections):
            raise AssertionError("再採番用の一時コマ階層が残っています")
        print("BMANGA_COMA_KNIFE_CUT_FINALIZE_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
