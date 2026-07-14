"""Blender実機: ページ間移動の保存耐久性・リンク・GP復元を検証する。"""

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
COLLISION_GP_ID = "gp_cross_page_collision"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _add_balloon_text(page, page_id: str, *, body: str = "ページ間移動"):
    balloon = page.balloons.add()
    balloon.id = "cross_balloon"
    balloon.shape = "rect"
    balloon.x_mm, balloon.y_mm = 12.0, 24.0
    balloon.width_mm, balloon.height_mm = 38.0, 21.0
    balloon.parent_kind, balloon.parent_key = "page", page_id
    text = page.texts.add()
    text.id = "cross_text"
    text.body = body
    text.x_mm, text.y_mm = 16.0, 28.0
    text.width_mm, text.height_mm = 25.0, 12.0
    text.parent_kind, text.parent_key = "page", page_id
    text.parent_balloon_id = balloon.id
    if hasattr(balloon, "text_id"):
        balloon.text_id = text.id
    return balloon, text


def _ensure_balloon_text_objects(context, page, balloon, text) -> None:
    from bmanga_dev.utils import balloon_curve_object, text_real_object

    assert balloon_curve_object.ensure_balloon_curve_object(
        scene=context.scene,
        entry=balloon,
        page=page,
    ) is not None
    assert text_real_object.ensure_text_real_object(
        scene=context.scene,
        entry=text,
        page=page,
    ) is not None


def _set_if_writable(value, name: str, setting) -> None:
    if not hasattr(value, name):
        return
    try:
        setattr(value, name, setting)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass


def _create_gp(context, parent_key: str, stable_id: str, title: str):
    from bmanga_dev.utils import gp_object_layer, gpencil, layer_object_model

    obj = gp_object_layer.create_layer_gp_object(
        scene=context.scene,
        bmanga_id=stable_id,
        title=title,
        z_index=214,
        parent_kind="page",
        parent_key=parent_key,
    )
    assert obj is not None
    layer = layer_object_model.content_layer(obj)
    assert layer is not None
    layer.opacity = 0.63
    _set_if_writable(layer, "tint_color", (0.11, 0.22, 0.33, 0.44))
    _set_if_writable(layer, "use_lights", False)
    frame = gpencil.ensure_active_frame(layer)
    assert frame is not None and frame.drawing is not None
    assert gpencil.add_stroke_to_drawing(
        frame.drawing,
        [(0.01, 0.02, 0.0), (0.03, 0.04, 0.0)],
        radii=[0.006, 0.009],
        opacities=[0.72, 0.83],
        curve_type="BEZIER",
        bezier_smooth=True,
    )
    material = next(
        item
        for item in obj.data.materials
        if item is not None and not item.name.startswith("BManga_Mask_Fill")
    )
    material.diffuse_color = (0.17, 0.29, 0.41, 0.73)
    style = getattr(material, "grease_pencil", None)
    assert style is not None
    style.color = (0.21, 0.32, 0.43, 0.81)
    style.fill_color = (0.51, 0.42, 0.33, 0.24)
    style.show_stroke = True
    style.show_fill = True
    _set_if_writable(style, "texture_angle", 0.37)
    _set_if_writable(style, "mix_factor", 0.26)
    _set_if_writable(style, "pixel_size", 73.0)
    _set_if_writable(style, "use_overlap_strokes", True)
    return obj


def _create_effect(context, parent_key: str):
    from bmanga_dev.operators import effect_line_op

    obj, _layer = effect_line_op._create_effect_layer(
        context,
        (18.0, 30.0, 42.0, 35.0),
        parent_key=parent_key,
    )
    assert obj is not None
    return obj


def _stage_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _assert_nested_equal(expected, actual, label: str) -> None:
    if isinstance(expected, float):
        assert abs(expected - float(actual)) <= 1.0e-6, label
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(expected) == len(actual), label
        for index, item in enumerate(expected):
            _assert_nested_equal(item, actual[index], f"{label}[{index}]")
    elif isinstance(expected, dict):
        assert isinstance(actual, dict) and set(expected) == set(actual), label
        for key, item in expected.items():
            _assert_nested_equal(item, actual[key], f"{label}.{key}")
    else:
        assert expected == actual, label


def _transfer_items(page_id: str, balloon_id: str, gp_id: str, effect_id: str) -> list:
    return [
        SimpleNamespace(kind="balloon", key=f"{page_id}:{balloon_id}"),
        SimpleNamespace(kind="gp", key=gp_id),
        SimpleNamespace(kind="effect", key=effect_id),
    ]


def _assert_source_present(context, page_id: str, gp_id: str, effect_id: str) -> None:
    from bmanga_dev.utils import (
        balloon_curve_object,
        layer_links,
        layer_object_model,
        text_real_object,
    )

    work = context.scene.bmanga_work
    page = next(item for item in work.pages if item.id == page_id)
    assert any(item.id == "cross_balloon" for item in page.balloons)
    assert any(item.id == "cross_text" for item in page.texts)
    assert balloon_curve_object.find_balloon_object("cross_balloon") is not None
    assert text_real_object.find_text_object(page_id, "cross_text") is not None
    assert layer_object_model.find_layer_object("gp", gp_id) is not None
    assert layer_object_model.find_layer_object("effect", effect_id) is not None
    assert sum(
        layer_object_model.stable_id(obj) == gp_id
        for obj in layer_object_model.iter_layer_objects("gp")
    ) == 1
    assert sum(
        layer_object_model.stable_id(obj) == effect_id
        for obj in layer_object_model.iter_layer_objects("effect")
    ) == 1
    assert len(set(layer_links._load_map(context).values())) == 1


def _target_stage_ids(stage: dict, source_gp_id: str, source_effect_id: str) -> tuple[str, str]:
    gp = next(item for item in stage["gp_layers"] if item.get("source_bmanga_id") == source_gp_id)
    effect = next(item for item in stage["effects"] if item.get("source_bmanga_id") == source_effect_id)
    return str(gp["bmanga_id"]), str(effect["bmanga_id"])


def _material_settings(materials: list) -> list:
    return [
        {key: value for key, value in material.items() if key != "name"}
        if isinstance(material, dict) else material
        for material in materials
    ]


def _transform_without_stack_z(transform: dict) -> dict:
    normalized = json.loads(json.dumps(transform))
    rows = normalized.get("page_relative_matrix", [])
    if isinstance(rows, list) and len(rows) == 4 and isinstance(rows[2], list) and len(rows[2]) == 4:
        rows[2][3] = 0.0
    return normalized


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_cross_page_durability_"))
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        assert "FINISHED" in bpy.ops.bmanga.work_new(
            filepath=str(temp_root / "CrossPageDurability.bmanga")
        )
        assert "FINISHED" in bpy.ops.bmanga.page_add("EXEC_DEFAULT")
        assert bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=1) == {"FINISHED"}

        from bmanga_dev.io import blend_io, page_io
        from bmanga_dev.utils import (
            balloon_curve_object,
            cross_page_gp_transfer,
            cross_page_stage,
            cross_page_transfer,
            json_io,
            layer_links,
            layer_object_model,
            paths,
            text_real_object,
        )

        context = bpy.context
        work = context.scene.bmanga_work
        target_page = work.pages[1]
        target_page_id = str(target_page.id)
        target_balloon, target_text = _add_balloon_text(
            target_page,
            target_page_id,
            body="移動先の既存要素",
        )
        _ensure_balloon_text_objects(context, target_page, target_balloon, target_text)
        _create_gp(
            context,
            target_page_id,
            COLLISION_GP_ID,
            "移動先の同一ID",
        )
        assert blend_io.save_page_blend(Path(work.work_dir), target_page_id)
        assert bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0) == {"FINISHED"}

        context = bpy.context
        work = context.scene.bmanga_work
        work_dir = Path(work.work_dir)
        source_page = work.pages[0]
        source_page_id = str(source_page.id)
        balloon, text = _add_balloon_text(source_page, source_page_id)
        _ensure_balloon_text_objects(context, source_page, balloon, text)
        source_gp = _create_gp(
            context,
            source_page_id,
            COLLISION_GP_ID,
            "移動元の同一ID",
        )
        source_effect = _create_effect(context, source_page_id)
        source_gp_id = layer_object_model.stable_id(source_gp)
        source_effect_id = layer_object_model.stable_id(source_effect)
        source_gp_snapshot = cross_page_gp_transfer.serialize_object(source_gp_id)
        assert source_gp_snapshot is not None
        old_uids = [
            f"balloon:{source_page_id}:{balloon.id}",
            f"text:{source_page_id}:{text.id}",
            f"gp:{source_gp_id}",
            f"effect:{source_effect_id}",
        ]
        layer_links.link_uids(context, old_uids)
        page_io.save_page_json(work_dir, source_page)
        items = _transfer_items(source_page_id, balloon.id, source_gp_id, source_effect_id)
        target_meta = paths.page_meta_path(work_dir, target_page_id)
        stage_path = cross_page_stage.staged_path(work_dir, target_page_id)
        target_before = target_meta.read_bytes()
        stage_before = _stage_bytes(stage_path)

        # 未対応種別が1件でも混ざれば、対応分も含めて一切動かさない。
        unsupported = items + [
            SimpleNamespace(kind="image", key="image_guard"),
            SimpleNamespace(kind="raster", key="raster_guard"),
            SimpleNamespace(kind="fill", key="fill_guard"),
        ]
        assert cross_page_transfer.transfer_layers_to_page(
            context, work, source_page, target_page_id, unsupported
        ) == 0
        _assert_source_present(context, source_page_id, source_gp_id, source_effect_id)
        assert target_meta.read_bytes() == target_before
        assert _stage_bytes(stage_path) == stage_before

        # 移動元 page.blend の保存失敗は、JSON・Object・リンク・stageを全復元する。
        original_save_page_blend = blend_io.save_page_blend
        save_calls = 0

        def fail_source_save_once(path, page_id):
            nonlocal save_calls
            save_calls += 1
            if save_calls == 1:
                return False
            return original_save_page_blend(path, page_id)

        blend_io.save_page_blend = fail_source_save_once
        try:
            assert cross_page_transfer.transfer_layers_to_page(
                context, work, source_page, target_page_id, items
            ) == 0
        finally:
            blend_io.save_page_blend = original_save_page_blend
        assert save_calls >= 2
        _assert_source_present(context, source_page_id, source_gp_id, source_effect_id)
        assert target_meta.read_bytes() == target_before
        assert _stage_bytes(stage_path) == stage_before

        source_blend = paths.page_blend_path(work_dir, source_page_id)
        bpy.ops.wm.open_mainfile(filepath=str(source_blend), load_ui=False)
        context = bpy.context
        work = context.scene.bmanga_work
        source_page = next(item for item in work.pages if item.id == source_page_id)
        _assert_source_present(context, source_page_id, source_gp_id, source_effect_id)

        # 成功時は移動元を保存し、移動先へ新IDとリンク復元情報を残す。
        items = _transfer_items(source_page_id, "cross_balloon", source_gp_id, source_effect_id)
        assert cross_page_transfer.transfer_layers_to_page(
            context, work, source_page, target_page_id, items
        ) == 3
        assert layer_object_model.find_layer_object("gp", source_gp_id) is None
        assert layer_object_model.find_layer_object("effect", source_effect_id) is None
        assert not any(item.id == "cross_balloon" for item in source_page.balloons)
        assert not any(item.id == "cross_text" for item in source_page.texts)
        assert balloon_curve_object.find_balloon_object("cross_balloon") is None
        assert text_real_object.find_text_object(source_page_id, "cross_text") is None
        assert not (set(layer_links._load_map(context)) & set(old_uids))
        stage = json_io.read_json(stage_path)
        moved_gp_id, moved_effect_id = _target_stage_ids(stage, source_gp_id, source_effect_id)
        assert moved_gp_id != source_gp_id and moved_effect_id != source_effect_id
        moved_group = stage[cross_page_stage.LINK_ENTRIES_KEY][0]["groups"][0]
        moved_balloon_uid = next(uid for uid in moved_group if uid.startswith("balloon:"))
        moved_text_uid = next(uid for uid in moved_group if uid.startswith("text:"))
        moved_balloon_id = moved_balloon_uid.split(":", 2)[2]
        moved_text_id = moved_text_uid.split(":", 2)[2]
        assert moved_balloon_id != "cross_balloon" and moved_text_id != "cross_text"
        assert set(moved_group) == {
            f"balloon:{target_page_id}:{moved_balloon_id}",
            f"text:{target_page_id}:{moved_text_id}",
            f"gp:{moved_gp_id}",
            f"effect:{moved_effect_id}",
        }

        # 移動元を実際に再読込しても、旧実体は戻らない。
        bpy.ops.wm.open_mainfile(filepath=str(source_blend), load_ui=False)
        context = bpy.context
        work = context.scene.bmanga_work
        source_page = next(item for item in work.pages if item.id == source_page_id)
        assert layer_object_model.find_layer_object("gp", source_gp_id) is None
        assert layer_object_model.find_layer_object("effect", source_effect_id) is None
        assert not any(item.id == "cross_balloon" for item in source_page.balloons)
        assert not any(item.id == "cross_text" for item in source_page.texts)
        assert balloon_curve_object.find_balloon_object("cross_balloon") is None
        assert text_real_object.find_text_object(source_page_id, "cross_text") is None

        target_blend = paths.page_blend_path(work_dir, target_page_id)
        bpy.ops.wm.open_mainfile(filepath=str(target_blend), load_ui=False)
        context = bpy.context
        work = context.scene.bmanga_work
        target_page = next(item for item in work.pages if item.id == target_page_id)
        restored_gp = layer_object_model.find_layer_object("gp", moved_gp_id)
        restored_effect = layer_object_model.find_layer_object("effect", moved_effect_id)
        assert restored_gp is not None and restored_effect is not None
        assert layer_object_model.find_layer_object("gp", COLLISION_GP_ID) is not None
        moved_balloon = next(item for item in target_page.balloons if item.id == moved_balloon_id)
        moved_text = next(item for item in target_page.texts if item.id == moved_text_id)
        assert moved_text.parent_balloon_id == moved_balloon.id
        if hasattr(moved_balloon, "text_id"):
            assert moved_balloon.text_id == moved_text.id
        restored_snapshot = cross_page_gp_transfer.serialize_object(moved_gp_id)
        assert restored_snapshot is not None
        _assert_nested_equal(
            _material_settings(source_gp_snapshot["materials"]),
            _material_settings(restored_snapshot["materials"]),
            "GP material/style",
        )
        _assert_nested_equal(
            source_gp_snapshot["layers"],
            restored_snapshot["layers"],
            "GP layer/stroke",
        )
        _assert_nested_equal(
            _transform_without_stack_z(source_gp_snapshot["object_transform"]),
            _transform_without_stack_z(restored_snapshot["object_transform"]),
            "GP object transform",
        )
        for key in ("title", "z_index", "visible", "locked"):
            assert source_gp_snapshot[key] == restored_snapshot[key], key
        mapping = layer_links._load_map(context)
        group_ids = {mapping.get(uid, "") for uid in moved_group}
        assert len(group_ids) == 1 and "" not in group_ids
        assert f"gp:{COLLISION_GP_ID}" not in mapping

        # 移動先の保存・再読込後も、実体とリンクが1組だけ残る。
        assert blend_io.save_page_blend(work_dir, target_page_id)
        assert not stage_path.exists()
        bpy.ops.wm.open_mainfile(filepath=str(target_blend), load_ui=False)
        assert layer_object_model.find_layer_object("gp", moved_gp_id) is not None
        assert layer_object_model.find_layer_object("effect", moved_effect_id) is not None
        mapping = layer_links._load_map(bpy.context)
        assert len({mapping.get(uid, "") for uid in moved_group}) == 1
        for kind, stable_id in (("gp", moved_gp_id), ("effect", moved_effect_id)):
            assert sum(
                layer_object_model.stable_id(obj) == stable_id
                for obj in layer_object_model.iter_layer_objects(kind)
            ) == 1

        print("BMANGA_CROSS_PAGE_TRANSFER_DURABILITY_OK", flush=True)
    finally:
        if module is not None:
            try:
                module.unregister()
            except Exception:  # noqa: BLE001
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        sys.exit(1)
