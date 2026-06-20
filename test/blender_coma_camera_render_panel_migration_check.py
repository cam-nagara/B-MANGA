"""Blender実機用: コマ編集カメラ/ビュー設定のB-MANGA側表示確認."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

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


class _DummyOp:
    def __init__(self, idname: str):
        object.__setattr__(self, "idname", idname)

    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)


class _RecordingLayout:
    def __init__(self, labels: list[str], operators: list[str], enabled: bool = True):
        object.__setattr__(self, "labels", labels)
        object.__setattr__(self, "operators", operators)
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "operator_context", "")

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"enabled", "operator_context", "scale_y", "alignment", "active"}:
            object.__setattr__(self, name, value)

    def _child(self):
        return _RecordingLayout(self.labels, self.operators, bool(getattr(self, "enabled", True)))

    def box(self, **_kwargs):
        return self._child()

    def row(self, **_kwargs):
        return self._child()

    def column(self, **_kwargs):
        return self._child()

    def split(self, **_kwargs):
        return self._child()

    def label(self, text: str = "", **_kwargs) -> None:
        if text:
            self.labels.append(str(text))

    def prop(self, data, prop_name: str, text: str | None = None, **_kwargs) -> None:
        label = text
        if label is None:
            try:
                label = data.bl_rna.properties[prop_name].name
            except Exception:  # noqa: BLE001
                label = prop_name
        if label:
            self.labels.append(str(label))

    def operator(self, idname: str, text: str = "", **_kwargs):
        self.operators.append(idname)
        if text:
            self.labels.append(str(text))
        return _DummyOp(idname)

    def template_list(self, *_args, **_kwargs) -> None:
        return

    def separator(self, **_kwargs) -> None:
        return

    def __getattr__(self, _name: str):
        def _fallback(*_args, **_kwargs):
            return self._child()

        return _fallback


def _draw_panel(panel_cls, context):
    labels: list[str] = []
    operators: list[str] = []
    dummy = type("DummyPanel", (), {"layout": _RecordingLayout(labels, operators)})()
    panel_cls.draw(dummy, context)
    return labels, operators


def _prepare_coma_context() -> None:
    from bmanga_panel_migration.core.mode import MODE_COMA, set_mode

    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new("MigrationCamera")
    cam = bpy.data.objects.new("MigrationCamera", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    set_mode(MODE_COMA, bpy.context)


def _check_bmanga_panel() -> None:
    from bmanga_panel_migration.panels import coma_camera_panel

    assert coma_camera_panel.BMANGA_PT_coma_camera.bl_label == "カメラ設定"
    labels, operators = _draw_panel(coma_camera_panel.BMANGA_PT_coma_camera, bpy.context)
    text_blob = "\n".join(labels)
    for forbidden in (
        "カメラアングル一覧",
        "出力解像度",
        "縮小モード",
        "Pencil+4 線幅を保存",
        "ページ一覧不透明度",
        "グレースケール表示",
        "全下絵を表示/非表示",
        "下絵_コマ",
        "すべての下絵を再読込",
        "下絵同期",
        "背景画像:",
    ):
        assert forbidden not in text_blob, forbidden
    assert "カメラプリセット" in labels
    assert "魚眼モード" in labels
    assert "魚眼FOV" in labels
    assert "bmanga.coma_camera_sync_references" not in operators
    assert "bmanga.coma_camera_angle_duplicate" in operators
    assert getattr(bpy.types, "BMANGA_OT_fisheye_save_pencil4_widths", None) is None
    assert getattr(bpy.types, "BMANGA_OT_coma_camera_toggle_all_backgrounds", None) is None
    assert getattr(bpy.types, "BMANGA_OT_coma_camera_toggle_koma_backgrounds", None) is None
    assert getattr(bpy.types, "BMANGA_OT_coma_camera_resolution_add", None) is None
    assert getattr(bpy.types, "BMANGA_OT_coma_camera_sync_references", None) is None


def _check_bmanga_view_panel() -> None:
    from bmanga_panel_migration.panels import view_panel

    labels, operators = _draw_panel(view_panel.BMANGA_PT_view, bpy.context)
    text_blob = "\n".join(labels)
    for required in (
        "ページ一覧",
        "ページ一覧表示",
        "全ページ",
        "前後ページ",
        "列数",
        "横間隔mm",
        "縦間隔mm",
        "ページ一覧不透明度",
        "ページ画像のスケール",
        "コマ内レイヤー",
        "グレースケール表示",
        "背景を透過",
        "ワールド背景色を被写体に影響させない",
        "ソリッド背景色",
        "サブディビジョンサーフェス",
        "コマを後ろにする",
        "ハッチング間隔を表示",
        "ハッチング回転",
        "ビューを更新",
    ):
        assert required in text_blob, required
    assert "前後ページ数" not in text_blob
    assert "bmanga.coma_camera_toggle_name_backgrounds" in operators
    assert "bmanga.page_preview_range_mode_set" in operators


def _check_camera_preset_duplicate() -> None:
    scene = bpy.context.scene
    settings = scene.bmanga_coma_camera_settings
    scene.camera.location = (1.0, 2.0, 3.0)
    scene.camera.data.shift_x = 0.125
    assert bpy.ops.bmanga.coma_camera_angle_add() == {"FINISHED"}
    settings.camera_angles[0].name = "正面"
    assert bpy.ops.bmanga.coma_camera_angle_duplicate() == {"FINISHED"}
    assert len(settings.camera_angles) == 2
    assert settings.camera_angles_index == 1
    assert settings.camera_angles[1].name.startswith("正面 コピー")
    assert abs(float(settings.camera_angles[1].shift_x) - 0.125) < 1.0e-6


def _check_render_panel() -> None:
    from bmanga_render_panel_migration import core
    from bmanga_render_panel_migration import panels as render_panels

    assert bpy.ops.bmanga_render.load_builtin_presets(reset=True) == {"FINISHED"}
    labels, operators = _draw_panel(render_panels.BMANGA_RENDER_PT_fisheye, bpy.context)
    text_blob = "\n".join(labels)
    for forbidden in (
        "魚眼モード",
        "魚眼FOV",
        "ページ画像のスケール",
    ):
        assert forbidden not in text_blob, forbidden
    for required in (
        "縮小モード",
        "縮小率",
        "Pencil+4 線幅を保存",
    ):
        assert required in text_blob, required
    assert "bmanga_render.save_pencil4_widths" in operators
    assert operators.count("bmanga_render.set_reduction_scale") >= 4

    scene = bpy.context.scene
    scene.render.resolution_x = 1200
    scene.render.resolution_y = 800
    scene.original_resolution_x = 1200
    scene.original_resolution_y = 800
    scene.fisheye_layout_mode = True
    scene.fisheye_fov = 2.6
    assert scene.bmanga_coma_camera_fisheye_layout_mode is True
    assert abs(float(scene.bmanga_coma_camera_fisheye_fov) - 2.6) < 1.0e-6
    assert scene.camera.data.type == "PANO"
    scene.reduction_mode = True
    assert scene.bmanga_coma_camera_reduction_mode is True
    assert bpy.ops.bmanga_render.set_reduction_scale(percentage=25.0) == {"FINISHED"}
    assert abs(float(scene.bmanga_coma_camera_preview_scale_percentage) - 25.0) < 1.0e-6
    assert scene.render.resolution_x == scene.render.resolution_y == 300
    scene.my_tool.bg_images_scale = 1.75
    assert abs(float(scene.bmanga_coma_camera_settings.bg_images_scale) - 1.75) < 1.0e-6
    assert core.fisheye_enabled(scene) is True


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bmanga = None
    render = None
    try:
        bmanga = _load_package("bmanga_panel_migration", ROOT)
        render = _load_package("bmanga_render_panel_migration", ROOT / "addons" / "b_manga_render")
        _prepare_coma_context()
        _check_bmanga_panel()
        _check_bmanga_view_panel()
        _check_camera_preset_duplicate()
        _check_render_panel()
        print("BMANGA_COMA_CAMERA_RENDER_PANEL_RETURN_OK")
    finally:
        if render is not None:
            render.unregister()
        if bmanga is not None:
            bmanga.unregister()


if __name__ == "__main__":
    main()
