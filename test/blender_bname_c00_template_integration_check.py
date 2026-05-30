"""Blender実機用: B-Name が c00.blend をコマ用テンプレートとして使えることを確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BLEND = Path(r"D:\TM Dropbox\Share\B-Name\c_file\c00.blend")


REQUIRED_COLLECTIONS = {
    "キャラ",
    "背景",
    "背景MH",
    "効果",
    "コマ枠",
    "グラデ_白",
    "グラデ_黒",
    "フォグ",
    "雲",
}

REQUIRED_VIEW_LAYERS = {
    "レイアウト",
    "キャラ",
    "キャラアルファ",
    "背景",
    "効果",
    "効果アルファ",
    "空",
}

REQUIRED_NODE_GROUPS = {
    "出力_キャラ",
    "出力_キャラアルファ",
    "出力_キャラ線画Pencil+4",
    "出力_背景",
    "出力_背景AOV",
    "出力_背景線画Pencil+4",
    "出力_効果",
    "出力_効果アルファ",
}


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_c00_template",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_c00_template"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _all_collection_names(collection) -> set[str]:
    names = {collection.name}
    for child in collection.children:
        names.update(_all_collection_names(child))
    return names


def _assert_template_elements_survived() -> dict:
    scene = bpy.context.scene
    collections = _all_collection_names(scene.collection)
    view_layers = {layer.name for layer in scene.view_layers}
    node_groups = {group.name for group in bpy.data.node_groups}
    missing = {
        "collections": sorted(REQUIRED_COLLECTIONS - collections),
        "view_layers": sorted(REQUIRED_VIEW_LAYERS - view_layers),
        "node_groups": sorted(REQUIRED_NODE_GROUPS - node_groups),
    }
    blocking = {key: value for key, value in missing.items() if value}
    assert not blocking, blocking

    camera = scene.camera
    camera_data = camera.data if camera is not None and camera.type == "CAMERA" else None
    assert camera_data is not None, "c00 camera was not preserved"
    if camera_data.type == "PANO":
        assert float(getattr(camera_data, "fisheye_fov", 0.0)) > 3.0

    mat = bpy.data.materials.get("マテリアルセット")
    socket_names = _material_input_names(mat) if mat is not None and getattr(mat, "use_nodes", False) else set()
    return {
        "collections": len(collections),
        "view_layers": len(view_layers),
        "node_groups": len(node_groups),
        "camera": camera.name,
        "camera_type": camera_data.type,
        "material_sockets": sorted(socket_names),
    }


def _material_input_names(material) -> set[str]:
    names: set[str] = set()

    def walk(node_tree, seen=None):
        seen = set() if seen is None else seen
        if node_tree is None:
            return
        key = int(node_tree.as_pointer())
        if key in seen:
            return
        seen.add(key)
        for node in node_tree.nodes:
            for socket in getattr(node, "inputs", []):
                names.add(socket.name)
            if getattr(node, "type", "") == "GROUP":
                walk(getattr(node, "node_tree", None), seen)

    walk(material.node_tree)
    return names


def _assert_bname_coma_state(work_dir: Path) -> dict:
    from bname_dev_c00_template.core.mode import MODE_COMA, get_mode
    from bname_dev_c00_template.utils.coma_camera_constants import MANAGED_IMAGE_PROP
    from bname_dev_c00_template.utils.geom import mm_to_px

    scene = bpy.context.scene
    assert get_mode(bpy.context) == MODE_COMA
    assert scene.bname_current_coma_page_id == "p0001"
    assert scene.bname_current_coma_id == "c01"
    assert Path(bpy.data.filepath).resolve() == (work_dir / "p0001" / "c01" / "c01.blend").resolve()
    assert bpy.context.scene.view_layers.get("コマ枠") is not None
    assert bpy.context.view_layer.name == "レイアウト", bpy.context.view_layer.name

    work = scene.bname_work
    paper = work.paper
    expected = (
        int(round(mm_to_px(float(paper.canvas_width_mm), int(paper.dpi)))),
        int(round(mm_to_px(float(paper.canvas_height_mm), int(paper.dpi)))),
    )
    actual = (int(scene.render.resolution_x), int(scene.render.resolution_y))
    assert actual == expected, (actual, expected)

    managed = []
    data = getattr(getattr(scene, "camera", None), "data", None)
    for bg in getattr(data, "background_images", []) or []:
        image = getattr(bg, "image", None)
        try:
            if image is not None and bool(image.get(MANAGED_IMAGE_PROP, False)):
                managed.append(bg)
        except Exception:
            pass
    assert managed, "B-Name page image background was not added to c00 camera"
    assert any(getattr(bg.image, "filepath", "") for bg in managed if getattr(bg, "image", None) is not None)
    return {
        "resolution": actual,
        "managed_backgrounds": len(managed),
    }


def main() -> None:
    template = Path(str(DEFAULT_BLEND))
    if not template.exists():
        raise FileNotFoundError(template)
    temp_root = Path(tempfile.mkdtemp(prefix="bname_c00_template_"))
    work_dir = temp_root / "C00_Template.bname"
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(work_dir))
        assert result == {"FINISHED"}, result
        work = bpy.context.scene.bname_work
        work.coma_blend_template_path = str(template)
        result = bpy.ops.bname.enter_coma_mode()
        assert result == {"FINISHED"}, result

        template_result = _assert_template_elements_survived()
        bname_result = _assert_bname_coma_state(work_dir)
        coma_path = work_dir / "p0001" / "c01" / "c01.blend"
        reopen_result = bpy.ops.wm.open_mainfile(filepath=str(coma_path))
        assert reopen_result == {"FINISHED"}, reopen_result
        reopen_bname_result = _assert_bname_coma_state(work_dir)
        print(
            "BNAME_C00_TEMPLATE_INTEGRATION_OK "
            f"template={template_result} bname={bname_result} "
            f"reopen={reopen_bname_result}"
        )
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
