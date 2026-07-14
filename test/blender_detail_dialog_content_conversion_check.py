"""Blender 5.1.2実機: 旧GP／効果線／UIDのページ内実体変換。

生成した一時blendだけを使い、実作品の探索・読込・変更は行わない。

実行例::

    blender.exe --background --factory-startup \
      --python test/blender_detail_dialog_content_conversion_check.py
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
PAGE_ID = "p0001"


def _load_addon():
    name = "bmanga_content_conversion_test"
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module, name


def _new_drawing(layer, frame_number: int, offset: float, material_index: int) -> None:
    frame = layer.frames.new(frame_number)
    drawing = frame.drawing
    drawing.add_strokes([3])
    stroke = drawing.strokes[0]
    stroke.cyclic = True
    stroke.material_index = material_index
    values = (
        ((1.0 + offset, 2.0, 0.3), 0.5, 0.6, 0.1, (0.1, 0.2, 0.3, 0.4)),
        ((4.0 + offset, 5.0, 0.6), 0.7, 0.8, 0.2, (0.2, 0.3, 0.4, 0.5)),
        ((7.0 + offset, 8.0, 0.9), 0.9, 1.0, 0.3, (0.3, 0.4, 0.5, 0.6)),
    )
    for point, (position, radius, opacity, rotation, color) in zip(stroke.points, values):
        point.position = position
        point.radius = radius
        point.opacity = opacity
        point.rotation = rotation
        point.vertex_color = color


def _layer_snapshot(obj, layer) -> dict:
    world_matrix = _stored_world_matrix(obj)
    frames = []
    for frame in layer.frames:
        strokes = []
        for stroke in frame.drawing.strokes:
            strokes.append({
                "cyclic": bool(stroke.cyclic),
                "material_index": int(stroke.material_index),
                "points": [{
                    "world": [round(float(value), 5) for value in (world_matrix @ point.position)],
                    "radius": round(float(point.radius), 7),
                    "opacity": round(float(point.opacity), 7),
                    "rotation": round(float(point.rotation), 7),
                    "color": [round(float(value), 7) for value in point.vertex_color],
                } for point in stroke.points],
            })
        frames.append({"number": int(frame.frame_number), "strokes": strokes})
    return {
        "hide": bool(layer.hide),
        "lock": bool(layer.lock),
        "opacity": round(float(layer.opacity), 7),
        "blend": str(layer.blend_mode),
        "frames": frames,
    }


def _stored_world_matrix(obj):
    basis = obj.matrix_basis.copy()
    if obj.parent is None:
        return basis
    return _stored_world_matrix(obj.parent) @ obj.matrix_parent_inverse @ basis


def _prepare_work(scene, temp_root: Path, package: str) -> None:
    work = scene.bmanga_work
    work.loaded = True
    work.work_dir = str(temp_root / "GeneratedContentConversion.bmanga")
    work.detail_data_version = 0
    page = work.pages.add()
    page.id = PAGE_ID
    page.title = "1ページ"
    page.dir_rel = PAGE_ID
    work.active_page_index = 0
    outliner = sys.modules[f"{package}.utils.outliner_model"]
    outliner.ensure_page_collection(scene, PAGE_ID, "1ページ")


def _create_master_gp(scene, package: str) -> tuple[object, dict[str, dict]]:
    gp_utils = sys.modules[f"{package}.utils.gpencil"]
    gp_parent = sys.modules[f"{package}.utils.gp_layer_parenting"]
    gp_data = gp_utils.ensure_gpencil("bmanga_master_sketch_data")
    obj = bpy.data.objects.new("bmanga_master_sketch", gp_data)
    scene.collection.objects.link(obj)
    obj.location = (0.012, -0.007, 0.003)
    obj.rotation_euler = (0.0, 0.0, 0.13)
    obj.scale = (1.1, 0.9, 1.0)
    material = bpy.data.materials.new("GeneratedLegacyGpMaterial")
    gp_data.materials.append(material)

    group = gp_data.layer_groups.new("下描き")
    group.is_expanded = False
    group.hide = True
    group.lock = True
    line = gp_data.layers.new("線画", set_active=True)
    line.opacity = 0.42
    line.blend_mode = "MULTIPLY"
    _new_drawing(line, 3, 0.0, 0)
    _new_drawing(line, 9, 0.5, 0)
    gp_parent.set_parent_key(line, PAGE_ID)
    gp_data.layers.move_to_layer_group(line, group)

    tone = gp_data.layers.new("トーン", set_active=True)
    tone.hide = True
    tone.lock = True
    tone.opacity = 0.73
    _new_drawing(tone, 5, 2.0, 0)
    gp_parent.set_parent_key(tone, PAGE_ID)

    mask = gp_data.layers.new("__bmanga_mask", set_active=False)
    _new_drawing(mask, 1, -2.0, 0)
    bpy.context.view_layer.update()
    return (
        obj,
        {
            "線画": _layer_snapshot(obj, line),
            "トーン": _layer_snapshot(obj, tone),
        },
        _layer_snapshot(obj, mask),
    )


def _create_legacy_effect(scene, package: str):
    gp_utils = sys.modules[f"{package}.utils.gpencil"]
    gp_parent = sys.modules[f"{package}.utils.gp_layer_parenting"]
    effect_core = sys.modules[f"{package}.core.effect_line"]
    data = gp_utils.ensure_gpencil("BManga_EffectLines_data")
    obj = bpy.data.objects.new("BManga_EffectLines", data)
    scene.collection.objects.link(obj)
    material = bpy.data.materials.new("GeneratedLegacyEffectMaterial")
    data.materials.append(material)
    layer = data.layers.new("集中線", set_active=True)
    layer.lock = True
    layer.opacity = 0.64
    layer.blend_mode = "ADD"
    _new_drawing(layer, 1, 4.0, 0)
    _new_drawing(layer, 7, 6.0, 0)
    gp_parent.set_parent_key(layer, PAGE_ID)
    params = effect_core.effect_params_to_dict(scene.bmanga_effect_line_params)
    meta = {
        "x": 18.0,
        "y": 24.0,
        "w": 54.0,
        "h": 61.0,
        "center_x": 44.0,
        "center_y": 52.0,
        "seed": 27,
        "params": params,
        "link_id": "generated-effect-link",
        "free_transform": {
            "center_x": 44.0,
            "center_y": 52.0,
            "rotation_deg": 8.0,
            "scale_x": 1.1,
            "scale_y": 0.9,
        },
    }
    data["bmanga_effect_line_meta"] = json.dumps({"集中線": meta}, ensure_ascii=False)
    bpy.context.view_layer.update()
    return obj, meta, _layer_snapshot(obj, layer)


def _add_legacy_links(scene) -> None:
    gp_uid = "gp:ptr_abc123"
    effect_uid = "effect:ptr_def456"
    scene["bmanga_layer_link_groups"] = json.dumps(
        {gp_uid: "generated-link-group", effect_uid: "generated-link-group"},
        ensure_ascii=False,
    )
    scene["bmanga_detail_legacy_uid_map"] = json.dumps({
        gp_uid: {"kind": "gp", "object": "bmanga_master_sketch", "layer": "線画"},
        effect_uid: {"kind": "effect", "object": "BManga_EffectLines", "layer": "集中線"},
    }, ensure_ascii=False)


def _create_legacy_page(path: Path, temp_root: Path, package: str):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    _prepare_work(scene, temp_root, package)
    _master, gp_snapshots, mask_snapshot = _create_master_gp(scene, package)
    _effect, effect_meta, effect_snapshot = _create_legacy_effect(scene, package)
    _add_legacy_links(scene)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=False)
    return gp_snapshots, mask_snapshot, effect_meta, effect_snapshot


def _make_unresolved_variant(source: Path, destination: Path) -> None:
    shutil.copy2(source, destination)
    bpy.ops.wm.open_mainfile(filepath=str(destination), load_ui=False)
    scene = bpy.context.scene
    del scene["bmanga_detail_legacy_uid_map"]
    bpy.ops.wm.save_as_mainfile(filepath=str(destination), compress=False)


def _make_mask_variant(source: Path, destination: Path) -> None:
    shutil.copy2(source, destination)
    bpy.ops.wm.open_mainfile(filepath=str(destination), load_ui=False)
    obj = bpy.data.objects["bmanga_master_sketch"]
    obj.hide_viewport = False
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    target = obj.data.layers["トーン"]
    obj.data.layers.active = target
    result = bpy.ops.grease_pencil.layer_mask_add(name="線画")
    assert result == {"FINISHED"}, result
    bpy.ops.wm.save_as_mainfile(filepath=str(destination), compress=False)


def _assert_preflight_blocks(migration, unresolved: Path, masked: Path) -> None:
    result = migration.inspect_page(PAGE_ID, unresolved)
    assert "unresolved_pointer_uid" in {issue.code for issue in result.issues}
    result = migration.inspect_page(PAGE_ID, masked)
    assert "unsupported_gp_mask" in {issue.code for issue in result.issues}


def _assert_converted(
    package: str,
    gp_snapshots: dict,
    mask_snapshot: dict,
    effect_meta: dict,
    effect_snapshot: dict,
) -> None:
    layer_model = sys.modules[f"{package}.utils.layer_object_model"]
    layer_uid = sys.modules[f"{package}.utils.layer_uid"]
    effect_object = sys.modules[f"{package}.utils.effect_line_object"]
    object_naming = sys.modules[f"{package}.utils.object_naming"]
    migration_manifest = importlib.import_module(
        f"{package}.io.detail_data_migration_manifest"
    )
    objects = list(layer_model.iter_layer_objects())
    assert len([obj for obj in objects if layer_model.layer_kind(obj) == "gp"]) == 2
    effects = [obj for obj in objects if layer_model.layer_kind(obj) == "effect"]
    assert len(effects) == 1
    assert bpy.data.objects.get("bmanga_master_sketch") is None
    assert bpy.data.objects.get("BManga_EffectLines") is None

    by_title = {layer_model.display_title(obj): obj for obj in objects}
    for title, expected in gp_snapshots.items():
        obj = by_title[title]
        content = layer_model.content_layer(obj)
        actual = _layer_snapshot(obj, content)
        _normalize_equivalent_world_points(actual, expected)
        assert actual == expected, json.dumps(
            {"title": title, "expected": expected, "actual": actual},
            ensure_ascii=False,
            indent=2,
        )
        assert any(
            mat.name == "GeneratedLegacyGpMaterial"
            or mat.name.startswith("GeneratedLegacyGpMaterial__")
            for mat in obj.data.materials
        )
        assert len(obj.data.layer_groups) == 0
        mask = obj.data.layers.get("__bmanga_mask")
        assert mask is not None
        actual_mask = _layer_snapshot(obj, mask)
        _normalize_equivalent_world_points(actual_mask, mask_snapshot)
        assert actual_mask == mask_snapshot
        layer_uid.make_managed_uid("gp", layer_model.stable_id(obj))

    work = bpy.context.scene.bmanga_work
    folder = next(item for item in work.layer_folders if item.title == "下描き")
    folder_collection = next(
        coll for coll in bpy.data.collections
        if str(coll.get("bmanga_id", "") or "") == folder.id
    )
    assert folder.expanded is False
    assert folder_collection.hide_viewport is True
    assert folder_collection.hide_render is True
    assert folder_collection.hide_select is True
    assert layer_model.folder_id(by_title["線画"]) == folder.id
    assert layer_model.z_index(by_title["トーン"]) > layer_model.z_index(by_title["線画"])

    effect = effects[0]
    actual_effect = _layer_snapshot(effect, layer_model.content_layer(effect))
    _normalize_equivalent_world_points(actual_effect, effect_snapshot)
    assert actual_effect == effect_snapshot
    assert any(
        mat.name == "GeneratedLegacyEffectMaterial"
        or mat.name.startswith("GeneratedLegacyEffectMaterial__")
        for mat in effect.data.materials
    )
    stored = json.loads(effect.data["bmanga_effect_line_meta"])["content"]
    for key in ("x", "y", "w", "h", "center_x", "center_y", "seed", "link_id"):
        assert stored[key] == effect_meta[key], key
    assert stored["params"] == effect_meta["params"]
    assert stored["free_transform"] == effect_meta["free_transform"]
    display = effect_object.find_effect_display_object(effect)
    assert display is not None
    assert len(display.data.vertices) > 0
    assert display.get(effect_object.PROP_EFFECT_CONTROLLER_ID) == layer_model.stable_id(effect)

    raw_links = json.loads(bpy.context.scene["bmanga_layer_link_groups"])
    assert len(raw_links) == 2
    assert set(raw_links.values()) == {"generated-link-group"}
    assert all(layer_uid.is_valid_uid(uid) for uid in raw_links)
    assert not any(layer_uid.is_legacy_pointer_uid(uid) for uid in raw_links)
    assert "bmanga_detail_legacy_uid_map" not in bpy.context.scene

    saved_manifest = migration_manifest.load_manifest(bpy.context.scene)
    assert saved_manifest["managedIds"]["gp"] == sorted(
        layer_model.stable_id(obj)
        for obj in objects
        if layer_model.layer_kind(obj) == "gp"
    )
    assert saved_manifest["managedIds"]["effect"] == [layer_model.stable_id(effect)]
    assert [item["id"] for item in saved_manifest["folders"]] == [folder.id]
    assert saved_manifest["folders"][0]["expanded"] is False
    assert saved_manifest["folders"][0]["visible"] is False
    assert saved_manifest["folders"][0]["locked"] is True
    assert saved_manifest["linkMap"] == raw_links
    migration_manifest.validate_manifest(
        bpy.context.scene,
        PAGE_ID,
        layer_model,
        object_naming,
        "bmanga_layer_link_groups",
    )


def _normalize_equivalent_world_points(actual: dict, expected: dict) -> None:
    """32-bit GP座標の行列変換で生じる丸め誤差だけを許容する。"""
    assert len(actual["frames"]) == len(expected["frames"])
    for actual_frame, expected_frame in zip(actual["frames"], expected["frames"]):
        assert len(actual_frame["strokes"]) == len(expected_frame["strokes"])
        for actual_stroke, expected_stroke in zip(
            actual_frame["strokes"], expected_frame["strokes"]
        ):
            assert len(actual_stroke["points"]) == len(expected_stroke["points"])
            for actual_point, expected_point in zip(
                actual_stroke["points"], expected_stroke["points"]
            ):
                deltas = [
                    abs(a - e)
                    for a, e in zip(actual_point["world"], expected_point["world"])
                ]
                assert max(deltas, default=0.0) <= 0.00002, deltas
                actual_point["world"] = expected_point["world"]


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_content_conversion_"))
    addon = None
    succeeded = False
    try:
        addon, package = _load_addon()
        legacy = temp_root / "generated_legacy_page.blend"
        unresolved = temp_root / "generated_unresolved_page.blend"
        masked = temp_root / "generated_masked_page.blend"
        staged = temp_root / "generated_staged_page.blend"
        gp_snapshots, mask_snapshot, effect_meta, effect_snapshot = _create_legacy_page(
            legacy, temp_root, package
        )
        _make_unresolved_variant(legacy, unresolved)
        _make_mask_variant(legacy, masked)

        migration = importlib.import_module(f"{package}.io.detail_data_blender_migration")
        _assert_preflight_blocks(migration, unresolved, masked)
        inspection = migration.inspect_page(PAGE_ID, legacy)
        assert not inspection.issues, inspection.issues
        assert inspection.facts["legacyGpCount"] == 2
        assert inspection.facts["legacyEffectCount"] == 1
        assert inspection.facts["legacyFolderCount"] == 1
        shutil.copy2(legacy, staged)
        migration.convert_page(SimpleNamespace(
            page_id=PAGE_ID,
            staged_path=staged,
            inspection_facts=inspection.facts,
        ))
        assert migration.validate_page(PAGE_ID, staged)
        bpy.ops.wm.open_mainfile(filepath=str(staged), load_ui=False)
        _assert_converted(
            package, gp_snapshots, mask_snapshot, effect_meta, effect_snapshot
        )
        succeeded = True
        print("DETAIL_DIALOG_CONTENT_CONVERSION_CHECK_OK")
    finally:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if addon is not None:
            addon.unregister()
        if succeeded:
            shutil.rmtree(temp_root, ignore_errors=False)
        else:
            print(f"FAILED_TEMP_ROOT={temp_root}")


if __name__ == "__main__":
    main()
