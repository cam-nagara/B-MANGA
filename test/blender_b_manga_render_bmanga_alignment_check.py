"""Blender 実機用: B-MANGA Render が現行 B-MANGA コマ用blend仕様へ従うことを確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

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
    image["_bmanga_coma_camera_ref"] = True
    image["bmanga_kind"] = "name"
    image["bmanga_full_page_mask"] = True
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
    state = context.scene.bmanga_render_state
    bpy.ops.bmanga_render.load_builtin_presets(reset=True)
    names = [preset.name for preset in state.presets]
    assert "ページ" not in names
    assert "すべて" not in names
    assert "旧出力シーン互換: ページ" in names
    assert "旧出力シーン互換: すべて" in names
    _standard_presets_have_no_old_page_dependencies(render_mod)

    target_index = names.index("キャラpen方向")
    state.active_preset_index = target_index
    from bmanga_render_bmanga_align import command_runner, eevr_bridge

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


class _TypedSocket:
    def __init__(self, name: str, kind: type) -> None:
        self.name = name
        self.kind = kind
        self._value = kind()

    @property
    def default_value(self):
        return self._value

    @default_value.setter
    def default_value(self, value) -> None:
        if type(value) is not self.kind:
            raise TypeError(f"expected {self.kind.__name__}")
        self._value = value


def _assert_aov_socket_coercion(render_mod) -> None:
    sockets = [
        _TypedSocket("落ち影切替", int),
        _TypedSocket("透過切替", bool),
        _TypedSocket("濃度", float),
    ]
    tree = SimpleNamespace(nodes=[SimpleNamespace(type="VALUE", inputs=sockets)])
    assert render_mod.command_runner._set_input_in_node_tree(tree, "落ち影切替", 1.0) == 1
    assert render_mod.command_runner._set_input_in_node_tree(tree, "透過切替", 1.0) == 1
    assert render_mod.command_runner._set_input_in_node_tree(tree, "濃度", 0.5) == 1
    assert sockets[0].default_value == 1 and type(sockets[0].default_value) is int
    assert sockets[1].default_value is True
    assert abs(sockets[2].default_value - 0.5) < 1.0e-6


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_render_align_"))
    work_dir = temp_root / "RenderAlign.bmanga"
    bmanga = None
    render = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bmanga = _load_package("bmanga_dev_align", ROOT)
        result = bpy.ops.bmanga.work_new(filepath=str(work_dir))
        assert result == {"FINISHED"}, result
        result = bpy.ops.bmanga.enter_coma_mode()
        assert result == {"FINISHED"}, result

        render = _load_package("bmanga_render_bmanga_align", ROOT / "addons" / "b_manga_render")
        scene = bpy.context.scene
        assert scene.bmanga_current_coma_page_id == "p0001"
        assert scene.bmanga_current_coma_id == "c01"
        assert Path(bpy.data.filepath).resolve() == (work_dir / "p0001" / "c01" / "c01.blend").resolve()

        _add_managed_page_image(scene)
        scene.camera.data.type = "PANO"
        scene.camera.data.panorama_type = "FISHEYE_EQUISOLID"
        scene.bmanga_coma_camera_original_resolution_x = 1200
        scene.bmanga_coma_camera_original_resolution_y = 900
        scene.bmanga_coma_camera_fisheye_layout_mode = True
        scene.bmanga_coma_camera_fisheye_fov = 4.0
        scene.bmanga_coma_camera_reduction_mode = True
        scene.bmanga_coma_camera_preview_scale_percentage = 50.0
        render.core._apply_output_resolution_mode(scene)

        assert render.core.fisheye_enabled(scene)
        assert render.core.reduction_enabled(scene)
        assert render.core.original_resolution(scene) == (1200, 900)
        assert scene.render.resolution_x == 600 and scene.render.resolution_y == 600
        assert scene.camera.data.type == "PANO"
        assert scene.camera.data.panorama_type == "FISHEYE_EQUISOLID"
        assert abs(float(scene.camera.data.fisheye_fov) - 4.0) < 1.0e-6
        _assert_aov_socket_coercion(render)

        scene.reduction_mode = True
        scene.preview_scale_percentage = 25.0
        assert scene.bmanga_coma_camera_reduction_mode is True
        assert abs(float(scene.bmanga_coma_camera_preview_scale_percentage) - 25.0) < 1.0e-6
        scene.reduction_mode = False
        assert scene.bmanga_coma_camera_reduction_mode is False
        scene.reduction_mode = True
        scene.preview_scale_percentage = 50.0
        render.core._apply_output_resolution_mode(scene)

        from bmanga_render_bmanga_align import bmanga_context

        context = bmanga_context.scene_context(scene)
        assert context.is_bmanga_coma
        assert context.passes_dir == work_dir / "p0001" / "c01" / "passes"
        assert "ページ画像_現在" in context.managed_page_images

        scene.render.resolution_x = 1200
        scene.render.resolution_y = 900
        _run_fisheye_preset_without_render(bpy.context, render)
        render.core._apply_output_resolution_mode(scene)
        assert scene.render.resolution_x == 600 and scene.render.resolution_y == 600
        assert scene["bmanga_render_fisheye_output_dir"] == str(work_dir / "p0001" / "c01" / "passes")
        assert str(scene["bmanga_render_fisheye_output_name"]).startswith("c01_")
        assert scene.bmanga_coma_camera_fisheye_layout_mode is True
        assert abs(float(scene.bmanga_coma_camera_fisheye_fov) - 4.0) < 1.0e-6
        assert scene.camera.data.panorama_type == "FISHEYE_EQUISOLID"

        scene["bmanga_render_fisheye_output_dir"] = "//passes\\"
        scene["bmanga_render_fisheye_output_name"] = "fisheye"
        assert render.eevr_bridge.setup(scene, scene.camera)
        assert scene["bmanga_render_fisheye_output_dir"] == str(work_dir / "p0001" / "c01" / "passes")
        assert scene["bmanga_render_fisheye_output_name"] == "c01_fisheye"

        print("BMANGA_RENDER_BMANGA_ALIGNMENT_OK")
    finally:
        if render is not None:
            try:
                render.unregister()
            except Exception:
                pass
        if bmanga is not None:
            try:
                bmanga.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
