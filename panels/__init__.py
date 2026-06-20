"""panels — N-Panel (View3D > UI region) の B-MANGA タブ."""

from __future__ import annotations

import bpy

from . import (
    balloon_panel as _legacy_balloon_panel,
    effect_line_panel as _legacy_effect_line_panel,
    export_panel,
    gpencil_panel,
    layer_panel as _legacy_layer_panel,
    page_panel,
    coma_camera_panel,
    coma_detail_panel,
    coma_list_panel as _legacy_coma_list_panel,
    coma_tools_panel as _legacy_coma_tools_panel,
    outliner_layer_panel,
    paper_panel,
    tool_panel,
    view_panel,
    work_panel,
)

_MODULES = (
    work_panel,
    paper_panel,
    page_panel,
    tool_panel,
    view_panel,
    coma_camera_panel,
    coma_detail_panel,
    gpencil_panel,
    outliner_layer_panel,
    export_panel,
)

_CANONICAL_PANEL_CATEGORY = "B-MANGA"
_OWNED_PANEL_PREFIXES = (
    "BMANGA_PT_",
    "BNAME_PT_",
    "B_NAME_PT_",
)


def _unregister_stale_bmanga_panel_classes() -> None:
    """旧タブ名や再読込残りのB-MANGAパネルを登録前に外す."""
    for class_name in list(dir(bpy.types)):
        if not class_name.startswith(_OWNED_PANEL_PREFIXES):
            continue
        cls = getattr(bpy.types, class_name, None)
        if cls is None:
            continue
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


def _normalize_bmanga_panel_categories() -> None:
    """登録済みB-MANGAパネルをB-MANGAタブへ統一する."""
    for class_name in dir(bpy.types):
        if not class_name.startswith("BMANGA_PT_"):
            continue
        cls = getattr(bpy.types, class_name, None)
        if cls is None:
            continue
        try:
            if (
                getattr(cls, "bl_space_type", "") == "VIEW_3D"
                and getattr(cls, "bl_region_type", "") == "UI"
            ):
                cls.bl_category = _CANONICAL_PANEL_CATEGORY
        except Exception:
            pass


def _unregister_legacy_image_layer_panel() -> None:
    """旧「画像レイヤー」独立パネルを登録済みクラス名からも確実に外す."""
    try:
        _legacy_layer_panel.unregister()
    except Exception:
        pass
    for class_name in ("BMANGA_PT_image_layers", "BMANGA_UL_image_layers"):
        cls = getattr(bpy.types, class_name, None)
        if cls is None:
            continue
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


def _unregister_legacy_tool_panels() -> None:
    """旧独立セクションを Reload Addons 後も残さない."""
    for module in (
        _legacy_balloon_panel,
        _legacy_effect_line_panel,
        _legacy_coma_list_panel,
        _legacy_coma_tools_panel,
    ):
        try:
            module.unregister()
        except Exception:
            pass
    for class_name in (
        "BMANGA_UL_balloons",
        "BMANGA_UL_texts",
        "BMANGA_PT_balloons",
        "BMANGA_PT_texts",
        "BMANGA_PT_effect_line",
        "BMANGA_OT_coma_enter_from_list",
        "BMANGA_UL_comas",
        "BMANGA_PT_comas",
        "BMANGA_PT_coma_tools",
        "BMANGA_PT_coma_shape",
        "BMANGA_PT_coma_border",
        "BMANGA_PT_coma_white_margin",
    ):
        cls = getattr(bpy.types, class_name, None)
        if cls is None:
            continue
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


def register() -> None:
    # 旧「画像レイヤー」/「フキダシ」/「テキスト」/「効果線」独立パネルは
    # 新 UI では登録しない。
    # Reload Addons 時に前回登録分が残っている場合もここで外す。
    _unregister_stale_bmanga_panel_classes()
    _unregister_legacy_image_layer_panel()
    _unregister_legacy_tool_panels()
    for module in _MODULES:
        module.register()
    _normalize_bmanga_panel_categories()


def unregister() -> None:
    for module in reversed(_MODULES):
        try:
            module.unregister()
        except Exception:
            pass
    _unregister_legacy_image_layer_panel()
    _unregister_legacy_tool_panels()
    _unregister_stale_bmanga_panel_classes()
