"""Blender 実機用: B-Name-Render が現行 B-Name コマ用blend仕様へ従うことを確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
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


def _add_managed_page_image(scene) -> None:
    camera = scene.camera
    assert camera is not None and camera.type == "CAMERA"
    image = bpy.data.images.new("ページ画像_現在", width=8, height=8, alpha=True)
    image["_bname_coma_camera_ref"] = True
    image["bname_kind"] = "name"
    image["bname_full_page_mask"] = True
    bg = camera.data.background_images.new()
    bg.image = image


def _standard_presets_have_no_old_page_dependencies(render_mod) -> None:
    for preset_name, commands in render_mod.preset_library.BUILTIN_PRESETS.items():
        if render_mod.core.preset_category_of(preset_name) == "LEGACY":
            continue
        for command in commands:
            assert command.get("collection_name", "") != "コマ枠", preset_name
            assert command.get("node_name", "") not in {"ページ", "全コマ統合"}, preset_name


def _run_fisheye_preset_without_render(context, render_mod) -> None:
    state = context.scene.bname_render_state
    bpy.ops.bname_render.load_builtin_presets(reset=True)
    names = [preset.name for preset in state.presets]
    assert "ページ" not in names
    assert "すべて" not in names
    assert "旧出力シーン互換: ページ" in names
    assert "旧出力シーン互換: すべて" in names
    _standard_presets_have_no_old_page_dependencies(render_mod)

    target_index = names.index("キャラpen方向")
    state.active_preset_index = target_index
    from bname_render_bname_align import command_runner, eevr_bridge

    calls: list[str] = []
    original_render_faces = eevr_bridge.render_faces

    def fake_render_faces():
        calls.append("方向画像レンダー")
        return {"FINISHED"}

    eevr_bridge.render_faces = fake_render_faces
    try:
        count = command_runner.run_active_preset(context)
    finally:
        eevr_bridge.render_faces = original_render_faces
        command_runner._restore_session(context.scene)
    assert count > 0
    assert calls == ["方向画像レンダー"], calls


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_render_align_"))
    work_dir = temp_root / "RenderAlign.bname"
    bname = None
    render = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bname = _load_package("bname_dev_align", ROOT)
        result = bpy.ops.bname.work_new(filepath=str(work_dir))
        assert result == {"FINISHED"}, result
        result = bpy.ops.bname.enter_coma_mode()
        assert result == {"FINISHED"}, result

        render = _load_package("bname_render_bname_align", ROOT / "addons" / "b_name_render")
        scene = bpy.context.scene
        assert scene.bname_current_coma_page_id == "p0001"
        assert scene.bname_current_coma_id == "c01"
        assert Path(bpy.data.filepath).resolve() == (work_dir / "p0001" / "c01" / "c01.blend").resolve()

        _add_managed_page_image(scene)
        scene.camera.data.type = "PANO"
        scene.camera.data.panorama_type = "FISHEYE_EQUISOLID"
        scene.bname_coma_camera_original_resolution_x = 1200
        scene.bname_coma_camera_original_resolution_y = 900
        scene.bname_coma_camera_fisheye_layout_mode = True
        scene.bname_coma_camera_fisheye_fov = 4.0
        scene.bname_coma_camera_reduction_mode = True
        scene.bname_coma_camera_preview_scale_percentage = 50.0
        render.core._apply_output_resolution_mode(scene)

        assert render.core.fisheye_enabled(scene)
        assert render.core.reduction_enabled(scene)
        assert render.core.original_resolution(scene) == (1200, 900)
        assert scene.render.resolution_x == 600 and scene.render.resolution_y == 600
        assert scene.camera.data.type == "PANO"
        assert scene.camera.data.panorama_type == "FISHEYE_EQUISOLID"
        assert abs(float(scene.camera.data.fisheye_fov) - 4.0) < 1.0e-6

        from bname_render_bname_align import bname_context

        context = bname_context.scene_context(scene)
        assert context.is_bname_coma
        assert context.passes_dir == work_dir / "p0001" / "c01" / "passes"
        assert "ページ画像_現在" in context.managed_page_images

        _run_fisheye_preset_without_render(bpy.context, render)
        assert scene["bname_render_fisheye_output_dir"] == str(work_dir / "p0001" / "c01" / "passes")
        assert str(scene["bname_render_fisheye_output_name"]).startswith("c01_")
        assert scene.bname_coma_camera_fisheye_layout_mode is True
        assert abs(float(scene.bname_coma_camera_fisheye_fov) - 4.0) < 1.0e-6
        assert scene.camera.data.panorama_type == "FISHEYE_EQUISOLID"

        scene["bname_render_fisheye_output_dir"] = "//passes/"
        scene["bname_render_fisheye_output_name"] = "fisheye"
        assert render.eevr_bridge.setup(scene, scene.camera)
        assert scene["bname_render_fisheye_output_dir"] == str(work_dir / "p0001" / "c01" / "passes")
        assert scene["bname_render_fisheye_output_name"] == "c01_fisheye"

        print("BNAME_RENDER_BNAME_ALIGNMENT_OK")
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
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
