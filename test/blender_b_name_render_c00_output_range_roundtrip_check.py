"""Blender実機用: c00由来下絵が残る状態で現在コマの出力範囲を優先する."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BLEND = Path(r"D:\TM Dropbox\Share\B-Name\c_file\c00.blend")
OUT_DIR = Path(os.environ.get("BNAME_RENDER_RANGE_OUT", "") or ROOT / ".codex" / "ai_audit" / "bname_render_c00_range")


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


def _ensure_pillow_path() -> None:
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    for candidate in (
        ROOT / "wheels" / "_installed" / f"pillow-12.2.0-{tag}-{tag}-win_amd64",
        ROOT / "wheels" / f"pillow-12.2.0-{tag}-{tag}-win_amd64.whl",
        ROOT / "wheels" / "_installed" / "pillow-12.2.0-cp313-cp313-win_amd64",
        ROOT / "wheels" / "pillow-12.2.0-cp313-cp313-win_amd64.whl",
        ROOT / "wheels" / "_installed" / "pillow-12.2.0-cp312-cp312-win_amd64",
        ROOT / "wheels" / "pillow-12.2.0-cp312-cp312-win_amd64.whl",
        ROOT / "wheels" / "_installed" / "pillow-12.2.0-cp311-cp311-win_amd64",
        ROOT / "wheels" / "pillow-12.2.0-cp311-cp311-win_amd64.whl",
    ):
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
            return


def _write_range_sheet(evidence: dict, path: Path) -> Path:
    _ensure_pillow_path()
    from PIL import Image, ImageDraw, ImageFont

    def font(size: int):
        for font_path in (r"C:\Windows\Fonts\YuGothM.ttc", r"C:\Windows\Fonts\meiryo.ttc", r"C:\Windows\Fonts\msgothic.ttc"):
            if Path(font_path).is_file():
                try:
                    return ImageFont.truetype(font_path, size=size)
                except Exception:
                    pass
        return ImageFont.load_default()

    cases = [
        ("通常", evidence["normal"]),
        ("魚眼+縮小", evidence["fisheye_reduction"]),
        ("再オープン後", evidence["after_reopen"]),
        ("B-Name-Render後", evidence["after_b_name_render"]),
    ]
    sheet = Image.new("RGB", (1120, 420), "white")
    draw = ImageDraw.Draw(sheet)
    title_font = font(20)
    body_font = font(13)
    draw.text((24, 18), "B-Name-Render c00 出力範囲 / 魚眼 / 縮小 / 再オープン検証", fill=(0, 0, 0), font=title_font)
    for index, (label, case) in enumerate(cases):
        x = 24 + index * 270
        y = 70
        draw.rectangle((x, y, x + 240, y + 300), fill=(246, 246, 246), outline=(120, 120, 120))
        draw.rectangle((x + 55, y + 24, x + 185, y + 254), outline=(210, 70, 70), width=2)
        draw.rectangle((x + 30, y + 84, x + 210, y + 194), outline=(40, 145, 70), width=4)
        border = case["border"]
        bx0 = x + 30 + int((x + 210 - (x + 30)) * border[0])
        bx1 = x + 30 + int((x + 210 - (x + 30)) * border[1])
        by0 = y + 84 + int((y + 194 - (y + 84)) * (1.0 - border[3]))
        by1 = y + 84 + int((y + 194 - (y + 84)) * (1.0 - border[2]))
        draw.rectangle((bx0, by0, bx1, by1), outline=(30, 90, 220), width=4)
        draw.text((x, y + 270), label, fill=(0, 0, 0), font=body_font)
        draw.text((x, y + 292), f"{case['resolution']} / {case.get('camera_type', '')}", fill=(0, 0, 0), font=body_font)
        draw.text((x, y + 314), f"下絵: {case['source']}", fill=(0, 0, 0), font=body_font)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)
    return path


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
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    temp_root = OUT_DIR
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
        assert fisheye["border"] == (0.0, 1.0, 0.0, 1.0), fisheye

        count = _run_render_preset_without_pixels(bpy.context, render, "キャラpen方向")
        after_preset = {
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "border": _border(scene),
            "source": scene.get("bname_coma_camera_render_border_source", ""),
            "command_count": count,
        }
        assert after_preset["resolution"] == [4299, 4299], after_preset
        assert after_preset["source"] == normal["source"], after_preset

        copy_path = temp_root / "bname_render_output_range_roundtrip.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(copy_path))
        reopen_json = temp_root / "bname_render_output_range_reopen.json"
        env = os.environ.copy()
        env["BNAME_RANGE_REOPEN_BLEND"] = str(copy_path)
        env["BNAME_RANGE_REOPEN_JSON"] = str(reopen_json)
        subprocess.run(
            [
                bpy.app.binary_path,
                "--factory-startup",
                "--background",
                "--python",
                str(ROOT / "test" / "blender_b_name_render_c00_output_range_reopen_worker.py"),
            ],
            check=True,
            env=env,
        )
        after_reopen = json.loads(reopen_json.read_text(encoding="utf-8"))
        assert after_reopen["resolution"] == fisheye["resolution"], after_reopen
        assert tuple(after_reopen["border"]) == fisheye["border"], after_reopen
        assert after_reopen["source"] == normal["source"], after_reopen
        assert after_reopen["camera_type"] == "PANO", after_reopen

        evidence = {
            "blend": str(blend_path),
            "old_background": old_bg.image.name,
            "managed_background": managed_bg.image.name,
            "normal": normal,
            "fisheye_reduction": fisheye,
            "after_reopen": after_reopen,
            "after_b_name_render": after_preset,
        }
        out_path = temp_root / "bname_render_output_range_roundtrip.json"
        evidence["visual_sheet"] = str(_write_range_sheet(evidence, temp_root / "bname_render_output_range_roundtrip.png"))
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


if __name__ == "__main__":
    main()
