"""Blender 実機用: B-Name-Render 分離の登録確認."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_package(package_name: str, package_root: Path):
    spec = importlib.util.spec_from_file_location(
        package_name,
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bname = None
    render = None
    try:
        bname = _load_package("bname_dev", ROOT)
        assert getattr(bpy.types, "BNAME_PT_export", None) is not None
        assert getattr(bpy.types, "BNAME_PT_export").bl_label == "ページ出力"
        assert getattr(bpy.types, "BNAME_PT_gpencil", None) is None
        assert getattr(bpy.types, "BNAME_OT_export_page", None) is not None
        assert getattr(bpy.types, "BNAME_OT_export_all_pages", None) is not None
        assert getattr(bpy.types, "BNAME_OT_export_pdf", None) is not None
        assert "bname_dev.operators.io_op" in sys.modules
        assert "bname_dev.panels.export_panel" in sys.modules

        render = _load_package("bname_render_dev", ROOT / "addons" / "b_name_render")
        assert getattr(bpy.types, "BNAME_RENDER_PT_main", None) is not None
        assert getattr(bpy.types.Scene, "bname_render_state", None) is not None
        assert getattr(bpy.types.Scene, "fisheye_layout_mode", None) is not None
        assert getattr(bpy.types.Scene, "my_tool", None) is not None
        from bname_render_dev import command_runner, core, eevr_bridge

        scene = bpy.context.scene
        cam_data = bpy.data.cameras.new("BNameRenderFisheyeCamera")
        cam_data.panorama_type = "FISHEYE_EQUISOLID"
        cam = bpy.data.objects.new("BNameRenderFisheyeCamera", cam_data)
        scene.collection.objects.link(cam)
        scene.camera = cam
        scene.render.resolution_x = 800
        scene.render.resolution_y = 600
        scene.original_resolution_x = 800
        scene.original_resolution_y = 600
        scene.fisheye_layout_mode = False
        scene.bname_coma_camera_fisheye_layout_mode = True
        scene.bname_coma_camera_fisheye_fov = 2.4
        core._apply_output_resolution_mode(scene)
        assert core.fisheye_enabled(scene), "B-Name側の魚眼モードをB-Name-Renderが認識していません"
        assert scene.render.resolution_x == 800 and scene.render.resolution_y == 800
        assert cam.data.type == "PANO"
        assert cam.data.panorama_type == "FISHEYE_EQUISOLID"
        assert abs(float(cam.data.fisheye_fov) - 2.4) < 1.0e-6
        assert abs(eevr_bridge._fisheye_fov(scene, cam) - 2.4) < 1.0e-6
        assert getattr(bpy.types, "BNAME_OT_fisheye_save_pencil4_widths", None) is None
        assert getattr(bpy.types, "BNAME_OT_coma_camera_toggle_all_backgrounds", None) is None
        assert getattr(bpy.types, "BNAME_OT_coma_camera_toggle_koma_backgrounds", None) is None
        assert getattr(bpy.types, "BNAME_OT_coma_camera_reload_backgrounds", None) is None
        assert getattr(bpy.types, "BNAME_OT_coma_camera_resolution_add", None) is None

        scene.fisheye_layout_mode = True
        scene.fisheye_fov = 2.8
        assert scene.bname_coma_camera_fisheye_layout_mode is True
        assert abs(float(scene.bname_coma_camera_fisheye_fov) - 2.8) < 1.0e-6
        scene.reduction_mode = True
        scene.preview_scale_percentage = 25.0
        assert scene.bname_coma_camera_reduction_mode is True
        assert abs(float(scene.bname_coma_camera_preview_scale_percentage) - 25.0) < 1.0e-6
        scene.my_tool.bg_images_scale = 1.4
        assert abs(float(scene.bname_coma_camera_settings.bg_images_scale) - 1.4) < 1.0e-6
        result = bpy.ops.bname_render.set_reduction_scale(percentage=50.0)
        assert result == {"FINISHED"}, result
        assert abs(float(scene.preview_scale_percentage) - 50.0) < 1.0e-6
        assert abs(float(scene.bname_coma_camera_preview_scale_percentage) - 50.0) < 1.0e-6

        result = bpy.ops.bname_render.load_builtin_presets(reset=True)
        assert result == {"FINISHED"}, result
        state = bpy.context.scene.bname_render_state
        preset_names = {item.name for item in state.presets}
        for required in ("効果", "キャラ", "キャラpen方向", "キャラpen合成", "背景", "背景pen合成", "画像ノード再読み込み"):
            assert required in preset_names, required
        assert "ページ" not in preset_names
        assert "すべて" not in preset_names
        assert "旧出力シーン互換: ページ" in preset_names
        assert "旧出力シーン互換: すべて" in preset_names
        for preset in state.presets:
            for command in preset.commands:
                assert not command.name.startswith("未設定"), (preset.name, command.name)
                if not preset.name.startswith("旧出力シーン互換"):
                    assert getattr(command, "collection_name", "") != "コマ枠"
                    assert getattr(command, "node_name", "") not in {"ページ", "全コマ統合"}

        state.active_preset_index = 0
        preset = state.presets[0]
        before = len(preset.commands)
        result = bpy.ops.bname_render.command_add(command_type="RENDER")
        assert result == {"FINISHED"}, result
        assert len(preset.commands) == before + 1
        added = preset.commands[preset.active_command_index]
        assert added.command_type == "RENDER", added.command_type
        assert added.name == "レンダー", added.name

        empty = state.presets.add()
        empty.name = "空プリセット"
        state.active_preset_index = len(state.presets) - 1
        result = bpy.ops.bname_render.command_card_click(index=0)
        assert result == {"CANCELLED"}, result

        restore_preset = state.presets.add()
        restore_preset.name = "状態復元"
        state.active_preset_index = len(state.presets) - 1
        restore_preset.commands.add().command_type = "STATE_BEGIN"
        restore_preset.commands.add().command_type = "STATE_BEGIN"
        restore_preset.commands.add().command_type = "STATE_END"
        bpy.context.scene.render.film_transparent = False
        result = bpy.ops.bname_render.preset_run()
        assert result == {"FINISHED"}, result
        assert bpy.context.scene.render.film_transparent is False

        coll = bpy.data.collections.new("復元確認")
        scene.collection.children.link(coll)
        bpy.context.view_layer.update()
        layer_coll = command_runner._find_layer_collection(scene.view_layers[0].layer_collection, "復元確認")
        assert layer_coll is not None and layer_coll.exclude is False
        restore_collection = state.presets.add()
        restore_collection.name = "表示復元"
        state.active_preset_index = len(state.presets) - 1
        restore_collection.commands.add().command_type = "STATE_BEGIN"
        command = restore_collection.commands.add()
        command.command_type = "SET_COLLECTION_EXCLUDE"
        command.collection_name = "復元確認"
        command.exclude_collection = True
        restore_collection.commands.add().command_type = "STATE_END"
        result = bpy.ops.bname_render.preset_run()
        assert result == {"FINISHED"}, result
        assert layer_coll.exclude is False

        print("BNAME_RENDER_SPLIT_OK")
    finally:
        if render is not None:
            try:
                render.unregister()
            except Exception:
                pass
        if bname is not None:
            try:
                bname.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
