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


def _create_template(path: Path, marker_suffix: str = "") -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.name = "TemplateScene"
    scene.render.engine = "CYCLES"
    scene.render.film_transparent = True
    scene.render.resolution_x = 321
    scene.render.resolution_y = 123

    suffix = f"_{marker_suffix}" if marker_suffix else ""
    coll = bpy.data.collections.new(f"BNAME_TEMPLATE_MARKER_COLLECTION{suffix}")
    scene.collection.children.link(coll)
    view_layer_name = f"BNAME_TEMPLATE_MARKER_VIEW_LAYER{suffix}"
    if view_layer_name not in scene.view_layers:
        scene.view_layers.new(name=view_layer_name)

    mesh = bpy.data.meshes.new(f"BNAME_TEMPLATE_MARKER_MESH{suffix}")
    mesh.from_pydata(
        [(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    obj = bpy.data.objects.new(f"BNAME_TEMPLATE_MARKER_OBJECT{suffix}", mesh)
    coll.objects.link(obj)

    mat = bpy.data.materials.new(f"BNAME_TEMPLATE_MARKER_MATERIAL{suffix}")
    mat.use_nodes = True
    obj.data.materials.append(mat)
    node_group = bpy.data.node_groups.new(f"BNAME_TEMPLATE_MARKER_NODE_GROUP{suffix}", "ShaderNodeTree")
    node_group.use_fake_user = True

    cam_data = bpy.data.cameras.new("Camera")
    cam_data.type = "PANO"
    cam = bpy.data.objects.new("Camera", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False, compress=True)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_coma_template_"))
    template_path = temp_root / "template.blend"
    coma_template_path = temp_root / "coma_template.blend"
    replacement_template_path = temp_root / "replacement_template.blend"
    work_dir = temp_root / "Template_Test.bname"
    mod = None
    try:
        _create_template(template_path)
        _create_template(coma_template_path, "COMA")
        _create_template(replacement_template_path, "REPLACE")
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()

        result = bpy.ops.bname.work_new(filepath=str(work_dir))
        assert result == {"FINISHED"}, result

        work = bpy.context.scene.bname_work
        work.coma_blend_template_path = str(template_path)
        result = bpy.ops.bname.enter_coma_mode()
        assert result == {"FINISHED"}, result

        assert Path(bpy.data.filepath).resolve() == (work_dir / "p0001" / "c01" / "c01.blend").resolve()
        assert bpy.context.scene.name == "TemplateScene"
        assert bpy.data.collections.get("BNAME_TEMPLATE_MARKER_COLLECTION") is not None
        assert bpy.data.objects.get("BNAME_TEMPLATE_MARKER_OBJECT") is not None
        assert bpy.context.scene.view_layers.get("BNAME_TEMPLATE_MARKER_VIEW_LAYER") is not None
        assert bpy.data.materials.get("BNAME_TEMPLATE_MARKER_MATERIAL") is not None
        assert bpy.data.node_groups.get("BNAME_TEMPLATE_MARKER_NODE_GROUP") is not None
        assert bpy.context.scene.camera is not None
        assert bpy.context.scene.camera.data.type == "PANO"

        result = bpy.ops.bname.exit_coma_mode()
        assert result == {"FINISHED"}, result

        work = bpy.context.scene.bname_work
        work.active_page_index = 0
        work.pages[0].active_coma_index = 0
        result = bpy.ops.bname.enter_coma_mode()
        assert result == {"FINISHED"}, result
        assert bpy.data.collections.get("BNAME_TEMPLATE_MARKER_COLLECTION") is not None
        assert bpy.data.objects.get("BNAME_TEMPLATE_MARKER_OBJECT") is not None
        assert bpy.data.node_groups.get("BNAME_TEMPLATE_MARKER_NODE_GROUP") is not None

        result = bpy.ops.bname.exit_coma_mode()
        assert result == {"FINISHED"}, result

        work = bpy.context.scene.bname_work
        work.active_page_index = 0
        result = bpy.ops.bname.coma_add()
        assert result == {"FINISHED"}, result
        page = work.pages[0]
        assert len(page.comas) >= 2
        coma_index = next(
            idx for idx, candidate in enumerate(page.comas)
            if str(getattr(candidate, "coma_id", "") or "") == "c02"
        )
        page.active_coma_index = coma_index
        page.comas[coma_index].coma_blend_template_path = str(coma_template_path)
        from bname_dev.utils import coma_scene

        resolved, error = coma_scene.resolve_coma_blend_template_path(
            work,
            Path(work.work_dir),
            page.comas[coma_index],
        )
        assert error == "", error
        assert resolved == coma_template_path.resolve(), resolved

        result = bpy.ops.bname.enter_coma_mode()
        assert result == {"FINISHED"}, result
        assert Path(bpy.data.filepath).resolve() == (work_dir / "p0001" / "c02" / "c02.blend").resolve()
        assert bpy.data.objects.get("BNAME_TEMPLATE_MARKER_OBJECT_COMA") is not None
        assert bpy.data.node_groups.get("BNAME_TEMPLATE_MARKER_NODE_GROUP_COMA") is not None
        assert bpy.data.objects.get("BNAME_TEMPLATE_MARKER_OBJECT") is None

        result = bpy.ops.bname.exit_coma_mode()
        assert result == {"FINISHED"}, result
        page_json = json.loads((work_dir / "p0001" / "page.json").read_text(encoding="utf-8"))
        stored_coma = next(item for item in page_json["comas"] if item["comaId"] == "c02")
        assert stored_coma["comaBlendTemplatePath"] == str(coma_template_path)
        assert stored_coma["comaBlendTemplateNeedsApply"] is False

        work = bpy.context.scene.bname_work
        work.active_page_index = 0
        page = work.pages[0]
        page.active_coma_index = coma_index
        page.comas[coma_index].coma_blend_template_path = str(replacement_template_path)
        assert page.comas[coma_index].coma_blend_template_needs_apply is True
        result = bpy.ops.bname.enter_coma_mode()
        assert result == {"FINISHED"}, result
        assert Path(bpy.data.filepath).resolve() == (work_dir / "p0001" / "c02" / "c02.blend").resolve()
        assert bpy.data.objects.get("BNAME_TEMPLATE_MARKER_OBJECT_REPLACE") is not None
        assert bpy.data.objects.get("BNAME_TEMPLATE_MARKER_OBJECT_COMA") is None

        result = bpy.ops.bname.exit_coma_mode()
        assert result == {"FINISHED"}, result
        page_json = json.loads((work_dir / "p0001" / "page.json").read_text(encoding="utf-8"))
        stored_coma = next(item for item in page_json["comas"] if item["comaId"] == "c02")
        assert stored_coma["comaBlendTemplatePath"] == str(replacement_template_path)
        assert stored_coma["comaBlendTemplateNeedsApply"] is False

        work = bpy.context.scene.bname_work
        work.active_page_index = 0
        page = work.pages[0]
        page.active_coma_index = coma_index
        page.comas[coma_index].coma_blend_template_path = ""
        assert page.comas[coma_index].coma_blend_template_needs_apply is True
        result = bpy.ops.bname.enter_coma_mode()
        assert result == {"FINISHED"}, result
        assert bpy.data.objects.get("BNAME_TEMPLATE_MARKER_OBJECT_REPLACE") is not None

        result = bpy.ops.bname.exit_coma_mode()
        assert result == {"FINISHED"}, result
        page_json = json.loads((work_dir / "p0001" / "page.json").read_text(encoding="utf-8"))
        stored_coma = next(item for item in page_json["comas"] if item["comaId"] == "c02")
        assert stored_coma["comaBlendTemplatePath"] == ""
        assert stored_coma["comaBlendTemplateNeedsApply"] is False

        work = bpy.context.scene.bname_work
        work.active_page_index = 0
        result = bpy.ops.bname.coma_add()
        assert result == {"FINISHED"}, result
        page = work.pages[0]
        coma_index = next(
            idx for idx, candidate in enumerate(page.comas)
            if str(getattr(candidate, "coma_id", "") or "") == "c03"
        )
        page.active_coma_index = coma_index
        assert page.comas[coma_index].coma_blend_template_path == ""
        result = bpy.ops.bname.enter_coma_mode(filepath=str(coma_template_path))
        assert result == {"FINISHED"}, result
        assert Path(bpy.data.filepath).resolve() == (work_dir / "p0001" / "c03" / "c03.blend").resolve()
        assert bpy.data.objects.get("BNAME_TEMPLATE_MARKER_OBJECT_COMA") is not None

        result = bpy.ops.bname.exit_coma_mode()
        assert result == {"FINISHED"}, result
        page_json = json.loads((work_dir / "p0001" / "page.json").read_text(encoding="utf-8"))
        stored_coma = next(item for item in page_json["comas"] if item["comaId"] == "c03")
        assert stored_coma["comaBlendTemplatePath"] == str(coma_template_path)

        result = bpy.ops.bname.work_new(filepath=str(temp_root / "Template_Prefs.bname"))
        assert result == {"FINISHED"}, result
        work = bpy.context.scene.bname_work
        work.coma_blend_template_path = ""

        from bname_dev import preferences
        from bname_dev.operators import object_tool_op
        from bname_dev.utils import coma_scene, edge_selection, object_selection
        from bname_dev.ui import overlay_coma_selection

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

            page = work.pages[0]
            coma = page.comas[0]
            hit = {
                "kind": "coma",
                "page": 0,
                "coma": 0,
                "part": "body",
                "key": object_selection.coma_key(page, coma),
            }
            assert object_tool_op.enter_coma_from_hit(bpy.context, hit)
            assert Path(bpy.data.filepath).resolve() == (
                temp_root / "Template_Prefs.bname" / "p0001" / "c01" / "c01.blend"
            ).resolve()
            assert bpy.data.objects.get("BNAME_TEMPLATE_MARKER_OBJECT") is not None
            assert bpy.data.node_groups.get("BNAME_TEMPLATE_MARKER_NODE_GROUP") is not None
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
        print("BNAME_COMA_TEMPLATE_OK")
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
