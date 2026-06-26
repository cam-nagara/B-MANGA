"""B-MANGA Line UI パネル."""

from __future__ import annotations

import bpy

from .core import AOV_NAME, has_outline


def _get_paper_dpi(scene) -> int:
    """用紙のDPIを取得。取得できなければ 600 を返す。"""
    work = getattr(scene, "bmanga_work", None)
    paper = getattr(work, "paper", None) if work else None
    dpi = int(getattr(paper, "dpi", 0) or 0) if paper else 0
    return dpi if dpi > 0 else 600


def _mm_to_px_label(mm: float, dpi: int) -> str:
    px = mm * dpi / 25.4
    return f"≈ {px:.1f} px ({dpi} DPI)"


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
        if obj is None or obj.type != "MESH":
            layout.label(text="メッシュオブジェクトを選択してください", icon="INFO")
            return

        settings = obj.bmanga_line_settings
        has_any = any(has_outline(o) for o in context.selected_objects)

        dpi = _get_paper_dpi(context.scene)

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
        col.prop(settings, "use_rim")
        col.prop(settings, "use_vertex_color")

        # --- カメラ設定 ---
        box = layout.box()
        box.label(text="カメラ設定", icon="CAMERA_DATA")

        # 距離補正
        col = box.column(align=True)
        col.prop(settings, "use_camera_compensation")
        sub = col.column(align=True)
        sub.enabled = settings.use_camera_compensation
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

        # --- コンポジット出力 ---
        box = layout.box()
        box.label(text="コンポジット出力", icon="NODE_COMPOSITING")
        col = box.column(align=True)
        aov_exists = any(
            aov.name == AOV_NAME for aov in context.view_layer.aovs
        )
        if aov_exists:
            col.label(text=f"AOV: {AOV_NAME} (設定済み)", icon="CHECKMARK")
        else:
            col.operator("bmanga_line.add_aov", icon="ADD")

        # --- 操作ボタン ---
        layout.separator()

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("bmanga_line.apply", icon="ADD")

        row = layout.row(align=True)
        row.enabled = has_any
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
