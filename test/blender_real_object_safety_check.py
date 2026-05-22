"""Blender 実機用: 作品要素がアドオン停止後も実体として残ることを確認."""

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


def _evaluated_polygon_count(obj) -> int:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return len(getattr(mesh, "polygons", []) or [])
    finally:
        evaluated.to_mesh_clear()


def _image_from_text_object(obj):
    mat = obj.active_material
    assert mat is not None, "text object has no material"
    assert getattr(mat, "use_nodes", False), "text material does not use nodes"
    for node in mat.node_tree.nodes:
        if getattr(node, "bl_idname", "") == "ShaderNodeTexImage":
            return getattr(node, "image", None)
    return None


def _make_source_png(path: Path) -> None:
    img = bpy.data.images.new("bname_real_object_source", width=2, height=2, alpha=True)
    img.pixels.foreach_set([
        1.0, 0.0, 0.0, 1.0,
        0.0, 1.0, 0.0, 1.0,
        0.0, 0.0, 1.0, 1.0,
        1.0, 1.0, 1.0, 1.0,
    ])
    img.filepath_raw = str(path)
    img.file_format = "PNG"
    img.save()


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_real_object_safety_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "RealObjectSafety.bname"))
        assert result == {"FINISHED"}, result

        from bname_dev.core.work import get_work
        from bname_dev.operators import balloon_op, text_op
        from bname_dev.utils import balloon_curve_object
        from bname_dev.utils import coma_border_object
        from bname_dev.utils import image_real_object
        from bname_dev.utils import layer_object_sync
        from bname_dev.utils import object_naming as on
        from bname_dev.utils import outliner_model
        from bname_dev.utils import page_grid
        from bname_dev.utils import paper_guide_object
        from bname_dev.utils import text_real_object
        from bname_dev.utils.layer_hierarchy import OUTSIDE_STACK_KEY, page_stack_key

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        assert work is not None and work.loaded
        assert "FINISHED" in bpy.ops.bname.page_add("EXEC_DEFAULT")
        page = work.pages[0]
        page2 = work.pages[1]
        assert len(page.comas) > 0, "work_new should create a basic frame coma"
        coma = page.comas[0]
        page_key = page_stack_key(page)
        page2_key = page_stack_key(page2)

        text, missing = text_op._create_text_entry(
            context,
            page,
            body="実体テキスト",
            speaker_type="normal",
            x_mm=24.0,
            y_mm=38.0,
            width_mm=30.0,
            height_mm=22.0,
            parent_kind="page",
            parent_key=page_key,
        )
        assert not missing
        balloon = balloon_op._create_balloon_entry(
            context,
            page,
            shape="ellipse",
            x=55.0,
            y=50.0,
            w=32.0,
            h=20.0,
            parent_kind="page",
            parent_key=page_key,
        )
        source_png = temp_root / "source.png"
        _make_source_png(source_png)
        image_entry = scene.bname_image_layers.add()
        image_entry.id = "image_real_0001"
        image_entry.title = "実体画像"
        image_entry.filepath = str(source_png)
        image_entry.x_mm = 12.0
        image_entry.y_mm = 16.0
        image_entry.width_mm = 20.0
        image_entry.height_mm = 12.0
        image_entry.opacity = 0.75
        image_entry.parent_kind = "page"
        image_entry.parent_key = page_key
        folder = work.layer_folders.add()
        folder.id = "folder_image_page2"
        folder.title = "画像フォルダ"
        folder.parent_key = page2_key
        folder.expanded = True
        folder_image_entry = scene.bname_image_layers.add()
        folder_image_entry.id = "image_real_folder_page2"
        folder_image_entry.title = "フォルダ内画像"
        folder_image_entry.filepath = str(source_png)
        folder_image_entry.x_mm = 18.0
        folder_image_entry.y_mm = 21.0
        folder_image_entry.width_mm = 16.0
        folder_image_entry.height_mm = 10.0
        folder_image_entry.parent_kind = "folder"
        folder_image_entry.parent_key = folder.id
        folder_image_entry.folder_key = folder.id
        outside_folder = work.layer_folders.add()
        outside_folder.id = "folder_image_outside"
        outside_folder.title = "ページ外画像フォルダ"
        outside_folder.parent_key = OUTSIDE_STACK_KEY
        outside_folder.expanded = True
        outside_folder_image_entry = scene.bname_image_layers.add()
        outside_folder_image_entry.id = "image_real_folder_outside"
        outside_folder_image_entry.title = "ページ外フォルダ内画像"
        outside_folder_image_entry.filepath = str(source_png)
        outside_folder_image_entry.x_mm = 11.0
        outside_folder_image_entry.y_mm = 13.0
        outside_folder_image_entry.width_mm = 9.0
        outside_folder_image_entry.height_mm = 5.0
        outside_folder_image_entry.parent_kind = "folder"
        outside_folder_image_entry.parent_key = outside_folder.id
        outside_folder_image_entry.folder_key = outside_folder.id
        outside_image_entry = scene.bname_image_layers.add()
        outside_image_entry.id = "image_real_outside"
        outside_image_entry.title = "ページ外画像"
        outside_image_entry.filepath = str(source_png)
        outside_image_entry.x_mm = 7.0
        outside_image_entry.y_mm = 9.0
        outside_image_entry.width_mm = 8.0
        outside_image_entry.height_mm = 6.0
        outside_image_entry.parent_kind = "none"
        outside_image_entry.parent_key = ""
        coma.white_margin.enabled = True
        coma.white_margin.width_mm = 1.5
        coma.border.style = "dashed"
        work.safe_area_overlay.opacity = 0.17
        work.safe_area_overlay.color = (0.25, 0.50, 0.75)
        old_safe_mat = bpy.data.materials.get("BName_SafeAreaFill") or bpy.data.materials.new("BName_SafeAreaFill")
        old_safe_mesh = bpy.data.meshes.get(f"{paper_guide_object.PAPER_SAFE_FILL_MESH_PREFIX}{page.id}")
        if old_safe_mesh is None:
            old_safe_mesh = bpy.data.meshes.new(f"{paper_guide_object.PAPER_SAFE_FILL_MESH_PREFIX}{page.id}")
        if len(old_safe_mesh.materials) == 0:
            old_safe_mesh.materials.append(old_safe_mat)

        layer_object_sync.mirror_work_to_outliner(scene, work)
        text_coll = outliner_model.ensure_text_collection(scene)

        text_obj = on.find_object_by_bname_id(
            text_real_object.text_object_bname_id(page, text),
            kind="text",
        )
        text_full_id = text_real_object.text_object_bname_id(page, text)
        assert text_obj is not None, "text real object was not created"
        assert text_obj.type == "MESH", f"text should be a mesh plane, got {text_obj.type}"
        assert text_coll.name == "text"
        assert list(text_obj.users_collection) == [text_coll], "text object is not in text collection"
        image = _image_from_text_object(text_obj)
        assert image is not None, "text object has no texture image"
        assert image.size[0] > 0 and image.size[1] > 0

        text.visible = False
        assert text_real_object.on_text_entry_changed(text), "text visibility sync helper failed"
        assert text_obj.hide_viewport and text_obj.hide_render, "text visibility was not synced"
        text.visible = True
        assert not text_obj.hide_viewport and not text_obj.hide_render, "text visibility restore failed"
        text.body = "実体テキスト更新"
        image = _image_from_text_object(text_obj)
        assert image is not None and image.size[0] > 0 and image.size[1] > 0
        text_real_objects = [
            obj
            for obj in bpy.data.objects
            if obj.get(on.PROP_KIND) == "text" and obj.get(on.PROP_ID) == text_full_id
        ]
        assert len(text_real_objects) == 1, "text real object was duplicated"

        old_empty = bpy.data.objects.get(f"text_{text.id}")
        assert old_empty is None or old_empty.type != "EMPTY", "legacy text Empty still exists"

        image_obj = on.find_object_by_bname_id(image_entry.id, kind="image")
        assert image_obj is not None, "image real object was not created"
        assert image_obj.type == "MESH", f"image should be a mesh plane, got {image_obj.type}"
        assert image_obj.active_material is not None, "image real object has no material"
        assert image_obj.data.uv_layers.active is not None, "image real object has no UV"
        legacy_image_empty = bpy.data.objects.get(f"image_{image_entry.id}")
        assert legacy_image_empty is None or legacy_image_empty.type != "EMPTY", "legacy image Empty still exists"
        folder_image_obj = on.find_object_by_bname_id(folder_image_entry.id, kind="image")
        assert folder_image_obj is not None, "folder image real object was not created"
        ox2, oy2 = page_grid.page_total_offset_mm(work, scene, 1)
        expected_x = ox2 + folder_image_entry.x_mm + folder_image_entry.width_mm * 0.5
        expected_y = oy2 + folder_image_entry.y_mm + folder_image_entry.height_mm * 0.5
        actual_x = folder_image_obj.location.x * 1000.0
        actual_y = folder_image_obj.location.y * 1000.0
        assert abs(actual_x - expected_x) < 1e-4, (actual_x, expected_x, folder_image_obj.location[:])
        assert abs(actual_y - expected_y) < 1e-4, (actual_y, expected_y, folder_image_obj.location[:])
        outside_image_obj = on.find_object_by_bname_id(outside_image_entry.id, kind="image")
        assert outside_image_obj is not None, "outside image real object was not created"
        expected_outside_x = outside_image_entry.x_mm + outside_image_entry.width_mm * 0.5
        expected_outside_y = outside_image_entry.y_mm + outside_image_entry.height_mm * 0.5
        assert abs(outside_image_obj.location.x * 1000.0 - expected_outside_x) < 1e-4
        assert abs(outside_image_obj.location.y * 1000.0 - expected_outside_y) < 1e-4
        outside_folder_image_obj = on.find_object_by_bname_id(outside_folder_image_entry.id, kind="image")
        assert outside_folder_image_obj is not None, "outside folder image real object was not created"
        expected_outside_folder_x = outside_folder_image_entry.x_mm + outside_folder_image_entry.width_mm * 0.5
        expected_outside_folder_y = outside_folder_image_entry.y_mm + outside_folder_image_entry.height_mm * 0.5
        assert abs(outside_folder_image_obj.location.x * 1000.0 - expected_outside_folder_x) < 1e-4
        assert abs(outside_folder_image_obj.location.y * 1000.0 - expected_outside_folder_y) < 1e-4
        page2.offset_x_mm += 17.0
        page2.offset_y_mm -= 11.0
        page_grid.apply_page_collection_transforms(context, work)
        ox2, oy2 = page_grid.page_total_offset_mm(work, scene, 1)
        expected_x = ox2 + folder_image_entry.x_mm + folder_image_entry.width_mm * 0.5
        expected_y = oy2 + folder_image_entry.y_mm + folder_image_entry.height_mm * 0.5
        actual_x = folder_image_obj.location.x * 1000.0
        actual_y = folder_image_obj.location.y * 1000.0
        assert abs(actual_x - expected_x) < 1e-4, (actual_x, expected_x, folder_image_obj.location[:])
        assert abs(actual_y - expected_y) < 1e-4, (actual_y, expected_y, folder_image_obj.location[:])

        def _guide_objects():
            return [
                obj
                for obj in bpy.data.objects
                if str(obj.get(paper_guide_object.PROP_GUIDE_OWNER_ID, "") or "") == page.id
                and str(obj.get(paper_guide_object.PROP_GUIDE_KIND, "") or "") == paper_guide_object.GUIDE_KIND_LINES
                and obj.type == "CURVE"
            ]

        guide_objects = _guide_objects()
        assert guide_objects, "paper guide objects were not created"
        assert len(guide_objects) == 1, "paper guides should be one curve object per page"
        assert any(len(getattr(obj.data, "splines", []) or []) > 0 for obj in guide_objects), (
            "paper guide splines were not created"
        )
        assert not any(bool(getattr(obj, "show_in_front", False)) for obj in guide_objects), (
            "paper guide should not rely on viewport in-front wire display"
        )
        work.paper.show_guides = False
        paper_guide_object.regenerate_all_paper_guides(scene, work)
        hidden_guide_objects = _guide_objects()
        assert hidden_guide_objects, "paper guide objects disappeared after hiding guides"
        assert not any(len(getattr(obj.data, "splines", []) or []) > 0 for obj in hidden_guide_objects), (
            "用紙ガイドをオフにしてもガイド線が残っています"
        )
        work.paper.show_guides = True
        paper_guide_object.regenerate_all_paper_guides(scene, work)
        guide_objects = _guide_objects()
        assert guide_objects, "paper guide objects disappeared after showing guides"
        assert any(len(getattr(obj.data, "splines", []) or []) > 0 for obj in guide_objects), (
            "用紙ガイドをオンに戻してもガイド線が復元されません"
        )
        safe_fill_obj = bpy.data.objects.get(f"{paper_guide_object.PAPER_SAFE_FILL_PREFIX}{page.id}")
        assert safe_fill_obj is not None, "safe area fill object was not created"
        assert safe_fill_obj.type == "MESH", f"safe area fill should be a mesh, got {safe_fill_obj.type}"
        assert getattr(safe_fill_obj, "display_type", "") == "SOLID", "safe area fill should display as solid"
        assert bool(getattr(safe_fill_obj, "show_in_front", False)), "safe area fill is not in front in viewport"
        assert bool(getattr(safe_fill_obj, "show_transparent", False)), "safe area fill is not transparent in viewport"
        safe_mat = safe_fill_obj.active_material
        assert safe_mat is not None, "safe area fill needs a viewport material in texture shading"
        assert safe_mat.name.startswith(paper_guide_object.PAPER_SAFE_FILL_VIEW_MATERIAL), safe_mat.name
        assert len(getattr(safe_fill_obj.data, "materials", [])) == 1, "safe area fill material slot count is wrong"
        assert bpy.data.materials.get("BName_SafeAreaFill") is None, "old safe area fill material was not removed"
        expected_safe_color = (0.25, 0.50, 0.75, 0.17)
        for actual, expected in zip(safe_fill_obj.color, expected_safe_color, strict=False):
            assert abs(float(actual) - expected) < 1.0e-4, (tuple(safe_fill_obj.color), expected_safe_color)
        for actual, expected in zip(safe_mat.diffuse_color, expected_safe_color, strict=False):
            assert abs(float(actual) - expected) < 1.0e-4, (tuple(safe_mat.diffuse_color), expected_safe_color)

        border_obj = bpy.data.objects.get(
            f"{coma_border_object.COMA_BORDER_NAME_PREFIX}{page.id}_{coma.id}"
        )
        assert border_obj is not None, "coma border curve was not created"
        assert border_obj.type == "CURVE", f"coma border should be a curve, got {border_obj.type}"
        assert safe_fill_obj.location.z < border_obj.location.z, (
            "safe area fill should stay behind coma objects to avoid viewport flicker"
        )
        assert all(obj.location.z > safe_fill_obj.location.z for obj in guide_objects), (
            "paper guide lines should be above safe area fill"
        )
        assert all(obj.location.z < border_obj.location.z for obj in guide_objects), (
            "paper guide lines should stay behind coma objects to avoid viewport flicker"
        )
        assert len(border_obj.data.splines) > 1, "dashed coma border did not create multiple real strokes"
        white_margin_obj = bpy.data.objects.get(
            f"{coma_border_object.COMA_WHITE_MARGIN_NAME_PREFIX}{page.id}_{coma.id}"
        )
        assert white_margin_obj is not None, "coma white margin object was not created"
        assert white_margin_obj.type == "MESH", f"coma white margin should be a mesh, got {white_margin_obj.type}"
        assert not border_obj.hide_viewport, "coma border should be visible"
        coma.border.visible = False
        assert border_obj.hide_viewport and border_obj.hide_render, "coma border visibility was not synced"
        coma.border.visible = True
        assert not border_obj.hide_viewport and not border_obj.hide_render, "coma border visibility restore failed"
        coma.visible = False
        assert border_obj.hide_viewport and border_obj.hide_render, "coma visibility was not synced to border"
        coma.visible = True
        assert not border_obj.hide_viewport and not border_obj.hide_render, "coma visibility restore failed"

        _ = balloon_curve_object.BALLOON_CURVE_NAME_PREFIX
        balloon_obj = on.find_object_by_bname_id(balloon.id, kind="balloon")
        assert balloon_obj is not None, "balloon mesh was not created"
        assert balloon_obj.type == "MESH", f"balloon should be a mesh, got {balloon_obj.type}"
        assert len(balloon_obj.data.polygons) == 0, "balloon should not keep B-Name generated display mesh"
        assert len(balloon_obj.data.materials) >= 2, "balloon should contain line and fill materials"
        assert _evaluated_polygon_count(balloon_obj) > 0, "balloon Geometry Nodes result has no polygons"
        modifier = balloon_obj.modifiers.get("B-Name Geometry Nodes")
        assert modifier is not None, "balloon should have Geometry Nodes modifier"
        modifier.show_viewport = False
        bpy.context.view_layer.update()
        assert _evaluated_polygon_count(balloon_obj) == 0, "balloon fallback mesh remains when Geometry Nodes is hidden"
        modifier.show_viewport = True
        bpy.context.view_layer.update()
        source_obj = bpy.data.objects.get(f"{balloon_curve_object.BALLOON_SOURCE_NAME_PREFIX}{balloon.id}")
        assert source_obj is not None, "balloon reference shape was not created"
        assert source_obj.hide_viewport and source_obj.hide_render and source_obj.hide_select, (
            "balloon reference shape should not be visible"
        )
        balloon_fill_obj = bpy.data.objects.get(f"{balloon_curve_object.BALLOON_FILL_NAME_PREFIX}{balloon.id}")
        assert balloon_fill_obj is None, "balloon fill object should not be separate"
        balloon.visible = False
        assert balloon_obj.hide_viewport and balloon_obj.hide_render, "balloon visibility was not synced"
        balloon.visible = True
        assert not balloon_obj.hide_viewport and not balloon_obj.hide_render, "balloon visibility restore failed"
        tail = balloon.tails.add()
        tail.type = "straight"
        tail.direction_deg = 270.0
        tail.length_mm = 12.0
        tail.root_width_mm = 5.0
        assert balloon_curve_object.on_balloon_entry_changed(balloon), "balloon tail sync failed"
        balloon_obj = on.find_object_by_bname_id(balloon.id, kind="balloon")
        assert balloon_obj is not None and balloon_obj.type == "MESH"
        tail_poly_count = _evaluated_polygon_count(balloon_obj)
        assert tail_poly_count > 0, "balloon tail was not added to real mesh"
        tail.length_mm = 14.0
        assert balloon_curve_object.on_balloon_entry_changed(balloon), "balloon tail update sync failed"
        balloon_obj = on.find_object_by_bname_id(balloon.id, kind="balloon")
        assert balloon_obj is not None and _evaluated_polygon_count(balloon_obj) > 0, "balloon tail update removed real mesh"
        balloon_real_objects = [
            obj
            for obj in bpy.data.objects
            if obj.get(on.PROP_KIND) == "balloon" and obj.get(on.PROP_ID) == balloon.id
        ]
        assert len(balloon_real_objects) == 1, "balloon curve object was duplicated"

        text_name = text_obj.name
        image_name = image.name
        border_name = border_obj.name
        image_obj_name = image_obj.name
        folder_image_obj_name = folder_image_obj.name
        outside_image_obj_name = outside_image_obj.name
        outside_folder_image_obj_name = outside_folder_image_obj.name
        guide_name = guide_objects[0].name
        safe_fill_name = safe_fill_obj.name
        white_margin_name = white_margin_obj.name
        balloon_name = balloon_obj.name
        balloon_fill_name = f"{balloon_curve_object.BALLOON_FILL_NAME_PREFIX}{balloon.id}"
        reopen_path = temp_root / "real_object_safety_reopen.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(reopen_path))
        mod.unregister()
        mod = None

        assert bpy.data.objects.get(text_name) is not None, "text object disappeared after unregister"
        assert bpy.data.objects.get(image_obj_name) is not None, "image object disappeared after unregister"
        assert bpy.data.objects.get(folder_image_obj_name) is not None, "folder image disappeared after unregister"
        assert bpy.data.objects.get(outside_image_obj_name) is not None, "outside image disappeared after unregister"
        assert bpy.data.objects.get(outside_folder_image_obj_name) is not None, "outside folder image disappeared after unregister"
        assert bpy.data.objects.get(border_name) is not None, "coma border disappeared after unregister"
        assert bpy.data.objects.get(guide_name) is not None, "paper guide disappeared after unregister"
        assert bpy.data.objects.get(safe_fill_name) is not None, "safe area fill disappeared after unregister"
        assert bpy.data.objects.get(white_margin_name) is not None, "coma white margin disappeared after unregister"
        assert bpy.data.objects.get(balloon_name) is not None, "balloon disappeared after unregister"
        assert bpy.data.objects.get(balloon_fill_name) is None, "balloon fill object reappeared after unregister"
        bpy.ops.wm.open_mainfile(filepath=str(reopen_path))
        assert bpy.data.objects.get(text_name) is not None, "text object disappeared after reopen"
        assert bpy.data.objects.get(image_obj_name) is not None, "image object disappeared after reopen"
        assert bpy.data.objects.get(folder_image_obj_name) is not None, "folder image disappeared after reopen"
        assert bpy.data.objects.get(outside_image_obj_name) is not None, "outside image disappeared after reopen"
        assert bpy.data.objects.get(outside_folder_image_obj_name) is not None, "outside folder image disappeared after reopen"
        assert bpy.data.objects.get(border_name) is not None, "coma border disappeared after reopen"
        assert bpy.data.objects.get(guide_name) is not None, "paper guide disappeared after reopen"
        assert bpy.data.objects.get(safe_fill_name) is not None, "safe area fill disappeared after reopen"
        assert bpy.data.objects.get(white_margin_name) is not None, "coma white margin disappeared after reopen"
        assert bpy.data.objects.get(balloon_name) is not None, "balloon disappeared after reopen"
        assert bpy.data.objects.get(balloon_fill_name) is None, "balloon fill object reappeared after reopen"
        reopened_image = bpy.data.images.get(image_name)
        assert reopened_image is not None, "text texture image disappeared after reopen"
        assert reopened_image.size[0] > 0 and reopened_image.size[1] > 0
        print("OK: real object safety check passed")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
