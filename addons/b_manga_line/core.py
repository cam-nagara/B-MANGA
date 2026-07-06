"""B-MANGA Line データモデル."""

from __future__ import annotations

import json
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
from .scale_utils import modifier_thickness_for_world_width
from .selection import selected_mesh_objects as _selected_mesh_objects

# ------------------------------------------------------------------
# 命名規則 — モディファイア・マテリアル・頂点グループ等の識別子
# ------------------------------------------------------------------
MODIFIER_NAME = "BML_Outline"
OUTLINE_WIDTH_ATTR_MODIFIER_NAME = "BML_OutlineWidthAttribute"
SHEET_OUTLINE_MODIFIER_NAME = "BML_SheetOutline"
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
AOV_OUTLINE_RAW_NAME = "BML_OutlineRaw"
AOV_OBJECT_MASK_NAME = "BML_ObjectMask"
AOV_INNER_LINES_NAME = "BML_InnerLines"
AOV_INTERSECTION_LINES_NAME = "BML_IntersectionLines"
AOV_COMPOSITE_NAME = "BML_LineComposite"
AOV_NAMES = (
    AOV_NAME,
    AOV_OUTLINE_RAW_NAME,
    AOV_OBJECT_MASK_NAME,
    AOV_INNER_LINES_NAME,
    AOV_INTERSECTION_LINES_NAME,
)
PROP_LINES_HIDDEN = "bml_lines_hidden"
PROP_LINE_ONLY = "bml_line_only"
PROP_LINE_ONLY_MATERIALS = "bml_line_only_materials"
PROP_LINE_ONLY_WORLD = "bml_line_only_world"
PROP_BASE_THICKNESS = "bml_base_thickness"
PROP_REF_DISTANCE = "bml_ref_distance"
PROP_REF_FOV_TAN = "bml_ref_fov_tan"
PROP_REF_MODE = "bml_ref_mode"
REF_MODE_VIEW = "VIEW"
REF_MODE_LOCKED = "LOCKED"
DEFAULT_LINE_WIDTH_REFERENCE_DISTANCE = 2.0
LINE_MODIFIER_NAMES = (
    SHEET_OUTLINE_MODIFIER_NAME,
    OUTLINE_WIDTH_ATTR_MODIFIER_NAME,
    MODIFIER_NAME,
    GN_MODIFIER_NAME,
)


# ------------------------------------------------------------------
# マルチセレクト伝搬
# ------------------------------------------------------------------

_propagating = False


def record_override_edits(obj) -> None:
    """Python 書き込みした設定をライブラリオーバーライドの操作として記録する.

    UI からの編集は自動記録されるが、スクリプト経由の setattr は
    operations_update() を呼ばないと保存時にライブラリ値へ戻ってしまう。
    """
    override = getattr(obj, "override_library", None)
    if override is None:
        return
    try:
        override.operations_update()
    except Exception:  # noqa: BLE001 - 記録に失敗しても編集自体は有効なまま続行する
        pass


def _propagate(self, context, prop_name):
    """変更されたプロパティを選択中の他オブジェクトにも反映."""
    global _propagating
    if _propagating:
        return False
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
                record_override_edits(obj)
                if has_line(obj):
                    changed.append(obj)
    finally:
        _propagating = False
    if changed:
        from . import batch_update
        batch_update.refresh_propagated_property(prop_name, changed, context)
        return True
    else:
        return False


def _refresh_full_line_settings(obj: bpy.types.Object, context) -> None:
    if not has_line(obj):
        return
    from . import presets
    presets.apply_line_settings(obj, context)


def _refresh_print_widths(context) -> None:
    from . import camera_comp
    camera_comp.refresh(context)


def _refresh_print_widths_for(
    context,
    objects,
    *,
    update_visibility: bool = False,
    width_targets=None,
) -> bool:
    from . import camera_comp
    return camera_comp.refresh_objects(
        context,
        objects,
        update_visibility=update_visibility,
        width_targets=width_targets,
    )


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


def _midpoint_angle_property(prop_name: str, description: str):
    return FloatProperty(
        name="検出角度",
        description=description,
        default=math.radians(100),
        min=math.radians(1),
        max=math.radians(180),
        precision=1,
        step=100,
        subtype="ANGLE",
        update=_make_weight_refresh_propagator(prop_name),
    )


def _sealed_midpoint_angle_property(description: str):
    return FloatProperty(
        name="検出角度",
        description=description,
        default=math.radians(100),
        min=math.radians(1),
        max=math.radians(180),
        precision=1,
        step=100,
        subtype="ANGLE",
    )


def inner_width_split_angle(settings=None, fallback: float | None = None) -> float:
    if fallback is not None:
        return max(0.0, float(fallback))
    return max(
        0.0,
        float(getattr(settings, "inner_line_angle", math.radians(60.0))),
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


def _line_color_property(description: str, update):
    return FloatVectorProperty(
        name="線の色",
        description=description,
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
        update=update,
    )


# ------------------------------------------------------------------
# 設定変更時のコールバック
# ------------------------------------------------------------------

def _ensure_outline_for_settings(owner: bpy.types.Object, settings, context) -> bool:
    if owner.type != "MESH":
        return False
    from . import outline_setup, vertex_analysis

    use_vg = (
        settings.use_uniform_line_width
        or vertex_analysis.has_width_controls(settings, "outline")
    )
    return outline_setup.apply_outline(
        owner,
        thickness=settings.outline_thickness,
        color=tuple(settings.outline_color),
        use_vertex_color=settings.use_vertex_color,
        even_thickness=settings.even_thickness,
        use_rim=settings.use_rim,
        offset=settings.outline_offset,
        use_vertex_group=use_vg,
        hide_through_transparent=settings.hide_through_transparent,
        scene=getattr(context, "scene", None),
    )


def set_outline_visibility_from_settings(obj: bpy.types.Object) -> bool:
    if obj.type != "MESH":
        return False
    settings = getattr(obj, "bmanga_line_settings", None)
    outline_on = settings is None or bool(getattr(settings, "outline_enabled", True))
    visible = outline_on and not bool(obj.get(PROP_LINES_HIDDEN, False))
    changed = False
    found = False
    # シートの境界チューブもアウトラインの一部として一緒に切り替える
    for name in (MODIFIER_NAME, SHEET_OUTLINE_MODIFIER_NAME):
        mod = obj.modifiers.get(name)
        if mod is None:
            continue
        found = True
        if mod.show_viewport != visible:
            mod.show_viewport = visible
            changed = True
        if mod.show_render != visible:
            mod.show_render = visible
            changed = True
    if not found:
        return False
    return changed


def _outline_visibility_rules_enabled(settings) -> bool:
    return bool(
        getattr(settings, "use_camera_culling", False)
        or getattr(settings, "use_outline_distance_limit", False)
    )


def _on_outline_enabled_changed(self, context):
    if _propagating:
        return
    owner = self.id_data
    created_owner = False
    visibility_changed = False
    if owner.type == "MESH" and has_line(owner):
        if self.outline_enabled:
            from . import batch_update; batch_update.defer_intersection_viewport([owner])
            if owner.modifiers.get(MODIFIER_NAME) is None:
                created_owner = _ensure_outline_for_settings(owner, self, context)
        if set_outline_visibility_from_settings(owner):
            visibility_changed = True
    _propagate(self, context, "outline_enabled")
    if self.outline_enabled and (
        created_owner
        or (visibility_changed and _outline_visibility_rules_enabled(self))
    ):
        _refresh_print_widths_for(
            context,
            [owner],
            update_visibility=True,
            width_targets=("outline",),
        )


def _on_color_changed(self, context):
    if _propagating:
        return
    from . import outline_setup
    owner = self.id_data
    if owner.type == "MESH":
        outline_setup.update_material_color(owner, tuple(self.outline_color))
    _propagate(self, context, "outline_color")


def _on_generated_color_changed(self, context, target: str, prop_name: str):
    if _propagating:
        return
    from . import inner_lines, intersection_lines, outline_setup
    owner = self.id_data
    if owner.type == "MESH":
        if target == "inner" and owner.modifiers.get(GN_MODIFIER_NAME) is not None:
            material = outline_setup.get_line_material(owner, target)
            inner_lines.update_parameters(owner, material=material)
        elif target == "intersection" and any(iter_intersection_modifiers(owner)):
            material = outline_setup.get_line_material(owner, target)
            intersection_lines.update_parameters(owner, material=material)
    _propagate(self, context, prop_name)


def _on_inner_color_changed(self, context):
    _on_generated_color_changed(self, context, "inner", "inner_line_color")


def _on_intersection_color_changed(self, context):
    _on_generated_color_changed(self, context, "intersection", "intersection_color")


def _on_thickness_changed(self, context):
    if _propagating:
        return
    from . import outline_setup
    owner = self.id_data
    if owner.type == "MESH":
        if self.use_camera_compensation and PROP_BASE_THICKNESS in owner:
            owner[PROP_BASE_THICKNESS] = self.outline_thickness
        if not _refresh_print_widths_for(context, [owner], width_targets=("outline",)):
            outline_setup.update_modifier_thickness(owner, self.outline_thickness)
    _propagate(self, context, "outline_thickness")


def _on_outline_offset_changed(self, context):
    if _propagating:
        return
    from . import outline_setup
    owner = self.id_data
    if owner.type == "MESH":
        outline_setup.update_modifier_offset(owner, self.outline_offset)
    _propagate(self, context, "outline_offset")


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
            from . import outline_setup
            outline_setup.update_modifier_rim(owner, self.use_rim)
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


def _on_sheet_exclusion_changed(self, context):
    # 2026-07-03 ユーザー確定: 板ポリ除外オプションは廃止（UI 非公開・挙動なし）。
    # 旧ファイル互換のためプロパティと伝搬だけ残す。
    if _propagating:
        return
    _propagate(self, context, "exclude_sheet_meshes")


def _on_auto_subdivision_changed(self, context):
    if _propagating:
        return
    owner = self.id_data
    if owner.type == "MESH" and has_line(owner):
        from . import modifier_stack, subdivision_lod

        if self.auto_subdivision_for_midpoint:
            subdivision_lod.ensure_auto_subdivision(
                owner,
                getattr(context, "scene", None),
            )
            modifier_stack.reorder_line_modifiers(owner)
        else:
            subdivision_lod.remove_auto_subdivision(owner)
    _propagate(self, context, "auto_subdivision_for_midpoint")


def _set_bool_setting_without_update(
    obj: bpy.types.Object,
    prop_name: str,
    value: bool,
) -> None:
    settings = getattr(obj, "bmanga_line_settings", None)
    if settings is None or not hasattr(settings, prop_name):
        return
    if bool(getattr(settings, prop_name)) == bool(value):
        return
    global _propagating
    old = _propagating
    _propagating = True
    try:
        setattr(settings, prop_name, bool(value))
    finally:
        _propagating = old


def sync_line_visibility_setting(obj: bpy.types.Object) -> None:
    """ライン表示状態をUIチェックボックスへ同期する."""
    _set_bool_setting_without_update(
        obj,
        "lines_visible",
        not bool(obj.get(PROP_LINES_HIDDEN, False)),
    )


def sync_line_only_setting(obj: bpy.types.Object) -> None:
    """ラインのみ表示状態をUIチェックボックスへ同期する."""
    _set_bool_setting_without_update(
        obj,
        "line_only_visible",
        bool(obj.get(PROP_LINE_ONLY, False)),
    )


def sync_line_display_settings(obj: bpy.types.Object) -> None:
    sync_line_visibility_setting(obj)
    sync_line_only_setting(obj)


def _scene_line_objects(context) -> list[bpy.types.Object]:
    scene = getattr(context, "scene", None)
    if scene is None:
        return []
    return [
        obj for obj in scene.objects
        if obj.type == "MESH" and obj.data is not None and has_line(obj)
    ]


def _world_background_node(world: bpy.types.World | None):
    if world is None or not getattr(world, "use_nodes", False) or world.node_tree is None:
        return None
    for node in world.node_tree.nodes:
        if node.type == "BACKGROUND":
            return node
    return None


def _line_only_world_state(scene: bpy.types.Scene) -> dict:
    world = scene.world
    background = _world_background_node(world)
    surface_link = None
    if world is not None and getattr(world, "use_nodes", False) and world.node_tree is not None:
        output = next((node for node in world.node_tree.nodes if node.type == "OUTPUT_WORLD"), None)
        if output is not None:
            for link in world.node_tree.links:
                if link.to_node == output and link.to_socket == output.inputs["Surface"]:
                    surface_link = {
                        "from_node": link.from_node.name,
                        "from_socket": link.from_socket.name,
                    }
                    break
    state = {
        "had_world": world is not None,
        "world_name": world.name if world is not None else "",
        "use_nodes": bool(getattr(world, "use_nodes", False)) if world else False,
        "color": tuple(getattr(world, "color", (0.05, 0.05, 0.05))) if world else None,
        "background_color": None,
        "background_strength": None,
        "surface_link": surface_link,
    }
    if background is not None:
        state["background_color"] = tuple(background.inputs["Color"].default_value)
        state["background_strength"] = float(background.inputs["Strength"].default_value)
    return state


def _ensure_line_only_world(context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    if PROP_LINE_ONLY_WORLD not in scene:
        scene[PROP_LINE_ONLY_WORLD] = json.dumps(
            _line_only_world_state(scene),
            ensure_ascii=False,
        )
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("BML_LineOnly_World")
        scene.world = world
    world.color = (1.0, 1.0, 1.0)
    world.use_nodes = True
    if world.node_tree is None:
        return
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    background = _world_background_node(world)
    if background is None:
        background = nodes.new("ShaderNodeBackground")
    output = next((node for node in nodes if node.type == "OUTPUT_WORLD"), None)
    if output is None:
        output = nodes.new("ShaderNodeOutputWorld")
    for link in list(links):
        if link.to_node == output and link.to_socket == output.inputs["Surface"]:
            links.remove(link)
    links.new(background.outputs["Background"], output.inputs["Surface"])
    background.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    background.inputs["Strength"].default_value = 1.0


def _restore_line_only_world(context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None or PROP_LINE_ONLY_WORLD not in scene:
        return
    raw = scene.get(PROP_LINE_ONLY_WORLD, "{}")
    try:
        state = json.loads(raw)
    except (TypeError, ValueError):
        state = {}
    if not state.get("had_world", False):
        try:
            scene.world = None
        except TypeError:
            pass
    else:
        world_name = str(state.get("world_name", ""))
        world = bpy.data.worlds.get(world_name) or scene.world
        if world is not None:
            scene.world = world
            if state.get("color") is not None:
                try:
                    world.color = tuple(state["color"][:3])
                except (TypeError, ValueError):
                    pass
            world.use_nodes = bool(state.get("use_nodes", False))
            if world.use_nodes:
                background = _world_background_node(world)
                if background is not None:
                    color = state.get("background_color")
                    strength = state.get("background_strength")
                    if color is not None:
                        background.inputs["Color"].default_value = tuple(color)
                    if strength is not None:
                        background.inputs["Strength"].default_value = float(strength)
                if world.node_tree is not None:
                    output = next(
                        (node for node in world.node_tree.nodes if node.type == "OUTPUT_WORLD"),
                        None,
                    )
                    if output is not None:
                        links = world.node_tree.links
                        for link in list(links):
                            if link.to_node == output and link.to_socket == output.inputs["Surface"]:
                                links.remove(link)
                        link_state = state.get("surface_link")
                        if isinstance(link_state, dict):
                            from_node = world.node_tree.nodes.get(str(link_state.get("from_node", "")))
                            from_socket = None
                            if from_node is not None:
                                from_socket = from_node.outputs.get(
                                    str(link_state.get("from_socket", ""))
                                )
                            if from_node is not None and from_socket is not None:
                                links.new(from_socket, output.inputs["Surface"])
    del scene[PROP_LINE_ONLY_WORLD]


def set_scene_line_only(context, enabled: bool) -> int:
    """シーン内のライン適用済みオブジェクトを一括でラインのみ表示にする."""
    from . import outline_setup, viewport_aov

    viewport_aov.disable_line_aov(context)
    line_objects = _scene_line_objects(context)
    if enabled:
        _ensure_line_only_world(context)
        for obj in line_objects:
            set_line_visibility(obj, True)
    changed = 0
    for obj in line_objects:
        before = bool(obj.get(PROP_LINE_ONLY, False))
        if outline_setup.set_line_only(obj, enabled):
            after = bool(obj.get(PROP_LINE_ONLY, False))
            if before != after or enabled:
                changed += 1
    if not enabled:
        _restore_line_only_world(context)
    return changed


def _on_lines_visible_changed(self, context):
    if _propagating:
        return
    owner = self.id_data
    visible = bool(self.lines_visible)
    if owner.type == "MESH" and has_line(owner):
        set_line_visibility(owner, visible)
        if visible:
            _refresh_print_widths(context)
    _propagate(self, context, "lines_visible")


def _on_line_only_visible_changed(self, context):
    if _propagating:
        return
    set_scene_line_only(context, bool(self.line_only_visible))


def _on_match_subsurf_viewport_to_render_changed(self, context):
    if _propagating:
        return
    from . import camera_comp, intersection_lines, subdivision_lod

    owner = self.id_data
    targets = _selected_mesh_objects(context, owner)
    if owner.type == "MESH" and owner not in targets:
        targets.append(owner)
    changed_targets = []
    if self.match_subsurf_viewport_to_render:
        for obj in targets:
            if subdivision_lod.sync_viewport_levels_to_render(obj):
                changed_targets.append(obj)
    else:
        for obj in targets:
            if subdivision_lod.reset_viewport_levels_to_zero(obj):
                changed_targets.append(obj)
    if changed_targets:
        refreshed = _refresh_intersection_scene(context)
        if not refreshed:
            for obj in changed_targets:
                intersection_lines.update_parameters(obj)
            refreshed = changed_targets
        camera_comp.refresh_objects(
            context,
            refreshed,
            width_targets=("intersection",),
        )
    _propagate(self, context, "match_subsurf_viewport_to_render")


def _sync_inner_line_creation(
    owner: bpy.types.Object,
    settings,
    context,
    *,
    create_missing: bool = True,
) -> bool:
    from . import camera_comp, inner_lines, outline_setup, plane_filter
    if owner.type != "MESH":
        return False
    if not settings.inner_line_enabled:
        inner_lines.disable_inner_lines(owner)
        return False
    if plane_filter.should_skip_inner_lines(owner, settings):
        inner_lines.remove_inner_lines(owner)
        return False
    if camera_comp.inner_line_creation_in_range(owner, getattr(context, "scene", None), settings):
        if not create_missing and owner.modifiers.get(GN_MODIFIER_NAME) is None:
            return False
        if not create_missing:
            inner_lines.enable_inner_lines(owner)
            return False
        mat = outline_setup.get_line_material(owner, "inner")
        return inner_lines.apply_inner_lines(
            owner,
            angle=settings.inner_line_angle,
            thickness=modifier_thickness_for_world_width(
                owner,
                settings.inner_line_thickness,
            ),
            offset=settings.inner_line_offset,
            material=mat,
            use_marked_edges=settings.use_marked_inner_edges,
            midpoint_factor=(
                settings.inner_edge_smooth_factor
                if settings.auto_subdivision_for_midpoint
                else 0.0
            ),
            midpoint_angle=inner_width_split_angle(settings),
            midpoint_jitter_percent=settings.inner_edge_midpoint_jitter_percent,
            width_curve_25=settings.inner_edge_width_curve_25,
            width_curve_50=settings.inner_edge_width_curve_50,
            width_curve_75=settings.inner_edge_width_curve_75,
        )
    inner_lines.disable_inner_lines(owner)
    return False


def _inner_midpoint_kwargs(settings) -> dict[str, float]:
    factor = (
        float(settings.inner_edge_smooth_factor)
        if bool(getattr(settings, "auto_subdivision_for_midpoint", False))
        else 0.0
    )
    return {
        "midpoint_factor": factor,
        "midpoint_angle": inner_width_split_angle(settings),
        "midpoint_jitter_percent": float(settings.inner_edge_midpoint_jitter_percent),
        "width_curve_25": float(settings.inner_edge_width_curve_25),
        "width_curve_50": float(settings.inner_edge_width_curve_50),
        "width_curve_75": float(settings.inner_edge_width_curve_75),
    }


def _on_inner_line_enabled_changed(self, context):
    if _propagating:
        return
    owner = self.id_data
    refreshed_owner = _sync_inner_line_creation(owner, self, context)
    _propagate(self, context, "inner_line_enabled")
    if refreshed_owner:
        _refresh_print_widths_for(
            context,
            [owner],
            update_visibility=True,
            width_targets=("inner",),
        )


def _on_inner_angle_changed(self, context):
    if _propagating:
        return
    from . import inner_lines
    owner = self.id_data
    if owner.type == "MESH" and owner.modifiers.get(GN_MODIFIER_NAME) is not None:
        inner_lines.update_parameters(
            owner,
            angle=self.inner_line_angle,
            **_inner_midpoint_kwargs(self),
        )
        _refresh_line_width_weights(self, context, "inner")
    _propagate(self, context, "inner_line_angle")


def _on_marked_inner_edges_changed(self, context):
    if _propagating:
        return
    from . import camera_comp, inner_lines, plane_filter
    owner = self.id_data
    refreshed_owner = False
    if owner.type == "MESH" and has_line(owner):
        if plane_filter.should_skip_inner_lines(owner, self):
            inner_lines.remove_inner_lines(owner)
        elif not camera_comp.inner_line_creation_in_range(
            owner,
            getattr(context, "scene", None),
            self,
        ):
            inner_lines.disable_inner_lines(owner)
        elif owner.modifiers.get(GN_MODIFIER_NAME) is not None:
            inner_lines.update_parameters(
                owner,
                use_marked_edges=self.use_marked_inner_edges,
                **_inner_midpoint_kwargs(self),
            )
        elif not self.inner_line_enabled:
            refreshed_owner = False
        else:
            refreshed_owner = _sync_inner_line_creation(owner, self, context)
    _propagate(self, context, "use_marked_inner_edges")
    if refreshed_owner:
        _refresh_print_widths_for(
            context,
            [owner],
            update_visibility=True,
            width_targets=("inner",),
        )


def _on_inner_thickness_changed(self, context):
    if _propagating:
        return
    from . import inner_lines
    owner = self.id_data
    if owner.type == "MESH":
        if not _refresh_print_widths_for(context, [owner], width_targets=("inner",)):
            inner_lines.update_parameters(
                owner,
                thickness=modifier_thickness_for_world_width(
                    owner,
                    self.inner_line_thickness,
                ),
                **_inner_midpoint_kwargs(self),
            )
    _propagate(self, context, "inner_line_thickness")


def _on_inner_offset_changed(self, context):
    if _propagating:
        return
    from . import inner_lines
    owner = self.id_data
    if owner.type == "MESH":
        inner_lines.update_parameters(
            owner,
            offset=self.inner_line_offset,
            **_inner_midpoint_kwargs(self),
        )
    _propagate(self, context, "inner_line_offset")


def _sync_inner_creation_range(owner: bpy.types.Object, settings, context) -> bool:
    """作成範囲の設定変更を内部線の実状態へ反映する。

    範囲判定と実状態が食い違う時だけ作成/有効化/無効化を行い、
    スライダードラッグ中の無駄な再構築を避ける。
    """
    from . import camera_comp, inner_lines, plane_filter
    if owner.type != "MESH":
        return False
    if not settings.inner_line_enabled:
        return False
    if plane_filter.should_skip_inner_lines(owner, settings):
        return False
    in_range = camera_comp.inner_line_creation_in_range(
        owner,
        getattr(context, "scene", None),
        settings,
    )
    mod = owner.modifiers.get(GN_MODIFIER_NAME)
    if not in_range:
        if mod is not None:
            inner_lines.disable_inner_lines(owner)
        return False
    if mod is None:
        return _sync_inner_line_creation(owner, settings, context)
    return inner_lines.enable_inner_lines(owner)


def _on_inner_creation_range_changed(self, context, prop_name: str) -> None:
    owner = self.id_data
    refreshed_owner = _sync_inner_creation_range(owner, self, context)
    _propagate(self, context, prop_name)
    if refreshed_owner:
        _refresh_print_widths_for(
            context,
            [owner],
            update_visibility=True,
            width_targets=("inner",),
        )


def _on_inner_creation_limit_changed(self, context):
    if _propagating:
        return
    _on_inner_creation_range_changed(self, context, "use_inner_line_creation_limit")


def _on_inner_creation_distance_changed(self, context):
    if _propagating:
        return
    _on_inner_creation_range_changed(self, context, "inner_line_creation_max_distance")


def _sync_intersection_creation(owner: bpy.types.Object, settings, context) -> None:
    from . import intersection_lines, outline_setup, plane_filter
    if owner.type != "MESH":
        return
    if not settings.intersection_enabled:
        intersection_lines.remove_intersection_lines(owner)
        return
    if plane_filter.should_exclude_generated_lines(owner, settings):
        intersection_lines.remove_intersection_lines(owner)
        intersection_lines.prune_excluded_intersections(getattr(context, "scene", None))
        return
    mat = outline_setup.get_line_material(owner, "intersection")
    intersection_lines.apply_intersection_lines(
        owner,
        thickness=modifier_thickness_for_world_width(
            owner,
            settings.intersection_thickness,
        ),
        offset=settings.intersection_line_offset,
        material=mat,
        method=settings.intersection_method,
        scene=getattr(context, "scene", None),
    )


def _refresh_intersection_scene(context) -> list[bpy.types.Object]:
    from . import intersection_lines
    scene = getattr(context, "scene", None)
    if scene is not None:
        return intersection_lines.refresh_scene_intersections(scene)
    return []


def _on_intersection_enabled_changed(self, context):
    if _propagating:
        return
    owner = self.id_data
    disabling = not bool(self.intersection_enabled)
    if disabling:
        _sync_intersection_creation(owner, self, context)
        propagated = _propagate(self, context, "intersection_enabled")
        if not propagated:
            from . import intersection_lines
            if intersection_lines.scene_has_enabled_intersections(
                getattr(context, "scene", None),
            ):
                refreshed = _refresh_intersection_scene(context)
                if refreshed:
                    _refresh_print_widths_for(
                        context,
                        refreshed,
                        update_visibility=True,
                        width_targets=("intersection",),
                    )
        return
    propagated = _propagate(self, context, "intersection_enabled")
    if propagated:
        if any(iter_intersection_modifiers(owner)):
            _refresh_print_widths_for(
                context,
                [owner],
                update_visibility=True,
                width_targets=("intersection",),
            )
    else:
        refreshed = _refresh_intersection_scene(context)
        if any(iter_intersection_modifiers(owner)) and owner not in refreshed:
            refreshed.append(owner)
        if refreshed:
            _refresh_print_widths_for(
                context,
                refreshed,
                update_visibility=True,
                width_targets=("intersection",),
            )


def _on_intersection_method_changed(self, context):
    if _propagating:
        return
    owner = self.id_data
    if not _propagate(self, context, "intersection_method"):
        refreshed = _refresh_intersection_scene(context)
    else:
        refreshed = []
    if any(iter_intersection_modifiers(owner)) and owner not in refreshed:
        refreshed.append(owner)
    if refreshed:
        _refresh_print_widths_for(
            context,
            refreshed,
            update_visibility=True,
            width_targets=("intersection",),
        )


def _on_intersection_thickness_changed(self, context):
    if _propagating:
        return
    from . import intersection_lines
    owner = self.id_data
    if owner.type == "MESH":
        if not _refresh_print_widths_for(
            context,
            [owner],
            width_targets=("intersection",),
        ):
            intersection_lines.update_parameters(
                owner,
                thickness=modifier_thickness_for_world_width(
                    owner,
                    self.intersection_thickness,
                ),
            )
    _propagate(self, context, "intersection_thickness")


def _on_intersection_offset_changed(self, context):
    if _propagating:
        return
    from . import intersection_lines
    owner = self.id_data
    if owner.type == "MESH":
        intersection_lines.update_parameters(
            owner,
            offset=self.intersection_line_offset,
        )
    _propagate(self, context, "intersection_line_offset")


def _on_intersection_creation_range_changed(self, context, prop_name: str) -> None:
    owner = self.id_data
    propagated = _propagate(self, context, prop_name)
    if propagated:
        return
    from . import intersection_lines
    scene = getattr(context, "scene", None)
    if not intersection_lines.scene_has_enabled_intersections(scene):
        return
    refreshed = _refresh_intersection_scene(context)
    if any(iter_intersection_modifiers(owner)) and owner not in refreshed:
        refreshed.append(owner)
    if refreshed:
        _refresh_print_widths_for(
            context,
            refreshed,
            update_visibility=True,
            width_targets=("intersection",),
        )


def _on_intersection_creation_limit_changed(self, context):
    if _propagating:
        return
    _on_intersection_creation_range_changed(
        self, context, "use_intersection_creation_limit",
    )


def _on_intersection_creation_distance_changed(self, context):
    if _propagating:
        return
    _on_intersection_creation_range_changed(
        self, context, "intersection_creation_max_distance",
    )


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
            width_targets = None if target is None else (target,)
            _refresh_print_widths_for(
                context,
                [owner],
                width_targets=width_targets,
            )
            return

        targets = ("outline", "inner", "intersection") if target is None else (target,)
        for item in targets:
            if item == "outline":
                _refresh_outline_width_weights(owner, self, vertex_analysis)
            else:
                _refresh_generated_width_weights(owner, self, item, vertex_analysis)


def _refresh_outline_width_weights(owner, settings, vertex_analysis) -> None:
    from . import outline_setup, outline_width_attribute

    mod = owner.modifiers.get(MODIFIER_NAME)
    if mod is None:
        if owner.modifiers.get(SHEET_OUTLINE_MODIFIER_NAME) is not None:
            outline_setup.sync_sheet_outline_width(owner)
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
    outline_width_attribute.ensure_outline_width_attribute(owner, settings)
    outline_setup.sync_sheet_outline_width(owner)


def _refresh_generated_width_weights(owner, settings, target, vertex_analysis) -> None:
    if target == "inner" and owner.modifiers.get(GN_MODIFIER_NAME) is None:
        return
    if target == "intersection" and not any(iter_intersection_modifiers(owner)):
        return
    group_name = vertex_analysis.width_group_name(target)
    if _needs_line_width_weights(settings, target):
        vertex_analysis.compute_and_apply_weights(owner, settings, target)
    else:
        vertex_analysis.clear_width_weights(owner, group_name=group_name)
    if target == "intersection":
        from . import intersection_lines
        intersection_lines.update_parameters(owner)
    elif target == "inner":
        from . import inner_lines
        inner_lines.update_parameters(owner, **_inner_midpoint_kwargs(settings))


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
    if self.use_uniform_line_width:
        _propagate(self, context, "use_camera_compensation")
        return
    if owner.type == "MESH":
        if self.use_camera_compensation:
            camera_comp.store_unit_reference(owner, context.scene)
        if (
            not camera_comp.refresh_objects(context, [owner])
            and not self.use_camera_compensation
        ):
            mod = owner.modifiers.get(MODIFIER_NAME)
            if mod is not None:
                mod.thickness = modifier_thickness_for_world_width(
                    owner,
                    self.outline_thickness,
                )
            from . import inner_lines, intersection_lines
            inner_lines.update_parameters(
                owner,
                thickness=modifier_thickness_for_world_width(
                    owner,
                    self.inner_line_thickness,
                ),
                **_inner_midpoint_kwargs(self),
            )
            intersection_lines.update_parameters(
                owner,
                thickness=modifier_thickness_for_world_width(
                    owner,
                    self.intersection_thickness,
                ),
            )
    _propagate(self, context, "use_camera_compensation")


def _on_camera_influence_changed(self, context):
    if _propagating:
        return
    from . import camera_comp
    owner = self.id_data
    if (
        owner.type == "MESH"
        and self.use_camera_compensation
        and not self.use_uniform_line_width
    ):
        camera_comp.refresh_objects(context, [owner])
    _propagate(self, context, "camera_compensation_influence")


def _on_line_width_reference_distance_changed(self, context):
    if _propagating:
        return
    from . import camera_comp
    owner = self.id_data
    if owner.type == "MESH" and not self.use_uniform_line_width:
        camera_comp.store_unit_reference(owner, context.scene)
        camera_comp.refresh_objects(context, [owner])
    _propagate(self, context, "line_width_reference_distance")


def _on_uniform_line_width_changed(self, context):
    if _propagating:
        return
    from . import camera_comp
    owner = self.id_data
    if owner.type == "MESH":
        mod = owner.modifiers.get(MODIFIER_NAME)
        if mod is not None:
            if not camera_comp.refresh_objects(context, [owner]):
                mod.thickness = modifier_thickness_for_world_width(
                    owner,
                    self.outline_thickness,
                )
                if self.use_uniform_line_width:
                    vg = _ensure_vertex_group(owner, VG_LINE_WIDTH)
                    if vg is not None:
                        mod.vertex_group = vg.name
                        mod.thickness_vertex_group = 0.0
                else:
                    _refresh_line_width_weights(self, context)
                from . import inner_lines, intersection_lines
                inner_lines.update_parameters(
                    owner,
                    thickness=modifier_thickness_for_world_width(
                        owner,
                        self.inner_line_thickness,
                    ),
                    **_inner_midpoint_kwargs(self),
                )
                intersection_lines.update_parameters(
                    owner,
                    thickness=modifier_thickness_for_world_width(
                        owner,
                        self.intersection_thickness,
                    ),
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
            camera_comp.refresh_visibility_objects(context, [owner])
        else:
            visible = not bool(owner.get(PROP_LINES_HIDDEN, False))
            set_line_visibility(owner, visible)


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

    outline_enabled: BoolProperty(
        name="アウトラインを追加",
        description="外側のアウトラインを描画する",
        default=True,
        update=_on_outline_enabled_changed,
    )  # type: ignore[valid-type]

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

    outline_offset: FloatProperty(
        name="オフセット",
        description="アウトラインを元の面からどちら側に出すかを調整する",
        default=0.0,
        min=-1.0,
        max=1.0,
        precision=3,
        step=10,
        update=_on_outline_offset_changed,
    )  # type: ignore[valid-type]

    outline_color: _line_color_property(
        "アウトラインの色",
        _on_color_changed,
    )  # type: ignore[valid-type]

    use_vertex_color: BoolProperty(
        name="頂点カラーで線幅を制御",
        description="頂点カラーの明度で線幅の強弱をつける",
        default=False,
        update=_make_weight_refresh_propagator("use_vertex_color"),
    )  # type: ignore[valid-type]

    auto_subdivision_for_midpoint: BoolProperty(
        name="中間頂点用サブディビジョンを自動設定",
        description=(
            "ライン適用時に鋭い辺へクリースを付け、"
            "カメラ距離に応じたサブディビジョンサーフェスを設定する"
        ),
        default=False,
        update=_on_auto_subdivision_changed,
    )  # type: ignore[valid-type]

    lines_visible: BoolProperty(
        name="ラインを表示",
        description="選択中のオブジェクトのライン表示を切り替える",
        default=True,
        update=_on_lines_visible_changed,
    )  # type: ignore[valid-type]

    line_only_visible: BoolProperty(
        name="ラインのみを表示",
        description="面を白く置き換え、ラインだけが見える表示へ切り替える",
        default=False,
        update=_on_line_only_visible_changed,
    )  # type: ignore[valid-type]

    match_subsurf_viewport_to_render: BoolProperty(
        name="ビューポートのレベル数をレンダーに合わせる",
        description=(
            "選択中のメッシュのサブディビジョンサーフェスで、"
            "ビューポートのレベル数をレンダーと同じ数値にする"
        ),
        default=False,
        update=_on_match_subsurf_viewport_to_render_changed,
    )  # type: ignore[valid-type]

    even_thickness: BoolProperty(
        name="面の厚みを均一に",
        description="凸凹した面でも均一な線幅にする",
        default=False,
        update=_on_even_thickness_changed,
    )  # type: ignore[valid-type]

    use_uniform_line_width: BoolProperty(
        name="線幅の均一化（頂点単位）",
        description="同じメッシュ内の奥行き差も見て、頂点ごとに画面上の線幅を揃える",
        default=False,
        update=_on_uniform_line_width_changed,
    )  # type: ignore[valid-type]

    use_rim: BoolProperty(
        name="リム面を生成",
        description="開いた辺にリム面を生成する（OFFで開いたメッシュのアーティファクト防止）",
        default=False,
        update=_on_rim_changed,
    )  # type: ignore[valid-type]

    hide_through_transparent: BoolProperty(
        name="透明面の塗りつぶしを防ぐ",
        description="透明・半透明の面越しに見える裏面側のラインを透明にする",
        default=False,
        update=_on_transparent_protection_changed,
    )  # type: ignore[valid-type]

    exclude_sheet_meshes: BoolProperty(
        name="板ポリは内部線・交差線を作らない",
        description="薄い板状のメッシュではアウトラインだけを作り、内部線と交差線を作らない",
        # 2026-07-03 ユーザー確定: 板ポリ除外だけは「初期値全オフ」の対象外でオン
        default=True,
        update=_on_sheet_exclusion_changed,
    )  # type: ignore[valid-type]

    inner_line_enabled: BoolProperty(
        name="内部線を追加",
        description="折れ目（稜線・谷線）を検出して線を追加する",
        default=False,
        update=_on_inner_line_enabled_changed,
    )  # type: ignore[valid-type]

    inner_line_angle: FloatProperty(
        name="検出角度",
        description="この角度以上の折れ目に線を描画する",
        default=math.radians(60),
        min=math.radians(1),
        max=math.radians(180),
        precision=1,
        step=100,
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
        default=0.0003,
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

    inner_line_offset: FloatProperty(
        name="オフセット",
        description="内部線を元の面からどれだけ浮かせるかを線幅基準で調整する",
        default=0.0,
        min=-1.0,
        max=1.0,
        precision=3,
        step=10,
        update=_on_inner_offset_changed,
    )  # type: ignore[valid-type]

    inner_line_color: _line_color_property(
        "内部線の色",
        _on_inner_color_changed,
    )  # type: ignore[valid-type]

    use_inner_line_creation_limit: BoolProperty(
        name="作成範囲を制限",
        description="カメラに写り、指定距離以内にあるオブジェクトにだけ内部線を作成する",
        default=True,
        update=_on_inner_creation_limit_changed,
    )  # type: ignore[valid-type]

    inner_line_creation_max_distance: FloatProperty(
        name="作成する距離 (m)",
        description="カメラに写るオブジェクトのうち、この距離以内のものだけに内部線を作成する",
        default=10.0,
        min=0.1,
        max=1000.0,
        subtype="DISTANCE",
        update=_on_inner_creation_distance_changed,
    )  # type: ignore[valid-type]

    # --- 交差線設定 ---

    intersection_method: EnumProperty(
        name="作成方式",
        description="交差線の作り方",
        items=[
            ("SHELL", "ライン素材（高速）",
             "ライン適用済みの他メッシュをまとめて参照し、個別の交差線を作らず交差部分を表示"),
            ("BOOLEAN", "Boolean（精密）",
             "Mesh Boolean で正確な交差曲線を生成。低密度メッシュでも滑らか"),
            ("SDF", "SDF（高速）",
             "SDF で交差を検出。トポロジーエラーが起きない。高密度メッシュ向き"),
        ],
        default="SHELL",
        update=_on_intersection_method_changed,
    )  # type: ignore[valid-type]

    intersection_enabled: BoolProperty(
        name="交差線を追加",
        description="他のオブジェクトとの交差部分に線を描画する",
        default=False,
        update=_on_intersection_enabled_changed,
    )  # type: ignore[valid-type]

    intersection_thickness: FloatProperty(
        name="交差線の太さ",
        description="印刷時の交差線の太さを保持する内部値",
        default=0.0003,
        min=0.0001,
        max=1.0,
        precision=4,
        step=0.01,
        update=_on_intersection_thickness_changed,
    )  # type: ignore[valid-type]

    intersection_thickness_mm: FloatProperty(
        name="交差線の太さ (mm)",
        description=(
            "印刷時の交差線の太さ (mm)。交差している面のライン上に"
            "この太さで描画します"
        ),
        get=_get_intersection_mm,
        set=_set_intersection_mm,
        min=0.1,
        max=1000.0,
        precision=2,
        step=5,
    )  # type: ignore[valid-type]

    intersection_line_offset: FloatProperty(
        name="オフセット",
        description="交差線の出方を線幅基準で調整する",
        default=0.0,
        min=-1.0,
        max=1.0,
        precision=3,
        step=10,
        update=_on_intersection_offset_changed,
    )  # type: ignore[valid-type]

    intersection_color: _line_color_property(
        "交差線の色",
        _on_intersection_color_changed,
    )  # type: ignore[valid-type]

    use_intersection_creation_limit: BoolProperty(
        name="作成範囲を制限",
        description="カメラに写り、指定距離以内にあるオブジェクトにだけ交差線を作成する",
        default=True,
        update=_on_intersection_creation_limit_changed,
    )  # type: ignore[valid-type]

    intersection_creation_max_distance: FloatProperty(
        name="作成する距離 (m)",
        description="カメラに写るオブジェクトのうち、この距離以内のものだけに交差線を作成する",
        default=10.0,
        min=0.1,
        max=1000.0,
        subtype="DISTANCE",
        update=_on_intersection_creation_distance_changed,
    )  # type: ignore[valid-type]

    # --- カメラ距離補正 ---

    use_camera_compensation: BoolProperty(
        name="線幅の均一化（オブジェクト単位）",
        description="オブジェクトごとのカメラ距離に合わせて画面上の線幅を揃える",
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

    line_width_reference_distance: FloatProperty(
        name="線幅基準距離 (m)",
        description="線幅欄に入力した太さとして扱う、カメラからのワールド内距離",
        default=DEFAULT_LINE_WIDTH_REFERENCE_DISTANCE,
        min=0.001,
        max=1000.0,
        precision=2,
        subtype="DISTANCE",
        update=_on_line_width_reference_distance_changed,
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

    edge_midpoint_angle: _midpoint_angle_property(
        "edge_midpoint_angle",
        "アウトラインを分割する角度。これ未満の角で分割し、以上の角は接続します",
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
        "分割された内部線ごとの中心付近の線幅を調整（正: 太く / 負: 細く）",
    )  # type: ignore[valid-type]
    inner_edge_midpoint_jitter_percent: _midpoint_jitter_property(
        "inner_edge_midpoint_jitter_percent",
        "内部線の中間頂点位置を辺の中央から前後何%の範囲でずらす",
    )  # type: ignore[valid-type]
    inner_edge_midpoint_angle: _sealed_midpoint_angle_property(
        "旧設定との互換用。現在は内部線の検出角度を線幅変化の区間分割にも使います",
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
        "分割された交差線ごとの中心付近の線幅を調整（正: 太く / 負: 細く）",
    )  # type: ignore[valid-type]
    intersection_edge_midpoint_jitter_percent: _midpoint_jitter_property(
        "intersection_edge_midpoint_jitter_percent",
        "交差線の中間頂点位置を辺の中央から前後何%の範囲でずらす",
    )  # type: ignore[valid-type]
    intersection_edge_midpoint_angle: _midpoint_angle_property(
        "intersection_edge_midpoint_angle",
        "交差線を分割する角度。これ未満の角で分割し、以上の角は接続します",
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
        precision=1,
        step=100,
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


from .line_visibility import (  # noqa: E402
    has_line,
    has_outline,
    is_intersection_modifier_name,
    iter_intersection_modifiers,
    iter_line_modifiers,
    set_line_visibility,
)


def _camera_poll(self, obj):
    return obj is None or getattr(obj, "type", None) == "CAMERA"


# ------------------------------------------------------------------
# 登録
# ------------------------------------------------------------------

def _make_settings_overridable() -> None:
    # リンク先ファイルでのライン設定上書きが保存されるよう、全プロパティへ
    # LIBRARY_OVERRIDABLE を一括付与する（未付与だと再読込でライブラリ値へ戻る）。
    # 本モジュールは PEP 563 (from __future__ import annotations) のため
    # __annotations__ の値が文字列のまま。評価して実体に戻してから付与する。
    annotations = getattr(BMangaLineSettings, "__annotations__", {})
    for name, prop in list(annotations.items()):
        if isinstance(prop, str):
            try:
                prop = eval(prop, globals())  # noqa: S307 - 自モジュール定義の再評価のみ
            except Exception:  # noqa: BLE001 - 未知の注釈はBlender既定処理に任せる
                continue
            annotations[name] = prop
        keywords = getattr(prop, "keywords", None)
        if isinstance(keywords, dict):
            keywords.setdefault("override", {"LIBRARY_OVERRIDABLE"})


def register() -> None:
    if hasattr(bpy.types.Scene, "bmanga_line_camera"):
        del bpy.types.Scene.bmanga_line_camera
    if hasattr(bpy.types.Object, "bmanga_line_settings"):
        del bpy.types.Object.bmanga_line_settings
    _make_settings_overridable()
    for cls in _CLASSES:
        registration.register_class(cls)
    bpy.types.Object.bmanga_line_settings = PointerProperty(
        type=BMangaLineSettings,
        override={"LIBRARY_OVERRIDABLE"},
    )
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
