"""Blender 実機用: コマblendテンプレートの初回コピー確認."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _create_template(path: Path, marker_suffix: str = "") -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.name = "TemplateScene"
    scene.render.engine = "CYCLES"
    scene.render.film_transparent = True
    scene.render.resolution_x = 321
    scene.render.resolution_y = 123
    if len(scene.view_layers) > 0:
        scene.view_layers[0].name = "レイアウト"

    suffix = f"_{marker_suffix}" if marker_suffix else ""
    coll = bpy.data.collections.new(f"BMANGA_TEMPLATE_MARKER_COLLECTION{suffix}")
    scene.collection.children.link(coll)
    view_layer_name = f"BMANGA_TEMPLATE_MARKER_VIEW_LAYER{suffix}"
    if view_layer_name not in scene.view_layers:
        scene.view_layers.new(name=view_layer_name)
    layout_view_layer = scene.view_layers.get("レイアウト")
    if layout_view_layer is not None and bpy.context.window is not None:
        bpy.context.window.view_layer = layout_view_layer

    mesh = bpy.data.meshes.new(f"BMANGA_TEMPLATE_MARKER_MESH{suffix}")
    mesh.from_pydata(
        [(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    obj = bpy.data.objects.new(f"BMANGA_TEMPLATE_MARKER_OBJECT{suffix}", mesh)
    coll.objects.link(obj)

    mat = bpy.data.materials.new(f"BMANGA_TEMPLATE_MARKER_MATERIAL{suffix}")
    mat.use_nodes = True
    obj.data.materials.append(mat)
    node_group = bpy.data.node_groups.new(f"BMANGA_TEMPLATE_MARKER_NODE_GROUP{suffix}", "ShaderNodeTree")
    node_group.use_fake_user = True

    cam_data = bpy.data.cameras.new("Camera")
    cam_data.type = "PANO"
    if hasattr(cam_data, "show_limits"):
        cam_data.show_limits = False
    cam = bpy.data.objects.new("Camera", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False, compress=True)


def _assert_camera_limits_enabled() -> None:
    cam = getattr(bpy.context.scene, "camera", None)
    assert cam is not None
    if hasattr(cam.data, "show_limits"):
        assert bool(cam.data.show_limits) is True


def _first_page_with_detail(work):
    from bmanga_dev.utils import page_detail

    page = work.pages[0]
    page_detail.ensure_page_detail(work, page)
    return page


def _coma_index(page, coma_id: str) -> int:
    return next(
        index
        for index, candidate in enumerate(page.comas)
        if str(getattr(candidate, "coma_id", "") or "") == coma_id
    )


def _coma_native_uid(entry) -> str:
    uid = str(entry.get("bmanga_domain_coma_uid", "") or "")
    assert uid.startswith("coma_"), uid
    return uid


def _coma_index_by_uid(page, coma_uid: str) -> int:
    return next(
        index
        for index, candidate in enumerate(page.comas)
        if _coma_native_uid(candidate) == coma_uid
    )


def _activate_coma(work, page, coma_index: int) -> None:
    from bmanga_dev.utils import active_collection_sync

    page_index = next(
        index
        for index, candidate in enumerate(work.pages)
        if str(getattr(candidate, "id", "") or "")
        == str(getattr(page, "id", "") or "")
    )
    work.active_page_index = page_index
    page.active_coma_index = coma_index
    active_collection_sync.request_active_coma(
        bpy.context,
        str(getattr(page, "id", "") or ""),
        str(getattr(page.comas[coma_index], "id", "") or ""),
    )


def _stored_coma_settings(work_dir: Path, page_id: str, coma_uid: str) -> dict:
    from bmanga_dev.utils import paths

    payload = json.loads(
        paths.page_meta_path(work_dir, page_id).read_text(encoding="utf-8")
    )
    node = next(
        value
        for value in payload["tree"]["nodes"].values()
        if value["kind"] == "coma" and value["nativeUid"] == coma_uid
    )
    return node["settings"]


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_template_"))
    template_path = temp_root / "template.blend"
    coma_template_path = temp_root / "coma_template.blend"
    replacement_template_path = temp_root / "replacement_template.blend"
    work_dir = temp_root / "Template_Test.bmanga"
    mod = None
    try:
        _create_template(template_path)
        _create_template(coma_template_path, "COMA")
        _create_template(replacement_template_path, "REPLACE")
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        from bmanga_dev.utils import paths

        result = bpy.ops.bmanga.work_new(filepath=str(work_dir))
        assert result == {"FINISHED"}, result

        work = bpy.context.scene.bmanga_work
        work.coma_blend_template_path = str(template_path)
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert result == {"FINISHED"}, result
        work = bpy.context.scene.bmanga_work
        result = bpy.ops.bmanga.enter_coma_mode()
        assert result == {"FINISHED"}, result

        assert Path(bpy.data.filepath).resolve() == paths.coma_blend_path(
            work_dir, "p0001", "c01"
        ).resolve()
        assert bpy.context.scene.name == "TemplateScene"
        assert bpy.data.collections.get("BMANGA_TEMPLATE_MARKER_COLLECTION") is not None
        assert bpy.data.objects.get("BMANGA_TEMPLATE_MARKER_OBJECT") is not None
        assert bpy.context.scene.view_layers.get("BMANGA_TEMPLATE_MARKER_VIEW_LAYER") is not None
        assert bpy.data.materials.get("BMANGA_TEMPLATE_MARKER_MATERIAL") is not None
        assert bpy.data.node_groups.get("BMANGA_TEMPLATE_MARKER_NODE_GROUP") is not None
        assert bpy.context.scene.camera is not None
        assert bpy.context.scene.camera.data.type == "PANO"
        _assert_camera_limits_enabled()
        assert bpy.context.scene.view_layers.get("コマ枠") is not None
        assert bpy.context.view_layer.name == "レイアウト", bpy.context.view_layer.name

        result = bpy.ops.bmanga.exit_coma_mode()
        assert result == {"FINISHED"}, result

        work = bpy.context.scene.bmanga_work
        work.active_page_index = 0
        page = _first_page_with_detail(work)
        _activate_coma(work, page, 0)
        result = bpy.ops.bmanga.enter_coma_mode()
        assert result == {"FINISHED"}, result
        assert bpy.data.collections.get("BMANGA_TEMPLATE_MARKER_COLLECTION") is not None
        assert bpy.data.objects.get("BMANGA_TEMPLATE_MARKER_OBJECT") is not None
        assert bpy.data.node_groups.get("BMANGA_TEMPLATE_MARKER_NODE_GROUP") is not None
        _assert_camera_limits_enabled()

        result = bpy.ops.bmanga.exit_coma_mode()
        assert result == {"FINISHED"}, result

        work = bpy.context.scene.bmanga_work
        work.active_page_index = 0
        _first_page_with_detail(work)
        result = bpy.ops.bmanga.coma_add()
        assert result == {"FINISHED"}, result
        page = _first_page_with_detail(work)
        assert len(page.comas) >= 2
        coma_index = _coma_index(page, "c02")
        _activate_coma(work, page, coma_index)
        page.comas[coma_index].coma_blend_template_path = str(coma_template_path)
        target_coma_uid = _coma_native_uid(page.comas[coma_index])
        target_blend_path = paths.coma_blend_path(
            work_dir, page.id, target_coma_uid
        ).resolve()
        from bmanga_dev.utils import coma_scene

        resolved, error = coma_scene.resolve_coma_blend_template_path(
            work,
            Path(work.work_dir),
            page.comas[coma_index],
        )
        assert error == "", error
        assert resolved == coma_template_path.resolve(), resolved

        result = bpy.ops.bmanga.enter_coma_mode()
        assert result == {"FINISHED"}, result
        assert Path(bpy.data.filepath).resolve() == target_blend_path
        assert bpy.data.objects.get("BMANGA_TEMPLATE_MARKER_OBJECT_COMA") is not None
        assert bpy.data.node_groups.get("BMANGA_TEMPLATE_MARKER_NODE_GROUP_COMA") is not None
        assert bpy.data.objects.get("BMANGA_TEMPLATE_MARKER_OBJECT") is None
        _assert_camera_limits_enabled()

        result = bpy.ops.bmanga.exit_coma_mode()
        assert result == {"FINISHED"}, result
        stored_coma = _stored_coma_settings(
            work_dir, "p0001", target_coma_uid
        )
        assert stored_coma["comaBlendTemplatePath"] == str(coma_template_path)
        assert stored_coma["comaBlendTemplateNeedsApply"] is False

        work = bpy.context.scene.bmanga_work
        work.active_page_index = 0
        page = _first_page_with_detail(work)
        coma_index = _coma_index_by_uid(page, target_coma_uid)
        _activate_coma(work, page, coma_index)
        page.comas[coma_index].coma_blend_template_path = str(replacement_template_path)
        assert page.comas[coma_index].coma_blend_template_needs_apply is True
        result = bpy.ops.bmanga.enter_coma_mode()
        assert result == {"FINISHED"}, result
        assert Path(bpy.data.filepath).resolve() == target_blend_path
        assert bpy.data.objects.get("BMANGA_TEMPLATE_MARKER_OBJECT_REPLACE") is not None
        assert bpy.data.objects.get("BMANGA_TEMPLATE_MARKER_OBJECT_COMA") is None
        _assert_camera_limits_enabled()

        result = bpy.ops.bmanga.exit_coma_mode()
        assert result == {"FINISHED"}, result
        stored_coma = _stored_coma_settings(
            work_dir, "p0001", target_coma_uid
        )
        assert stored_coma["comaBlendTemplatePath"] == str(replacement_template_path)
        assert stored_coma["comaBlendTemplateNeedsApply"] is False

        work = bpy.context.scene.bmanga_work
        work.active_page_index = 0
        page = _first_page_with_detail(work)
        coma_index = _coma_index_by_uid(page, target_coma_uid)
        _activate_coma(work, page, coma_index)
        page.comas[coma_index].coma_blend_template_path = ""
        assert page.comas[coma_index].coma_blend_template_needs_apply is True
        result = bpy.ops.bmanga.enter_coma_mode()
        assert result == {"FINISHED"}, result
        assert bpy.data.objects.get("BMANGA_TEMPLATE_MARKER_OBJECT_REPLACE") is not None
        _assert_camera_limits_enabled()

        result = bpy.ops.bmanga.exit_coma_mode()
        assert result == {"FINISHED"}, result
        stored_coma = _stored_coma_settings(
            work_dir, "p0001", target_coma_uid
        )
        assert stored_coma["comaBlendTemplatePath"] == ""
        assert stored_coma["comaBlendTemplateNeedsApply"] is False

        work = bpy.context.scene.bmanga_work
        work.active_page_index = 0
        _first_page_with_detail(work)
        result = bpy.ops.bmanga.coma_add()
        assert result == {"FINISHED"}, result
        page = _first_page_with_detail(work)
        coma_index = _coma_index(page, "c03")
        _activate_coma(work, page, coma_index)
        assert page.comas[coma_index].coma_blend_template_path == ""
        selected_coma_uid = _coma_native_uid(page.comas[coma_index])
        selected_blend_path = paths.coma_blend_path(
            work_dir, page.id, selected_coma_uid
        ).resolve()
        result = bpy.ops.bmanga.enter_coma_mode(filepath=str(coma_template_path))
        assert result == {"FINISHED"}, result
        assert Path(bpy.data.filepath).resolve() == selected_blend_path
        assert bpy.data.objects.get("BMANGA_TEMPLATE_MARKER_OBJECT_COMA") is not None
        _assert_camera_limits_enabled()

        result = bpy.ops.bmanga.exit_coma_mode()
        assert result == {"FINISHED"}, result
        stored_coma = _stored_coma_settings(
            work_dir, "p0001", selected_coma_uid
        )
        assert stored_coma["comaBlendTemplatePath"] == str(coma_template_path)

        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "Template_Prefs.bmanga"))
        assert result == {"FINISHED"}, result
        work = bpy.context.scene.bmanga_work
        work.coma_blend_template_path = ""

        from bmanga_dev import preferences
        from bmanga_dev.operators import object_tool_op
        from bmanga_dev.utils import coma_scene, edge_selection, object_selection
        from bmanga_dev.ui import overlay_coma_selection

        original_get_preferences = preferences.get_preferences
        preferences.get_preferences = lambda _context=None: SimpleNamespace(
            coma_blend_template_path=str(template_path)
        )
        try:
            resolved, error = coma_scene.resolve_coma_blend_template_path(
                work,
                Path(work.work_dir),
            )
            assert error == "", error
            assert resolved == template_path.resolve(), resolved

            page = _first_page_with_detail(work)
            coma = page.comas[0]
            hit = {
                "kind": "coma",
                "page": 0,
                "coma": 0,
                "part": "body",
                "key": object_selection.coma_key(page, coma),
            }
            assert object_tool_op.enter_coma_from_hit(bpy.context, hit)
            assert Path(bpy.data.filepath).resolve() == paths.coma_blend_path(
                temp_root / "Template_Prefs.bmanga", "p0001", "c01"
            ).resolve()
            assert bpy.data.objects.get("BMANGA_TEMPLATE_MARKER_OBJECT") is not None
            assert bpy.data.node_groups.get("BMANGA_TEMPLATE_MARKER_NODE_GROUP") is not None
            _assert_camera_limits_enabled()
        finally:
            preferences.get_preferences = original_get_preferences

        region = SimpleNamespace(x=100, y=50)
        event = SimpleNamespace(
            mouse_x=999,
            mouse_y=999,
            mouse_region_x=18,
            mouse_region_y=24,
        )
        edge_selection.update_overlay_pointer(bpy.context, region, event)
        assert edge_selection.get_overlay_pointer(bpy.context) == (18, 24)
        assert overlay_coma_selection._is_handle_hovered((20.0, 25.0), (18, 24))
        print("BMANGA_COMA_TEMPLATE_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
