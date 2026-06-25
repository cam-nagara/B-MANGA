"""B-MANGA Line データモデル."""

from __future__ import annotations

import math

import bpy
from bpy.props import (
    BoolProperty,
    FloatProperty,
    FloatVectorProperty,
    PointerProperty,
)

# ------------------------------------------------------------------
# 命名規則 — モディファイア・マテリアル・頂点グループ等の識別子
# ------------------------------------------------------------------
MODIFIER_NAME = "BML_Outline"
MATERIAL_NAME = "BML_Outline"
VG_LINE_WIDTH = "BML_LineWidth"
COLOR_ATTR_NAME = "BML_LineWidth"
GN_MODIFIER_NAME = "BML_InnerLines"
GN_TREE_NAME = "BML_InnerLines"
AO_ATTR_NAME = "BML_AO"
AOV_NAME = "BML_Line"
PROP_BASE_THICKNESS = "bml_base_thickness"
PROP_REF_DISTANCE = "bml_ref_distance"


# ------------------------------------------------------------------
# 設定変更時のコールバック
# ------------------------------------------------------------------

def _on_color_changed(self, _context):
    from . import outline_setup
    outline_setup.update_material_color(tuple(self.outline_color))


def _on_thickness_changed(self, context):
    from . import outline_setup
    for obj in context.selected_objects:
        if obj.type == "MESH":
            outline_setup.update_modifier_thickness(obj, self.outline_thickness)


def _on_even_thickness_changed(self, context):
    for obj in context.selected_objects:
        if obj.type != "MESH":
            continue
        mod = obj.modifiers.get(MODIFIER_NAME)
        if mod is not None:
            mod.use_even_offset = self.even_thickness


def _on_inner_angle_changed(self, context):
    from . import inner_lines
    for obj in context.selected_objects:
        if obj.type == "MESH":
            inner_lines.update_parameters(obj, angle=self.inner_line_angle)


def _on_inner_thickness_changed(self, context):
    from . import inner_lines
    for obj in context.selected_objects:
        if obj.type == "MESH":
            inner_lines.update_parameters(obj, thickness=self.inner_line_thickness)


def _on_camera_comp_changed(self, context):
    from . import camera_comp
    if self.use_camera_compensation:
        for obj in context.selected_objects:
            if obj.type == "MESH":
                camera_comp.store_reference(obj, context.scene)
    else:
        for obj in context.selected_objects:
            if obj.type != "MESH":
                continue
            mod = obj.modifiers.get(MODIFIER_NAME)
            base_t = obj.get(PROP_BASE_THICKNESS)
            if mod is not None and base_t is not None:
                mod.thickness = -abs(base_t)


def _on_culling_changed(self, context):
    from . import camera_comp
    if not self.use_camera_culling:
        for obj in context.scene.objects:
            if obj.type != "MESH":
                continue
            for mod_name in (MODIFIER_NAME, GN_MODIFIER_NAME):
                mod = obj.modifiers.get(mod_name)
                if mod is not None:
                    mod.show_viewport = True
                    mod.show_render = True
        if self.use_inner_line_distance_limit:
            camera_comp.refresh(context)


def _on_inner_distance_changed(self, context):
    from . import camera_comp
    if not self.use_inner_line_distance_limit:
        for obj in context.scene.objects:
            if obj.type != "MESH":
                continue
            mod = obj.modifiers.get(GN_MODIFIER_NAME)
            if mod is not None:
                mod.show_viewport = True
                mod.show_render = True
        if self.use_camera_culling:
            camera_comp.refresh(context)


# ------------------------------------------------------------------
# プロパティグループ
# ------------------------------------------------------------------

class BMangaLineSettings(bpy.types.PropertyGroup):
    """シーンごとの B-MANGA Line 設定."""

    outline_thickness: FloatProperty(
        name="線幅",
        description="アウトラインの太さ",
        default=0.002,
        min=0.0001,
        max=0.1,
        precision=4,
        step=0.1,
        update=_on_thickness_changed,
    )  # type: ignore[valid-type]

    outline_color: FloatVectorProperty(
        name="線の色",
        description="アウトラインの色 (scene-linear RGB)",
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_color_changed,
    )  # type: ignore[valid-type]

    use_vertex_color: BoolProperty(
        name="頂点カラーで線幅を制御",
        description="頂点カラーの明度で線幅の強弱をつける",
        default=False,
    )  # type: ignore[valid-type]

    even_thickness: BoolProperty(
        name="均一な厚み",
        description="凸凹した面でも均一な線幅にする",
        default=True,
        update=_on_even_thickness_changed,
    )  # type: ignore[valid-type]

    inner_line_enabled: BoolProperty(
        name="内部線を追加",
        description="折れ目（稜線・谷線）を検出して線を追加する",
        default=False,
    )  # type: ignore[valid-type]

    inner_line_angle: FloatProperty(
        name="検出角度",
        description="この角度以上の折れ目に線を描画する",
        default=math.radians(30),
        min=math.radians(1),
        max=math.radians(180),
        subtype="ANGLE",
        update=_on_inner_angle_changed,
    )  # type: ignore[valid-type]

    inner_line_thickness: FloatProperty(
        name="内部線の太さ",
        description="内部線ジオメトリの半径",
        default=0.0005,
        min=0.0001,
        max=0.05,
        precision=4,
        step=0.01,
        update=_on_inner_thickness_changed,
    )  # type: ignore[valid-type]

    # --- カメラ距離補正 ---

    use_camera_compensation: BoolProperty(
        name="カメラ距離で線幅を補正",
        description="カメラから離れても画面上の線幅を維持する",
        default=False,
        update=_on_camera_comp_changed,
    )  # type: ignore[valid-type]

    camera_compensation_influence: FloatProperty(
        name="補正の影響度",
        description="カメラ距離補正の効き具合（0: 補正なし / 1: 完全維持）",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )  # type: ignore[valid-type]

    # --- AO 線幅制御 ---

    use_ao_influence: BoolProperty(
        name="AOで線幅を制御",
        description="焼き付けたAOの暗い部分で線を太くする",
        default=False,
    )  # type: ignore[valid-type]

    ao_influence_strength: FloatProperty(
        name="AO影響度",
        description="AO情報が線幅に影響する強さ",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )  # type: ignore[valid-type]

    # --- エッジ角度による線幅調整 ---

    edge_smooth_factor: FloatProperty(
        name="中間頂点の線幅調整",
        description="平坦な辺の線幅を調整（正: 太く / 負: 細く）",
        default=0.0,
        min=-1.0,
        max=1.0,
        subtype="FACTOR",
    )  # type: ignore[valid-type]

    # --- カメラ範囲カリング ---

    use_camera_culling: BoolProperty(
        name="カメラ範囲外を非表示",
        description="カメラビュー外のオブジェクトのラインを非表示にして動作を軽くする",
        default=False,
        update=_on_culling_changed,
    )  # type: ignore[valid-type]

    culling_margin: FloatProperty(
        name="余白",
        description="カメラ範囲外の判定に追加する余白角度",
        default=math.radians(10),
        min=0.0,
        max=math.radians(90),
        subtype="ANGLE",
    )  # type: ignore[valid-type]

    # --- 内部線距離制限 ---

    use_inner_line_distance_limit: BoolProperty(
        name="距離で内部線を制限",
        description="カメラから指定距離以上離れたオブジェクトの内部線を非表示にする",
        default=False,
        update=_on_inner_distance_changed,
    )  # type: ignore[valid-type]

    inner_line_max_distance: FloatProperty(
        name="内部線の最大表示距離",
        description="この距離を超えたオブジェクトの内部線を非表示にする",
        default=20.0,
        min=0.1,
        max=1000.0,
        subtype="DISTANCE",
    )  # type: ignore[valid-type]


# ------------------------------------------------------------------
# ヘルパー
# ------------------------------------------------------------------

_CLASSES = (BMangaLineSettings,)


def get_settings(context) -> BMangaLineSettings | None:
    scene = getattr(context, "scene", None)
    return getattr(scene, "bmanga_line_settings", None) if scene is not None else None


def has_outline(obj: bpy.types.Object) -> bool:
    return obj.type == "MESH" and obj.modifiers.get(MODIFIER_NAME) is not None


# ------------------------------------------------------------------
# 登録
# ------------------------------------------------------------------

def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bmanga_line_settings = PointerProperty(type=BMangaLineSettings)


def unregister() -> None:
    if hasattr(bpy.types.Scene, "bmanga_line_settings"):
        del bpy.types.Scene.bmanga_line_settings
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
