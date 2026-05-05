"""Blender実機用: c00.blend 上で B-Name-Render 全プリセットの実行準備を確認."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BLEND = Path(r"D:\TM Dropbox\Share\B-Name\c_file\c00.blend")
DEFAULT_EEVR_ZIP = Path(r"D:\Develop\Blender\暫定安定版_編集禁止\eeVR-master_ミウラ修正 (19).zip")


def _load_render_package():
    package_root = ROOT / "addons" / "b_name_render"
    spec = importlib.util.spec_from_file_location(
        "bname_render_c00_exec",
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_render_c00_exec"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _load_eevr_package(zip_path: Path, temp_root: Path):
    if not zip_path.exists():
        raise FileNotFoundError(zip_path)
    extract_dir = temp_root / "eevr"
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extract_dir)
    package_dirs = [path for path in extract_dir.iterdir() if path.is_dir()]
    assert package_dirs, f"eeVR package root not found: {zip_path}"
    package_root = package_dirs[0]
    spec = importlib.util.spec_from_file_location(
        "eevr",
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eevr"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _patch_rendering(command_runner, eevr_bridge):
    calls: list[dict] = []

    def fake_render(scene, engine: str, sample_count: int) -> None:
        command_runner._configure_render(scene, "BLENDER_WORKBENCH", 1)
        command_runner._ensure_renderable_view_layers(scene)
        view_layer_ready = any(bool(layer.use) for layer in scene.view_layers if hasattr(layer, "use"))
        assert view_layer_ready
        calls.append(
            {
                "kind": "render",
                "engine": engine,
                "samples": int(sample_count),
                "resolution": [int(scene.render.resolution_x), int(scene.render.resolution_y)],
                "view_layer_ready": view_layer_ready,
            }
        )

    def fake_eevr(kind: str):
        def run():
            calls.append({"kind": kind})
            return {"FINISHED"}

        return run

    originals = SimpleNamespace(
        render=command_runner._render,
        render_image=eevr_bridge.render_image,
        render_faces=eevr_bridge.render_faces,
        assemble_images=eevr_bridge.assemble_images,
    )
    command_runner._render = fake_render
    eevr_bridge.render_image = fake_eevr("eeVR魚眼レンダー")
    eevr_bridge.render_faces = fake_eevr("eeVR方向画像レンダー")
    eevr_bridge.assemble_images = fake_eevr("eeVRパノラマ合成")
    return calls, originals


def _restore_rendering(command_runner, eevr_bridge, originals) -> None:
    command_runner._render = originals.render
    eevr_bridge.render_image = originals.render_image
    eevr_bridge.render_faces = originals.render_faces
    eevr_bridge.assemble_images = originals.assemble_images


def _run_all_presets(context, render_mod) -> dict:
    from bname_render_c00_exec import command_runner, core, eevr_bridge

    state = context.scene.bname_render_state
    bpy.ops.bname_render.load_builtin_presets(reset=True)
    calls, originals = _patch_rendering(command_runner, eevr_bridge)
    errors: dict[str, str] = {}
    command_counts: dict[str, int] = {}
    try:
        context.scene.fisheye_layout_mode = False
        for index, preset in enumerate(state.presets):
            state.active_preset_index = index
            try:
                command_counts[preset.name] = command_runner.run_active_preset(context)
            except Exception as exc:  # noqa: BLE001
                errors[preset.name] = str(exc)
                command_runner._restore_session(context.scene)
    finally:
        _restore_rendering(command_runner, eevr_bridge, originals)
        command_runner._restore_session(context.scene)
    assert not errors, errors
    assert len(command_counts) >= 30, command_counts
    assert sum(1 for call in calls if call["kind"] == "render") >= 60, calls
    assert all(call.get("view_layer_ready", True) for call in calls if call["kind"] == "render")
    return {
        "preset_count": len(command_counts),
        "command_total": sum(command_counts.values()),
        "render_call_count": sum(1 for call in calls if call["kind"] == "render"),
    }


def _assert_eevr_bridge(context, temp_root: Path) -> dict:
    from bname_render_c00_exec import command_runner, eevr_bridge

    eevr_zip = Path(os.environ.get("BNAME_EEVR_ZIP", str(DEFAULT_EEVR_ZIP)))
    eevr_mod = _load_eevr_package(eevr_zip, temp_root)
    calls, originals = _patch_rendering(command_runner, eevr_bridge)
    try:
        scene = context.scene
        scene.fisheye_layout_mode = True
        command = SimpleNamespace(
            command_type="FISHEYE_RENDER_IMAGE_OR_LAYER",
            node_group_name="出力_背景線画Pencil+4",
            label_contains="背景線画_Pencil+4",
            engine="CYCLES",
            sample_count=1,
            folder_path=str(temp_root / "eevr_out"),
            text_value="eevr_bridge_check",
        )
        command_runner._run_command(context, command)
        props = scene.eeVR
        assert props.renderModeEnum == "DOME"
        assert props.domeMethodEnum == "1"
        assert props.fovModeEnum == "180"
        assert bool(props.save_images_to_directory)
        assert props.images_save_directory == str(temp_root / "eevr_out")
        assert scene["outputFolderName"] == str(temp_root / "eevr_out")
        assert scene["outputImageName"] == "eevr_bridge_check"
        assert any(call["kind"] == "eeVR魚眼レンダー" for call in calls)
        return {
            "renderMode": props.renderModeEnum,
            "domeMethod": props.domeMethodEnum,
            "fovMode": props.fovModeEnum,
            "output": props.images_save_directory,
        }
    finally:
        _restore_rendering(command_runner, eevr_bridge, originals)
        try:
            eevr_mod.unregister()
        except Exception:
            pass


def main() -> None:
    blend_path = Path(os.environ.get("BNAME_C00_BLEND", str(DEFAULT_BLEND)))
    if not blend_path.exists():
        raise FileNotFoundError(blend_path)
    temp_root = Path(tempfile.mkdtemp(prefix="bname_render_c00_exec_"))
    render_mod = None
    try:
        bpy.ops.wm.open_mainfile(filepath=str(blend_path))
        render_mod = _load_render_package()
        normal = _run_all_presets(bpy.context, render_mod)
        eevr = _assert_eevr_bridge(bpy.context, temp_root)
        print(
            "BNAME_RENDER_C00_EXECUTION_OK "
            + json.dumps({"normal": normal, "eevr": eevr}, ensure_ascii=False, sort_keys=True)
        )
    finally:
        if render_mod is not None:
            try:
                render_mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
