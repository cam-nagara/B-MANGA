"""B-MANGA Line データモデル."""

from __future__ import annotations

import math

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    PointerProperty,
)

from . import registration

# ------------------------------------------------------------------
# 命名規則 — モディファイア・マテリアル・頂点グループ等の識別子
# ------------------------------------------------------------------
MODIFIER_NAME = "BML_Outline"
MATERIAL_NAME = "BML_Outline"
VG_LINE_WIDTH = "BML_LineWidth"
VG_INNER_LINE_WIDTH = "BML_InnerLineWidth"
VG_INTERSECTION_LINE_WIDTH = "BML_IntersectionLineWidth"
COLOR_ATTR_NAME = "BML_LineWidth"
GENERATED_LINE_ATTR = "BML_GeneratedLine"
GN_MODIFIER_NAME = "BML_InnerLines"
GN_TREE_NAME = "BML_InnerLines"
INTERSECTION_MODIFIER_NAME = "BML_IntersectionLines"
INTERSECTION_MODIFIER_PREFIX = INTERSECTION_MODIFIER_NAME + "__"
INTERSECTION_TREE_BOOLEAN = "BML_Intersection_Boolean"
INTERSECTION_TREE_SDF = "BML_Intersection_SDF"
AO_ATTR_NAME = "BML_AO"
AOV_NAME = "BML_Line"
PROP_LINES_HIDDEN = "bml_lines_hidden"
PROP_LINE_ONLY = "bml_line_only"
PROP_LINE_ONLY_MATERIALS = "bml_line_only_materials"
PROP_BASE_THICKNESS = "bml_base_thickness"
PROP_REF_DISTANCE = "bml_ref_distance"
PROP_REF_FOV_TAN = "bml_ref_fov_tan"
PROP_REF_MODE = "bml_ref_mode"
REF_MODE_VIEW = "VIEW"
REF_MODE_LOCKED = "LOCKED"
LINE_MODIFIER_NAMES = (
    MODIFIER_NAME,
    GN_MODIFIER_NAME,
)


# ------------------------------------------------------------------
# マルチセレクト伝搬
# ------------------------------------------------------------------

_propagating = False


def _add_unique_mesh_object(items: list[bpy.types.Object], obj) -> None:
    if obj is None or getattr(obj, "type", None) != "MESH":
        return
    if obj not in items:
        items.append(obj)


def _selected_mesh_objects(context, owner: bpy.types.Object) -> list[bpy.types.Object]:
    """パネル更新中の制限付き context でも実選択メッシュを拾う."""
    items: list[bpy.types.Object] = []
    for obj in getattr(context, "selected_objects", ()) or ():
        _add_unique_mesh_object(items, obj)

    global_context = getattr(bpy, "context", None)
    for obj in getattr(global_context, "selected_objects", ()) or ():
        _add_unique_mesh_object(items, obj)

    scenes = []
    for scene in (
        getattr(context, "scene", None),
        getattr(global_context, "scene", None),
    ):
        if scene is not None and scene not in scenes:
            scenes.append(scene)
    for scene in getattr(owner, "users_scene", ()) or ():
        if scene is not None and scene not in scenes:
            scenes.append(scene)

    for scene in scenes:
        for obj in getattr(scene, "objects", ()) or ():
            try:
                selected = obj.select_get()
            except (ReferenceError, RuntimeError):
                selected = False
            if selected:
                _add_unique_mesh_object(items, obj)
    return items


def _propagate(self, context, prop_name):
    """変更されたプロパティを選択中の他オブジェクトにも反映."""
    global _propagating
    if _propagating:
        return
    changed: list[bpy.types.Object] = []
    _propagating = True
    try:
        owner = self.id_data
        raw = getattr(self, prop_name)
        value = tuple(raw) if hasattr(raw, "__iter__") and not isinstance(raw, str) else raw
        for obj in _selected_mesh_objects(context, owner):
            if obj == owner or obj.type != "MESH":
                continue
            s = getattr(obj, "bmanga_line_settings", None)
            if s is not None:
                setattr(s, prop_name, value)
                if has_line(obj):
                    changed.append(obj)
    finally:
        _propagating = False
    if changed:
        from . import batch_update
        batch_update.refresh_propagated_property(prop_name, changed, context)
    else:
        _refresh_print_widths(context)


def _refresh_full_line_settings(obj: bpy.types.Object, context) -> None:
    if not has_line(obj):
        return
    from . import presets
    presets.apply_line_settings(obj, context)


def _refresh_print_widths(context) -> None:
    from . import camera_comp
    camera_comp.refresh(context)


def _make_propagator(prop_name):
    """伝搬のみ行うコールバックを生成."""
    def _callback(self, context):
        if _propagating:
            return
        _propagate(self, context, prop_name)
    return _callback


def _make_weight_refresh_propagator(prop_name):
    """線幅ウェイトを再計算してから選択中オブジェクトへ伝搬."""
    def _callback(self, context):
        if _propagating:
            return
        _refresh_line_width_weights(self, context, _line_width_target_for_prop(prop_name))
        _propagate(self, context, prop_name)
    return _callback


def _line_width_target_for_prop(prop_name: str) -> str:
    if prop_name.startswith("inner_edge_"):
        return "inner"
    if prop_name.startswith("intersection_edge_"):
        return "intersection"
    return "outline"


def _needs_line_width_weights(settings, target: str = "outline") -> bool:
    if settings.use_uniform_line_width:
        return True
    if target == "inner":
        return abs(settings.inner_edge_smooth_factor) > 0.001
    if target == "intersection":
        return abs(settings.intersection_edge_smooth_factor) > 0.001
    return (
        abs(settings.edge_smooth_factor) > 0.001
        or settings.use_vertex_color
        or settings.use_ao_influence
    )


def _ensure_vertex_group(owner: bpy.types.Object, name: str) -> bpy.types.VertexGroup | None:
    if owner.type != "MESH" or owner.data is None:
        return None
    vg = owner.vertex_groups.get(name)
    if vg is None:
        vg = owner.vertex_groups.new(name=name)
    if owner.data.vertices:
        vg.add(list(range(len(owner.data.vertices))), 1.0, "REPLACE")
    return vg


def _midpoint_factor_property(prop_name: str, description: str):
    return FloatProperty(
        name="中間頂点の線幅調整",
        description=description,
        default=0.0,
        min=-1.0,
        max=1.0,
        subtype="FACTOR",
        update=_make_weight_refresh_propagator(prop_name),
    )


def _midpoint_jitter_property(prop_name: str, description: str):
    return FloatProperty(
        name="中間頂点の乱れ (%)",
        description=description,
        default=0.0,
        min=0.0,
        max=50.0,
        precision=1,
        step=5,
        subtype="PERCENTAGE",
        update=_make_weight_refresh_propagator(prop_name),
    )


def _curve_point_property(prop_name: str, label: str, description: str, default: float):
    return FloatProperty(
        name=label,
        description=description,
        default=default,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_make_weight_refresh_propagator(prop_name),
    )


# ------------------------------------------------------------------
# 設定変更時のコールバック
# ------------------------------------------------------------------

def _on_color_changed(self, context):
    if _propagating:
        return
    from . import outline_setup
    owner = self.id_data
    if owner.type == "MESH":
        outline_setup.update_material_color(owner, tuple(self.outline_color))
    _propagate(self, context, "outline_color")


def _on_thickness_changed(self, context):
    if _propagating:
        return
    from . import outline_setup
    owner = self.id_data
    if owner.type == "MESH":
        outline_setup.update_modifier_thickness(owner, self.outline_thickness)
        if self.use_camera_compensation and PROP_BASE_THICKNESS in owner:
            owner[PROP_BASE_THICKNESS] = self.outline_thickness
        _refresh_print_widths(context)
    _propagate(self, context, "outline_thickness")


def _on_even_thickness_changed(self, context):
    if _propagating:
        return
    owner = self.id_data
    if owner.type == "MESH":
        mod = owner.modifiers.get(MODIFIER_NAME)
        if mod is not None:
            mod.use_even_offset = self.even_thickness
    _propagate(self, context, "even_thickness")


def _on_rim_changed(self, context):
    if _propagating:
        return
    owner = self.id_data
    if owner.type == "MESH":
        mod = owner.modifiers.get(MODIFIER_NAME)
        if mod is not None:
            mod.use_rim = self.use_rim
    _propagate(self, context, "use_rim")


def _on_transparent_protection_changed(self, context):
    if _propagating:
        return
    from . import outline_setup
    owner = self.id_data
    if owner.type == "MESH":
        outline_setup.update_transparent_protection(
            owner,
            self.hide_through_transparent,
            tuple(self.outline_color),
        )
    _propagate(self, context, "hide_through_transparent")


def _sync_inner_line_creation(owner: bpy.types.Object, settings, context) -> None:
    from . import camera_comp, inner_lines, outline_setup
    if owner.type != "MESH":
        return
    if not settings.inner_line_enabled:
        inner_lines.remove_inner_lines(owner)
        return
    if camera_comp.inner_line_creation_in_range(owner, getattr(context, "scene", None), settings):
        mat = outline_setup.get_outline_material(owner)
        inner_lines.apply_inner_lines(
            owner,
            angle=settings.inner_line_angle,
            thickness=settings.inner_line_thickness,
            material=mat,
            use_marked_edges=settings.use_marked_inner_edges,
        )
    else:
        inner_lines.remove_inner_lines(owner)


def _on_inner_line_enabled_changed(self, context):
    if _propagating:
        return
    owner = self.id_data
    _sync_inner_line_creation(owner, self, context)
    _propagate(self, context, "inner_line_enabled")


def _on_inner_angle_changed(self, context):
    if _propagating:
        return
    from . import inner_lines
    owner = self.id_data
    if owner.type == "MESH":
        inner_lines.update_parameters(owner, angle=self.inner_line_angle)
        _refresh_line_width_weights(self, context)
    _propagate(self, context, "inner_line_angle")


def _on_marked_inner_edges_changed(self, context):
    if _propagating:
        return
    owner = self.id_data
    _sync_inner_line_creation(owner, self, context)
    _propagate(self, context, "use_marked_inner_edges")


def _on_inner_thickness_changed(self, context):
    if _propagating:
        return
    from . import inner_lines
    owner = self.id_data
    if owner.type == "MESH":
        inner_lines.update_parameters(owner, thickness=self.inner_line_thickness)
        _refresh_print_widths(context)
    _propagate(self, context, "inner_line_thickness")


def _on_inner_creation_limit_changed(self, context):
    if _propagating:
        return
    owner = self.id_data
    _sync_inner_line_creation(owner, self, context)
    _propagate(self, context, "use_inner_line_creation_limit")


def _on_inner_creation_distance_changed(self, context):
    if _propagating:
        return
    owner = self.id_data
    _sync_inner_line_creation(owner, self, context)
    _propagate(self, context, "inner_line_creation_max_distance")


def _sync_intersection_creation(owner: bpy.types.Object, settings, context) -> None:
    from . import intersection_lines, outline_setup
    if owner.type != "MESH":
        return
    if not settings.intersection_enabled:
        intersection_lines.remove_intersection_lines(owner)
        return
    mat = outline_setup.get_outline_material(owner)
    intersection_lines.apply_intersection_lines(
        owner,
        thickness=settings.intersection_thickness,
        material=mat,
        method=settings.intersection_method,
        scene=getattr(context, "scene", None),
    )


def _refresh_intersection_scene(context) -> None:
    from . import intersection_lines
    scene = getattr(context, "scene", None)
    if scene is not None:
        intersection_lines.refresh_scene_intersections(scene)


def _on_intersection_enabled_changed(self, context):
    if _propagating:
        return
    owner = self.id_data
    _sync_intersection_creation(owner, self, context)
    _refresh_intersection_scene(context)
    _propagate(self, context, "intersection_enabled")


def _on_intersection_method_changed(self, context):
    if _propagating:
        return
    owner = self.id_data
    _sync_intersection_creation(owner, self, context)
    _refresh_intersection_scene(context)
    _propagate(self, context, "intersection_method")


def _on_intersection_thickness_changed(self, context):
    if _propagating:
        return
    from . import intersection_lines
    owner = self.id_data
    if owner.type == "MESH":
        intersection_lines.update_parameters(
            owner, thickness=self.intersection_thickness,
        )
        _refresh_print_widths(context)
    _propagate(self, context, "intersection_thickness")


def _on_intersection_creation_limit_changed(self, context):
    if _propagating:
        return
    owner = self.id_data
    _sync_intersection_creation(owner, self, context)
    _refresh_intersection_scene(context)
    _propagate(self, context, "use_intersection_creation_limit")


def _on_intersection_creation_distance_changed(self, context):
    if _propagating:
        return
    owner = self.id_data
    _sync_intersection_creation(owner, self, context)
    _refresh_intersection_scene(context)
    _propagate(self, context, "intersection_creation_max_distance")


def _refresh_line_width_weights(self, context, target: str | None = None) -> None:
    from . import vertex_analysis
    owner = self.id_data
    if owner.type == "MESH":
        if self.use_uniform_line_width:
            mod = owner.modifiers.get(MODIFIER_NAME)
            if mod is not None:
                vg = _ensure_vertex_group(owner, VG_LINE_WIDTH)
                if vg is not None:
                    mod.vertex_group = vg.name
                    mod.thickness_vertex_group = 0.0
            _refresh_print_widths(context)
            return

        targets = ("outline", "inner", "intersection") if target is None else (target,)
        for item in targets:
            if item == "outline":
                _refresh_outline_width_weights(owner, self, vertex_analysis)
            else:
                _refresh_generated_width_weights(owner, self, item, vertex_analysis)


def _refresh_outline_width_weights(owner, settings, vertex_analysis) -> None:
    mod = owner.modifiers.get(MODIFIER_NAME)
    if mod is None:
        return
    if _needs_line_width_weights(settings, "outline"):
        vg = _ensure_vertex_group(owner, VG_LINE_WIDTH)
        if vg is not None:
            mod.vertex_group = vg.name
            mod.thickness_vertex_group = 0.0
        vertex_analysis.compute_and_apply_weights(owner, settings, "outline")
    else:
        mod.vertex_group = ""
        vertex_analysis.clear_width_weights(owner, group_name=VG_LINE_WIDTH)


def _refresh_generated_width_weights(owner, settings, target, vertex_analysis) -> None:
    group_name = vertex_analysis.width_group_name(target)
    if _needs_line_width_weights(settings, target):
        vertex_analysis.compute_and_apply_weights(owner, settings, target)
    else:
        vertex_analysis.clear_width_weights(owner, group_name=group_name)


def _on_edge_smooth_changed(self, context):
    if _propagating:
        return
    _refresh_line_width_weights(self, context, "outline")
    _propagate(self, context, "edge_smooth_factor")


def _on_edge_midpoint_jitter_changed(self, context):
    if _propagating:
        return
    _refresh_line_width_weights(self, context, "outline")
    _propagate(self, context, "edge_midpoint_jitter_percent")


def _on_camera_comp_changed(self, context):
    if _propagating:
        return
    from . import camera_comp
    owner = self.id_data
    if self.use_camera_compensation:
        if owner.type == "MESH":
            camera_comp.store_unit_reference(owner, context.scene)
            camera_comp.refresh(context)
    else:
        if owner.type == "MESH":
            mod = owner.modifiers.get(MODIFIER_NAME)
            if mod is not None:
                mod.thickness = abs(self.outline_thickness)
            from . import inner_lines, intersection_lines
            inner_lines.update_parameters(owner, thickness=self.inner_line_thickness)
            intersection_lines.update_parameters(
                owner, thickness=self.intersection_thickness,
            )
    _propagate(self, context, "use_camera_compensation")


def _on_camera_influence_changed(self, context):
    if _propagating:
        return
    from . import camera_comp
    owner = self.id_data
    if owner.type == "MESH" and self.use_camera_compensation:
        camera_comp.refresh(context)
    _propagate(self, context, "camera_compensation_influence")


def _on_uniform_line_width_changed(self, context):
    if _propagating:
        return
    from . import camera_comp
    owner = self.id_data
    if owner.type == "MESH":
        mod = owner.modifiers.get(MODIFIER_NAME)
        if mod is not None:
            if not camera_comp.refresh_objects(context, [owner]):
                mod.thickness = abs(self.outline_thickness)
                if self.use_uniform_line_width:
                    vg = _ensure_vertex_group(owner, VG_LINE_WIDTH)
                    if vg is not None:
                        mod.vertex_group = vg.name
                        mod.thickness_vertex_group = 0.0
                else:
                    _refresh_line_width_weights(self, context)
                from . import inner_lines, intersection_lines
                inner_lines.update_parameters(owner, thickness=self.inner_line_thickness)
                intersection_lines.update_parameters(
                    owner, thickness=self.intersection_thickness,
                )
    _propagate(self, context, "use_uniform_line_width")


def _on_culling_changed(self, context):
    if _propagating:
        return
    _refresh_visibility_rules(self, context)
    _propagate(self, context, "use_camera_culling")


def _on_culling_margin_changed(self, context):
    if _propagating:
        return
    _refresh_visibility_rules(self, context)
    _propagate(self, context, "culling_margin")


def _on_inner_distance_changed(self, context):
    if _propagating:
        return
    _refresh_visibility_rules(self, context)
    _propagate(self, context, "use_inner_line_distance_limit")


def _on_outline_distance_changed(self, context):
    if _propagating:
        return
    _refresh_visibility_rules(self, context)
    _propagate(self, context, "use_outline_distance_limit")


def _on_intersection_distance_changed(self, context):
    if _propagating:
        return
    _refresh_visibility_rules(self, context)
    _propagate(self, context, "use_intersection_distance_limit")


def _make_visibility_value_propagator(prop_name):
    """表示距離の数値変更を即時反映してから選択中へ伝搬."""
    def _callback(self, context):
        if _propagating:
            return
        _refresh_visibility_rules(self, context)
        _propagate(self, context, prop_name)
    return _callback


def _refresh_visibility_rules(self, context):
    from . import camera_comp
    owner = self.id_data
    if owner.type == "MESH":
        if (
            self.use_camera_culling
            or self.use_outline_distance_limit
            or self.use_inner_line_distance_limit
            or self.use_intersection_distance_limit
        ):
            camera_comp.refresh(context)
        else:
            visible = not bool(owner.get(PROP_LINES_HIDDEN, False))
            for mod in iter_line_modifiers(owner):
                mod.show_viewport = visible
                mod.show_render = visible


# ------------------------------------------------------------------
# 線幅UI値
# ------------------------------------------------------------------

_BU_PER_MM = 0.001


def _get_outline_mm(self):
    return self.outline_thickness / _BU_PER_MM


def _set_outline_mm(self, value):
    self.outline_thickness = value * _BU_PER_MM


def _get_inner_mm(self):
    return self.inner_line_thickness / _BU_PER_MM


def _set_inner_mm(self, value):
    self.inner_line_thickness = value * _BU_PER_MM


def _get_intersection_mm(self):
    return self.intersection_thickness / _BU_PER_MM


def _set_intersection_mm(self, value):
    self.intersection_thickness = value * _BU_PER_MM


# ------------------------------------------------------------------
# プロパティグループ
# ------------------------------------------------------------------

class BMangaLineSettings(bpy.types.PropertyGroup):
    """オブジェクトごとの B-MANGA Line 設定."""

    outline_thickness: FloatProperty(
        name="線幅",
        description="印刷時のアウトラインの太さを保持する内部値",
        default=0.0003,
        min=0.0001,
        max=1.0,
        precision=4,
        step=0.1,
        update=_on_thickness_changed,
    )  # type: ignore[valid-type]

    outline_thickness_mm: FloatProperty(
        name="線幅 (mm)",
        description="印刷時のアウトラインの太さ (mm)",
        get=_get_outline_mm,
        set=_set_outline_mm,
        min=0.1,
        max=1000.0,
        precision=2,
        step=5,
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
        update=_make_weight_refresh_propagator("use_vertex_color"),
    )  # type: ignore[valid-type]

    even_thickness: BoolProperty(
        name="面の厚みを均一に",
        description="凸凹した面でも均一な線幅にする",
        default=True,
        update=_on_even_thickness_changed,
    )  # type: ignore[valid-type]

    use_uniform_line_width: BoolProperty(
        name="線幅の均一化",
        description="カメラビューと出力解像度に合わせて、指定したmm幅で描画する",
        default=False,
        update=_on_uniform_line_width_changed,
    )  # type: ignore[valid-type]

    use_rim: BoolProperty(
        name="リム面を生成",
        description="開いた辺にリム面を生成する（OFFで開いたメッシュのアーティファクト防止）",
        default=True,
        update=_on_rim_changed,
    )  # type: ignore[valid-type]

    hide_through_transparent: BoolProperty(
        name="透明面の塗りつぶしを防ぐ",
        description="透明・半透明の面越しに見える裏面側のラインを透明にする",
        default=False,
        update=_on_transparent_protection_changed,
    )  # type: ignore[valid-type]

    inner_line_enabled: BoolProperty(
        name="内部線を追加",
        description="折れ目（稜線・谷線）を検出して線を追加する",
        default=True,
        update=_on_inner_line_enabled_changed,
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

    use_marked_inner_edges: BoolProperty(
        name="指定済みの辺だけ線にする",
        description="シャープまたはクリースを指定した辺だけを内部線にする",
        default=False,
        update=_on_marked_inner_edges_changed,
    )  # type: ignore[valid-type]

    inner_line_thickness: FloatProperty(
        name="内部線の太さ",
        description="印刷時の内部線の太さを保持する内部値",
        default=0.0005,
        min=0.0001,
        max=1.0,
        precision=4,
        step=0.01,
        update=_on_inner_thickness_changed,
    )  # type: ignore[valid-type]

    inner_line_thickness_mm: FloatProperty(
        name="内部線の太さ (mm)",
        description="印刷時の内部線の太さ (mm)",
        get=_get_inner_mm,
        set=_set_inner_mm,
        min=0.1,
        max=1000.0,
        precision=2,
        step=5,
    )  # type: ignore[valid-type]

    use_inner_line_creation_limit: BoolProperty(
        name="作成範囲を制限",
        description="カメラから指定距離以内のオブジェクトにだけ内部線を作成する",
        default=True,
        update=_on_inner_creation_limit_changed,
    )  # type: ignore[valid-type]

    inner_line_creation_max_distance: FloatProperty(
        name="作成する距離 (m)",
        description="この距離以内のオブジェクトにだけ内部線を作成する",
        default=10.0,
        min=0.1,
        max=1000.0,
        subtype="DISTANCE",
        update=_on_inner_creation_distance_changed,
    )  # type: ignore[valid-type]

    # --- 交差線設定 ---

    intersection_method: EnumProperty(
        name="検出方式",
        description="交差線の検出に使用するアルゴリズム",
        items=[
            ("BOOLEAN", "Boolean（精密）",
             "Mesh Boolean で正確な交差曲線を生成。低密度メッシュでも滑らか"),
            ("SDF", "SDF（高速）",
             "SDF で交差を検出。トポロジーエラーが起きない。高密度メッシュ向き"),
        ],
        default="BOOLEAN",
        update=_on_intersection_method_changed,
    )  # type: ignore[valid-type]

    intersection_enabled: BoolProperty(
        name="交差線を追加",
        description="他のオブジェクトとの交差部分に線を描画する",
        default=True,
        update=_on_intersection_enabled_changed,
    )  # type: ignore[valid-type]

    intersection_thickness: FloatProperty(
        name="交差線の太さ",
        description="印刷時の交差線の太さを保持する内部値",
        default=0.0005,
        min=0.0001,
        max=1.0,
        precision=4,
        step=0.01,
        update=_on_intersection_thickness_changed,
    )  # type: ignore[valid-type]

    intersection_thickness_mm: FloatProperty(
        name="交差線の太さ (mm)",
        description="印刷時の交差線の太さ (mm)",
        get=_get_intersection_mm,
        set=_set_intersection_mm,
        min=0.1,
        max=1000.0,
        precision=2,
        step=5,
    )  # type: ignore[valid-type]

    use_intersection_creation_limit: BoolProperty(
        name="作成範囲を制限",
        description="カメラから指定距離以内のオブジェクトにだけ交差線を作成する",
        default=True,
        update=_on_intersection_creation_limit_changed,
    )  # type: ignore[valid-type]

    intersection_creation_max_distance: FloatProperty(
        name="作成する距離 (m)",
        description="この距離以内のオブジェクトにだけ交差線を作成する",
        default=10.0,
        min=0.1,
        max=1000.0,
        subtype="DISTANCE",
        update=_on_intersection_creation_distance_changed,
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
        update=_on_camera_influence_changed,
    )  # type: ignore[valid-type]

    # --- AO 線幅制御 ---

    use_ao_influence: BoolProperty(
        name="AOで線幅を制御",
        description="焼き付けたAOの暗い部分で線を太くする",
        default=False,
        update=_make_weight_refresh_propagator("use_ao_influence"),
    )  # type: ignore[valid-type]

    ao_influence_strength: FloatProperty(
        name="AO影響度",
        description="AO情報が線幅に影響する強さ",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_make_weight_refresh_propagator("ao_influence_strength"),
    )  # type: ignore[valid-type]

    # --- エッジ角度による線幅調整 ---

    edge_smooth_factor: FloatProperty(
        name="中間頂点の線幅調整",
        description="平坦な辺の線幅を調整（正: 太く / 負: 細く）",
        default=0.0,
        min=-1.0,
        max=1.0,
        subtype="FACTOR",
        update=_on_edge_smooth_changed,
    )  # type: ignore[valid-type]

    edge_midpoint_jitter_percent: FloatProperty(
        name="中間頂点の乱れ (%)",
        description="辺の中央から前後何%の範囲で中間頂点の位置をランダムにずらす",
        default=0.0,
        min=0.0,
        max=50.0,
        precision=1,
        step=5,
        subtype="PERCENTAGE",
        update=_on_edge_midpoint_jitter_changed,
    )  # type: ignore[valid-type]

    edge_width_curve_25: FloatProperty(
        name="25%",
        description="角から中間頂点まで25%進んだ地点の細り具合",
        default=0.25,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_make_weight_refresh_propagator("edge_width_curve_25"),
    )  # type: ignore[valid-type]

    edge_width_curve_50: FloatProperty(
        name="50%",
        description="角から中間頂点まで50%進んだ地点の細り具合",
        default=0.50,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_make_weight_refresh_propagator("edge_width_curve_50"),
    )  # type: ignore[valid-type]

    edge_width_curve_75: FloatProperty(
        name="75%",
        description="角から中間頂点まで75%進んだ地点の細り具合",
        default=0.75,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_make_weight_refresh_propagator("edge_width_curve_75"),
    )  # type: ignore[valid-type]

    inner_edge_smooth_factor: _midpoint_factor_property(
        "inner_edge_smooth_factor",
        "検出角度で見つけた角と角の間の内部線幅を調整（正: 太く / 負: 細く）",
    )  # type: ignore[valid-type]
    inner_edge_midpoint_jitter_percent: _midpoint_jitter_property(
        "inner_edge_midpoint_jitter_percent",
        "内部線の中間頂点位置を辺の中央から前後何%の範囲でずらす",
    )  # type: ignore[valid-type]
    inner_edge_width_curve_25: _curve_point_property(
        "inner_edge_width_curve_25", "25%",
        "内部線の角から中間頂点まで25%進んだ地点の細り具合", 0.25,
    )  # type: ignore[valid-type]
    inner_edge_width_curve_50: _curve_point_property(
        "inner_edge_width_curve_50", "50%",
        "内部線の角から中間頂点まで50%進んだ地点の細り具合", 0.50,
    )  # type: ignore[valid-type]
    inner_edge_width_curve_75: _curve_point_property(
        "inner_edge_width_curve_75", "75%",
        "内部線の角から中間頂点まで75%進んだ地点の細り具合", 0.75,
    )  # type: ignore[valid-type]

    intersection_edge_smooth_factor: _midpoint_factor_property(
        "intersection_edge_smooth_factor",
        "検出角度で見つけた角と角の間の交差線幅を調整（正: 太く / 負: 細く）",
    )  # type: ignore[valid-type]
    intersection_edge_midpoint_jitter_percent: _midpoint_jitter_property(
        "intersection_edge_midpoint_jitter_percent",
        "交差線の中間頂点位置を辺の中央から前後何%の範囲でずらす",
    )  # type: ignore[valid-type]
    intersection_edge_width_curve_25: _curve_point_property(
        "intersection_edge_width_curve_25", "25%",
        "交差線の角から中間頂点まで25%進んだ地点の細り具合", 0.25,
    )  # type: ignore[valid-type]
    intersection_edge_width_curve_50: _curve_point_property(
        "intersection_edge_width_curve_50", "50%",
        "交差線の角から中間頂点まで50%進んだ地点の細り具合", 0.50,
    )  # type: ignore[valid-type]
    intersection_edge_width_curve_75: _curve_point_property(
        "intersection_edge_width_curve_75", "75%",
        "交差線の角から中間頂点まで75%進んだ地点の細り具合", 0.75,
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
        update=_on_culling_margin_changed,
    )  # type: ignore[valid-type]

    # --- カメラ距離による線種別非表示 ---

    use_outline_distance_limit: BoolProperty(
        name="遠距離ラインを非表示",
        description="カメラから指定距離以上離れたオブジェクトのアウトラインを非表示にして軽くする",
        default=False,
        update=_on_outline_distance_changed,
    )  # type: ignore[valid-type]

    outline_max_distance: FloatProperty(
        name="非表示にする距離 (m)",
        description="この距離以上離れたオブジェクトのアウトラインを非表示にする",
        default=20.0,
        min=0.1,
        max=1000.0,
        subtype="DISTANCE",
        update=_make_visibility_value_propagator("outline_max_distance"),
    )  # type: ignore[valid-type]

    use_inner_line_distance_limit: BoolProperty(
        name="遠距離ラインを非表示",
        description="カメラから指定距離以上離れたオブジェクトの内部線を非表示にして軽くする",
        default=False,
        update=_on_inner_distance_changed,
    )  # type: ignore[valid-type]

    inner_line_max_distance: FloatProperty(
        name="非表示にする距離 (m)",
        description="この距離以上離れたオブジェクトの内部線を非表示にする",
        default=20.0,
        min=0.1,
        max=1000.0,
        subtype="DISTANCE",
        update=_make_visibility_value_propagator("inner_line_max_distance"),
    )  # type: ignore[valid-type]

    use_intersection_distance_limit: BoolProperty(
        name="遠距離ラインを非表示",
        description="カメラから指定距離以上離れたオブジェクトの交差線を非表示にして軽くする",
        default=False,
        update=_on_intersection_distance_changed,
    )  # type: ignore[valid-type]

    intersection_max_distance: FloatProperty(
        name="非表示にする距離 (m)",
        description="この距離以上離れたオブジェクトの交差線を非表示にする",
        default=20.0,
        min=0.1,
        max=1000.0,
        subtype="DISTANCE",
        update=_make_visibility_value_propagator("intersection_max_distance"),
    )  # type: ignore[valid-type]


# ------------------------------------------------------------------
# ヘルパー
# ------------------------------------------------------------------

_CLASSES = (BMangaLineSettings,)


def get_settings(context) -> BMangaLineSettings | None:
    """アクティブオブジェクトの設定を取得."""
    obj = getattr(context, "active_object", None)
    if obj is None:
        return None
    return getattr(obj, "bmanga_line_settings", None)


def has_outline(obj: bpy.types.Object) -> bool:
    return obj.type == "MESH" and obj.modifiers.get(MODIFIER_NAME) is not None


def iter_line_modifiers(obj: bpy.types.Object):
    if obj.type != "MESH":
        return
    for name in LINE_MODIFIER_NAMES:
        mod = obj.modifiers.get(name)
        if mod is not None:
            yield mod
    yield from iter_intersection_modifiers(obj)


def is_intersection_modifier_name(name: str) -> bool:
    return name == INTERSECTION_MODIFIER_NAME or name.startswith(INTERSECTION_MODIFIER_PREFIX)


def iter_intersection_modifiers(obj: bpy.types.Object):
    if obj.type != "MESH":
        return
    for mod in obj.modifiers:
        if is_intersection_modifier_name(mod.name):
            yield mod


def has_line(obj: bpy.types.Object) -> bool:
    return obj.type == "MESH" and any(iter_line_modifiers(obj))


def set_line_visibility(obj: bpy.types.Object, visible: bool) -> bool:
    mods = list(iter_line_modifiers(obj))
    if not mods:
        return False
    for mod in mods:
        mod.show_viewport = visible
        mod.show_render = visible
    obj[PROP_LINES_HIDDEN] = not visible
    return True


def _camera_poll(self, obj):
    return obj is None or getattr(obj, "type", None) == "CAMERA"


# ------------------------------------------------------------------
# 登録
# ------------------------------------------------------------------

def register() -> None:
    if hasattr(bpy.types.Scene, "bmanga_line_camera"):
        del bpy.types.Scene.bmanga_line_camera
    if hasattr(bpy.types.Object, "bmanga_line_settings"):
        del bpy.types.Object.bmanga_line_settings
    for cls in _CLASSES:
        registration.register_class(cls)
    bpy.types.Object.bmanga_line_settings = PointerProperty(type=BMangaLineSettings)
    bpy.types.Scene.bmanga_line_camera = PointerProperty(
        type=bpy.types.Object,
        name="別カメラ指定",
        description="通常はカメラビューのカメラを使います。別カメラで判定したい場合だけ指定します",
        poll=_camera_poll,
    )


def unregister() -> None:
    if hasattr(bpy.types.Scene, "bmanga_line_camera"):
        del bpy.types.Scene.bmanga_line_camera
    if hasattr(bpy.types.Object, "bmanga_line_settings"):
        del bpy.types.Object.bmanga_line_settings
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
