"""Fast updates for already-created B-MANGA Liner outlines."""

from __future__ import annotations

import bpy

from . import outline_setup, outline_width_attribute, plane_filter
from .core import (
    DEFAULT_LINE_WIDTH_REFERENCE_DISTANCE,
    MODIFIER_NAME,
    PROP_BASE_THICKNESS,
    PROP_LINE_ONLY,
    PROP_REF_DISTANCE,
    SHEET_OUTLINE_MODIFIER_NAME,
)
from .scale_utils import modifier_thickness_for_world_width


def _existing_outline_material(obj: bpy.types.Object) -> bpy.types.Material | None:
    slot = outline_setup._first_outline_slot(obj)
    if slot is None:
        return None
    try:
        return obj.material_slots[slot].material
    except (IndexError, TypeError):
        return None


def _outline_material_slots_are_stable(
    obj: bpy.types.Object,
    material_offset: int,
    mat: bpy.types.Material,
) -> bool:
    """アウトライン素材帯 [n,2n) と非表示素材帯 [2n,3n) が両方健全か.

    後者（BML_SheetRimHidden の n 個パディング）まで確認することで、
    2026-07-09 以前の旧スロット構成（非表示素材が1個しか無い保存済み
    ファイル）を高速更新パスがそのまま通してしまわないようにする。
    不安定と判定された場合は呼び出し元が `apply_outline()` へフォール
    バックし、`_ensure_outline_material_slots()` がスロット構成を修復する。
    """
    if obj.type != "MESH" or obj.data is None:
        return False
    if material_offset <= 0:
        return False
    source_count = max(1, material_offset)
    hidden = outline_setup._get_or_create_hidden_rim_material()
    total_needed = material_offset + source_count * 2
    if len(obj.data.materials) < total_needed:
        return False
    for index in range(material_offset, material_offset + source_count):
        try:
            if obj.data.materials[index] is not mat:
                return False
        except (IndexError, TypeError):
            return False
    for index in range(material_offset + source_count, total_needed):
        try:
            if obj.data.materials[index] is not hidden:
                return False
        except (IndexError, TypeError):
            return False
    return outline_setup._has_surface_material(obj)


def _material_needs_repair(
    mat: bpy.types.Material,
    target: str,
    hide_through_transparent: bool,
    double_sided: bool,
) -> bool:
    if not mat.use_nodes or mat.node_tree is None:
        return True
    current = bool(mat.get(outline_setup.PROP_HIDE_THROUGH_TRANSPARENT, False))
    current_double_sided = bool(mat.get(outline_setup.PROP_DOUBLE_SIDED, False))
    current_build = int(mat.get(outline_setup.PROP_MATERIAL_BUILD, 0) or 0)
    return (
        current != hide_through_transparent
        or current_double_sided != double_sided
        or current_build != outline_setup._LINE_MATERIAL_BUILD_VERSION
        or not outline_setup._has_aov_node(mat, target)
    )


def _sync_existing_outline_material(
    mat: bpy.types.Material,
    color: tuple[float, ...],
    *,
    hide_through_transparent: bool,
    double_sided: bool,
) -> None:
    if _material_needs_repair(
        mat,
        "outline",
        hide_through_transparent,
        double_sided,
    ):
        outline_setup._repair_line_material(
            mat,
            color,
            target="outline",
            hide_through_transparent=hide_through_transparent,
            double_sided=double_sided,
        )
        return
    outline_setup._update_emission_color(mat, color)
    outline_setup._configure_material(mat, double_sided=double_sided)


def _sync_existing_outline_width_controls(
    obj: bpy.types.Object,
    mod: bpy.types.Modifier | None,
    *,
    use_vertex_color: bool,
    use_vertex_group: bool,
) -> None:
    need_vg = use_vertex_color or use_vertex_group
    if mod is not None and need_vg:
        vg = outline_setup._ensure_vertex_group(obj)
        if use_vertex_color:
            outline_setup._ensure_color_attribute(obj)
        mod.vertex_group = vg.name
        mod.thickness_vertex_group = 0.0
    elif mod is not None:
        mod.vertex_group = ""

    outline_width_attribute.ensure_outline_width_attribute(
        obj,
        getattr(obj, "bmanga_line_settings", None),
    )


def _store_outline_reference_width(
    obj: bpy.types.Object,
    thickness: float,
    scene,
) -> None:
    if scene is not None and scene.camera is not None:
        obj[PROP_BASE_THICKNESS] = abs(thickness)
        obj[PROP_REF_DISTANCE] = DEFAULT_LINE_WIDTH_REFERENCE_DISTANCE


def _existing_sheet_outline_state(
    obj: bpy.types.Object,
) -> tuple[bpy.types.Modifier, bpy.types.Material, str] | None:
    if not (plane_filter.is_sheet_mesh(obj) or outline_setup._uses_boundary_tube_only(obj)):
        return None
    if obj.modifiers.get(MODIFIER_NAME) is not None:
        return None
    mod = obj.modifiers.get(SHEET_OUTLINE_MODIFIER_NAME)
    if mod is None:
        return None
    mat = _existing_outline_material(obj)
    if mat is None or not outline_setup._has_surface_material(obj):
        return None

    tree = outline_setup._get_or_create_sheet_outline_tree()
    if mod.node_group is not tree:
        mod.node_group = tree
    sid_mat = outline_setup._find_socket_identifier(
        tree,
        outline_setup._SHEET_TUBE_MATERIAL_SOCKET,
    )
    if sid_mat is None:
        return None
    return mod, mat, sid_mat


def _existing_solid_outline_state(
    obj: bpy.types.Object,
) -> tuple[bpy.types.Modifier, bpy.types.Material, int] | None:
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is None:
        return None
    material_offset = outline_setup._first_outline_slot(obj)
    mat = _existing_outline_material(obj)
    if (
        material_offset is None
        or mat is None
        or not _outline_material_slots_are_stable(obj, material_offset, mat)
    ):
        return None
    return mod, mat, material_offset


def _update_existing_sheet_outline(
    obj: bpy.types.Object,
    *,
    thickness: float,
    color: tuple[float, ...],
    use_vertex_color: bool,
    use_vertex_group: bool,
    hide_through_transparent: bool,
    scene,
) -> bool:
    state = _existing_sheet_outline_state(obj)
    if state is None:
        return False
    mod, mat, sid_mat = state
    _sync_existing_outline_material(
        mat,
        color,
        hide_through_transparent=hide_through_transparent,
        double_sided=outline_setup._outline_double_sided(obj),
    )
    outline_setup._ensure_sheet_line_material_slot(obj, mat)
    mod[sid_mat] = mat

    local_thickness = modifier_thickness_for_world_width(obj, thickness)
    outline_setup.sync_sheet_outline_width(obj, local_thickness)
    visible = outline_setup._sheet_outline_visible(obj)
    mod.show_viewport = visible
    mod.show_render = visible
    _sync_existing_outline_width_controls(
        obj,
        None,
        use_vertex_color=use_vertex_color,
        use_vertex_group=use_vertex_group,
    )
    _store_outline_reference_width(obj, thickness, scene)
    if bool(obj.get(PROP_LINE_ONLY, False)):
        outline_setup._restore_outline_materials(
            obj,
            obj.data,
            hide_through_transparent_override=True,
        )
    return True


def _update_existing_solid_outline(
    obj: bpy.types.Object,
    *,
    thickness: float,
    color: tuple[float, ...],
    use_vertex_color: bool,
    even_thickness: bool,
    use_rim: bool,
    offset: float,
    use_vertex_group: bool,
    hide_through_transparent: bool,
    scene,
) -> bool:
    state = _existing_solid_outline_state(obj)
    if state is None:
        return False
    mod, mat, material_offset = state

    _sync_existing_outline_material(
        mat,
        color,
        hide_through_transparent=hide_through_transparent,
        double_sided=False,
    )

    mod.thickness = modifier_thickness_for_world_width(obj, thickness)
    mod.use_flip_normals = True
    mod.use_even_offset = even_thickness
    outline_setup._configure_solidify_shape(obj, mod, use_rim, offset)
    mod.material_offset = material_offset
    # material_offset_rim は加算オフセット。境界チューブを作らない
    # オブジェクトではリム面が境界のラインとして描かれるため n を加算する
    # （apply_outline() と同じ規則）。境界チューブ併用オブジェクトだけ、
    # 直後の ensure_sheet_outline が非表示素材帯(2n)へ上書きする。
    mod.material_offset_rim = material_offset
    outline_setup.ensure_sheet_outline(obj, mod, mat, material_offset=material_offset)
    _sync_existing_outline_width_controls(
        obj,
        mod,
        use_vertex_color=use_vertex_color,
        use_vertex_group=use_vertex_group,
    )
    _store_outline_reference_width(obj, thickness, scene)

    if bool(obj.get(PROP_LINE_ONLY, False)):
        outline_setup._restore_outline_materials(
            obj,
            obj.data,
            hide_through_transparent_override=True,
        )
        outline_setup._configure_line_only_solidify_shape(obj, use_rim, offset)
    return True


def update_existing_outline(
    obj: bpy.types.Object,
    thickness: float = 0.0003,
    color: tuple[float, ...] = (0.0, 0.0, 0.0, 1.0),
    use_vertex_color: bool = False,
    even_thickness: bool = True,
    use_rim: bool = True,
    offset: float = 0.0,
    *,
    use_vertex_group: bool = False,
    hide_through_transparent: bool = False,
    scene=None,
) -> bool:
    """Update a valid existing outline without rebuilding its structure."""
    if obj.type != "MESH" or obj.data is None:
        return False
    if plane_filter.is_sheet_mesh(obj) or outline_setup._uses_boundary_tube_only(obj):
        return _update_existing_sheet_outline(
            obj,
            thickness=thickness,
            color=color,
            use_vertex_color=use_vertex_color,
            use_vertex_group=use_vertex_group,
            hide_through_transparent=hide_through_transparent,
            scene=scene,
        )
    return _update_existing_solid_outline(
        obj,
        thickness=thickness,
        color=color,
        use_vertex_color=use_vertex_color,
        even_thickness=even_thickness,
        use_rim=use_rim,
        offset=offset,
        use_vertex_group=use_vertex_group,
        hide_through_transparent=hide_through_transparent,
        scene=scene,
    )
