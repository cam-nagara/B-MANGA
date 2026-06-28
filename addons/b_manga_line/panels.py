"""B-MANGA Line UI パネル."""

from __future__ import annotations

import bpy

from . import edge_width_curve
from .core import PROP_LINE_ONLY, has_line, has_outline


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
        from . import camera_comp, outline_setup

        outline_setup.ensure_aov_passes(context.scene)
        layout = self.layout
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            layout.label(text="メッシュオブジェクトを選択してください", icon="INFO")
            return

        settings = obj.bmanga_line_settings
        has_any = any(has_outline(o) for o in context.selected_objects)
        has_line_any = any(has_line(o) for o in context.selected_objects)
        line_only_any = any(
            bool(o.get(PROP_LINE_ONLY, False)) for o in context.selected_objects
        )

        dpi = _get_paper_dpi(context.scene)

        # --- プリセット管理 ---
        box = layout.box()
        box.label(text="ラインプリセット", icon="PRESET")
        col = box.column(align=True)
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

        # --- アウトライン設定 ---
        box = layout.box()
        box.label(text="アウトライン設定", icon="MOD_SOLIDIFY")
        col = box.column(align=True)
        row = col.row(align=True)
        row.prop(settings, "outline_thickness_mm")
        sub_label = row.row(align=True)
        sub_label.alignment = "RIGHT"
        sub_label.label(text=_mm_to_px_label(settings.outline_thickness_mm, dpi))
        col.prop(settings, "outline_color")
        col.prop(settings, "even_thickness")
        col.prop(settings, "use_uniform_line_width")
        col.prop(settings, "use_rim")
        col.prop(settings, "use_vertex_color")
        col.separator()
        col.prop(settings, "use_outline_distance_limit")
        sub = col.column(align=True)
        sub.enabled = settings.use_outline_distance_limit
        sub.prop(settings, "outline_max_distance")

        # --- カメラ設定 ---
        box = layout.box()
        box.label(text="カメラ設定", icon="CAMERA_DATA")

        # 距離補正
        col = box.column(align=True)
        line_camera = camera_comp.get_line_camera(context.scene)
        camera_name = line_camera.name if line_camera else "未設定"
        override_camera = getattr(context.scene, "bmanga_line_camera", None)
        basis = "別カメラ指定" if override_camera else "カメラビュー"
        col.label(text=f"基準: {basis} ({camera_name})", icon="CAMERA_DATA")
        col.prop(context.scene, "bmanga_line_camera")
        col.prop(settings, "use_camera_compensation")
        sub = col.column(align=True)
        sub.enabled = settings.use_camera_compensation and not settings.use_uniform_line_width
        sub.prop(settings, "camera_compensation_influence")
        row = sub.row(align=True)
        row.operator("bmanga_line.reset_camera_ref", icon="FILE_REFRESH")
        row.operator("bmanga_line.refresh_camera", icon="PLAY")

        col.separator()

        # ビューカリング
        col.prop(settings, "use_camera_culling")
        sub = col.column(align=True)
        sub.enabled = settings.use_camera_culling
        sub.prop(settings, "culling_margin")

        # --- 線幅の詳細制御 ---
        box = layout.box()
        box.label(text="線幅の詳細制御", icon="BRUSHES_ALL")
        col = box.column(align=True)
        col.prop(settings, "edge_smooth_factor")
        col.prop(settings, "edge_midpoint_jitter_percent")
        curve = col.column(align=True)
        curve.label(text="中間頂点への変化グラフ")
        edge_width_curve.sync_node_to_settings(settings)
        curve_node = edge_width_curve.ensure_node(settings)
        if curve_node is not None:
            curve.template_curve_mapping(curve_node, "mapping", type="NONE")
        else:
            curve.label(text="グラフを表示できません", icon="ERROR")
        col.separator()
        col.prop(settings, "use_ao_influence")
        sub = col.column(align=True)
        sub.enabled = settings.use_ao_influence
        sub.prop(settings, "ao_influence_strength")
        row = sub.row(align=True)
        row.operator("bmanga_line.bake_ao", icon="SHADING_RENDERED")
        col.separator()
        row = col.row(align=True)
        row.enabled = has_any
        row.operator("bmanga_line.sync_weights", icon="VPAINT_HLT")

        # --- 内部線設定 ---
        box = layout.box()
        box.label(text="内部線（稜線・谷線）", icon="MOD_EDGESPLIT")
        col = box.column(align=True)
        col.prop(settings, "inner_line_enabled")
        sub = col.column(align=True)
        sub.enabled = settings.inner_line_enabled
        sub.prop(settings, "inner_line_angle")
        row = sub.row(align=True)
        row.prop(settings, "inner_line_thickness_mm")
        sub_label = row.row(align=True)
        sub_label.alignment = "RIGHT"
        sub_label.label(text=_mm_to_px_label(settings.inner_line_thickness_mm, dpi))
        col.separator()
        sub_dist = col.column(align=True)
        sub_dist.enabled = settings.inner_line_enabled
        sub_dist.prop(settings, "use_inner_line_distance_limit")
        sub_dist2 = sub_dist.column(align=True)
        sub_dist2.enabled = settings.use_inner_line_distance_limit
        sub_dist2.prop(settings, "inner_line_max_distance")

        # --- 交差線設定 ---
        box = layout.box()
        box.label(text="交差線（オブジェクト間）", icon="MOD_BOOLEAN")
        col = box.column(align=True)
        col.prop(settings, "intersection_enabled")
        sub = col.column(align=True)
        sub.enabled = settings.intersection_enabled
        sub.prop(settings, "intersection_method")
        sub.prop(settings, "intersection_target")
        row = sub.row(align=True)
        row.prop(settings, "intersection_thickness_mm")
        sub_label = row.row(align=True)
        sub_label.alignment = "RIGHT"
        sub_label.label(text=_mm_to_px_label(settings.intersection_thickness_mm, dpi))
        sub.separator()
        sub.prop(settings, "use_intersection_distance_limit")
        sub_dist = sub.column(align=True)
        sub_dist.enabled = settings.use_intersection_distance_limit
        sub_dist.prop(settings, "intersection_max_distance")

        # --- 操作ボタン ---
        layout.separator()

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("bmanga_line.apply", icon="ADD")

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

        row = layout.row(align=True)
        row.enabled = has_line_any
        op = row.operator("bmanga_line.set_visibility", text="ラインを表示", icon="HIDE_OFF")
        op.visible = True
        op = row.operator("bmanga_line.set_visibility", text="ラインを非表示", icon="HIDE_ON")
        op.visible = False

        row = layout.row(align=True)
        row.enabled = has_line_any or line_only_any
        op = row.operator("bmanga_line.set_line_only", text="ラインのみを表示", icon="OVERLAY")
        op.line_only = True
        op = row.operator(
            "bmanga_line.set_line_only",
            text="通常表示に戻す",
            icon="MATERIAL",
        )
        op.line_only = False

        row = layout.row(align=True)
        row.enabled = has_line_any
        row.operator("bmanga_line.remove", icon="REMOVE")

        # --- 選択情報 ---
        mesh_count = sum(1 for obj in context.selected_objects if obj.type == "MESH")
        outline_count = sum(
            1 for obj in context.selected_objects if has_outline(obj)
        )
        if mesh_count > 0:
            layout.separator()
            info = layout.column(align=True)
            info.scale_y = 0.8
            info.label(
                text=f"選択メッシュ: {mesh_count}  ライン適用済み: {outline_count}",
                icon="INFO",
            )


_CLASSES = (BMANGA_LINE_PT_main,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
