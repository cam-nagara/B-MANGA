"""Blender実機用: コマID再採番がページ上の読み順に従うことを確認."""

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
        "bmanga_dev_coma_renumber",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_coma_renumber"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _add_coma(page, coma_id: str, x_mm: float, y_mm: float):
    entry = page.comas.add()
    entry.id = coma_id
    entry.coma_id = coma_id
    entry.title = coma_id
    entry.shape_type = "rect"
    entry.rect_x_mm = x_mm
    entry.rect_y_mm = y_mm
    entry.rect_width_mm = 60.0
    entry.rect_height_mm = 40.0
    return entry


def _reset_page_comas(page) -> None:
    while len(page.comas):
        page.comas.remove(len(page.comas) - 1)


def _build_four_comas(page) -> None:
    _reset_page_comas(page)
    _add_coma(page, "c90", 20.0, 220.0)   # 上左
    _add_coma(page, "c30", 120.0, 160.0)  # 下右
    _add_coma(page, "c10", 120.0, 220.0)  # 上右
    _add_coma(page, "c50", 20.0, 160.0)   # 下左


def _assert_order(page, expected: list[tuple[str, float, float]]) -> None:
    actual = [
        (
            str(getattr(coma, "id", "") or ""),
            round(float(getattr(coma, "rect_x_mm", 0.0) or 0.0), 3),
            round(float(getattr(coma, "rect_y_mm", 0.0) or 0.0), 3),
        )
        for coma in page.comas
    ]
    if actual != expected:
        raise AssertionError(f"コマの読み順再採番が違います: expected={expected}, actual={actual}")


def _add_parented_entries(context, page, page_id: str) -> None:
    from bmanga_dev_coma_renumber.utils import gp_layer_parenting as gp_parent
    from bmanga_dev_coma_renumber.utils import gpencil as gp_utils
    from bmanga_dev_coma_renumber.utils import object_naming as on

    balloon = page.balloons.add()
    balloon.id = "balloon_read_order"
    balloon.parent_kind = "coma"
    balloon.parent_key = f"{page_id}:c10"

    text = page.texts.add()
    text.id = "text_read_order"
    text.parent_kind = "coma"
    text.parent_key = f"{page_id}:c90"

    folder = context.scene.bmanga_work.layer_folders.add()
    folder.id = "folder_read_order"
    folder.parent_key = f"{page_id}:c50"

    image = context.scene.bmanga_image_layers.add()
    image.id = "image_read_order"
    image.parent_kind = "coma"
    image.parent_key = f"{page_id}:c50"

    image_path = context.scene.bmanga_image_path_layers.add()
    image_path.id = "image_path_read_order"
    image_path.parent_kind = "coma"
    image_path.parent_key = f"{page_id}:c50"

    fill = context.scene.bmanga_fill_layers.add()
    fill.id = "fill_read_order"
    fill.parent_kind = "coma"
    fill.parent_key = f"{page_id}:c50"

    raster = context.scene.bmanga_raster_layers.add()
    raster.id = "raster_read_order"
    raster.parent_kind = "coma"
    raster.parent_key = f"{page_id}:c50"

    from bmanga_dev_coma_renumber.utils import gp_object_layer, layer_object_model

    gp_obj = gp_object_layer.create_layer_gp_object(
        scene=context.scene,
        bmanga_id="gp_read_order",
        title="gp_read_order",
        z_index=210,
        parent_kind="coma",
        parent_key=f"{page_id}:c30",
    )
    assert layer_object_model.content_layer(gp_obj) is not None

    obj = bpy.data.objects.new("parent_key_probe", None)
    obj[on.PROP_MANAGED] = True
    obj[on.PROP_PARENT_KEY] = f"{page_id}:c30"
    bpy.context.scene.collection.objects.link(obj)


def _assert_parented_entries(context, page, page_id: str) -> None:
    from bmanga_dev_coma_renumber.utils import gp_layer_parenting as gp_parent
    from bmanga_dev_coma_renumber.utils import layer_object_model
    from bmanga_dev_coma_renumber.utils import object_naming as on

    assert page.balloons[0].parent_key == f"{page_id}:c01"
    assert page.texts[0].parent_key == f"{page_id}:c02"
    assert context.scene.bmanga_work.layer_folders[0].parent_key == f"{page_id}:c04"
    assert context.scene.bmanga_image_layers[0].parent_key == f"{page_id}:c04"
    assert context.scene.bmanga_image_path_layers[0].parent_key == f"{page_id}:c04"
    assert context.scene.bmanga_fill_layers[0].parent_key == f"{page_id}:c04"
    assert context.scene.bmanga_raster_layers[0].parent_key == f"{page_id}:c04"

    gp_obj = layer_object_model.find_layer_object("gp", "gp_read_order")
    assert gp_obj is not None
    assert layer_object_model.parent_key(gp_obj) == f"{page_id}:c03"

    obj = bpy.data.objects.get("parent_key_probe")
    assert str(obj.get(on.PROP_PARENT_KEY, "") or "") == f"{page_id}:c03"


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_renumber_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "ComaRenumber.bmanga"))
        assert "FINISHED" in result, result

        from bmanga_dev_coma_renumber.utils import object_naming as on

        context = bpy.context
        work = context.scene.bmanga_work
        assert "FINISHED" in bpy.ops.bmanga.page_add("EXEC_DEFAULT")

        work.paper.read_direction = "left"
        page = work.pages[0]
        _build_four_comas(page)
        work.paper.read_direction = "right"
        _build_four_comas(work.pages[1])
        assert not bpy.ops.bmanga.coma_renumber_active_page.poll()

        assert "FINISHED" in bpy.ops.bmanga.open_page_file(index=0)
        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]
        page_id = str(page.id)
        work.paper.read_direction = "left"
        _add_parented_entries(context, page, page_id)
        assert "FINISHED" in bpy.ops.bmanga.coma_renumber_active_page("EXEC_DEFAULT")
        _assert_order(
            page,
            [
                ("c01", 120.0, 220.0),
                ("c02", 20.0, 220.0),
                ("c03", 120.0, 160.0),
                ("c04", 20.0, 160.0),
            ],
        )
        _assert_parented_entries(context, page, page_id)
        assert on.find_collection_by_bmanga_id(f"{page_id}:c01", kind="coma") is not None
        assert not [
            coll.name for coll in bpy.data.collections
            if str(coll.get(on.PROP_ID, "") or "").find("__coma_renumber_tmp__") >= 0
        ]

        assert "FINISHED" in bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT")
        assert "FINISHED" in bpy.ops.bmanga.open_page_file(index=1)
        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[1]
        work.paper.read_direction = "right"
        assert "FINISHED" in bpy.ops.bmanga.coma_renumber_active_page("EXEC_DEFAULT")
        _assert_order(
            page,
            [
                ("c01", 20.0, 220.0),
                ("c02", 120.0, 220.0),
                ("c03", 20.0, 160.0),
                ("c04", 120.0, 160.0),
            ],
        )

        print("BMANGA_COMA_RENUMBER_READING_ORDER_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
