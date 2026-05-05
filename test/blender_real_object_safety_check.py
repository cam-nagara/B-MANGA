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


def _image_from_text_object(obj):
    mat = obj.active_material
    assert mat is not None, "text object has no material"
    assert getattr(mat, "use_nodes", False), "text material does not use nodes"
    for node in mat.node_tree.nodes:
        if getattr(node, "bl_idname", "") == "ShaderNodeTexImage":
            return getattr(node, "image", None)
    return None


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
        from bname_dev.utils import layer_object_sync
        from bname_dev.utils import object_naming as on
        from bname_dev.utils import outliner_model
        from bname_dev.utils import text_real_object
        from bname_dev.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        assert len(page.comas) > 0, "work_new should create a basic frame coma"
        coma = page.comas[0]
        page_key = page_stack_key(page)

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

        layer_object_sync.mirror_work_to_outliner(scene, work)
        text_coll = outliner_model.ensure_text_collection(scene)

        text_obj = on.find_object_by_bname_id(
            text_real_object.text_object_bname_id(page, text),
            kind="text",
        )
        text_full_id = text_real_object.text_object_bname_id(page, text)
        assert text_obj is not None, "text real object was not created"
        assert text_obj.type == "MESH", f"text should be a mesh plane, got {text_obj.type}"
        assert list(text_obj.users_collection) == [text_coll], "text object is not in テキスト collection"
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

        border_obj = bpy.data.objects.get(
            f"{coma_border_object.COMA_BORDER_NAME_PREFIX}{page.id}_{coma.id}"
        )
        assert border_obj is not None, "coma border curve was not created"
        assert border_obj.type == "CURVE", f"coma border should be a curve, got {border_obj.type}"
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
        assert balloon_obj is not None, "balloon curve was not created"
        assert balloon_obj.type == "CURVE", f"balloon should be a curve, got {balloon_obj.type}"
        balloon.visible = False
        assert balloon_obj.hide_viewport and balloon_obj.hide_render, "balloon visibility was not synced"
        balloon.visible = True
        assert not balloon_obj.hide_viewport and not balloon_obj.hide_render, "balloon visibility restore failed"
        balloon_real_objects = [
            obj
            for obj in bpy.data.objects
            if obj.get(on.PROP_KIND) == "balloon" and obj.get(on.PROP_ID) == balloon.id
        ]
        assert len(balloon_real_objects) == 1, "balloon curve object was duplicated"

        text_name = text_obj.name
        image_name = image.name
        border_name = border_obj.name
        balloon_name = balloon_obj.name
        reopen_path = temp_root / "real_object_safety_reopen.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(reopen_path))
        mod.unregister()
        mod = None

        assert bpy.data.objects.get(text_name) is not None, "text object disappeared after unregister"
        assert bpy.data.objects.get(border_name) is not None, "coma border disappeared after unregister"
        assert bpy.data.objects.get(balloon_name) is not None, "balloon disappeared after unregister"
        bpy.ops.wm.open_mainfile(filepath=str(reopen_path))
        assert bpy.data.objects.get(text_name) is not None, "text object disappeared after reopen"
        assert bpy.data.objects.get(border_name) is not None, "coma border disappeared after reopen"
        assert bpy.data.objects.get(balloon_name) is not None, "balloon disappeared after reopen"
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
