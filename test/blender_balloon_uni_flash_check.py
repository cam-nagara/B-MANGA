"""Blender 実機用: フキダシからウニフラッシュを排除したことを確認。"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _evaluated_polygon_count(obj) -> int:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return len(getattr(mesh, "polygons", []) or [])
    finally:
        evaluated.to_mesh_clear()


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_balloon_uni_flash",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_balloon_uni_flash"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _enum_ids(prop) -> set[str]:
    return {str(getattr(item, "identifier", "") or "") for item in prop.enum_items}


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_balloon_uni_flash_removed_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "UniFlashRemoved.bname"))
        assert "FINISHED" in result, result

        from bname_dev_balloon_uni_flash.core.work import get_work
        from bname_dev_balloon_uni_flash.io import export_balloon, schema
        from bname_dev_balloon_uni_flash.operators import balloon_op
        from bname_dev_balloon_uni_flash.utils import balloon_curve_object, geometry_nodes_bridge
        from bname_dev_balloon_uni_flash.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        page_key = page_stack_key(page)

        entry = balloon_op._create_balloon_entry(
            context,
            page,
            shape="uni_flash",
            x=32.0,
            y=48.0,
            w=90.0,
            h=46.0,
            parent_kind="page",
            parent_key=page_key,
        )
        assert entry.shape == "ellipse", "旧ウニフラッシュ指定が楕円へ読み替わっていません"

        balloon_shape_ids = _enum_ids(entry.bl_rna.properties["shape"])
        assert "uni_flash" not in balloon_shape_ids, "フキダシ形状にウニフラッシュが残っています"

        saved = schema.balloon_entry_to_dict(entry)
        assert saved["shape"] == "ellipse", "保存データにウニフラッシュ形状が残っています"
        assert not any(str(key).startswith("uniFlash") for key in saved.get("shapeParams", {})), (
            "保存データにウニフラッシュ専用設定が残っています"
        )

        restored = page.balloons.add()
        schema.balloon_entry_from_dict(
            restored,
            {
                "id": "legacy_flash",
                "shape": "uni_flash",
                "xMm": 12.0,
                "yMm": 18.0,
                "widthMm": 40.0,
                "heightMm": 24.0,
                "shapeParams": {"uniFlashSpacingMm": 1.2, "uniFlashMaxLineCount": 400},
            },
        )
        assert restored.shape == "ellipse", "旧保存データのウニフラッシュが楕円へ読み替わっていません"
        page.balloons.remove(len(page.balloons) - 1)

        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        assert obj is not None and obj.type == "MESH", "フキダシのオブジェクトが作成されていません"
        assert len(obj.data.polygons) == 0, "フキダシ本体にB-Name側の表示メッシュが残っています"
        assert _evaluated_polygon_count(obj) > 0, "Geometry Nodesの表示結果が空です"
        modifier = obj.modifiers.get("B-Name Geometry Nodes")
        assert modifier is not None, "フキダシにGeometry Nodesモディファイアがありません"
        assert modifier.node_group is not None and modifier.node_group.name == "BName_GN_Balloon"
        assert obj.get(geometry_nodes_bridge.PROP_GN_KIND) == "balloon", "フキダシがウニフラッシュ用ノードを使っています"
        modifier.show_viewport = False
        bpy.context.view_layer.update()
        assert _evaluated_polygon_count(obj) == 0, "Geometry Nodesを非表示にしてもB-Name側の表示が残っています"
        modifier.show_viewport = True
        bpy.context.view_layer.update()

        layer = export_balloon.render_balloon_layer(entry, canvas_height_px=1200, dpi=144)
        assert layer is not None, "フキダシを書き出せません"
        assert layer.image.size[0] > 0 and layer.image.size[1] > 0
        print("BNAME_BALLOON_UNI_FLASH_REMOVED_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
