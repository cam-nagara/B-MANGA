"""B-MANGA Liner 購入素材メッシュ最適化の操作と画面."""

from __future__ import annotations

from dataclasses import dataclass
import textwrap

import bpy
from bpy.props import EnumProperty, StringProperty

from . import registration
from .mesh_optimizer_geometry import (
    CandidateResult,
    OptimizeOptions,
    UnsafeMeshError,
    build_candidate,
    validate_source_object,
)


OPTIMIZED_PROP = "bml_surface_mesh_optimized"
OPTIMIZED_QUALITY_PROP = "bml_surface_mesh_optimized_quality"
QUALITY_SCENE_PROP = "bmanga_line_mesh_optimize_quality"
RESULT_SCENE_PROP = "bmanga_line_mesh_optimize_result"
ERROR_SCENE_PROP = "bmanga_line_mesh_optimize_error"

_QUALITY_ITEMS = (
    ("STANDARD", "標準", "曲面部分を1段階細分化します"),
    ("CLOSE", "近接用", "曲面部分を2段階細分化します"),
)
_CONFIRM_FACE_COUNT = 250_000
_MISSING = object()
_LEGACY_REPAIR_PROPS = (
    "bml_soup_mesh_line_repaired",
    "bml_soup_mesh_line_repaired_welded_verts",
)
_MUTATED_OBJECT_PROPS = (
    OPTIMIZED_PROP,
    OPTIMIZED_QUALITY_PROP,
    *_LEGACY_REPAIR_PROPS,
    "bml_sheet_mesh",
    "bml_sheet_signature",
    "bml_pending_line_update_targets",
    "bml_pending_line_create_targets",
    "bml_pending_line_visual_targets",
    "bml_reflected_fp_outline",
    "bml_reflected_fp_inner",
    "bml_reflected_fp_intersection",
    "bml_reflected_fp_selection",
)


@dataclass
class _Prepared:
    obj: bpy.types.Object
    old_mesh: bpy.types.Mesh
    candidate: CandidateResult


@dataclass
class _CommitSnapshot:
    auto_subdivision: bool
    properties: dict[str, object]


def is_optimized(obj: bpy.types.Object | None) -> bool:
    return bool(obj is not None and obj.get(OPTIMIZED_PROP, False))


def _selected_meshes(context) -> list[bpy.types.Object]:
    objects: list[bpy.types.Object] = []
    seen: set[int] = set()
    for obj in getattr(context, "selected_objects", ()) or ():
        if obj.type != "MESH" or obj.data is None:
            continue
        pointer = obj.as_pointer()
        if pointer in seen:
            continue
        seen.add(pointer)
        objects.append(obj)
    return sorted(objects, key=lambda item: item.name_full)


def _options(scene) -> OptimizeOptions:
    quality = str(getattr(scene, QUALITY_SCENE_PROP, "STANDARD"))
    if quality == "CLOSE":
        return OptimizeOptions(passes=2, max_output_faces=4_000_000)
    return OptimizeOptions(passes=1, max_output_faces=2_000_000)


def _triangle_count(objects) -> int:
    total = 0
    for obj in objects:
        obj.data.calc_loop_triangles()
        total += len(obj.data.loop_triangles)
    return total


def _remove_candidate(mesh: bpy.types.Mesh | None) -> None:
    if mesh is not None and mesh.name in bpy.data.meshes and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _discard_prepared(prepared: list[_Prepared]) -> None:
    for item in prepared:
        _remove_candidate(item.candidate.mesh)


def _prepare(objects, options, *, progress=None, max_batch_faces=6_000_000):
    prepared: list[_Prepared] = []
    unchanged = 0
    output_faces = 0
    try:
        for index, obj in enumerate(objects):
            try:
                validate_source_object(obj)
                result = build_candidate(
                    obj.data,
                    f"{obj.data.name}_BML_OptimizedCandidate",
                    options,
                )
            except UnsafeMeshError as exc:
                raise UnsafeMeshError(f"{obj.name}: {exc}") from exc
            if result.mesh is None:
                unchanged += 1
                if progress is not None:
                    progress(index + 1)
                continue
            output_faces += len(result.mesh.polygons)
            if output_faces > max_batch_faces:
                _remove_candidate(result.mesh)
                raise UnsafeMeshError(
                    f"一括候補が安全上限の{max_batch_faces:,}面を超えます"
                )
            prepared.append(_Prepared(obj, obj.data, result))
            if progress is not None:
                progress(index + 1)
    except Exception:
        _discard_prepared(prepared)
        raise
    return prepared, unchanged


def _clear_line_state(obj: bpy.types.Object) -> None:
    from . import mesh_fingerprint, plane_filter, update_state

    for key in _LEGACY_REPAIR_PROPS:
        if key in obj:
            del obj[key]
    mesh_fingerprint.clear(obj)
    plane_filter.clear_cache(obj)
    update_state.mark_pending(obj)


def _disable_line_only_smoothing(obj: bpy.types.Object) -> None:
    from . import core

    core._set_bool_setting_without_update(
        obj,
        "auto_subdivision_for_midpoint",
        False,
    )


def _capture_commit_state(obj: bpy.types.Object) -> _CommitSnapshot:
    return _CommitSnapshot(
        auto_subdivision=bool(
            getattr(obj.bmanga_line_settings, "auto_subdivision_for_midpoint", False)
        ),
        properties={
            key: obj[key] if key in obj else _MISSING
            for key in _MUTATED_OBJECT_PROPS
        },
    )


def _restore_commit_state(obj: bpy.types.Object, snapshot: _CommitSnapshot) -> None:
    from . import core

    for key, value in snapshot.properties.items():
        if value is _MISSING:
            if key in obj:
                del obj[key]
        else:
            obj[key] = value
    core._set_bool_setting_without_update(
        obj,
        "auto_subdivision_for_midpoint",
        snapshot.auto_subdivision,
    )


def _commit(prepared: list[_Prepared], quality: str) -> None:
    assigned: list[_Prepared] = []
    snapshots = {
        item.obj.as_pointer(): _capture_commit_state(item.obj)
        for item in prepared
    }
    try:
        for item in prepared:
            candidate = item.candidate.mesh
            if candidate is None:
                continue
            item.obj.data = candidate
            assigned.append(item)
        for item in assigned:
            item.obj[OPTIMIZED_PROP] = True
            item.obj[OPTIMIZED_QUALITY_PROP] = quality
            _disable_line_only_smoothing(item.obj)
            _clear_line_state(item.obj)
    except Exception:
        for item in assigned:
            item.obj.data = item.old_mesh
            _restore_commit_state(
                item.obj,
                snapshots[item.obj.as_pointer()],
            )
        _discard_prepared(prepared)
        raise

    old_meshes = {item.old_mesh for item in assigned}
    for old_mesh in old_meshes:
        old_name = old_mesh.name
        users = [item for item in assigned if item.old_mesh == old_mesh]
        if old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)
        for index, item in enumerate(users):
            suffix = "" if len(users) == 1 else f"_{index + 1}"
            item.obj.data.name = f"{old_name}_Optimized{suffix}"


def _summary(prepared: list[_Prepared], unchanged: int) -> str:
    stats = [item.candidate.stats for item in prepared]
    added_vertices = sum(max(0, item.output_vertices - item.source_vertices) for item in stats)
    removed_faces = sum(
        item.removed_degenerate_faces + item.removed_duplicate_faces for item in stats
    )
    return (
        f"最適化 {len(prepared)}件 / 変更不要 {unchanged}件 / "
        f"追加頂点 {added_vertices:,} / 除去面 {removed_faces:,}"
    )


def _set_result(scene, *, result: str = "", error: str = "") -> None:
    setattr(scene, RESULT_SCENE_PROP, result)
    setattr(scene, ERROR_SCENE_PROP, error)


class BMANGA_LINE_OT_optimize_purchased_mesh(bpy.types.Operator):
    """選択中の購入素材を最終レンダリング用メッシュへ最適化する."""

    bl_idname = "bmanga_line.optimize_purchased_mesh"
    bl_label = "選択メッシュを最適化"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and bool(_selected_meshes(context))

    def invoke(self, context, _event):
        face_count = _triangle_count(_selected_meshes(context))
        if face_count < _CONFIRM_FACE_COUNT:
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self, width=440)

    def draw(self, context):
        objects = _selected_meshes(context)
        faces = _triangle_count(objects)
        self.layout.label(text=f"選択メッシュ {len(objects)}件 / 三角面換算 {faces:,}面", icon="INFO")
        self.layout.label(text="検証合格後に選択対象を一括確定します", icon="CHECKMARK")

    def execute(self, context):
        objects = _selected_meshes(context)
        _set_result(context.scene)
        progress = context.window_manager
        progress.progress_begin(0, max(1, len(objects)))
        try:
            quality = str(getattr(context.scene, QUALITY_SCENE_PROP, "STANDARD"))
            batch_limit = 8_000_000 if quality == "CLOSE" else 6_000_000
            prepared, unchanged = _prepare(
                objects,
                _options(context.scene),
                progress=progress.progress_update,
                max_batch_faces=batch_limit,
            )
            if prepared:
                _commit(prepared, quality)
            message = _summary(prepared, unchanged)
        except UnsafeMeshError as exc:
            message = f"最適化を中止しました: {exc}"
            _set_result(context.scene, error=message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        except Exception as exc:  # noqa: BLE001
            message = f"最適化中にエラーが発生しました: {exc}"
            _set_result(context.scene, error=message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        finally:
            progress.progress_end()
        _set_result(context.scene, result=message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class BMANGA_LINE_PT_mesh_optimizer(bpy.types.Panel):
    bl_label = "購入素材メッシュ最適化"
    bl_idname = "BMANGA_LINE_PT_mesh_optimizer"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BMLiner"
    bl_order = 4

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    def draw(self, context):
        layout = self.layout
        layout.label(text="ライン反映前の静的メッシュが対象です", icon="INFO")
        layout.prop(context.scene, QUALITY_SCENE_PROP, expand=True)
        layout.operator("bmanga_line.optimize_purchased_mesh", icon="MOD_SUBSURF")
        error = str(getattr(context.scene, ERROR_SCENE_PROP, "") or "")
        result = str(getattr(context.scene, RESULT_SCENE_PROP, "") or "")
        message = error or result
        icon = "ERROR" if error else "CHECKMARK"
        for index, line in enumerate(textwrap.wrap(message, width=34)):
            layout.label(text=line, icon=icon if index == 0 else "NONE")


_CLASSES = (
    BMANGA_LINE_OT_optimize_purchased_mesh,
    BMANGA_LINE_PT_mesh_optimizer,
)


def register() -> None:
    for cls in _CLASSES:
        registration.register_class(cls)
    bpy.types.Scene.bmanga_line_mesh_optimize_quality = EnumProperty(
        name="品質",
        items=_QUALITY_ITEMS,
        default="STANDARD",
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.bmanga_line_mesh_optimize_result = StringProperty(
        default="", options={"SKIP_SAVE"}
    )
    bpy.types.Scene.bmanga_line_mesh_optimize_error = StringProperty(
        default="", options={"SKIP_SAVE"}
    )


def unregister() -> None:
    for name in (ERROR_SCENE_PROP, RESULT_SCENE_PROP, QUALITY_SCENE_PROP):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(_CLASSES):
        registration.unregister_class(cls)
