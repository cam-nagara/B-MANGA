"""Blender実機用: 画像パスの実体生成・保存・プリセット確認."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_image_path"
PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYPgPAAEDAQCW"
    "A0r4AAAAAElFTkSuQmCC"
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _write_png(path: Path) -> None:
    path.write_bytes(base64.b64decode(PNG_1PX))


def _uv_values(obj) -> list[tuple[float, float]]:
    uv_layer = getattr(obj.data, "uv_layers", None)
    assert uv_layer is not None and uv_layer.active is not None, "UV がありません"
    return [tuple(data.uv) for data in uv_layer.active.data]


def _point_colors(obj) -> list[tuple[float, float, float, float]]:
    attr = getattr(obj.data, "attributes", None)
    assert attr is not None, "頂点属性がありません"
    layer = attr.get("bmanga_path_content_color")
    assert layer is not None, "色の頂点属性がありません"
    return [tuple(data.color) for data in layer.data]


def _polygon_widths(obj) -> list[float]:
    widths: list[float] = []
    for poly in obj.data.polygons:
        xs = [float(obj.data.vertices[i].co.x) for i in poly.vertices]
        widths.append(max(xs) - min(xs))
    return widths


def _material_has_content_mask(mat) -> bool:
    nt = getattr(mat, "node_tree", None)
    if nt is None:
        return False
    has_tex = False
    has_coord = False
    for node in nt.nodes:
        if (
            node.bl_idname == "ShaderNodeTexImage"
            and node.label == "コマ内容マスク"
            and getattr(node, "image", None) is not None
        ):
            has_tex = True
        if (
            node.bl_idname == "ShaderNodeTexCoord"
            and node.label == "コマ内容マスク座標"
            and getattr(node, "object", None) is not None
        ):
            has_coord = True
    return has_tex and has_coord


def _assert_close(actual: float, expected: float, label: str, tol: float = 1.0e-4) -> None:
    assert abs(float(actual) - float(expected)) <= tol, f"{label}: expected {expected}, got {actual}"


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_image_path_"))
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(temp_root / "config")
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "ImagePath.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file()
        assert "FINISHED" in result, result

        from bmanga_dev_image_path.io import image_path_presets, schema
        from bmanga_dev_image_path.utils import image_path_object, layer_stack as layer_stack_utils
        from bmanga_dev_image_path.utils import mask_apply
        from bmanga_dev_image_path.utils import object_selection, object_state_sync
        from bmanga_dev_image_path.utils.geom import mm_to_m
        from bmanga_dev_image_path.utils.layer_hierarchy import coma_stack_key, page_stack_key
        from bmanga_dev_image_path.operators import object_tool_op, object_tool_selection

        image_path = temp_root / "source.png"
        _write_png(image_path)

        context = bpy.context
        scene = context.scene
        work = scene.bmanga_work
        page = work.pages[0]
        page_key = page_stack_key(page)
        coma = page.comas[0]
        coma.shape_type = "rect"
        coma.rect_x_mm = 0.0
        coma.rect_y_mm = 0.0
        coma.rect_width_mm = 80.0
        coma.rect_height_mm = 80.0
        coma_key = coma_stack_key(page, coma)
        from bmanga_dev_image_path.utils import coma_plane

        coma_plane.ensure_coma_mask(scene, work, page, coma)

        entry = scene.bmanga_image_path_layers.add()
        entry.id = "image_path_test"
        entry.title = "画像パステスト"
        entry.filepath = str(image_path)
        entry.parent_kind = "page"
        entry.parent_key = page_key
        entry.path_points_json = json.dumps([[10.0, 20.0], [50.0, 20.0], [70.0, 45.0]])
        entry.draw_mode = "stamp"
        entry.brush_size_mm = 10.0
        entry.aspect_ratio = 1.25
        entry.spacing_percent = 50.0
        entry.stamp_angle_mode = "line"

        obj = image_path_object.ensure_image_path_object(scene=scene, entry=entry, page=page)
        assert obj is not None, "画像パス実体が作成されません"
        assert len(obj.data.polygons) >= 8, f"スタンプ数が少なすぎます: {len(obj.data.polygons)}"
        assert object_state_sync.is_sync_candidate(obj), "画像パスが標準移動の同期対象ではありません"
        assert obj.data.materials and obj.data.materials[0] is not None, "画像パスのマテリアルがありません"
        assert _point_colors(obj), "画像パスに色属性がありません"
        page_mod = obj.modifiers.get(mask_apply.MOD_NAME_PAGE_MASK)
        assert page_mod is not None and getattr(page_mod, "object", None) is not None, (
            "ページ直下の画像パスにページマスクがありません"
        )

        stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        assert stack is not None
        uid = layer_stack_utils.target_uid("image_path", entry.id)
        assert any(layer_stack_utils.stack_item_uid(item) == uid for item in stack), "レイヤー一覧に画像パスがありません"
        key = object_selection.image_path_key(entry)
        assert object_selection.parse_key(key) == ("image_path", "", entry.id)
        bounds = object_tool_selection.selection_bounds_for_key(context, key)
        assert bounds is not None and bounds.width > 0.0 and bounds.height > 0.0, "画像パスの選択枠が取れません"

        fake_op = SimpleNamespace(_drag_action="move")
        snapshots = object_tool_op.BMANGA_OT_object_tool._make_snapshots(
            fake_op,
            context,
            [key],
            primary_key=key,
            action="move",
        )
        assert snapshots, "画像パスの移動準備ができません"
        fake_op._snapshots = snapshots
        handle_before_points = json.loads(entry.path_points_json)
        object_tool_op.BMANGA_OT_object_tool._apply_snapshots(fake_op, context, 4.0, -2.5)
        handle_after_points = json.loads(entry.path_points_json)
        _assert_close(handle_after_points[0][0], handle_before_points[0][0] + 4.0, "ハンドル移動後 X")
        _assert_close(handle_after_points[0][1], handle_before_points[0][1] - 2.5, "ハンドル移動後 Y")

        before_points = json.loads(entry.path_points_json)
        obj.location.x += mm_to_m(7.0)
        assert image_path_object.sync_entry_points_from_object(scene, obj), "画像パス移動が点列へ戻りません"
        after_points = json.loads(entry.path_points_json)
        _assert_close(after_points[0][0], before_points[0][0] + 7.0, "移動後 X")
        _assert_close(after_points[0][1], before_points[0][1], "移動後 Y")

        entry.draw_mode = "ribbon"
        entry.ribbon_repeat_mode = "repeat"
        entry.brush_size_mm = 10.0
        entry.aspect_ratio = 1.0
        entry.spacing_percent = 100.0
        entry.image_angle_deg = 0.0
        obj = image_path_object.ensure_image_path_object(scene=scene, entry=entry, page=page)
        assert len(obj.data.polygons) == 2, f"リボン面数が不正です: {len(obj.data.polygons)}"
        repeat_uvs = _uv_values(obj)
        assert max(u for u, _v in repeat_uvs) > 4.0, repeat_uvs

        entry.ribbon_repeat_mode = "stretch"
        obj = image_path_object.ensure_image_path_object(scene=scene, entry=entry, page=page)
        stretch_uvs = _uv_values(obj)
        _assert_close(max(u for u, _v in stretch_uvs), 1.0, "一枚リボン UV")

        entry.draw_mode = "stamp"
        entry.inout_size_enabled = True
        entry.inout_opacity_enabled = True
        entry.inout_color_enabled = True
        entry.in_percent = 20.0
        entry.out_percent = 30.0
        entry.in_start_percent = 50.0
        entry.out_start_percent = 50.0
        entry.inout_start_color = (1.0, 0.0, 0.0, 1.0)
        entry.inout_end_color = (0.0, 0.0, 1.0, 0.25)
        obj = image_path_object.ensure_image_path_object(scene=scene, entry=entry, page=page)
        image_colors = _point_colors(obj)
        assert min(c[3] for c in image_colors) < max(c[3] for c in image_colors), "画像の不透明度入り抜きが効いていません"
        assert any(c[0] > c[2] + 0.2 for c in image_colors), "画像の入り色が反映されていません"
        assert any(c[2] > c[0] + 0.2 for c in image_colors), "画像の抜き色が反映されていません"
        image_widths = _polygon_widths(obj)
        assert min(image_widths) < max(image_widths), "画像のサイズ入り抜きが効いていません"

        shape_entry = scene.bmanga_image_path_layers.add()
        shape_entry.id = "image_path_shape_test"
        shape_entry.title = "生成形状パス"
        shape_entry.content_source = "shape"
        shape_entry.parent_kind = "page"
        shape_entry.parent_key = page_key
        shape_entry.path_points_json = json.dumps([[10.0, 95.0], [80.0, 95.0]])
        shape_entry.brush_size_mm = 8.0
        shape_entry.spacing_percent = 80.0
        shape_entry.inout_size_enabled = True
        shape_entry.inout_opacity_enabled = True
        shape_entry.inout_color_enabled = True
        shape_entry.in_percent = 10.0
        shape_entry.out_percent = 40.0
        shape_entry.in_start_percent = 45.0
        shape_entry.out_start_percent = 45.0
        shape_entry.inout_start_color = (0.0, 1.0, 0.0, 1.0)
        shape_entry.inout_end_color = (1.0, 0.0, 1.0, 0.35)
        expected_vertices = {"circle": 16, "square": 4, "polygon": 6, "star": 10, "heart": 20}
        for kind, vertex_count in expected_vertices.items():
            shape_entry.shape_kind = kind
            shape_entry.shape_sides = 6
            shape_obj = image_path_object.ensure_image_path_object(scene=scene, entry=shape_entry, page=page)
            assert shape_obj is not None, f"{kind} の生成形状が作成されません"
            assert len(shape_obj.data.polygons) >= 2, f"{kind} の生成形状スタンプが少なすぎます"
            assert len(shape_obj.data.polygons[0].vertices) == vertex_count, f"{kind} の頂点数が不正です"
        shape_entry.shape_kind = "polygon"
        shape_entry.shape_sides = 7
        shape_obj = image_path_object.ensure_image_path_object(scene=scene, entry=shape_entry, page=page)
        assert len(shape_obj.data.polygons[0].vertices) == 7, "多角形の角数が反映されていません"
        shape_colors = _point_colors(shape_obj)
        assert min(c[3] for c in shape_colors) < max(c[3] for c in shape_colors), "生成形状の不透明度入り抜きが効いていません"
        assert any(c[1] > c[0] + 0.2 and c[1] > c[2] + 0.2 for c in shape_colors), "生成形状の入り色が反映されていません"
        assert any(c[0] > c[1] + 0.2 and c[2] > c[1] + 0.2 for c in shape_colors), "生成形状の抜き色が反映されていません"
        shape_widths = _polygon_widths(shape_obj)
        assert min(shape_widths) < max(shape_widths), "生成形状のサイズ入り抜きが効いていません"

        coma_entry = scene.bmanga_image_path_layers.add()
        coma_entry.id = "image_path_coma_test"
        coma_entry.title = "コマ内画像パス"
        coma_entry.filepath = str(image_path)
        coma_entry.parent_kind = "coma"
        coma_entry.parent_key = coma_key
        coma_entry.path_points_json = json.dumps([[5.0, 5.0], [75.0, 5.0], [75.0, 75.0]])
        coma_entry.draw_mode = "stamp"
        coma_obj = image_path_object.ensure_image_path_object(scene=scene, entry=coma_entry, page=page)
        assert coma_obj is not None
        coma_mod = coma_obj.modifiers.get(mask_apply.MOD_NAME_COMA_MASK)
        assert coma_mod is not None and getattr(coma_mod, "object", None) is not None, (
            "コマ内画像パスにコママスクがありません"
        )
        assert coma_obj.data.materials and _material_has_content_mask(coma_obj.data.materials[0]), (
            "コマ内画像パスにコマ内容マスクがありません"
        )

        data = schema.work_to_dict(work)
        assert data["schemaVersion"] >= 8
        assert data["image_path_layers"][0]["id"] == "image_path_test"
        assert data["image_path_layers"][1]["contentSource"] == "shape"
        assert data["image_path_layers"][1]["shapeKind"] == "polygon"
        scene.bmanga_image_path_layers.clear()
        schema.work_from_dict(work, data)
        assert len(scene.bmanga_image_path_layers) == 3
        restored = scene.bmanga_image_path_layers[0]
        assert restored.id == "image_path_test"
        assert restored.inout_size_enabled and restored.inout_opacity_enabled and restored.inout_color_enabled
        restored_shape = scene.bmanga_image_path_layers[1]
        assert restored_shape.content_source == "shape"
        assert restored_shape.shape_kind == "polygon"
        assert restored_shape.shape_sides == 7

        presets = image_path_presets.list_all_presets(None)
        names = [preset.name for preset in presets]
        assert {"標準スタンプ", "標準リボン", "一枚リボン", "円形スタンプ"}.issubset(set(names)), names
        preset = image_path_presets.load_preset_by_name("一枚リボン", None)
        assert preset is not None
        image_path_presets.apply_preset_to_entry(preset, restored)
        assert restored.draw_mode == "ribbon"
        assert restored.ribbon_repeat_mode == "stretch"
        shape_preset = image_path_presets.load_preset_by_name("円形スタンプ", None)
        assert shape_preset is not None
        image_path_presets.apply_preset_to_entry(shape_preset, restored_shape)
        assert restored_shape.content_source == "shape"
        assert restored_shape.shape_kind == "circle"

        image_path_presets.save_local_preset(None, restored, "テスト画像パス", insert_after="一枚リボン")
        renamed = image_path_presets.rename_preset(None, "テスト画像パス", "テスト画像パス改")
        assert renamed.name == "テスト画像パス改"
        duplicated = image_path_presets.duplicate_preset(None, "テスト画像パス改", "テスト画像パス複製")
        assert duplicated.name == "テスト画像パス複製"
        image_path_presets.delete_preset(None, "テスト画像パス複製")
        assert image_path_presets.load_preset_by_name("テスト画像パス複製", None) is None

        print("BMANGA_IMAGE_PATH_TOOL_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
