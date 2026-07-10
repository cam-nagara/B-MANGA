"""B-MANGA Line preset storage and application."""

from __future__ import annotations

import json
import os
from pathlib import Path

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)

from . import core, modifier_stack, registration


_STORE_FILE_NAME = "b_manga_line_presets.json"
_STORE_VERSION = 1
_loaded_scene_pointers: set[int] = set()
_saving_scene_snapshots: dict[int, tuple[object, list[dict], int, str]] = {}
_preset_name_updates_suspended = 0


_SETTING_FIELDS = (
    "lines_visible",
    "outline_enabled",
    "outline_thickness",
    "outline_offset",
    "outline_color",
    "use_outline_creation_limit",
    "outline_creation_max_distance",
    "use_vertex_color",
    "auto_subdivision_for_midpoint",
    "even_thickness",
    "exclude_sheet_meshes",
    "use_uniform_line_width",
    "limit_uniform_width_to_setting",
    "use_rim",
    "hide_through_transparent",
    "weld_mesh_for_outline",
    "inner_line_enabled",
    "inner_line_angle",
    "use_marked_inner_edges",
    "inner_line_thickness",
    "inner_line_offset",
    "inner_line_color",
    "use_inner_line_creation_limit",
    "inner_line_creation_max_distance",
    "intersection_method",
    "intersection_enabled",
    "intersection_thickness",
    "intersection_line_offset",
    "intersection_color",
    "use_intersection_creation_limit",
    "intersection_creation_max_distance",
    "selection_line_enabled",
    "selection_line_angle",
    "selection_line_thickness",
    "selection_line_offset",
    "selection_line_color",
    "use_selection_line_creation_limit",
    "selection_line_creation_max_distance",
    "use_camera_compensation",
    "camera_compensation_influence",
    "line_width_reference_distance",
    "line_width_distance_falloff",
    "edge_smooth_factor",
    "edge_midpoint_jitter_percent",
    "edge_midpoint_angle",
    "edge_width_curve_25",
    "edge_width_curve_50",
    "edge_width_curve_75",
    "inner_edge_smooth_factor",
    "inner_edge_midpoint_jitter_percent",
    "inner_edge_midpoint_angle",
    "inner_edge_width_curve_25",
    "inner_edge_width_curve_50",
    "inner_edge_width_curve_75",
    "intersection_edge_smooth_factor",
    "intersection_edge_midpoint_jitter_percent",
    "intersection_edge_midpoint_angle",
    "intersection_edge_width_curve_25",
    "intersection_edge_width_curve_50",
    "intersection_edge_width_curve_75",
    "selection_edge_smooth_factor",
    "selection_edge_midpoint_jitter_percent",
    "selection_edge_midpoint_angle",
    "selection_edge_width_curve_25",
    "selection_edge_width_curve_50",
    "selection_edge_width_curve_75",
    "use_camera_culling",
    "culling_margin",
    "use_outline_distance_limit",
    "outline_max_distance",
    "use_inner_line_distance_limit",
    "inner_line_max_distance",
    "use_intersection_distance_limit",
    "intersection_max_distance",
    "use_selection_line_distance_limit",
    "selection_line_max_distance",
    "bump_line_enabled",
    "bump_line_color",
    "bump_line_thickness",
    "bump_line_threshold",
)
_COLOR_FIELDS = {
    "outline_color",
    "inner_line_color",
    "intersection_color",
    "selection_line_color",
    "bump_line_color",
}


def _selected_meshes(context) -> list[bpy.types.Object]:
    return [obj for obj in context.selected_objects if obj.type == "MESH"]


def _store_path() -> Path:
    override = os.environ.get("BMANGA_LINE_PRESET_STORE_DIR", "").strip()
    if override:
        return Path(override) / _STORE_FILE_NAME
    cfg = bpy.utils.user_resource("CONFIG", create=True)
    return Path(cfg) / _STORE_FILE_NAME


def _preset_to_dict(preset) -> dict:
    settings = {}
    for name in _SETTING_FIELDS:
        value = getattr(preset, name)
        if name in _COLOR_FIELDS:
            value = list(value)
        settings[name] = value
    return {
        "name": str(getattr(preset, "name", "") or "ラインプリセット"),
        "settings": settings,
    }


def _apply_dict_to_preset(data: dict, preset) -> None:
    preset.name = str(data.get("name", "") or "ラインプリセット")
    settings = data.get("settings", {})
    if not isinstance(settings, dict):
        return
    for name in _SETTING_FIELDS:
        if name not in settings:
            continue
        value = settings[name]
        if name in _COLOR_FIELDS and isinstance(value, list):
            value = tuple(value)
        try:
            setattr(preset, name, value)
        except (TypeError, ValueError):
            pass


def _read_store() -> dict:
    path = _store_path()
    if not path.is_file():
        return {"version": _STORE_VERSION, "presets": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 壊れた外部設定でアドオン起動を止めない
        return {"version": _STORE_VERSION, "presets": []}
    if not isinstance(raw, dict):
        return {"version": _STORE_VERSION, "presets": []}
    presets = raw.get("presets", [])
    if not isinstance(presets, list):
        raw["presets"] = []
    return raw


def _scene_preset_dicts(scene) -> list[dict]:
    if scene is None or not hasattr(scene, "bmanga_line_presets"):
        return []
    return [_preset_to_dict(item) for item in scene.bmanga_line_presets]


def _merge_preset_dicts(primary: list[dict], additions: list[dict]) -> tuple[list[dict], bool]:
    merged = list(primary)
    names = {str(item.get("name", "") or "") for item in merged}
    changed = False
    for item in additions:
        name = str(item.get("name", "") or "")
        if not name or name in names:
            continue
        merged.append(item)
        names.add(name)
        changed = True
    return merged, changed


def _safe_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _sync_preset_name_from_index(scene) -> None:
    presets = getattr(scene, "bmanga_line_presets", None)
    index = _safe_int(getattr(scene, "bmanga_line_preset_index", -1), -1)
    if presets is not None and 0 <= index < len(presets):
        scene.bmanga_line_preset_name = str(
            presets[index].name or "ラインプリセット"
        )


def _on_preset_index_changed(scene, _context) -> None:
    _sync_preset_name_from_index(scene)


def _populate_scene_presets(scene, preset_dicts: list[dict], *, index: int, name: str) -> None:
    global _preset_name_updates_suspended

    _preset_name_updates_suspended += 1
    try:
        collection = scene.bmanga_line_presets
        collection.clear()
        for data in preset_dicts:
            item = collection.add()
            _apply_dict_to_preset(data, item)
        if collection:
            active_index = max(0, min(index, len(collection) - 1))
            scene.bmanga_line_preset_index = active_index
            scene.bmanga_line_preset_name = str(
                collection[active_index].name or "ラインプリセット"
            )
        else:
            scene.bmanga_line_preset_index = -1
            scene.bmanga_line_preset_name = str(name or "ラインプリセット")
    finally:
        _preset_name_updates_suspended -= 1


def _write_store(scene) -> Path:
    data = {
        "version": _STORE_VERSION,
        "presets": _scene_preset_dicts(scene),
        "active_index": int(getattr(scene, "bmanga_line_preset_index", -1)),
        "preset_name": str(getattr(scene, "bmanga_line_preset_name", "") or "ラインプリセット"),
    }
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _scene_snapshot(scene) -> tuple[list[dict], int, str]:
    return (
        _scene_preset_dicts(scene),
        _safe_int(getattr(scene, "bmanga_line_preset_index", -1), -1),
        str(getattr(scene, "bmanga_line_preset_name", "") or "ラインプリセット"),
    )


def ensure_presets_loaded(scene) -> None:
    if scene is None or not hasattr(scene, "bmanga_line_presets"):
        return
    pointer = scene.as_pointer()
    if pointer in _loaded_scene_pointers:
        return
    existing = _scene_preset_dicts(scene)
    stored = _read_store()
    stored_presets = [
        item for item in stored.get("presets", [])
        if isinstance(item, dict)
    ]
    merged, changed = _merge_preset_dicts(stored_presets, existing)
    index = _safe_int(stored.get("active_index", -1), -1)
    name = str(stored.get("preset_name", "") or "ラインプリセット")
    _populate_scene_presets(scene, merged, index=index, name=name)
    _loaded_scene_pointers.add(pointer)
    if changed or (existing and not stored_presets):
        _write_store(scene)


def _preset_name(scene) -> str:
    ensure_presets_loaded(scene)
    raw = getattr(scene, "bmanga_line_preset_name", "")
    return str(raw).strip() or "ラインプリセット"


def _active_preset(scene):
    ensure_presets_loaded(scene)
    presets = scene.bmanga_line_presets
    index = scene.bmanga_line_preset_index
    if 0 <= index < len(presets):
        return presets[index]
    return None


def _on_preset_name_changed(preset, _context) -> None:
    if _preset_name_updates_suspended:
        return
    scene = getattr(preset, "id_data", None)
    if scene is None or not hasattr(scene, "bmanga_line_presets"):
        return
    name = str(getattr(preset, "name", "") or "").strip()
    if not name:
        return
    active = _active_preset(scene)
    if active is not None and active.as_pointer() == preset.as_pointer():
        scene.bmanga_line_preset_name = name
    _write_store(scene)


def copy_settings_to_preset(settings, preset) -> None:
    for name in _SETTING_FIELDS:
        value = getattr(settings, name)
        if name in _COLOR_FIELDS:
            value = tuple(value)
        setattr(preset, name, value)


def copy_preset_to_settings(preset, settings) -> None:
    old = core._propagating
    core._propagating = True
    try:
        for name in _SETTING_FIELDS:
            setattr(settings, name, getattr(preset, name))
    finally:
        core._propagating = old


def preset_matches_settings(preset, settings) -> bool:
    for name in _SETTING_FIELDS:
        if not core._setting_values_equal(getattr(preset, name), getattr(settings, name)):
            return False
    return True


def copy_preset_to_preset(source, target) -> None:
    for name in _SETTING_FIELDS:
        value = getattr(source, name)
        if name in _COLOR_FIELDS:
            value = tuple(value)
        setattr(target, name, value)


def copy_settings_to_settings(source, target) -> None:
    old = core._propagating
    core._propagating = True
    try:
        for name in _SETTING_FIELDS:
            value = getattr(source, name)
            if name in _COLOR_FIELDS:
                value = tuple(value)
            setattr(target, name, value)
    finally:
        core._propagating = old


def _update_view_layer(context) -> None:
    view_layer = getattr(context, "view_layer", None)
    update = getattr(view_layer, "update", None)
    if callable(update):
        update()


def _refresh_after_line_settings(
    context,
    sources: list[bpy.types.Object] | tuple[bpy.types.Object, ...] | None = None,
    *,
    refresh_intersections: bool = True,
) -> None:
    from . import camera_comp, intersection_lines, outline_setup

    camera_comp.refresh(context)
    _update_view_layer(context)
    if refresh_intersections:
        intersection_targets = intersection_lines.refresh_scene_intersections(
            context.scene,
            sources=sources,
        )
        if intersection_targets:
            camera_comp.refresh_objects(
                context,
                intersection_targets,
                update_visibility=True,
                width_targets=("intersection",),
            )
    outline_setup.ensure_aov_passes(context.scene)


def _duplicate_name(presets, source_name: str) -> str:
    base = (source_name.strip() if source_name else "") or "ラインプリセット"
    stem = f"{base} コピー"
    return _unique_preset_name(presets, stem)


def _unique_preset_name(presets, base_name: str) -> str:
    stem = (base_name.strip() if base_name else "") or "ラインプリセット"
    existing = {item.name for item in presets}
    if stem not in existing:
        return stem
    index = 2
    while True:
        candidate = f"{stem} {index}"
        if candidate not in existing:
            return candidate
        index += 1


def _reflect_applied_display_settings(
    objects: list[bpy.types.Object],
    context,
    *,
    line_targets=None,
) -> None:
    """プリセット適用時に抑制した表示系コールバックを明示反映する."""
    if not objects:
        return
    from . import camera_comp

    # 個別の「反映」では、未反映の他線種の有効/無効を表示へ波及させない。
    # 全線種をまとめて反映する経路だけがグローバル表示設定を同期する。
    if line_targets is None:
        visibility_refresh_targets = []
        for obj in objects:
            settings = obj.bmanga_line_settings
            if bool(getattr(settings, "lines_visible", True)):
                was_hidden = bool(obj.get(core.PROP_LINES_HIDDEN, False))
                obj[core.PROP_LINES_HIDDEN] = False
                core.sync_line_visibility_setting(obj)
                if (
                    bool(getattr(settings, "use_camera_culling", False))
                    or bool(getattr(settings, "use_outline_distance_limit", False))
                    or bool(getattr(settings, "use_inner_line_distance_limit", False))
                    or bool(getattr(settings, "use_intersection_distance_limit", False))
                    or bool(getattr(settings, "use_selection_line_distance_limit", False))
                ):
                    if was_hidden:
                        visibility_refresh_targets.append(obj)
                else:
                    core.set_line_visibility(obj, True)
            else:
                core.set_line_visibility(obj, False)
        if visibility_refresh_targets:
            camera_comp.refresh_visibility_objects(context, visibility_refresh_targets)

    if core.is_scene_line_only_enabled(context):
        # 「ラインのみを表示」ON中に新規追加したオブジェクトの素材は、
        # シーン全体の再白色化(set_materials_line_only)を経由しないと
        # 白化されないままになる(2026-07-09 徹底チェックで実機確認)。
        # 全素材走査は過去の重い固着バグの原因だったため復活させず、
        # 今回の適用対象オブジェクトの非BML素材だけを個別に白化する。
        from . import line_only_display

        for obj in objects:
            if getattr(obj, "type", None) != "MESH" or obj.data is None:
                continue
            for slot in obj.material_slots:
                mat = slot.material
                if line_only_display.is_line_only_surface_material(mat):
                    line_only_display._enable_material_line_only(mat)


def apply_line_settings(
    obj: bpy.types.Object,
    context,
    *,
    refresh_scene: bool = True,
    transforms_fresh: bool = False,
    line_targets=None,
) -> bool:
    if obj.type != "MESH":
        return False
    if not transforms_fresh:
        _update_view_layer(context)

    from . import (
        camera_comp,
        inner_lines,
        intersection_lines,
        outline_fast_update,
        outline_setup,
        plane_filter,
        scale_utils,
        selection_lines,
        subdivision_lod,
        vertex_analysis,
    )

    settings = obj.bmanga_line_settings
    target_set = set(line_targets or ("outline", "inner", "intersection", "selection"))
    if target_set and target_set - {"outline", "inner", "intersection", "selection"}:
        target_set = {"outline", "inner", "intersection", "selection"}

    midpoint_targets = target_set & {"outline", "inner", "intersection", "selection"}
    if midpoint_targets and settings.auto_subdivision_for_midpoint:
        subdivision_lod.ensure_auto_subdivision(obj, context.scene)
    elif midpoint_targets:
        subdivision_lod.remove_auto_subdivision(obj)

    if "outline" in target_set:
        has_outline = core.has_outline(obj)
        in_creation_range = camera_comp.outline_line_creation_in_range(
            obj, context.scene, settings,
        )
        if settings.outline_enabled and (has_outline or in_creation_range):
            use_vg = (
                settings.use_uniform_line_width
                or vertex_analysis.has_width_controls(settings, "outline")
            )
            outline_kwargs = {
                "thickness": settings.outline_thickness,
                "color": tuple(settings.outline_color),
                "use_vertex_color": settings.use_vertex_color,
                "even_thickness": settings.even_thickness,
                "use_rim": settings.use_rim,
                "offset": settings.outline_offset,
                "use_vertex_group": use_vg,
                "hide_through_transparent": settings.hide_through_transparent,
                "scene": context.scene,
            }
            ok = False
            if has_outline:
                ok = outline_fast_update.update_existing_outline(obj, **outline_kwargs)
            if not ok:
                ok = outline_setup.apply_outline(obj, **outline_kwargs)
            if not ok:
                return False
        elif not settings.outline_enabled:
            outline_setup.remove_outline_geometry(obj)

    skip_inner = plane_filter.should_skip_inner_lines(obj, settings)
    if "inner" in target_set:
        has_inner = obj.modifiers.get(core.GN_MODIFIER_NAME) is not None
        in_creation_range = camera_comp.inner_line_creation_in_range(
            obj, context.scene, settings,
        )
        if (
            settings.inner_line_enabled
            and not skip_inner
            and (has_inner or in_creation_range)
        ):
            inner_lines.apply_inner_lines(
                obj,
                angle=settings.inner_line_angle,
                thickness=scale_utils.modifier_thickness_for_world_width(
                    obj,
                    settings.inner_line_thickness,
                ),
                offset=settings.inner_line_offset,
                material=outline_setup.get_line_material(obj, "inner"),
                use_marked_edges=False,
                midpoint_factor=(
                    settings.inner_edge_smooth_factor
                    if settings.auto_subdivision_for_midpoint
                    else 0.0
                ),
                midpoint_angle=core.inner_width_split_angle(settings),
                midpoint_jitter_percent=settings.inner_edge_midpoint_jitter_percent,
                width_curve_25=settings.inner_edge_width_curve_25,
                width_curve_50=settings.inner_edge_width_curve_50,
                width_curve_75=settings.inner_edge_width_curve_75,
            )
        elif not settings.inner_line_enabled or skip_inner:
            inner_lines.remove_inner_lines(obj)

    if "selection" in target_set:
        has_selection = obj.modifiers.get(core.SELECTION_LINE_MODIFIER_NAME) is not None
        in_creation_range = camera_comp.selection_line_creation_in_range(
            obj, context.scene, settings,
        )
        if (
            settings.selection_line_enabled
            and (has_selection or in_creation_range)
        ):
            selection_lines.apply_selection_lines(
                obj,
                angle=settings.selection_line_angle,
                thickness=scale_utils.modifier_thickness_for_world_width(
                    obj,
                    settings.selection_line_thickness,
                ),
                offset=settings.selection_line_offset,
                material=outline_setup.get_line_material(obj, "selection"),
                midpoint_factor=(
                    settings.selection_edge_smooth_factor
                    if settings.auto_subdivision_for_midpoint
                    else 0.0
                ),
                midpoint_angle=settings.selection_edge_midpoint_angle,
                midpoint_jitter_percent=settings.selection_edge_midpoint_jitter_percent,
                width_curve_25=settings.selection_edge_width_curve_25,
                width_curve_50=settings.selection_edge_width_curve_50,
                width_curve_75=settings.selection_edge_width_curve_75,
            )
        elif not settings.selection_line_enabled:
            selection_lines.remove_selection_lines(obj)

    if "intersection" in target_set:
        intersection_in_range = camera_comp.intersection_line_creation_in_range(
            obj, context.scene, settings,
        )
        if not (
            settings.intersection_enabled
            and not plane_filter.should_exclude_generated_lines(obj, settings)
            and (
                intersection_in_range
                or any(core.iter_intersection_modifiers(obj))
            )
        ):
            intersection_lines.remove_intersection_lines(obj)

    camera_comp.store_unit_reference(obj, context.scene)

    if not settings.use_uniform_line_width:
        for target in target_set:
            group_name = vertex_analysis.width_group_name(target)
            if vertex_analysis.has_width_controls(settings, target):
                vertex_analysis.compute_and_apply_weights(obj, settings, target)
            else:
                vertex_analysis.clear_width_weights(obj, group_name=group_name)

    visible = not bool(obj.get(core.PROP_LINES_HIDDEN, False))
    if target_set == {"outline", "inner", "intersection", "selection"}:
        core.set_line_visibility(obj, visible)
    else:
        core.set_line_targets_visibility(obj, visible, target_set)
    modifier_stack.reorder_line_modifiers(obj)

    if refresh_scene:
        _refresh_after_line_settings(context)

    return True


class BMangaLinePreset(bpy.types.PropertyGroup):
    """Saved B-MANGA Line settings."""

    name: StringProperty(
        name="プリセット名",
        default="ラインプリセット",
        update=_on_preset_name_changed,
    )
    lines_visible: BoolProperty(default=True)
    # 2026-07-09 表示モードはプリセット保存対象から除外（_SETTING_FIELDS 参照）。
    # 旧プリセットJSON互換のためプロパティ自体は残す。
    line_only_visible: BoolProperty(default=False)
    outline_enabled: BoolProperty(default=True)
    outline_thickness: FloatProperty(default=0.0003, min=0.00001, max=1.0)
    outline_offset: FloatProperty(default=0.0, min=-1.0, max=1.0)
    outline_color: FloatVectorProperty(
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
    )
    use_outline_creation_limit: BoolProperty(default=False)
    outline_creation_max_distance: FloatProperty(default=10.0, min=0.1, max=1000.0)
    use_vertex_color: BoolProperty(default=False)
    auto_subdivision_for_midpoint: BoolProperty(default=True)
    even_thickness: BoolProperty(default=False)
    # 2026-07-03 ユーザー確定: 板ポリ除外だけは「初期値全オフ」の対象外でオン
    exclude_sheet_meshes: BoolProperty(default=True)
    use_uniform_line_width: BoolProperty(default=True)
    limit_uniform_width_to_setting: BoolProperty(default=False)
    use_rim: BoolProperty(default=False)
    hide_through_transparent: BoolProperty(default=False)
    weld_mesh_for_outline: BoolProperty(default=True)

    inner_line_enabled: BoolProperty(default=False)
    inner_line_angle: FloatProperty(default=1.0471975512, min=0.0174532925, max=3.1415926536)
    use_marked_inner_edges: BoolProperty(default=False)
    inner_line_thickness: FloatProperty(default=0.0003, min=0.00001, max=1.0)
    inner_line_offset: FloatProperty(default=0.0, min=-1.0, max=1.0)
    inner_line_color: FloatVectorProperty(
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
    )
    use_inner_line_creation_limit: BoolProperty(default=True)
    inner_line_creation_max_distance: FloatProperty(default=10.0, min=0.1, max=1000.0)

    intersection_method: EnumProperty(
        items=[
            ("SHELL", "ライン素材（高速）", ""),
            ("BOOLEAN", "Boolean（精密）", ""),
            ("SDF", "SDF（高速）", ""),
        ],
        default="SHELL",
    )
    intersection_enabled: BoolProperty(default=False)
    intersection_thickness: FloatProperty(default=0.0003, min=0.00001, max=1.0)
    intersection_line_offset: FloatProperty(default=0.0, min=-1.0, max=1.0)
    intersection_color: FloatVectorProperty(
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
    )
    use_intersection_creation_limit: BoolProperty(default=True)
    intersection_creation_max_distance: FloatProperty(default=10.0, min=0.1, max=1000.0)

    selection_line_enabled: BoolProperty(default=False)
    selection_line_angle: FloatProperty(default=1.0471975512, min=0.0174532925, max=3.1415926536)
    selection_line_thickness: FloatProperty(default=0.0003, min=0.00001, max=1.0)
    selection_line_offset: FloatProperty(default=0.0, min=-1.0, max=1.0)
    selection_line_color: FloatVectorProperty(
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
    )
    use_selection_line_creation_limit: BoolProperty(default=False)
    selection_line_creation_max_distance: FloatProperty(default=10.0, min=0.1, max=1000.0)

    use_camera_compensation: BoolProperty(default=False)
    camera_compensation_influence: FloatProperty(default=1.0, min=0.0, max=1.0)
    line_width_reference_distance: FloatProperty(
        default=core.DEFAULT_LINE_WIDTH_REFERENCE_DISTANCE,
        min=0.001,
        max=1000.0,
    )
    line_width_distance_falloff: FloatProperty(default=1.0, min=0.0, max=2.0)

    edge_smooth_factor: FloatProperty(default=0.0, min=-1.0, max=1.0)
    edge_midpoint_jitter_percent: FloatProperty(default=0.0, min=0.0, max=50.0)
    edge_midpoint_angle: FloatProperty(default=1.7453292520, min=0.0174532925, max=3.1415926536)
    edge_width_curve_25: FloatProperty(default=0.25, min=0.0, max=1.0)
    edge_width_curve_50: FloatProperty(default=0.50, min=0.0, max=1.0)
    edge_width_curve_75: FloatProperty(default=0.75, min=0.0, max=1.0)

    inner_edge_smooth_factor: FloatProperty(default=0.0, min=-1.0, max=1.0)
    inner_edge_midpoint_jitter_percent: FloatProperty(default=0.0, min=0.0, max=50.0)
    inner_edge_midpoint_angle: FloatProperty(default=1.7453292520, min=0.0174532925, max=3.1415926536)
    inner_edge_width_curve_25: FloatProperty(default=0.25, min=0.0, max=1.0)
    inner_edge_width_curve_50: FloatProperty(default=0.50, min=0.0, max=1.0)
    inner_edge_width_curve_75: FloatProperty(default=0.75, min=0.0, max=1.0)

    intersection_edge_smooth_factor: FloatProperty(default=0.0, min=-1.0, max=1.0)
    intersection_edge_midpoint_jitter_percent: FloatProperty(default=0.0, min=0.0, max=50.0)
    intersection_edge_midpoint_angle: FloatProperty(default=1.7453292520, min=0.0174532925, max=3.1415926536)
    intersection_edge_width_curve_25: FloatProperty(default=0.25, min=0.0, max=1.0)
    intersection_edge_width_curve_50: FloatProperty(default=0.50, min=0.0, max=1.0)
    intersection_edge_width_curve_75: FloatProperty(default=0.75, min=0.0, max=1.0)

    selection_edge_smooth_factor: FloatProperty(default=0.0, min=-1.0, max=1.0)
    selection_edge_midpoint_jitter_percent: FloatProperty(default=0.0, min=0.0, max=50.0)
    selection_edge_midpoint_angle: FloatProperty(default=1.7453292520, min=0.0174532925, max=3.1415926536)
    selection_edge_width_curve_25: FloatProperty(default=0.25, min=0.0, max=1.0)
    selection_edge_width_curve_50: FloatProperty(default=0.50, min=0.0, max=1.0)
    selection_edge_width_curve_75: FloatProperty(default=0.75, min=0.0, max=1.0)

    use_camera_culling: BoolProperty(default=True)
    culling_margin: FloatProperty(default=0.1745329252, min=0.0, max=1.5707963268)

    use_outline_distance_limit: BoolProperty(default=False)
    outline_max_distance: FloatProperty(default=20.0, min=0.1, max=1000.0)

    use_inner_line_distance_limit: BoolProperty(default=False)
    inner_line_max_distance: FloatProperty(default=20.0, min=0.1, max=1000.0)

    use_intersection_distance_limit: BoolProperty(default=False)
    intersection_max_distance: FloatProperty(default=20.0, min=0.1, max=1000.0)

    use_selection_line_distance_limit: BoolProperty(default=False)
    selection_line_max_distance: FloatProperty(default=20.0, min=0.1, max=1000.0)

    bump_line_enabled: BoolProperty(default=False)
    bump_line_color: FloatVectorProperty(
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
    )
    bump_line_thickness: FloatProperty(default=0.3, min=0.01, max=50.0)
    bump_line_threshold: FloatProperty(default=0.65, min=0.0, max=1.0)


class BMANGA_LINE_OT_preset_save(bpy.types.Operator):
    """現在のライン設定をプリセットとして保存"""

    bl_idname = "bmanga_line.preset_save"
    bl_label = "保存/更新"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    def execute(self, context):
        from . import settings_draft

        settings_draft.flush(context)
        scene = context.scene
        ensure_presets_loaded(scene)
        obj = context.active_object
        presets = scene.bmanga_line_presets
        preset = _active_preset(scene)
        if preset is None:
            name = _unique_preset_name(presets, _preset_name(scene))
            preset = presets.add()
            copy_settings_to_preset(obj.bmanga_line_settings, preset)
            preset.name = name
            index = len(presets) - 1
        else:
            index = scene.bmanga_line_preset_index
            name = str(preset.name or "").strip()
            if not name:
                name = _unique_preset_name(presets, "ラインプリセット")
                preset.name = name
            copy_settings_to_preset(obj.bmanga_line_settings, preset)
        scene.bmanga_line_preset_index = index
        scene.bmanga_line_preset_name = name
        _write_store(scene)
        self.report({"INFO"}, f"ラインプリセット「{name}」を保存しました")
        return {"FINISHED"}


class BMANGA_LINE_OT_preset_add(bpy.types.Operator):
    """現在のライン設定を新しいプリセットとして追加"""

    bl_idname = "bmanga_line.preset_add"
    bl_label = "追加"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    def execute(self, context):
        from . import settings_draft

        settings_draft.flush(context)
        scene = context.scene
        ensure_presets_loaded(scene)
        obj = context.active_object
        presets = scene.bmanga_line_presets
        name = _unique_preset_name(presets, "ラインプリセット")
        preset = presets.add()
        copy_settings_to_preset(obj.bmanga_line_settings, preset)
        preset.name = name
        scene.bmanga_line_preset_index = len(presets) - 1
        scene.bmanga_line_preset_name = name
        _write_store(scene)
        self.report({"INFO"}, f"ラインプリセット「{name}」を追加しました")
        return {"FINISHED"}


class BMANGA_LINE_OT_preset_apply_selected(bpy.types.Operator):
    """プリセットを選択中の全オブジェクトに適用"""

    bl_idname = "bmanga_line.preset_apply_selected"
    bl_label = "選択中に適用"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_preset(context.scene) is not None and bool(_selected_meshes(context))

    def execute(self, context):
        ensure_presets_loaded(context.scene)
        preset = _active_preset(context.scene)
        if preset is None:
            self.report({"WARNING"}, "プリセットが選択されていません")
            return {"CANCELLED"}
        from . import selection, settings_draft, update_state

        settings_draft.discard(context)
        targets = selection.updatable_mesh_objects(context)
        skipped = len(_selected_meshes(context)) - len(targets)
        count = 0
        unchanged = 0
        for obj in targets:
            if preset_matches_settings(preset, obj.bmanga_line_settings):
                unchanged += 1
                continue
            copy_preset_to_settings(preset, obj.bmanga_line_settings)
            update_state.mark_pending(obj)
            count += 1
        settings_draft.invalidate(context)
        message = f"{count} オブジェクトにプリセット設定を適用しました"
        if unchanged > 0:
            message += f"（変更なし{unchanged}件）"
        if skipped > 0:
            message += f"（ロック中のため{skipped}件を除外）"
        self.report({"INFO"}, message)
        return {"FINISHED"}


class BMANGA_LINE_OT_preset_duplicate(bpy.types.Operator):
    """選択中のラインプリセットを複製"""

    bl_idname = "bmanga_line.preset_duplicate"
    bl_label = "複製"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_preset(context.scene) is not None

    def execute(self, context):
        scene = context.scene
        ensure_presets_loaded(scene)
        presets = scene.bmanga_line_presets
        source = _active_preset(scene)
        if source is None:
            self.report({"WARNING"}, "プリセットが選択されていません")
            return {"CANCELLED"}
        name = _duplicate_name(presets, source.name)
        duplicate = presets.add()
        copy_preset_to_preset(source, duplicate)
        duplicate.name = name
        scene.bmanga_line_preset_index = len(presets) - 1
        scene.bmanga_line_preset_name = name
        _write_store(scene)
        self.report({"INFO"}, f"ラインプリセット「{name}」を複製しました")
        return {"FINISHED"}


class BMANGA_LINE_OT_preset_delete(bpy.types.Operator):
    """選択中のラインプリセットを削除"""

    bl_idname = "bmanga_line.preset_delete"
    bl_label = "削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_preset(context.scene) is not None

    def invoke(self, context, event):
        preset = _active_preset(context.scene)
        name = str(getattr(preset, "name", "") or "") if preset is not None else ""
        return context.window_manager.invoke_confirm(
            self,
            event,
            title="確認",
            message=f"プリセット『{name}』を削除します。",
            confirm_text="削除",
            icon="WARNING",
        )

    def execute(self, context):
        scene = context.scene
        ensure_presets_loaded(scene)
        presets = scene.bmanga_line_presets
        index = scene.bmanga_line_preset_index
        if not (0 <= index < len(presets)):
            return {"CANCELLED"}
        name = presets[index].name
        presets.remove(index)
        scene.bmanga_line_preset_index = min(index, len(presets) - 1)
        if 0 <= scene.bmanga_line_preset_index < len(presets):
            scene.bmanga_line_preset_name = presets[scene.bmanga_line_preset_index].name
        else:
            scene.bmanga_line_preset_name = "ラインプリセット"
        _write_store(scene)
        self.report({"INFO"}, f"ラインプリセット「{name}」を削除しました")
        return {"FINISHED"}


_CLASSES = (
    BMangaLinePreset,
    BMANGA_LINE_OT_preset_save,
    BMANGA_LINE_OT_preset_add,
    BMANGA_LINE_OT_preset_apply_selected,
    BMANGA_LINE_OT_preset_duplicate,
    BMANGA_LINE_OT_preset_delete,
)


@persistent
def _on_load_post(_dummy) -> None:
    _loaded_scene_pointers.clear()
    for scene in _iter_scenes():
        ensure_presets_loaded(scene)


@persistent
def _on_save_pre(_dummy) -> None:
    from . import settings_draft

    settings_draft.flush_all()
    _saving_scene_snapshots.clear()
    for scene in _iter_scenes():
        if not hasattr(scene, "bmanga_line_presets"):
            continue
        ensure_presets_loaded(scene)
        preset_dicts, index, name = _scene_snapshot(scene)
        _saving_scene_snapshots[scene.as_pointer()] = (scene, preset_dicts, index, name)
        if preset_dicts:
            _write_store(scene)
        scene.bmanga_line_presets.clear()
        scene.bmanga_line_preset_index = -1
        scene.bmanga_line_preset_name = "ラインプリセット"


@persistent
def _on_save_post(_dummy) -> None:
    for pointer, (scene, preset_dicts, index, name) in list(_saving_scene_snapshots.items()):
        if hasattr(scene, "bmanga_line_presets"):
            _populate_scene_presets(scene, preset_dicts, index=index, name=name)
            _loaded_scene_pointers.add(pointer)
    _saving_scene_snapshots.clear()


def _iter_scenes():
    try:
        return tuple(bpy.data.scenes)
    except Exception:  # noqa: BLE001 - 制限状態では後で通常登録時に読み込む
        return ()


def _append_handler(name: str, handler) -> None:
    try:
        handlers = getattr(bpy.app.handlers, name, None)
        if handlers is not None and handler not in handlers:
            handlers.append(handler)
    except Exception:  # noqa: BLE001 - 制限状態では登録だけ継続
        pass


def _remove_handler(name: str, handler) -> None:
    try:
        handlers = getattr(bpy.app.handlers, name, None)
        if handlers is not None and handler in handlers:
            handlers.remove(handler)
    except Exception:  # noqa: BLE001
        pass


def register() -> None:
    for attr in (
        "bmanga_line_preset_name",
        "bmanga_line_preset_index",
        "bmanga_line_presets",
    ):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
    for cls in _CLASSES:
        registration.register_class(cls)
    bpy.types.Scene.bmanga_line_presets = CollectionProperty(
        type=BMangaLinePreset,
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.bmanga_line_preset_index = IntProperty(
        default=-1,
        options={"SKIP_SAVE"},
        update=_on_preset_index_changed,
    )
    bpy.types.Scene.bmanga_line_preset_name = StringProperty(
        name="プリセット名",
        default="ラインプリセット",
        options={"SKIP_SAVE"},
    )
    _loaded_scene_pointers.clear()
    for scene in _iter_scenes():
        ensure_presets_loaded(scene)
    _append_handler("load_post", _on_load_post)
    _append_handler("save_pre", _on_save_pre)
    _append_handler("save_post", _on_save_post)
    _append_handler("save_post_fail", _on_save_post)


def unregister() -> None:
    _remove_handler("load_post", _on_load_post)
    _remove_handler("save_pre", _on_save_pre)
    _remove_handler("save_post", _on_save_post)
    _remove_handler("save_post_fail", _on_save_post)
    _saving_scene_snapshots.clear()
    _loaded_scene_pointers.clear()
    for attr in (
        "bmanga_line_preset_name",
        "bmanga_line_preset_index",
        "bmanga_line_presets",
    ):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
