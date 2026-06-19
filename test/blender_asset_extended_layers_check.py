"""Blender実機用: コマ/ラスター/GPの素材登録と別ページ移動を確認."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_asset_extended",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_asset_extended"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _drop_collection(context, coll, world_x_mm: float, world_y_mm: float) -> None:
    from bmanga_dev_asset_extended.utils import asset_bundle
    from bmanga_dev_asset_extended.utils.geom import mm_to_m

    inst = bpy.data.objects.new(f"drop_{coll.name}", None)
    inst.instance_type = "COLLECTION"
    inst.instance_collection = coll
    inst.location = (mm_to_m(world_x_mm), mm_to_m(world_y_mm), 0.0)
    context.scene.collection.objects.link(inst)
    if not asset_bundle.process_dropped_collection_instance(context, inst):
        raise AssertionError(f"{coll.name}: 配置復元に失敗しました")


def _stack_index(context, kind: str, key_contains: str) -> int:
    from bmanga_dev_asset_extended.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    for index, item in enumerate(stack):
        if str(getattr(item, "kind", "") or "") != kind:
            continue
        key = str(getattr(item, "key", "") or "")
        label = str(getattr(item, "label", "") or getattr(item, "name", "") or "")
        if key_contains in key or key_contains in label:
            return index
    raise AssertionError(f"{kind}:{key_contains} がレイヤー一覧にありません")


def _register_index(context, index: int, name: str):
    from bmanga_dev_asset_extended.utils import asset_bundle

    coll = asset_bundle.register_selected_layers_as_asset(context, index=index, name=name)
    if coll is None or coll.asset_data is None:
        raise AssertionError(f"{name}: アセット登録されていません")
    return coll


def _make_balloon(context, page, parent_key: str):
    from bmanga_dev_asset_extended.operators import balloon_op
    from bmanga_dev_asset_extended.utils import balloon_curve_object

    entry = page.balloons.add()
    entry.id = balloon_op._allocate_balloon_id(page, context.scene.bmanga_work)
    entry.title = "素材フキダシ"
    entry.shape = "ellipse"
    entry.x_mm = 25.0
    entry.y_mm = 30.0
    entry.width_mm = 45.0
    entry.height_mm = 28.0
    entry.parent_kind = "coma"
    entry.parent_key = parent_key
    balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    return entry


def _make_raster(context, parent_key: str):
    from bmanga_dev_asset_extended.operators import raster_layer_op

    result = bpy.ops.bmanga.raster_layer_add(
        "EXEC_DEFAULT",
        dpi=30,
        bit_depth="gray8",
        enter_paint=False,
    )
    if "FINISHED" not in result:
        raise AssertionError("ラスターを追加できません")
    entry = context.scene.bmanga_raster_layers[context.scene.bmanga_active_raster_layer_index]
    entry.title = "素材ラスター"
    entry.parent_kind = "coma"
    entry.parent_key = parent_key
    entry.scope = "page"
    image = raster_layer_op.ensure_raster_image(context, entry, create_missing=True)
    width, height = int(image.size[0]), int(image.size[1])
    pixels = [0.0] * (width * height * 4)
    for y in range(height // 3, height // 3 + max(1, height // 8)):
        for x in range(width // 4, width // 4 + max(1, width // 3)):
            index = (y * width + x) * 4
            pixels[index:index + 4] = [0.0, 0.0, 0.0, 1.0]
    image.pixels = pixels
    image.update()
    raster_layer_op.save_raster_png(context, entry, force=True)
    raster_layer_op.ensure_raster_plane(context, entry)
    return entry


def _make_gp(context, parent_key: str):
    from bmanga_dev_asset_extended.utils import gp_layer_parenting as gp_parent
    from bmanga_dev_asset_extended.utils import gpencil as gp_utils
    from bmanga_dev_asset_extended.utils.geom import mm_to_m

    obj = gp_utils.ensure_master_gpencil(context.scene)
    layer = obj.data.layers.new("素材GP")
    gp_parent.set_parent_key(layer, parent_key)
    gp_utils.ensure_layer_material(obj, layer, activate=True, assign_existing=True)
    frame = gp_utils.ensure_active_frame(layer, frame_number=1)
    points = [
        (mm_to_m(20.0), mm_to_m(20.0), 0.0),
        (mm_to_m(40.0), mm_to_m(45.0), 0.0),
        (mm_to_m(65.0), mm_to_m(25.0), 0.0),
    ]
    if not gp_utils.add_stroke_to_drawing(frame.drawing, points, radius=0.01):
        raise AssertionError("GPストロークを作成できません")
    return layer


def _page_center_world(context, page_index: int) -> tuple[float, float]:
    from bmanga_dev_asset_extended.utils import page_grid

    work = context.scene.bmanga_work
    ox, oy = page_grid.page_total_offset_mm(work, context.scene, page_index)
    paper = work.paper
    return ox + float(paper.canvas_width_mm) * 0.5, oy + float(paper.canvas_height_mm) * 0.5


def _gp_layers_for_parent(context, parent_key: str):
    from bmanga_dev_asset_extended.utils import gp_layer_parenting as gp_parent
    from bmanga_dev_asset_extended.utils import gpencil as gp_utils

    obj = gp_utils.get_master_gpencil()
    return [
        layer
        for layer in getattr(getattr(obj, "data", None), "layers", []) or []
        if gp_parent.parent_key(layer) == parent_key
    ]


def main() -> None:
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        temp_root = Path(tempfile.mkdtemp(prefix="bmanga_asset_extended_"))
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "AssetExtended.bmanga"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")
        result = bpy.ops.bmanga.page_add("EXEC_DEFAULT")
        if "FINISHED" not in result:
            raise AssertionError(f"ページ追加に失敗しました: {result}")

        from bmanga_dev_asset_extended.utils import layer_stack as layer_stack_utils
        from bmanga_dev_asset_extended.utils.layer_hierarchy import coma_stack_key

        context = bpy.context
        work = context.scene.bmanga_work
        work.active_page_index = 0
        page1 = work.pages[0]
        page2 = work.pages[1]
        panel = page1.comas[0]
        parent_key = coma_stack_key(page1, panel)
        _make_balloon(context, page1, parent_key)
        raster = _make_raster(context, parent_key)
        gp_layer = _make_gp(context, parent_key)
        layer_stack_utils.sync_layer_stack_after_data_change(context)

        # 選択中のラスターを別ページへ移動できること。
        raster_index = _stack_index(context, "raster", raster.id)
        layer_stack_utils.select_stack_index(context, raster_index)
        result = bpy.ops.bmanga.layer_move_to_page("EXEC_DEFAULT", target_page_id=page2.id)
        if "FINISHED" not in result:
            raise AssertionError(f"別ページへ移動できません: {result}")
        if raster.parent_key != page2.id or raster.parent_kind != "page":
            raise AssertionError("ラスターの移動先ページが反映されていません")
        raster.parent_kind = "coma"
        raster.parent_key = parent_key
        work.active_page_index = 0
        layer_stack_utils.sync_layer_stack_after_data_change(context)

        # コマを中身ごと素材登録し、別ページへ配置できること。
        coma_index = _stack_index(context, "coma", panel.coma_id)
        coll = _register_index(context, coma_index, "コマ一式")
        before_comas = len(page2.comas)
        before_balloons = len(page2.balloons)
        before_rasters = len(context.scene.bmanga_raster_layers)
        before_gp = len(getattr(gp_layer.id_data, "layers", []))
        work.active_page_index = 1
        wx, wy = _page_center_world(context, 1)
        _drop_collection(context, coll, wx, wy)
        if len(page2.comas) != before_comas + 1:
            raise AssertionError("コマ素材が復元されていません")
        new_parent = coma_stack_key(page2, page2.comas[-1])
        if len(page2.balloons) != before_balloons + 1:
            raise AssertionError("コマ内フキダシが復元されていません")
        if len(context.scene.bmanga_raster_layers) != before_rasters + 1:
            raise AssertionError("コマ内ラスターが復元されていません")
        if len(getattr(gp_layer.id_data, "layers", [])) != before_gp + 1:
            raise AssertionError("コマ内GPが復元されていません")
        if page2.balloons[-1].parent_key != new_parent:
            raise AssertionError("コマ内フキダシの所属が新しいコマに向いていません")
        if context.scene.bmanga_raster_layers[len(context.scene.bmanga_raster_layers) - 1].parent_key != new_parent:
            raise AssertionError("コマ内ラスターの所属が新しいコマに向いていません")
        if not _gp_layers_for_parent(context, new_parent):
            raise AssertionError("コマ内GPの所属が新しいコマに向いていません")

        # ラスター単体、GP単体もB-MANGA素材として登録・配置できること。
        work.active_page_index = 0
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        raster_index = _stack_index(context, "raster", raster.id)
        raster_coll = _register_index(context, raster_index, "ラスター単体")
        before_rasters = len(context.scene.bmanga_raster_layers)
        work.active_page_index = 1
        _drop_collection(context, raster_coll, wx, wy)
        if len(context.scene.bmanga_raster_layers) != before_rasters + 1:
            raise AssertionError("ラスター単体素材が復元されていません")

        work.active_page_index = 0
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        gp_index = _stack_index(context, "gp", gp_layer.name)
        gp_coll = _register_index(context, gp_index, "GP単体")
        before_gp = len(getattr(gp_layer.id_data, "layers", []))
        work.active_page_index = 1
        _drop_collection(context, gp_coll, wx, wy)
        if len(getattr(gp_layer.id_data, "layers", [])) != before_gp + 1:
            raise AssertionError("GP単体素材が復元されていません")

        print("BMANGA_ASSET_EXTENDED_LAYERS_OK")
    finally:
        if mod is not None:
            mod.unregister()


if __name__ == "__main__":
    main()
