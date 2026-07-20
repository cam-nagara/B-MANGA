"""Blender実機用: フキダシのパス線 (輪郭に沿ったスタンプ/リボン) を確認。"""

from __future__ import annotations

import base64
import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_balloon_path_line"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _sub(path: str):
    __import__(f"{MOD_NAME}.{path}")
    return sys.modules[f"{MOD_NAME}.{path}"]


def _write_png(path: Path) -> None:
    path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAEAAAAAQCAYAAACm53kpAAAASklEQVR4nO3SQQrAIBAEQe9/"
            "6daFEFqRbJvCYxgMWVFhZpOZzGxsBHB23rpp8vQJhQmE/QoKCgoKCgoKCgoKCgpKB4b1"
            "BCQdbp8JAAAAAElFTkSuQmCC"
        )
    )


def _poly_count(obj) -> int:
    data = getattr(obj, "data", None)
    return len(getattr(data, "polygons", []) or [])


def _material_has_image(mat) -> bool:
    for node in getattr(getattr(mat, "node_tree", None), "nodes", []) or []:
        if getattr(node, "bl_idname", "") == "ShaderNodeTexImage":
            return True
    return False


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_balloon_path_line_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        image_path = temp_root / "balloon_path_line.png"
        _write_png(image_path)
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "BalloonPathLine.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        balloon_op = _sub("operators.balloon_op")
        balloon_curve_object = _sub("utils.balloon_curve_object")
        balloon_line_mesh = _sub("utils.balloon_line_mesh")
        balloon_path_line = _sub("utils.balloon_path_line")
        balloon_presets = _sub("io.balloon_presets")
        object_naming = _sub("utils.object_naming")
        get_work = _sub("core.work").get_work
        page_stack_key = _sub("utils.layer_hierarchy").page_stack_key

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        page_key = page_stack_key(page)

        entry = balloon_op._create_balloon_entry(
            context,
            page,
            shape="rect",
            x=40.0,
            y=40.0,
            w=40.0,
            h=30.0,
            parent_kind="page",
            parent_key=page_key,
        )
        assert entry is not None
        balloon_id = str(entry.id)

        # --- A. プロパティ追加の確認 (既定値) ---
        for field, default in (
            ("line_image_source", "image"),
            ("line_image_shape_kind", "circle"),
            ("line_image_shape_sides", 6),
            ("line_image_draw_mode", "ribbon"),
            ("line_image_ribbon_repeat_mode", "repeat"),
            ("line_image_stamp_angle_mode", "line"),
        ):
            assert hasattr(entry, field), f"新設プロパティがありません: {field}"
            assert getattr(entry, field) == default, f"{field} の既定値が想定と異なります"
        assert hasattr(entry, "line_image_brush_size_mm")
        assert hasattr(entry, "line_image_inout_size_enabled")
        assert hasattr(entry, "line_image_inout_start_color")
        assert hasattr(entry, "line_image_inout_end_color")
        # 既存の線種「画像」用プロパティをそのまま再利用している (新設し直していない)。
        assert hasattr(entry, "line_image_path")
        assert hasattr(entry, "line_image_angle_deg")

        body_object = object_naming.find_object_by_bmanga_id(balloon_id, kind="balloon")
        assert body_object is not None, "フキダシ本体カーブが見つかりません"

        def _path_line_obj():
            name = f"{balloon_path_line.BALLOON_PATH_LINE_MESH_NAME_PREFIX}{balloon_id}"
            return bpy.data.objects.get(name)

        def _main_line_obj():
            return bpy.data.objects.get(balloon_line_mesh._line_mesh_object_name(balloon_id))

        # --- B. 既定 (画像未読込) では生成されない ---
        assert entry.line_style == "solid", "既定の線種がsolidではありません"
        assert not balloon_path_line.line_image_active(entry), "画像未読込なのにパス線が有効です"
        assert _path_line_obj() is None, "画像未読込なのにパス線オブジェクトが生成されています"
        assert _main_line_obj() is not None, "既定状態で主線メッシュが消えています"

        # --- C. 内容=生成形状 でパス線オブジェクトが生成される ---
        entry.line_image_source = "shape"
        entry.line_image_shape_kind = "circle"
        entry.line_image_draw_mode = "stamp"
        entry.line_image_brush_size_mm = 3.0
        entry.line_image_spacing_percent = 150.0
        assert balloon_path_line.line_image_active(entry), "生成形状指定でパス線が有効になっていません"
        path_obj = _path_line_obj()
        assert path_obj is not None, "生成形状のパス線オブジェクトが生成されていません"
        assert _poly_count(path_obj) > 0, "生成形状のパス線メッシュが空です"
        assert _main_line_obj() is None, "パス線が有効な間も主線メッシュが残っています"

        # --- D. 内容=画像・パス未指定では生成されない (画像未読込) ---
        entry.line_image_source = "image"
        entry.line_image_path = ""
        assert not balloon_path_line.line_image_active(entry), "画像パス未指定なのにパス線が有効です"
        assert _path_line_obj() is None, "画像パス未指定なのにパス線オブジェクトが残っています"
        assert _main_line_obj() is not None, "パス線非対象時に主線メッシュが復活していません"

        # --- F. 内容=画像・パス指定でパス線が復活し、素材に画像が接続される ---
        entry.line_image_path = str(image_path)
        entry.line_image_draw_mode = "ribbon"
        entry.line_image_ribbon_repeat_mode = "stretch"
        assert balloon_path_line.line_image_active(entry), "画像指定でパス線が有効になっていません"
        image_obj = _path_line_obj()
        assert image_obj is not None, "画像指定のパス線オブジェクトが生成されていません"
        assert _poly_count(image_obj) > 0, "画像指定のパス線メッシュが空です"
        assert image_obj.data.materials, "パス線メッシュに素材がありません"
        assert _material_has_image(image_obj.data.materials[0]), "パス線素材に画像が接続されていません"
        assert _main_line_obj() is None, "画像パス線が有効な間も主線メッシュが残っています"

        # --- F2. リボン表示 (内容=画像) は閉ループで継ぎ目が無い (頂点2つにつき面1つ) ---
        ribbon_vert_count = len(image_obj.data.vertices)
        ribbon_face_count = len(image_obj.data.polygons)
        assert ribbon_vert_count > 0 and ribbon_face_count > 0
        assert ribbon_face_count == ribbon_vert_count // 2, (
            f"リボンパス線が閉ループになっていません (頂点{ribbon_vert_count} 面{ribbon_face_count})"
        )

        # --- G. 線種プリセット保存・適用の往復 ---
        entry.line_image_source = "shape"
        entry.line_image_shape_kind = "star"
        entry.line_image_shape_sides = 5
        entry.line_image_draw_mode = "stamp"
        entry.line_image_brush_size_mm = 4.5
        entry.line_image_aspect_ratio = 1.5
        entry.line_image_spacing_percent = 120.0
        entry.line_image_color = (0.2, 0.4, 0.6, 0.8)
        entry.line_image_stamp_angle_mode = "fixed"
        entry.line_image_angle_deg = 12.0
        entry.line_image_inout_size_enabled = True
        entry.line_image_inout_opacity_enabled = True
        entry.line_image_inout_color_enabled = True
        entry.line_image_inout_start_color = (1.0, 0.0, 0.0, 1.0)
        entry.line_image_inout_end_color = (0.0, 0.0, 1.0, 0.5)
        snapshot = balloon_presets.snapshot_style_from_entry(entry)
        for field in (
            "line_image_source",
            "line_image_shape_kind",
            "line_image_shape_sides",
            "line_image_draw_mode",
            "line_image_brush_size_mm",
            "line_image_aspect_ratio",
            "line_image_spacing_percent",
            "line_image_color",
            "line_image_stamp_angle_mode",
            "line_image_angle_deg",
            "line_image_inout_size_enabled",
            "line_image_inout_opacity_enabled",
            "line_image_inout_color_enabled",
            "line_image_inout_start_color",
            "line_image_inout_end_color",
        ):
            assert field in snapshot, f"パス線設定がプリセットスナップショットにありません: {field}"

        other_entry = balloon_op._create_balloon_entry(
            context,
            page,
            shape="ellipse",
            x=100.0,
            y=40.0,
            w=30.0,
            h=30.0,
            parent_kind="page",
            parent_key=page_key,
        )
        assert other_entry is not None
        balloon_presets.apply_style_to_entry(other_entry, snapshot)
        assert other_entry.line_image_source == "shape"
        assert other_entry.line_image_shape_kind == "star"
        assert other_entry.line_image_shape_sides == 5
        assert other_entry.line_image_draw_mode == "stamp"
        assert abs(other_entry.line_image_brush_size_mm - 4.5) < 1.0e-3
        assert abs(other_entry.line_image_aspect_ratio - 1.5) < 1.0e-3
        assert abs(other_entry.line_image_spacing_percent - 120.0) < 1.0e-3
        assert tuple(round(c, 3) for c in other_entry.line_image_color) == (0.2, 0.4, 0.6, 0.8)
        assert other_entry.line_image_stamp_angle_mode == "fixed"
        assert abs(other_entry.line_image_angle_deg - 12.0) < 1.0e-3
        assert other_entry.line_image_inout_size_enabled is True
        assert other_entry.line_image_inout_opacity_enabled is True
        assert other_entry.line_image_inout_color_enabled is True
        assert tuple(round(c, 3) for c in other_entry.line_image_inout_start_color) == (1.0, 0.0, 0.0, 1.0)
        assert tuple(round(c, 3) for c in other_entry.line_image_inout_end_color) == (0.0, 0.0, 1.0, 0.5)
        # 既存の線種「画像」用プロパティも往復に含まれる (共有フィールド)。
        assert other_entry.line_image_path == entry.line_image_path
        assert abs(other_entry.line_image_angle_deg - entry.line_image_angle_deg) < 1.0e-3
        balloon_op._delete_balloon_by_id(context, str(page.id), str(other_entry.id))

        # --- H. フキダシ削除でパス線オブジェクトも消える ---
        assert _path_line_obj() is not None, "削除確認の前提となるパス線オブジェクトがありません"
        balloon_op._delete_balloon_by_id(context, str(page.id), balloon_id)
        assert _path_line_obj() is None, "フキダシ削除後もパス線オブジェクトが残っています"
        assert object_naming.find_object_by_bmanga_id(balloon_id, kind="balloon") is None, (
            "フキダシ削除後も本体カーブが残っています"
        )

        print("BMANGA_BALLOON_PATH_LINE_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
        os_exit_code = 0
    except Exception:
        import traceback

        traceback.print_exc()
        os_exit_code = 1
    import os

    os._exit(os_exit_code)
