"""問題メッシュ自動修復・Catmull-Clark向け四角面化の操作と画面."""

from __future__ import annotations

from dataclasses import dataclass
import textwrap

import bpy
from bpy.props import EnumProperty, StringProperty

from . import mesh_optimizer, registration
from .mesh_quad_repair_geometry import (
    QuadRepairCandidate,
    QuadRepairError,
    build_candidate,
    options_for_quality,
    validate_source_object,
)


QUALITY_SCENE_PROP = "bmanga_line_quad_repair_quality"
RESULT_SCENE_PROP = "bmanga_line_quad_repair_result"
ERROR_SCENE_PROP = "bmanga_line_quad_repair_error"

_QUALITY_ITEMS = (
    ("STANDARD", "標準", "形状を保ちながら実用的な密度の四角面へ再構成します"),
    ("CLOSE", "近接用", "近接表示向けに細かい四角面へ再構成します"),
)
_CONFIRM_FACE_COUNT = 250_000


@dataclass
class _Prepared:
    obj: bpy.types.Object
    old_mesh: bpy.types.Mesh
    candidate: QuadRepairCandidate


def _selected_meshes(context) -> list[bpy.types.Object]:
    result = []
    seen: set[int] = set()
    for obj in getattr(context, "selected_objects", ()) or ():
        if obj.type != "MESH" or obj.data is None:
            continue
        pointer = obj.as_pointer()
        if pointer in seen:
            continue
        seen.add(pointer)
        result.append(obj)
    return sorted(result, key=lambda item: item.name_full)


def _triangle_count(objects) -> int:
    total = 0
    for obj in objects:
        obj.data.calc_loop_triangles()
        total += len(obj.data.loop_triangles)
    return total


def _remove_mesh(mesh: bpy.types.Mesh | None) -> None:
    if mesh is not None and mesh.name in bpy.data.meshes and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _discard(prepared: list[_Prepared]) -> None:
    for item in prepared:
        _remove_mesh(item.candidate.mesh)


def _prepare(context, objects, quality, *, progress=None):
    from . import core

    options = options_for_quality(quality)
    prepared: list[_Prepared] = []
    total_faces = 0
    batch_limit = 8_000_000 if quality == "CLOSE" else 6_000_000
    try:
        for index, obj in enumerate(objects):
            try:
                if core.is_settings_locked(obj):
                    raise QuadRepairError("ライン設定のロックを解除してください")
                validate_source_object(obj)
                candidate = build_candidate(context, obj, options)
            except QuadRepairError as exc:
                raise QuadRepairError(f"{obj.name}: {exc}") from exc
            total_faces += candidate.stats.output_faces
            if total_faces > batch_limit:
                _remove_mesh(candidate.mesh)
                raise QuadRepairError(
                    f"一括候補が安全上限の{batch_limit:,}面を超えます"
                )
            prepared.append(_Prepared(obj, obj.data, candidate))
            if progress is not None:
                progress(index + 1)
    except Exception:
        _discard(prepared)
        raise
    return prepared


def _commit(prepared: list[_Prepared], quality: str) -> None:
    assignments = [
        (item.obj, item.old_mesh, item.candidate.mesh)
        for item in prepared
    ]
    mesh_optimizer.commit_candidate_meshes(
        assignments,
        f"QUAD_{quality}",
        mesh_name_suffix="QuadRepaired",
    )


def _summary(prepared: list[_Prepared]) -> str:
    stats = [item.candidate.stats for item in prepared]
    strong = sum(item.used_voxel_repair for item in stats)
    direct = len(stats) - strong
    output_faces = sum(item.output_faces for item in stats)
    repaired = sum(
        item.welded_vertices + item.removed_faces + item.removed_loose_elements
        for item in stats
    )
    return (
        f"四角面化 {len(stats)}件 / 通常 {direct}件 / 強力修復 {strong}件 / "
        f"出力 {output_faces:,}面 / 修復要素 {repaired:,}"
    )


def _set_result(scene, *, result: str = "", error: str = "") -> None:
    setattr(scene, RESULT_SCENE_PROP, result)
    setattr(scene, ERROR_SCENE_PROP, error)


class BMANGA_LINE_OT_auto_repair_quad_mesh(bpy.types.Operator):
    """選択メッシュを修復してCatmull-Clark向け四角面へ再構成する."""

    bl_idname = "bmanga_line.auto_repair_quad_mesh"
    bl_label = "選択メッシュを自動修復・四角面化"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and bool(_selected_meshes(context))

    def invoke(self, context, _event):
        face_count = _triangle_count(_selected_meshes(context))
        if face_count < _CONFIRM_FACE_COUNT:
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        objects = _selected_meshes(context)
        faces = _triangle_count(objects)
        self.layout.label(
            text=f"選択メッシュ {len(objects)}件 / 三角面換算 {faces:,}面",
            icon="INFO",
        )
        self.layout.label(text="全候補の検証合格後に一括確定します", icon="CHECKMARK")

    def execute(self, context):
        objects = _selected_meshes(context)
        quality = str(getattr(context.scene, QUALITY_SCENE_PROP, "STANDARD"))
        _set_result(context.scene)
        progress = context.window_manager
        progress.progress_begin(0, max(1, len(objects)))
        try:
            prepared = _prepare(
                context,
                objects,
                quality,
                progress=progress.progress_update,
            )
            _commit(prepared, quality)
            message = _summary(prepared)
        except QuadRepairError as exc:
            message = f"自動修復・四角面化を中止しました: {exc}"
            _set_result(context.scene, error=message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        except Exception as exc:  # noqa: BLE001
            message = f"自動修復・四角面化中にエラーが発生しました: {exc}"
            _set_result(context.scene, error=message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        finally:
            progress.progress_end()
        _set_result(context.scene, result=message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class BMANGA_LINE_PT_auto_repair_quad_mesh(bpy.types.Panel):
    bl_label = "問題メッシュ自動修復・四角面化"
    bl_idname = "BMANGA_LINE_PT_auto_repair_quad_mesh"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BMLiner"
    bl_order = 5

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    def draw(self, context):
        layout = self.layout
        layout.label(text="ライン反映前の静的メッシュが対象です", icon="INFO")
        layout.prop(context.scene, QUALITY_SCENE_PROP, expand=True)
        layout.operator("bmanga_line.auto_repair_quad_mesh", icon="MOD_REMESH")
        error = str(getattr(context.scene, ERROR_SCENE_PROP, "") or "")
        result = str(getattr(context.scene, RESULT_SCENE_PROP, "") or "")
        message = error or result
        icon = "ERROR" if error else "CHECKMARK"
        for index, line in enumerate(textwrap.wrap(message, width=34)):
            layout.label(text=line, icon=icon if index == 0 else "NONE")


_CLASSES = (
    BMANGA_LINE_OT_auto_repair_quad_mesh,
    BMANGA_LINE_PT_auto_repair_quad_mesh,
)


def register() -> None:
    for cls in _CLASSES:
        registration.register_class(cls)
    bpy.types.Scene.bmanga_line_quad_repair_quality = EnumProperty(
        name="品質",
        items=_QUALITY_ITEMS,
        default="STANDARD",
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.bmanga_line_quad_repair_result = StringProperty(
        default="",
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.bmanga_line_quad_repair_error = StringProperty(
        default="",
        options={"SKIP_SAVE"},
    )


def unregister() -> None:
    for name in (ERROR_SCENE_PROP, RESULT_SCENE_PROP, QUALITY_SCENE_PROP):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(_CLASSES):
        registration.unregister_class(cls)
