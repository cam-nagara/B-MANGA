"""B-MANGA Liner UI パネル."""

from __future__ import annotations

import bpy

from . import registration
from .core import has_line, has_outline, is_settings_locked


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
    """B-MANGA Liner メインパネル"""

    bl_label = "B-MANGA Liner"
    bl_idname = "BMANGA_LINE_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BMLiner"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        _draw_render_range_selection(layout, context)
        _draw_global_display_controls(layout, context)
        if obj is None or obj.type != "MESH":
            layout.separator()
            layout.label(text="メッシュオブジェクトを選択してください", icon="INFO")
            return
        layout.separator()
        _draw_actions(layout, context, obj)


class _BMangaLineMeshPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BMLiner"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"


def _active_settings(context):
    obj = context.active_object
    return getattr(obj, "bmanga_line_settings", None) if obj is not None else None


def _draw_presets(layout, context) -> None:
    from . import presets as line_presets

    line_presets.ensure_presets_loaded(context.scene)
    col = layout.column(align=True)
    row = col.row(align=True)
    row.operator("bmanga_line.preset_add", icon="ADD")
    row.operator("bmanga_line.preset_save", icon="FILE_TICK")
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
        row.operator("bmanga_line.preset_duplicate", icon="DUPLICATE")
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
        "use_outline_creation_limit",
        "outline_creation_max_distance",
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
    sub = col.column(align=True)
    sub.enabled = settings.use_uniform_line_width
    sub.prop(settings, "line_width_distance_falloff")

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
    head = col.row(align=True)
    head.prop(settings, enabled_prop)
    head.prop(settings, color_prop, text="")
    sub = col.column(align=True)
    sub.enabled = bool(getattr(settings, enabled_prop))
    row = sub.row(align=True)
    row.prop(settings, thickness_prop)
    sub_label = row.row(align=True)
    sub_label.alignment = "RIGHT"
    sub_label.label(text=_mm_to_px_label(getattr(settings, thickness_prop), dpi))
    range_row = sub.row(align=True)
    range_row.prop(settings, range_enabled_prop)
    range_cell = range_row.row(align=True)
    range_cell.enabled = bool(getattr(settings, range_enabled_prop))
    range_cell.prop(settings, range_distance_prop)


def _draw_line_settings(layout, context, settings) -> None:
    if settings is None:
        return
    row = layout.row(align=True)
    row.scale_y = 1.2
    row.operator("bmanga_line.reflect_all", icon="FILE_REFRESH")
    row = layout.row(align=True)
    row.enabled = any(has_line(o) for o in context.selected_objects)
    row.operator("bmanga_line.remove_all", icon="TRASH")
    row = layout.row(align=True)
    row.operator("bmanga_line.detail_settings", text="詳細設定", icon="PREFERENCES")

    # ロック中は「すべてのラインを反映」以外をグレーアウトする
    # （ロック外の選択オブジェクトには効くため、このボタンだけは押下可のまま）。
    body = layout.column()
    body.enabled = not bool(getattr(settings, "settings_locked", False))

    row = body.row(align=True)
    row.prop(settings, "auto_subdivision_for_midpoint")
    op = row.operator("bmanga_line.update_auto_subdivision", text="反映", icon="FILE_REFRESH")
    op.action = "REFLECT"
    op = row.operator("bmanga_line.update_auto_subdivision", text="削除", icon="TRASH")
    op.action = "DELETE"

    # 各線種のボタンは「反映」1つに統合済み（無ければ作成、有れば更新、
    # メッシュ編集後なら作り直す）。バンプ線もモディファイア/マテリアルを
    # 生成しない画像空間処理だが、同じ「反映」ボタンで見た目を同期する。
    for index, (target, label, draw_func) in enumerate((
        ("outline", "アウトライン", _draw_outline),
        ("inner", "稜谷線", _draw_inner_line),
        ("intersection", "交差線", _draw_intersection),
        ("selection", "選択線", _draw_selection_line),
        ("bump", "バンプ線", _draw_bump_line),
    )):
        body.separator()
        section = body.column(align=True)
        header = section.row(align=True)
        header.label(text=label)
        buttons = header.row(align=True)
        op = buttons.operator("bmanga_line.reflect_target", text="反映", icon="FILE_REFRESH")
        op.target = target
        draw_func(section, context, settings)


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


def _draw_bump_line(layout, context, settings) -> None:
    dpi = _get_paper_dpi(context.scene)
    col = layout.column(align=True)
    head = col.row(align=True)
    head.prop(settings, "bump_line_enabled")
    head.prop(settings, "bump_line_color", text="")
    sub = col.column(align=True)
    sub.enabled = bool(settings.bump_line_enabled)
    row = sub.row(align=True)
    row.prop(settings, "bump_line_thickness")
    sub_label = row.row(align=True)
    sub_label.alignment = "RIGHT"
    sub_label.label(text=_mm_to_px_label(settings.bump_line_thickness, dpi))
    sub.prop(settings, "bump_line_threshold")
    sub.label(text="レンダリング結果にのみ反映されます（ビューポート非対応）", icon="INFO")


def _draw_detail_cell(row, settings, prop_name: str | None) -> None:
    col = row.column(align=True)
    if prop_name:
        col.prop(settings, prop_name)
    else:
        col.label(text="")


def _draw_midpoint_width_controls(
    layout,
    settings,
    target: str,
    _label: str,
    factor_prop: str,
    jitter_prop: str,
    angle_prop: str | None,
) -> None:
    from . import edge_width_curve

    col = layout.column(align=True)
    if angle_prop:
        col.prop(settings, angle_prop)
    col.prop(settings, factor_prop)
    col.prop(settings, jitter_prop)
    col.label(text="中間頂点への変化グラフ")
    node = edge_width_curve.get_node(target)
    draw_curve = getattr(col, "template_curve_mapping", None)
    if node is not None and callable(draw_curve):
        draw_curve(node, "mapping", type="NONE")


def _draw_line_detail_grid(layout, settings) -> None:
    layout.prop(settings, "auto_subdivision_for_midpoint")
    box = layout.box()
    header = box.row(align=True)
    labels = ("アウトライン", "稜谷線", "交差線", "選択線")
    for index, label in enumerate(labels):
        col = header.column(align=True)
        col.label(text=label)
        if index < len(labels) - 1:
            header.separator()

    rows = (
        ("outline_enabled", "inner_line_enabled", "intersection_enabled", "selection_line_enabled"),
        ("outline_thickness_mm", "inner_line_thickness_mm", "intersection_thickness_mm", "selection_line_thickness_mm"),
        ("outline_color", "inner_line_color", "intersection_color", "selection_line_color"),
        ("use_outline_creation_limit", "use_inner_line_creation_limit", "use_intersection_creation_limit", "use_selection_line_creation_limit"),
        ("outline_creation_max_distance", "inner_line_creation_max_distance", "intersection_creation_max_distance", "selection_line_creation_max_distance"),
        ("outline_offset", "inner_line_offset", "intersection_line_offset", "selection_line_offset"),
        ("use_outline_distance_limit", "use_inner_line_distance_limit", "use_intersection_distance_limit", "use_selection_line_distance_limit"),
        ("outline_max_distance", "inner_line_max_distance", "intersection_max_distance", "selection_line_max_distance"),
        ("even_thickness", None, None, None),
        ("use_rim", None, None, None),
        ("hide_through_transparent", None, None, None),
        ("use_vertex_color", None, None, None),
        ("weld_mesh_for_outline", None, None, None),
    )
    for props in rows:
        row = box.row(align=True)
        for index, prop_name in enumerate(props):
            _draw_detail_cell(row, settings, prop_name)
            if index < len(props) - 1:
                row.separator()

    row = box.row(align=True)
    controls = (
        ("outline", "線幅の詳細", "edge_smooth_factor", "edge_midpoint_jitter_percent", "edge_midpoint_angle"),
        ("inner", "線幅の詳細", "inner_edge_smooth_factor", "inner_edge_midpoint_jitter_percent", "inner_line_angle"),
        ("intersection", "線幅の詳細", "intersection_edge_smooth_factor", "intersection_edge_midpoint_jitter_percent", "intersection_edge_midpoint_angle"),
        ("selection", "線幅の詳細", "selection_edge_smooth_factor", "selection_edge_midpoint_jitter_percent", "selection_edge_midpoint_angle"),
    )
    for index, control in enumerate(controls):
        _draw_midpoint_width_controls(row, settings, *control)
        if index < len(controls) - 1:
            row.separator()


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
        from . import edge_width_curve

        settings = _active_settings(context)
        if settings is not None:
            for target in ("outline", "inner", "intersection", "selection"):
                edge_width_curve.reset_node_from_settings(settings, target)
        return context.window_manager.invoke_props_dialog(self, width=980)

    def execute(self, context):
        from . import edge_width_curve

        settings = _active_settings(context)
        if settings is not None:
            for target in ("outline", "inner", "intersection", "selection"):
                edge_width_curve.sync_settings_and_node(settings, target)
        return {"FINISHED"}

    def cancel(self, context):
        from . import edge_width_curve

        settings = _active_settings(context)
        if settings is not None:
            for target in ("outline", "inner", "intersection", "selection"):
                edge_width_curve.reset_node_from_settings(settings, target)

    def draw(self, context):
        settings = _active_settings(context)
        if settings is not None:
            self.layout.enabled = not bool(getattr(settings, "settings_locked", False))
            _draw_line_detail_grid(self.layout, settings)


def _draw_global_display_controls(layout, context) -> None:
    scene = context.scene
    from . import aov_compositor

    col = layout.column(align=True)
    col.prop(scene, "bmanga_line_lines_visible")
    col.prop(scene, "bmanga_line_line_only_visible")
    col.prop(scene, "bmanga_line_match_subsurf_viewport_to_render")

    row = col.row(align=True)
    row.enabled = not aov_compositor.line_aov_compositor_exists(scene)
    row.operator("bmanga_line.setup_aov_composite", icon="NODETREE")


def _draw_actions(layout, context, obj) -> None:
    from . import update_state

    mesh_count = sum(1 for selected in context.selected_objects if selected.type == "MESH")
    locked_count = sum(
        1 for selected in context.selected_objects
        if selected.type == "MESH" and is_settings_locked(selected)
    )
    lock_row = layout.row(align=True)
    lock_cell = lock_row.row(align=True)
    lock_cell.enabled = locked_count < mesh_count
    lock_op = lock_cell.operator("bmanga_line.set_settings_lock", text="選択をロック", icon="LOCKED")
    lock_op.lock = True
    unlock_cell = lock_row.row(align=True)
    unlock_cell.enabled = locked_count > 0
    unlock_op = unlock_cell.operator("bmanga_line.set_settings_lock", text="ロック解除", icon="UNLOCKED")
    unlock_op.lock = False
    if locked_count > 0:
        status = layout.row(align=True)
        status.scale_y = 0.8
        status.label(text=f"ロック中: {locked_count}/{mesh_count}", icon="INFO")

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

    outline_count = sum(1 for selected in context.selected_objects if has_outline(selected))
    if mesh_count > 0:
        layout.separator()
        info = layout.column(align=True)
        info.scale_y = 0.8
        info.label(
            text=f"選択メッシュ: {mesh_count}  ライン適用済み: {outline_count}",
            icon="INFO",
        )
        if is_settings_locked(obj):
            info.label(text="ロック中（設定・更新は変更されません）", icon="LOCKED")
            return
        pending = update_state.pending_label(obj)
        if pending:
            info.label(text=pending, icon="ERROR")


class BMANGA_LINE_PT_presets(_BMangaLineMeshPanel, bpy.types.Panel):
    bl_label = "ラインプリセット"
    bl_idname = "BMANGA_LINE_PT_presets"
    bl_order = 1

    def draw(self, context):
        _draw_presets(self.layout, context)


class BMANGA_LINE_PT_line_settings(_BMangaLineMeshPanel, bpy.types.Panel):
    bl_label = "ライン設定"
    bl_idname = "BMANGA_LINE_PT_line_settings"
    bl_order = 2

    def draw(self, context):
        _draw_line_settings(self.layout, context, _active_settings(context))


class BMANGA_LINE_PT_camera(_BMangaLineMeshPanel, bpy.types.Panel):
    bl_label = "カメラ設定"
    bl_idname = "BMANGA_LINE_PT_camera"
    bl_order = 3

    def draw(self, context):
        settings = _active_settings(context)
        self.layout.enabled = not bool(getattr(settings, "settings_locked", False))
        _draw_camera(self.layout, context, settings)


_CLASSES = (
    BMANGA_LINE_OT_detail_settings,
    BMANGA_LINE_PT_main,
    BMANGA_LINE_PT_presets,
    BMANGA_LINE_PT_line_settings,
    BMANGA_LINE_PT_camera,
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
