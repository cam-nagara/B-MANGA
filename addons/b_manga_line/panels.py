"""B-MANGA Line UI パネル."""

from __future__ import annotations

import bpy

from . import registration
from .core import PROP_LINE_ONLY, has_line, has_outline, sync_line_display_settings


def _get_paper_dpi(scene) -> int:
    """用紙のDPIを取得。取得できなければ 600 を返す。"""
    work = getattr(scene, "bmanga_work", None)
    paper = getattr(work, "paper", None) if work else None
    dpi = int(getattr(paper, "dpi", 0) or 0) if paper else 0
    return dpi if dpi > 0 else 600


def _mm_to_px_label(mm: float, dpi: int) -> str:
    px = mm * dpi / 25.4
    return f"≈ {px:.1f} px ({dpi} DPI)"


def _is_linked_line_object(obj: bpy.types.Object) -> bool:
    data = getattr(obj, "data", None)
    return (
        obj.type == "MESH"
        and has_line(obj)
        and (
            obj.library is not None
            or getattr(data, "library", None) is not None
            or getattr(obj, "override_library", None) is not None
            or getattr(data, "override_library", None) is not None
        )
    )


class BMANGA_LINE_PT_main(bpy.types.Panel):
    """B-MANGA Line メインパネル"""

    bl_label = "B-MANGA Line"
    bl_idname = "BMANGA_LINE_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "B-MANGA Line"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        _draw_render_range_selection(layout, context)
        if obj is None or obj.type != "MESH":
            layout.separator()
            layout.label(text="メッシュオブジェクトを選択してください", icon="INFO")
            return
        layout.separator()
        _draw_actions(layout, context, obj)


class _BMangaLineMeshPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "B-MANGA Line"
    bl_parent_id = "BMANGA_LINE_PT_main"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"


def _active_settings(context):
    obj = context.active_object
    return getattr(obj, "bmanga_line_settings", None) if obj is not None else None


def _draw_presets(layout, context) -> None:
    col = layout.column(align=True)
    col.prop(context.scene, "bmanga_line_preset_name")
    col.operator("bmanga_line.preset_save", icon="ADD")
    presets = context.scene.bmanga_line_presets
    if presets:
        col.template_list(
            "UI_UL_list",
            "bmanga_line_presets",
            context.scene,
            "bmanga_line_presets",
            context.scene,
            "bmanga_line_preset_index",
            rows=3,
        )
        row = col.row(align=True)
        row.operator("bmanga_line.preset_apply_selected", icon="CHECKMARK")
        row.operator("bmanga_line.preset_delete", icon="TRASH")
    else:
        col.label(text="保存されたプリセットはありません", icon="INFO")


def _draw_render_range_selection(layout, _context) -> None:
    row = layout.row(align=True)
    row.operator("bmanga_line.select_render_range_meshes", icon="VIEW_CAMERA")


def _draw_outline(layout, context, settings) -> None:
    dpi = _get_paper_dpi(context.scene)
    _draw_basic_line_settings(
        layout,
        settings,
        "outline_enabled",
        "outline_thickness_mm",
        "outline_color",
        "use_outline_distance_limit",
        "outline_max_distance",
        dpi,
    )


def _draw_camera(layout, context, settings) -> None:
    from . import camera_comp

    col = layout.column(align=True)
    line_camera = camera_comp.get_line_camera(context.scene)
    camera_name = line_camera.name if line_camera else "未設定"
    override_camera = getattr(context.scene, "bmanga_line_camera", None)
    basis = "別カメラ指定" if override_camera else "カメラビュー"
    col.label(text=f"基準: {basis} ({camera_name})", icon="CAMERA_DATA")
    col.prop(context.scene, "bmanga_line_camera")
    col.separator()
    row = col.row(align=True)
    row.prop(settings, "line_width_reference_distance")
    row.operator(
        "bmanga_line.reset_camera_ref",
        text="選択原点まで",
        icon="EMPTY_ARROWS",
    )
    col.separator()
    col.prop(settings, "use_camera_compensation")
    sub = col.column(align=True)
    sub.enabled = settings.use_camera_compensation and not settings.use_uniform_line_width
    sub.prop(settings, "camera_compensation_influence")
    row = sub.row(align=True)
    row.operator("bmanga_line.reset_camera_ref", icon="FILE_REFRESH")
    row.operator("bmanga_line.refresh_camera", icon="PLAY")

    col.separator()
    col.prop(settings, "use_uniform_line_width")

    col.separator()
    col.prop(settings, "use_camera_culling")
    sub = col.column(align=True)
    sub.enabled = settings.use_camera_culling
    sub.prop(settings, "culling_margin")


def _draw_basic_line_settings(
    layout,
    settings,
    enabled_prop: str,
    thickness_prop: str,
    color_prop: str,
    range_enabled_prop: str,
    range_distance_prop: str,
    dpi: int,
) -> None:
    col = layout.column(align=True)
    col.prop(settings, enabled_prop)
    sub = col.column(align=True)
    sub.enabled = bool(getattr(settings, enabled_prop))
    row = sub.row(align=True)
    row.prop(settings, thickness_prop)
    sub_label = row.row(align=True)
    sub_label.alignment = "RIGHT"
    sub_label.label(text=_mm_to_px_label(getattr(settings, thickness_prop), dpi))
    sub.prop(settings, color_prop)
    sub.separator()
    sub.prop(settings, range_enabled_prop)
    range_col = sub.column(align=True)
    range_col.enabled = bool(getattr(settings, range_enabled_prop))
    range_col.prop(settings, range_distance_prop)


def _draw_inner_line(layout, context, settings) -> None:
    dpi = _get_paper_dpi(context.scene)
    _draw_basic_line_settings(
        layout,
        settings,
        "inner_line_enabled",
        "inner_line_thickness_mm",
        "inner_line_color",
        "use_inner_line_creation_limit",
        "inner_line_creation_max_distance",
        dpi,
    )


def _draw_intersection(layout, context, settings) -> None:
    dpi = _get_paper_dpi(context.scene)
    _draw_basic_line_settings(
        layout,
        settings,
        "intersection_enabled",
        "intersection_thickness_mm",
        "intersection_color",
        "use_intersection_creation_limit",
        "intersection_creation_max_distance",
        dpi,
    )


def _draw_selection_line(layout, context, settings) -> None:
    dpi = _get_paper_dpi(context.scene)
    _draw_basic_line_settings(
        layout,
        settings,
        "selection_line_enabled",
        "selection_line_thickness_mm",
        "selection_line_color",
        "use_selection_line_creation_limit",
        "selection_line_creation_max_distance",
        dpi,
    )


def _draw_detail_cell(row, settings, prop_name: str | None) -> None:
    col = row.column(align=True)
    if prop_name:
        col.prop(settings, prop_name)
    else:
        col.label(text="")


def _draw_line_detail_grid(layout, settings) -> None:
    box = layout.box()
    header = box.row(align=True)
    for label in ("アウトライン", "稜谷線", "交差線", "選択線"):
        col = header.column(align=True)
        col.label(text=label)

    rows = (
        ("edge_midpoint_angle", "inner_line_angle", "intersection_edge_midpoint_angle", "selection_edge_midpoint_angle"),
        ("outline_offset", "inner_line_offset", "intersection_line_offset", "selection_line_offset"),
        ("edge_smooth_factor", "inner_edge_smooth_factor", "intersection_edge_smooth_factor", "selection_edge_smooth_factor"),
        ("edge_midpoint_jitter_percent", "inner_edge_midpoint_jitter_percent", "intersection_edge_midpoint_jitter_percent", "selection_edge_midpoint_jitter_percent"),
        ("edge_width_curve_25", "inner_edge_width_curve_25", "intersection_edge_width_curve_25", "selection_edge_width_curve_25"),
        ("edge_width_curve_50", "inner_edge_width_curve_50", "intersection_edge_width_curve_50", "selection_edge_width_curve_50"),
        ("edge_width_curve_75", "inner_edge_width_curve_75", "intersection_edge_width_curve_75", "selection_edge_width_curve_75"),
        ("use_outline_distance_limit", "use_inner_line_distance_limit", "use_intersection_distance_limit", "use_selection_line_distance_limit"),
        ("outline_max_distance", "inner_line_max_distance", "intersection_max_distance", "selection_line_max_distance"),
        ("even_thickness", None, None, None),
        ("use_rim", None, None, None),
        ("hide_through_transparent", None, None, None),
        ("use_vertex_color", None, None, None),
        (None, None, "intersection_method", None),
    )
    for props in rows:
        row = box.row(align=True)
        for prop_name in props:
            _draw_detail_cell(row, settings, prop_name)


class BMANGA_LINE_OT_detail_settings(bpy.types.Operator):
    """ライン詳細設定を表示"""

    bl_idname = "bmanga_line.detail_settings"
    bl_label = "詳細設定"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=980)

    def execute(self, _context):
        return {"FINISHED"}

    def draw(self, context):
        settings = _active_settings(context)
        if settings is not None:
            _draw_line_detail_grid(self.layout, settings)


def _draw_actions(layout, context, obj) -> None:
    from . import viewport_aov

    has_line_any = any(has_line(o) for o in context.selected_objects)
    line_only_any = any(
        bool(o.get(PROP_LINE_ONLY, False)) for o in context.selected_objects
    ) or viewport_aov.is_line_aov_active(context)
    row = layout.row(align=True)
    row.scale_y = 1.4
    row.operator("bmanga_line.apply", icon="ADD")
    settings = getattr(obj, "bmanga_line_settings", None)
    if settings is not None:
        layout.prop(settings, "auto_subdivision_for_midpoint")
        layout.operator("bmanga_line.detail_settings", icon="PREFERENCES")

    linked_line_count = sum(
        1 for linked_obj in context.scene.objects
        if _is_linked_line_object(linked_obj)
    )
    row = layout.row(align=True)
    row.enabled = linked_line_count > 0
    row.operator("bmanga_line.refresh_linked", text="リンク素材のラインを補正", icon="FILE_REFRESH")

    row = layout.row(align=True)
    row.enabled = linked_line_count > 0 and has_line(obj)
    row.operator(
        "bmanga_line.apply_active_to_linked",
        text="リンク素材へ選択設定を上書き",
        icon="LINKED",
    )

    if settings is not None:
        sync_line_display_settings(obj)
        row = layout.row(align=True)
        row.enabled = has_line_any
        row.prop(settings, "lines_visible")

        row = layout.row(align=True)
        row.enabled = has_line_any or line_only_any
        row.prop(settings, "line_only_visible")

        row = layout.row(align=True)
        row.prop(settings, "match_subsurf_viewport_to_render")

    row = layout.row(align=True)
    row.enabled = has_line_any
    row.operator("bmanga_line.setup_aov_composite", icon="NODETREE")

    row = layout.row(align=True)
    row.enabled = has_line_any
    row.operator("bmanga_line.remove", icon="REMOVE")

    mesh_count = sum(1 for selected in context.selected_objects if selected.type == "MESH")
    outline_count = sum(1 for selected in context.selected_objects if has_outline(selected))
    if mesh_count > 0:
        layout.separator()
        info = layout.column(align=True)
        info.scale_y = 0.8
        info.label(
            text=f"選択メッシュ: {mesh_count}  ライン適用済み: {outline_count}",
            icon="INFO",
        )


class BMANGA_LINE_PT_presets(_BMangaLineMeshPanel, bpy.types.Panel):
    bl_label = "ラインプリセット"
    bl_idname = "BMANGA_LINE_PT_presets"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        _draw_presets(self.layout, context)


class BMANGA_LINE_PT_outline(_BMangaLineMeshPanel, bpy.types.Panel):
    bl_label = "アウトライン設定"
    bl_idname = "BMANGA_LINE_PT_outline"

    def draw(self, context):
        _draw_outline(self.layout, context, _active_settings(context))


class BMANGA_LINE_PT_camera(_BMangaLineMeshPanel, bpy.types.Panel):
    bl_label = "カメラ設定"
    bl_idname = "BMANGA_LINE_PT_camera"

    def draw(self, context):
        _draw_camera(self.layout, context, _active_settings(context))


class BMANGA_LINE_PT_inner_line(_BMangaLineMeshPanel, bpy.types.Panel):
    bl_label = "稜谷線"
    bl_idname = "BMANGA_LINE_PT_inner_line"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        _draw_inner_line(self.layout, context, _active_settings(context))


class BMANGA_LINE_PT_intersection(_BMangaLineMeshPanel, bpy.types.Panel):
    bl_label = "交差線（オブジェクト間）"
    bl_idname = "BMANGA_LINE_PT_intersection"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        _draw_intersection(self.layout, context, _active_settings(context))


class BMANGA_LINE_PT_selection_line(_BMangaLineMeshPanel, bpy.types.Panel):
    bl_label = "選択線"
    bl_idname = "BMANGA_LINE_PT_selection_line"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        _draw_selection_line(self.layout, context, _active_settings(context))


_CLASSES = (
    BMANGA_LINE_OT_detail_settings,
    BMANGA_LINE_PT_main,
    BMANGA_LINE_PT_presets,
    BMANGA_LINE_PT_outline,
    BMANGA_LINE_PT_camera,
    BMANGA_LINE_PT_inner_line,
    BMANGA_LINE_PT_intersection,
    BMANGA_LINE_PT_selection_line,
)


def register() -> None:
    for cls in _CLASSES:
        registration.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
