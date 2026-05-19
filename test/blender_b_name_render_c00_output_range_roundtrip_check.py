"""Blender実機用: c00由来下絵が残る状態で現在コマの出力範囲を優先する."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BLEND = Path(r"D:\TM Dropbox\Share\B-Name\c_file\c00.blend")


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


def _ensure_camera(scene):
    if scene.camera is not None and scene.camera.type == "CAMERA":
        return scene.camera
    cam_data = bpy.data.cameras.new("B-Name-Render範囲監査カメラ")
    cam = bpy.data.objects.new("B-Name-Render範囲監査カメラ", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    return cam


def _add_background(camera, image_name: str, *, managed: bool, scale: float, width: int, height: int):
    image = bpy.data.images.new(image_name, width=width, height=height, alpha=True)
    if managed:
        image["_bname_coma_camera_ref"] = True
        image["bname_kind"] = "koma"
        image["bname_page_id"] = "p0001"
        image["bname_coma_id"] = "c00"
        image["bname_full_page_mask"] = False
    bg = camera.data.background_images.new()
    bg.image = image
    bg.scale = scale
    return bg


def _border(scene) -> tuple[float, float, float, float]:
    return (
        round(float(scene.render.border_min_x), 6),
        round(float(scene.render.border_max_x), 6),
        round(float(scene.render.border_min_y), 6),
        round(float(scene.render.border_max_y), 6),
    )


def _run_render_preset_without_pixels(context, render_mod, preset_name: str) -> int:
    from bname_render_range import command_runner, eevr_bridge

    state = context.scene.bname_render_state
    bpy.ops.bname_render.load_builtin_presets(reset=True)
    names = [preset.name for preset in state.presets]
    state.active_preset_index = names.index(preset_name)
    calls: list[str] = []

    original_render = command_runner._render
    original_faces = eevr_bridge.render_faces

    def fake_render(scene, engine: str, sample_count: int) -> None:
        calls.append(f"{engine}:{sample_count}")

    def fake_faces():
        calls.append("方向画像レンダー")
        return {"FINISHED"}

    command_runner._render = fake_render
    eevr_bridge.render_faces = fake_faces
    try:
        count = command_runner.run_active_preset(context)
    finally:
        command_runner._render = original_render
        eevr_bridge.render_faces = original_faces
        command_runner._restore_session(context.scene)
    assert calls, calls
    return count


def main() -> None:
    blend_path = Path(os.environ.get("BNAME_C00_BLEND", str(DEFAULT_BLEND)))
    if not blend_path.exists():
        raise FileNotFoundError(blend_path)
    temp_root = Path(tempfile.mkdtemp(prefix="bname_render_range_"))
    bname = None
    render = None
    try:
        bpy.ops.wm.open_mainfile(filepath=str(blend_path))
        bname = _load_package("bname_dev_range", ROOT)
        render = _load_package("bname_render_range", ROOT / "addons" / "b_name_render")
        scene = bpy.context.scene
        camera = _ensure_camera(scene)
        scene.bname_current_coma_page_id = "p0001"
        scene.bname_current_coma_id = "c00"
        scene.render.resolution_x = 6071
        scene.render.resolution_y = 8598
        scene.bname_coma_camera_original_resolution_x = 6071
        scene.bname_coma_camera_original_resolution_y = 8598
        scene.bname_coma_camera_fisheye_layout_mode = False
        scene.bname_coma_camera_reduction_mode = False
        scene.bname_coma_camera_preview_scale_percentage = 50.0

        old_bg = _add_background(camera, "旧テンプレート_コマ01", managed=False, scale=0.25, width=500, height=900)
        managed_bg = _add_background(camera, "BName_コマ_p0001_c00_page", managed=True, scale=0.75, width=1200, height=600)
        assert camera.data.background_images[-2].image == old_bg.image
        assert camera.data.background_images[-1].image == managed_bg.image

        from bname_dev_range.utils import coma_camera

        coma_camera.update_render_border_from_current_coma(bpy.context)
        normal = {
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "border": _border(scene),
            "source": scene.get("bname_coma_camera_render_border_source", ""),
        }
        assert normal["source"] == "BName_コマ_p0001_c00_page", normal
        assert scene.render.use_border is True

        scene.bname_coma_camera_fisheye_layout_mode = True
        scene.bname_coma_camera_reduction_mode = True
        scene.bname_coma_camera_fisheye_fov = 3.1415927
        coma_camera.resync_coma_camera_output_layout(bpy.context)
        fisheye = {
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "border": _border(scene),
            "source": scene.get("bname_coma_camera_render_border_source", ""),
            "camera_type": scene.camera.data.type,
        }
        assert fisheye["resolution"] == [4299, 4299], fisheye
        assert fisheye["source"] == normal["source"], fisheye
        assert fisheye["camera_type"] == "PANO", fisheye

        count = _run_render_preset_without_pixels(bpy.context, render, "キャラpen方向")
        after_preset = {
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "border": _border(scene),
            "source": scene.get("bname_coma_camera_render_border_source", ""),
            "command_count": count,
        }
        assert after_preset["resolution"] == [4299, 4299], after_preset
        assert after_preset["source"] == normal["source"], after_preset

        evidence = {
            "blend": str(blend_path),
            "old_background": old_bg.image.name,
            "managed_background": managed_bg.image.name,
            "normal": normal,
            "fisheye_reduction": fisheye,
            "after_b_name_render": after_preset,
        }
        out_path = temp_root / "bname_render_output_range_roundtrip.json"
        out_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"BNAME_RENDER_C00_OUTPUT_RANGE_OK {out_path}")
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
