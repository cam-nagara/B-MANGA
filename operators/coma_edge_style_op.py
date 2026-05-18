"""コマ枠の辺/頂点選択状態を保持する."""

from __future__ import annotations

import bpy


def sync_selected_style_props(_context) -> None:
    """互換用 no-op。選択状態だけを保持する."""
    return


def register() -> None:
    from bpy.props import EnumProperty, IntProperty, StringProperty

    bpy.types.WindowManager.bname_edge_select_kind = EnumProperty(
        name="選択種別",
        items=[
            ("none", "未選択", ""),
            ("edge", "辺", ""),
            ("border", "枠線全体", ""),
            ("vertex", "頂点", ""),
        ],
        default="none",
    )
    bpy.types.WindowManager.bname_edge_select_page = IntProperty(default=-1)
    bpy.types.WindowManager.bname_edge_select_coma = IntProperty(default=-1)
    bpy.types.WindowManager.bname_edge_select_edge = IntProperty(default=-1)
    bpy.types.WindowManager.bname_edge_select_vertex = IntProperty(default=-1)
    bpy.types.WindowManager.bname_edge_select_vertices = StringProperty(default="")
    bpy.types.WindowManager.bname_overlay_pointer_x = IntProperty(default=-1)
    bpy.types.WindowManager.bname_overlay_pointer_y = IntProperty(default=-1)
    bpy.types.WindowManager.bname_overlay_pointer_valid = bpy.props.BoolProperty(default=False)


def unregister() -> None:
    for prop in (
        "bname_edge_select_kind",
        "bname_edge_select_page",
        "bname_edge_select_coma",
        "bname_edge_select_edge",
        "bname_edge_select_vertex",
        "bname_edge_select_vertices",
        "bname_overlay_pointer_x",
        "bname_overlay_pointer_y",
        "bname_overlay_pointer_valid",
    ):
        try:
            delattr(bpy.types.WindowManager, prop)
        except AttributeError:
            pass
