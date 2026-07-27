"""Blender実機用: 他ページ選択ゲートと旧ページプレビュー実体の移行確認."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_page_preview_selection_gate"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("B-MANGA addon spec could not be created")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    module.register()
    return module


def _add_page_entries(page, page_key: str) -> tuple[str, str]:
    balloon = page.balloons.add()
    balloon.id = "other_page_balloon"
    balloon.parent_kind = "page"
    balloon.parent_key = page_key
    balloon.x_mm = 20.0
    balloon.y_mm = 30.0
    balloon.width_mm = 30.0
    balloon.height_mm = 20.0
    text = page.texts.add()
    text.id = "other_page_text"
    text.parent_kind = "page"
    text.parent_key = page_key
    text.x_mm = 22.0
    text.y_mm = 32.0
    text.width_mm = 20.0
    text.height_mm = 10.0
    return balloon.id, text.id


def _add_legacy_preview(page_id: str) -> None:
    from bmanga_dev_page_preview_selection_gate.utils import object_naming as on
    from bmanga_dev_page_preview_selection_gate.utils import page_preview_object

    mesh = bpy.data.meshes.new(f"{page_preview_object.PREVIEW_MESH_PREFIX}{page_id}")
    mesh.from_pydata(
        [(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    material = bpy.data.materials.new(
        f"{page_preview_object.PREVIEW_MATERIAL_PREFIX}{page_id}"
    )
    mesh.materials.append(material)
    obj = bpy.data.objects.new(
        f"{page_preview_object.PREVIEW_OBJECT_PREFIX}{page_id}",
        mesh,
    )
    obj[on.PROP_KIND] = page_preview_object.PREVIEW_KIND
    collection = bpy.data.collections.new(page_preview_object.PREVIEW_COLLECTION_NAME)
    collection[on.PROP_KIND] = page_preview_object.PREVIEW_KIND
    bpy.context.scene.collection.children.link(collection)
    collection.objects.link(obj)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_page_preview_gate_"))
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        result = bpy.ops.bmanga.work_new(
            filepath=str(temp_root / "PagePreviewGate.bmanga")
        )
        assert "FINISHED" in result, result
        assert "FINISHED" in bpy.ops.bmanga.page_add("EXEC_DEFAULT")
        assert bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0) == {"FINISHED"}

        from bmanga_dev_page_preview_selection_gate.utils import (
            layer_hierarchy,
            object_selection,
            page_file_scene,
            page_preview_object,
        )
        from bmanga_dev_page_preview_selection_gate.operators import (
            object_tool_selection,
        )

        context = bpy.context
        work = context.scene.bmanga_work
        current_page = work.pages[0]
        other_page = work.pages[1]
        other_page_key = layer_hierarchy.page_stack_key(other_page)
        balloon_id, text_id = _add_page_entries(other_page, other_page_key)

        assert page_file_scene.editable_page_ids(context.scene) == {
            str(current_page.id)
        }
        assert page_file_scene.is_page_child_pickable(
            context.scene,
            str(current_page.id),
        )
        assert not page_file_scene.is_page_child_pickable(
            context.scene,
            str(other_page.id),
        )

        keys = {
            str(candidate["key"])
            for candidate in object_tool_selection._iter_rect_select_candidates(context)
        }
        assert object_selection.page_key(other_page) in keys
        assert object_selection.balloon_key(other_page, other_page.balloons[0]) not in keys
        assert object_selection.text_key(other_page, other_page.texts[0]) not in keys
        assert balloon_id and text_id

        _add_legacy_preview(str(other_page.id))
        assert bpy.data.collections.get(page_preview_object.PREVIEW_COLLECTION_NAME)
        page_preview_object.sync_page_previews(context, work, force=False)
        assert bpy.data.collections.get(page_preview_object.PREVIEW_COLLECTION_NAME) is None
        assert not list(page_preview_object._iter_preview_objects())
        assert not any(
            mesh.name.startswith(page_preview_object.PREVIEW_MESH_PREFIX)
            for mesh in bpy.data.meshes
        )
        assert not any(
            material.name.startswith(page_preview_object.PREVIEW_MATERIAL_PREFIX)
            for material in bpy.data.materials
        )

        bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath, check_existing=False)
        assert bpy.data.collections.get(page_preview_object.PREVIEW_COLLECTION_NAME) is None
        print("BMANGA_PAGE_PREVIEW_SELECTION_GATE_OK")
    finally:
        if module is not None:
            try:
                module.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
