"""Blender 実機用: 既存実体の保持と標準操作の書き戻し確認."""

from __future__ import annotations

import importlib.util
import math
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _new_work(work_dir: Path):
    result = bpy.ops.bmanga.work_new(filepath=str(work_dir))
    assert result == {"FINISHED"}, result
    work = bpy.context.scene.bmanga_work
    assert work is not None and len(work.pages) > 0
    return work, work.pages[0]


def _add_balloon(page, balloon_id: str):
    entry = page.balloons.add()
    entry.id = balloon_id
    entry.title = balloon_id
    entry.shape = "rect"
    entry.x_mm = 20.0
    entry.y_mm = 30.0
    entry.width_mm = 40.0
    entry.height_mm = 20.0
    entry.visible = True
    return entry


def _first_bezier_x(obj) -> float:
    for spline in obj.data.splines:
        if str(getattr(spline, "type", "")) == "BEZIER" and len(spline.bezier_points) > 0:
            return float(spline.bezier_points[0].co.x)
        if len(spline.points) > 0:
            return float(spline.points[0].co.x)
    raise AssertionError("curve point not found")


def _move_first_curve_point_x(obj, delta: float) -> None:
    for spline in obj.data.splines:
        if str(getattr(spline, "type", "")) == "BEZIER" and len(spline.bezier_points) > 0:
            spline.bezier_points[0].co.x += delta
            return
        if len(spline.points) > 0:
            spline.points[0].co.x += delta
            return
    raise AssertionError("curve point not found")


def _assert_orphan_balloon_is_preserved(scene, page) -> None:
    from bmanga_dev.utils import balloon_curve_object, object_naming, object_preserve

    entry = _add_balloon(page, "preserve_orphan_balloon")
    obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
    assert obj is not None
    original_name = obj.name
    page.balloons.remove(len(page.balloons) - 1)
    balloon_curve_object.cleanup_orphan_balloon_objects(scene)
    kept = bpy.data.objects.get(original_name)
    assert kept is not None, "orphan balloon object was deleted"
    assert object_preserve.is_preserved(kept), "orphan balloon object was not preserved"
    assert not bool(kept.get(object_naming.PROP_MANAGED, False)), "preserved object is still managed"
    balloon_curve_object.remove_balloon_objects_by_id(str(entry.id))
    assert bpy.data.objects.get(original_name) is kept, "explicit delete removed preserved balloon"


def _assert_preserved_balloon_band_meshes_survive_explicit_delete() -> None:
    from bmanga_dev.utils import balloon_curve_object, balloon_fill_mesh, balloon_line_mesh, object_preserve

    balloon_id = "preserved_band_mesh_skip"
    line_mesh = bpy.data.meshes.new("preserved_band_line_mesh")
    line_obj = bpy.data.objects.new(f"{balloon_line_mesh.BALLOON_LINE_MESH_NAME_PREFIX}{balloon_id}", line_mesh)
    fill_mesh = bpy.data.meshes.new("preserved_band_fill_mesh")
    fill_obj = bpy.data.objects.new(f"{balloon_fill_mesh.BALLOON_FILL_MESH_NAME_PREFIX}{balloon_id}", fill_mesh)
    bpy.context.scene.collection.objects.link(line_obj)
    bpy.context.scene.collection.objects.link(fill_obj)
    object_preserve.preserve_object(line_obj, "test")
    object_preserve.preserve_object(fill_obj, "test")
    balloon_curve_object.remove_balloon_objects_by_id(balloon_id)
    assert bpy.data.objects.get(line_obj.name) is line_obj, "explicit delete removed preserved line mesh"
    assert bpy.data.objects.get(fill_obj.name) is fill_obj, "explicit delete removed preserved fill mesh"


def _assert_wrong_type_balloon_is_preserved(scene, page) -> None:
    from bmanga_dev.utils import balloon_curve_object, object_naming, object_preserve

    entry = _add_balloon(page, "wrong_type_balloon")
    mesh = bpy.data.meshes.new("wrong_type_balloon_mesh")
    legacy = bpy.data.objects.new("balloon_wrong_type_balloon", mesh)
    bpy.context.scene.collection.objects.link(legacy)
    object_naming.stamp_identity(
        legacy,
        kind="balloon",
        bmanga_id=entry.id,
        title="旧フキダシ",
        z_index=10,
        parent_key=str(page.id),
    )
    obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
    assert obj is not None and obj.type == "CURVE", "new balloon curve was not created"
    assert bpy.data.objects.get(legacy.name) is legacy, "wrong type balloon object was deleted"
    assert object_preserve.is_preserved(legacy), "wrong type balloon object was not preserved"


def _assert_explicit_balloon_delete_still_removes_current_object(scene, page) -> None:
    from bmanga_dev.operators import balloon_op
    from bmanga_dev.utils import balloon_curve_object

    entry = _add_balloon(page, "explicit_delete_balloon")
    obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
    assert obj is not None
    obj_name = obj.name
    balloon_op._delete_balloon_by_id(bpy.context, str(page.id), str(entry.id))
    assert bpy.data.objects.get(obj_name) is None, "explicit balloon delete did not remove current object"
    assert all(str(getattr(e, "id", "") or "") != "explicit_delete_balloon" for e in page.balloons)


def _assert_preserved_object_does_not_write_back(scene, page) -> None:
    from bmanga_dev.utils import balloon_curve_object, object_preserve, object_state_sync

    entry = _add_balloon(page, "preserved_writeback_skip")
    obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
    assert obj is not None
    object_preserve.preserve_object(obj, "test")
    old_x = float(entry.x_mm)
    obj.location.x += 0.5
    obj.scale.x = 3.0
    assert not object_state_sync.sync_from_blender_object(scene, obj)
    assert abs(float(entry.x_mm) - old_x) < 1.0e-7, "preserved object wrote back to entry"


def _assert_legacy_text_is_preserved(scene, work, page) -> None:
    from bmanga_dev.utils import object_naming, object_preserve, text_real_object

    entry = page.texts.add()
    entry.id = "legacy_text_keep"
    entry.body = "旧テキスト"
    entry.x_mm = 10.0
    entry.y_mm = 20.0
    entry.width_mm = 30.0
    entry.height_mm = 12.0
    legacy = bpy.data.objects.new(f"text_{entry.id}", None)
    bpy.context.scene.collection.objects.link(legacy)
    object_naming.stamp_identity(
        legacy,
        kind="text",
        bmanga_id=entry.id,
        title="旧テキスト",
        z_index=10,
        parent_key=str(page.id),
    )
    text_real_object.sync_all_text_real_objects(scene, work)
    assert bpy.data.objects.get(legacy.name) is legacy, "legacy text object was deleted"
    assert object_preserve.is_preserved(legacy), "legacy text object was not preserved"
    new_obj = text_real_object.find_text_object(str(page.id), str(entry.id))
    assert new_obj is not None and new_obj.type == "MESH", "new text mesh was not created"


def _assert_legacy_plane_is_preserved(scene) -> None:
    from bmanga_dev.utils import empty_layer_object, object_preserve

    mesh = bpy.data.meshes.new("text_mesh_legacy_plane")
    legacy = bpy.data.objects.new("text_plane_legacy_plane", mesh)
    bpy.context.scene.collection.objects.link(legacy)
    empty_layer_object.cleanup_legacy_plane_objects()
    assert bpy.data.objects.get(legacy.name) is legacy, "legacy plane object was deleted"
    assert bpy.data.meshes.get(mesh.name) is mesh, "legacy plane mesh was deleted"
    assert object_preserve.is_preserved(legacy), "legacy plane object was not preserved"


def _assert_effect_source_is_preserved(scene) -> None:
    from bmanga_dev.utils import effect_line_object, object_naming, object_preserve

    controller = bpy.data.objects.new("effect_controller_preserve", None)
    bpy.context.scene.collection.objects.link(controller)
    object_naming.stamp_identity(
        controller,
        kind="effect",
        bmanga_id="effect_source_preserve",
        title="効果線",
        z_index=10,
        parent_key="",
    )
    source = effect_line_object.ensure_effect_frame_source_object(
        scene=scene,
        controller_obj=controller,
        outline_mm=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
    )
    assert source is not None
    source_name = source.name
    effect_line_object.preserve_effect_frame_source_object(controller)
    kept = bpy.data.objects.get(source_name)
    assert kept is source, "effect source object was deleted"
    assert object_preserve.is_preserved(kept), "effect source object was not preserved"


def _assert_old_balloon_curve_is_not_rebuilt(scene, page) -> None:
    from bmanga_dev.utils import balloon_curve_object, balloon_curve_source_state

    entry = _add_balloon(page, "old_curve_keep")
    obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
    assert obj is not None
    if balloon_curve_source_state.PROP_BASE_SNAPSHOT in obj:
        del obj[balloon_curve_source_state.PROP_BASE_SNAPSHOT]
    if balloon_curve_source_state.PROP_SOURCE_STATE in obj:
        del obj[balloon_curve_source_state.PROP_SOURCE_STATE]
    _move_first_curve_point_x(obj, 0.0123)
    before_x = _first_bezier_x(obj)
    entry.width_mm += 15.0
    balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
    after_x = _first_bezier_x(obj)
    assert abs(after_x - before_x) < 1.0e-7, "old balloon curve was rebuilt"
    assert balloon_curve_source_state.detect_state(obj) == balloon_curve_source_state.STATE_FREEFORM


def _assert_balloon_transform_writeback(scene, page) -> None:
    from bmanga_dev.utils import balloon_curve_object, balloon_object_writeback

    entry = _add_balloon(page, "transform_writeback_balloon")
    obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
    assert obj is not None
    obj.location.x += 0.025
    obj.location.y += 0.015
    obj.rotation_euler[2] = math.radians(12.0)
    obj.scale.x = 1.5
    obj.scale.y = 0.5
    assert balloon_object_writeback.sync_entry_transform_from_object(scene, obj)
    assert abs(entry.width_mm - 60.0) < 1.0e-4, entry.width_mm
    assert abs(entry.height_mm - 10.0) < 1.0e-4, entry.height_mm
    assert abs(entry.rotation_deg - 12.0) < 1.0e-4, entry.rotation_deg
    assert abs(abs(obj.scale.x) - 1.0) < 1.0e-6
    assert abs(abs(obj.scale.y) - 1.0) < 1.0e-6


def _assert_image_transform_writeback(scene, work, page) -> None:
    from bmanga_dev.utils import empty_layer_object, image_real_object

    entry = scene.bmanga_image_layers.add()
    entry.id = "image_transform_writeback"
    entry.title = "画像"
    entry.parent_kind = "page"
    entry.parent_key = str(page.id)
    entry.x_mm = 10.0
    entry.y_mm = 12.0
    entry.width_mm = 20.0
    entry.height_mm = 10.0
    obj = image_real_object.ensure_image_real_object(scene=scene, entry=entry, page=page)
    assert obj is not None
    obj.location.x += 0.020
    obj.rotation_euler[2] = math.radians(8.0)
    obj.scale.x = 2.0
    assert empty_layer_object.sync_entry_position_from_object(scene, obj)
    assert abs(entry.width_mm - 40.0) < 1.0e-4, entry.width_mm
    assert abs(entry.rotation_deg - 8.0) < 1.0e-4, entry.rotation_deg


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_object_preservation_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        work, page = _new_work(temp_root / "ObjectPreservation.bmanga")
        scene = bpy.context.scene
        _assert_orphan_balloon_is_preserved(scene, page)
        _assert_preserved_balloon_band_meshes_survive_explicit_delete()
        _assert_wrong_type_balloon_is_preserved(scene, page)
        _assert_explicit_balloon_delete_still_removes_current_object(scene, page)
        _assert_preserved_object_does_not_write_back(scene, page)
        _assert_legacy_text_is_preserved(scene, work, page)
        _assert_legacy_plane_is_preserved(scene)
        _assert_effect_source_is_preserved(scene)
        _assert_old_balloon_curve_is_not_rebuilt(scene, page)
        _assert_balloon_transform_writeback(scene, page)
        _assert_image_transform_writeback(scene, work, page)
        print("BMANGA_OBJECT_PRESERVATION_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
