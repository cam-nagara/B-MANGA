"""Blender 5.1実機: 固定対象解決とGPキャンセル復元を生成データで検証する。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_detail_target_state_bridge"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _make_layer_object(addon, kind: str, stable_id: str):
    gpencil = importlib.import_module(f"{MODULE_NAME}.utils.gpencil")
    naming = importlib.import_module(f"{MODULE_NAME}.utils.object_naming")
    data = gpencil.ensure_gpencil(f"Bridge_{stable_id}_Data")
    layer = gpencil.ensure_layer(data, "content")
    obj = bpy.data.objects.new(f"Bridge_{stable_id}", data)
    bpy.context.scene.collection.objects.link(obj)
    naming.stamp_identity(
        obj,
        kind=kind,
        bmanga_id=stable_id,
        title="テストレイヤー",
        z_index=10,
        parent_key="p0001",
    )
    return obj, layer


def _add_probe_stroke(addon, layer, x_value: float):
    gpencil = importlib.import_module(f"{MODULE_NAME}.utils.gpencil")
    frame = gpencil.ensure_active_frame(layer, frame_number=1)
    drawing = frame.drawing
    ok = gpencil.add_stroke_to_drawing(
        drawing,
        ((x_value, 0.0, 0.0), (x_value + 1.0, 1.0, 0.0)),
        radii=(0.25, 0.5),
        opacities=(0.75, 0.5),
        cyclic=True,
    )
    assert ok
    return drawing


def _check_stack_resolution(addon, gp_obj):
    bridge = importlib.import_module(f"{MODULE_NAME}.utils.detail_target_resolver")
    stack = bpy.context.scene.bmanga_layer_stack
    item = stack.add()
    item.kind = "gp"
    item.key = "gp_bridge"
    item.label = "テストレイヤー"
    target = bridge.resolve_target_from_stack(bpy.context, "gp:gp_bridge")
    assert target.object_ref is gp_obj
    assert target.stable_id == "gp_bridge"
    assert bridge.target_is_live(bpy.context, target)
    assert bridge.is_pointer_derived_uid("gp:ptr_7ffabc")
    return target


def _check_gp_cancel_restore(addon, target):
    adapters = importlib.import_module(f"{MODULE_NAME}.utils.detail_state_adapters")
    state = importlib.import_module(f"{MODULE_NAME}.utils.detail_dialog_state")
    model = importlib.import_module(f"{MODULE_NAME}.utils.layer_object_model")
    snapshot = state.snapshot_detail_state(
        target,
        registry=adapters.ACTUAL_DETAIL_STATE_REGISTRY,
    )
    obj = target.object_ref
    obj["bmanga_title"] = "変更後"
    obj.location = (8.0, 9.0, 10.0)
    target.data.opacity = 0.2
    point = next(iter(target.data.frames[0].drawing.strokes[0].points))
    point.position = (99.0, 98.0, 97.0)
    _add_probe_stroke(addon, target.data, 20.0)
    try:
        state.restore_detail_state(target, snapshot)
    except state.DetailRestoreError as exc:
        for adapter_name, error in exc.failures:
            print("RESTORE_FAILURE", adapter_name, repr(error))
            for field_name, field_error in getattr(error, "failures", ()):
                print("RESTORE_FIELD_FAILURE", field_name, repr(field_error))
        raise
    restored = model.content_layer(obj)
    strokes = restored.frames[0].drawing.strokes
    first = strokes[0].points[0]
    assert obj["bmanga_title"] == "テストレイヤー"
    assert tuple(round(v, 5) for v in obj.location) == (0.0, 0.0, 0.0)
    assert abs(float(restored.opacity) - 1.0) < 1.0e-6
    assert len(strokes) == 1
    assert tuple(round(v, 5) for v in first.position) == (0.0, 0.0, 0.0)
    assert abs(float(first.radius) - 0.25) < 1.0e-6
    assert abs(float(first.opacity) - 0.75) < 1.0e-6
    assert bool(strokes[0].cyclic)


def _check_effect_display_normalization(addon):
    bridge = importlib.import_module(f"{MODULE_NAME}.utils.detail_target_resolver")
    effect_obj, _layer = _make_layer_object(addon, "effect", "effect_bridge")
    mesh = bpy.data.meshes.new("BridgeEffectDisplayMesh")
    display = bpy.data.objects.new("BridgeEffectDisplay", mesh)
    bpy.context.scene.collection.objects.link(display)
    display["bmanga_kind"] = "effect_display"
    display["bmanga_id"] = "effect_display_bridge"
    display["bmanga_effect_controller_id"] = "effect_bridge"
    target = bridge.resolve_target_from_object(bpy.context, display)
    assert target.kind == "effect"
    assert target.stable_id == "effect_bridge"
    assert target.object_ref is effect_obj
    assert target.params.as_pointer() == bpy.context.scene.bmanga_effect_line_params.as_pointer()


def _check_duplicate_text_ids_remain_page_scoped(addon):
    bridge = importlib.import_module(f"{MODULE_NAME}.utils.detail_target_resolver")
    naming = importlib.import_module(f"{MODULE_NAME}.utils.object_naming")
    work = bpy.context.scene.bmanga_work
    work.loaded = True
    pages = []
    for page_id, body in (("p0001", "1ページ"), ("p0002", "2ページ")):
        page = work.pages.add()
        page.id = page_id
        text = page.texts.add()
        text.id = "text_0001"
        text.body = body
        pages.append((page, text))
        item = bpy.context.scene.bmanga_layer_stack.add()
        item.kind = "text"
        item.key = f"{page_id}:text_0001"
        item.label = body

    first = bridge.resolve_target_from_stack(bpy.context, "text:p0001:text_0001")
    second = bridge.resolve_target_from_stack(bpy.context, "text:p0002:text_0001")
    assert str(first.data.body) == "1ページ"
    assert str(second.data.body) == "2ページ"
    assert first.stable_id == "p0001:text_0001"
    assert second.stable_id == "p0002:text_0001"

    mesh = bpy.data.meshes.new("BridgeTextPage2Mesh")
    obj = bpy.data.objects.new("BridgeTextPage2", mesh)
    bpy.context.scene.collection.objects.link(obj)
    naming.stamp_identity(
        obj,
        kind="text",
        bmanga_id="p0002:text_0001",
        title="2ページ",
        z_index=10,
        parent_key="p0002",
    )
    right_clicked = bridge.resolve_target_from_object(bpy.context, obj)
    assert str(right_clicked.data.body) == "2ページ"
    assert right_clicked.stable_id == "p0002:text_0001"
    assert right_clicked.stack_uid == "text:p0002:text_0001"

    try:
        bridge.resolve_target_from_object(bpy.context, "text_0001", "text")
    except Exception:
        pass  # ページを欠く重複IDを推測で先頭ページへ結び付けないことが要件
    else:
        raise AssertionError("bare duplicate text id must be rejected")


def _check_duplicate_balloon_ids_remain_page_scoped(addon):
    bridge = importlib.import_module(f"{MODULE_NAME}.utils.detail_target_resolver")
    naming = importlib.import_module(f"{MODULE_NAME}.utils.object_naming")
    work = bpy.context.scene.bmanga_work
    pages = []
    for page_id, title in (("p0101", "1ページのフキダシ"), ("p0102", "2ページのフキダシ")):
        page = work.pages.add()
        page.id = page_id
        balloon = page.balloons.add()
        balloon.id = "balloon_0001"
        balloon.title = title
        pages.append((page, balloon))

    # ページ用blendファイルでは表示中ページの実Objectだけが存在する。
    mesh = bpy.data.meshes.new("BridgeBalloonPage2Mesh")
    obj = bpy.data.objects.new("BridgeBalloonPage2", mesh)
    bpy.context.scene.collection.objects.link(obj)
    naming.stamp_identity(
        obj,
        kind="balloon",
        bmanga_id="balloon_0001",
        title="2ページのフキダシ",
        z_index=10,
        parent_key="p0102",
    )
    item = bpy.context.scene.bmanga_layer_stack.add()
    item.kind = "balloon"
    item.key = "p0102:balloon_0001"
    item.label = "2ページのフキダシ"

    right_clicked = bridge.resolve_target_from_object(bpy.context, obj)
    assert right_clicked.stable_id == "p0102:balloon_0001"
    assert right_clicked.stack_uid == "balloon:p0102:balloon_0001"
    assert str(right_clicked.data.title) == "2ページのフキダシ"

    exact = bridge.resolve_target_from_object(
        bpy.context,
        "p0101:balloon_0001",
        "balloon",
    )
    assert exact.stable_id == "p0101:balloon_0001"
    assert str(exact.data.title) == "1ページのフキダシ"

    try:
        bridge.resolve_target_from_object(bpy.context, "balloon_0001", "balloon")
    except Exception:
        pass  # ページを欠く重複IDを推測で先頭ページへ結び付けないことが要件
    else:
        raise AssertionError("bare duplicate balloon id must be rejected")


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _load_addon()
    try:
        gp_obj, layer = _make_layer_object(addon, "gp", "gp_bridge")
        _add_probe_stroke(addon, layer, 0.0)
        target = _check_stack_resolution(addon, gp_obj)
        _check_gp_cancel_restore(addon, target)
        _check_effect_display_normalization(addon)
        _check_duplicate_text_ids_remain_page_scoped(addon)
        _check_duplicate_balloon_ids_remain_page_scoped(addon)
        print("DETAIL_TARGET_STATE_BRIDGE_CHECK_OK")
    finally:
        addon.unregister()


if __name__ == "__main__":
    main()
