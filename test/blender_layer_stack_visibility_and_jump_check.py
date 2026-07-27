"""Blender 5.2実機: コマ一括非表示と10段レイヤー移動の回帰確認。"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_layer_visibility_jump"


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


def _stack_index(context, kind: str, key: str) -> int:
    from bmanga_dev_layer_visibility_jump.utils import layer_stack

    stack = layer_stack.sync_layer_stack(context, preserve_active_index=True)
    found = next(
        (
            index
            for index, item in enumerate(stack)
            if str(item.kind) == kind and str(item.key) == key
        ),
        -1,
    )
    if found < 0:
        rows = [(str(item.kind), str(item.key), str(item.parent_key)) for item in stack]
        raise AssertionError(f"stack row not found: {kind}:{key}; rows={rows}")
    return found


def _add_text(page, text_id: str, parent_key: str):
    entry = page.texts.add()
    entry.id = text_id
    entry.title = text_id
    entry.body = text_id
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    entry.x_mm = 20.0
    entry.y_mm = 20.0
    entry.width_mm = 20.0
    entry.height_mm = 12.0
    return entry


def _check_coma_visibility(context, page, coma) -> None:
    from bmanga_dev_layer_visibility_jump.utils import (
        layer_stack,
        object_naming,
        outliner_model,
        text_real_object,
    )

    page_key = str(page.id)
    coma_id = str(coma.coma_id or coma.id)
    coma_key = f"{page_key}:{coma_id}"
    text = _add_text(page, "visibility_child", coma_key)
    text.visible = True
    text_obj = text_real_object.ensure_text_real_object(
        scene=context.scene,
        entry=text,
        page=page,
    )
    assert text_obj is not None
    collection = outliner_model.ensure_coma_collection(
        context.scene,
        page_key,
        coma_id,
        str(coma.title or ""),
    )
    assert collection is not None
    assert str(text_obj.get("bmanga_parent_key", "") or "") == coma_key

    layer_stack.sync_layer_stack_after_data_change(context)
    coma_index = _stack_index(context, "coma", coma_key)
    result = bpy.ops.bmanga.layer_stack_toggle_visibility(
        "EXEC_DEFAULT",
        index=coma_index,
    )
    assert "FINISHED" in result, result
    assert not bool(coma.visible)
    assert bool(collection.hide_viewport)
    assert bool(collection.hide_render)
    assert bool(text.visible), "コマ非表示が子レイヤー固有の表示設定を壊しています"
    context.view_layer.update()
    assert not bool(text_obj.visible_get()), "コマ内のテキスト実体が表示されたままです"
    assert bool(text_obj.hide_render), "コマ内テキストがレンダー対象のままです"
    assert bool(text_obj.get("bmanga_coma_parent_hidden", False))

    # 非表示中の再生成でも親状態を失わず、コマ外へ移した時は固有状態へ戻る。
    text_obj = text_real_object.ensure_text_real_object(
        scene=context.scene,
        entry=text,
        page=page,
    )
    assert not bool(text_obj.visible_get()) and bool(text_obj.hide_render)
    text.parent_kind = "page"
    text.parent_key = page_key
    text_obj = text_real_object.ensure_text_real_object(
        scene=context.scene,
        entry=text,
        page=page,
    )
    context.view_layer.update()
    assert bool(text_obj.visible_get()) and not bool(text_obj.hide_render)
    text.parent_kind = "coma"
    text.parent_key = coma_key
    text_obj = text_real_object.ensure_text_real_object(
        scene=context.scene,
        entry=text,
        page=page,
    )
    context.view_layer.update()
    assert not bool(text_obj.visible_get()) and bool(text_obj.hide_render)

    # Collection再確保時にも保存済みのcoma.visibleが正として復旧される。
    collection.hide_viewport = False
    collection.hide_render = False
    restored = outliner_model.ensure_coma_collection(
        context.scene,
        page_key,
        coma_id,
        str(coma.title or ""),
    )
    assert restored is collection
    assert bool(collection.hide_viewport) and bool(collection.hide_render)

    coma_index = _stack_index(context, "coma", coma_key)
    result = bpy.ops.bmanga.layer_stack_toggle_visibility(
        "EXEC_DEFAULT",
        index=coma_index,
    )
    assert "FINISHED" in result, result
    assert bool(coma.visible)
    assert not bool(collection.hide_viewport)
    assert not bool(collection.hide_render)
    assert bool(text.visible)
    context.view_layer.update()
    assert bool(text_obj.visible_get()), "コマ再表示後に子レイヤーが復帰しません"
    assert not bool(text_obj.get("bmanga_coma_parent_hidden", False))

    from bmanga_dev_layer_visibility_jump.io import export_pipeline, export_stack_order

    coma.visible = False
    sample = export_pipeline.ExportLayer(
        "text",
        export_pipeline.Image.new("RGBA", (1, 1), (0, 0, 0, 255)),
        0,
        0,
        group_path=("comas", coma_id, "content"),
        visible=True,
    )
    hidden_sample = export_stack_order.apply_coma_visibility(page, [sample])[0]
    assert not bool(hidden_sample.visible), "書き出しでコマ内レイヤーが表示されたままです"
    coma.visible = True

    # 子が元から非表示だった場合は、親の往復で勝手に表示へ変えない。
    text.visible = False
    coma_index = _stack_index(context, "coma", coma_key)
    assert "FINISHED" in bpy.ops.bmanga.layer_stack_toggle_visibility(
        "EXEC_DEFAULT",
        index=coma_index,
    )
    coma_index = _stack_index(context, "coma", coma_key)
    assert "FINISHED" in bpy.ops.bmanga.layer_stack_toggle_visibility(
        "EXEC_DEFAULT",
        index=coma_index,
    )
    assert not bool(text.visible), "子レイヤー固有の非表示が親の往復で失われました"
    assert object_naming.find_collection_by_bmanga_id(coma_key, kind="coma") is collection


def _check_ten_step_move(context, page) -> None:
    from bmanga_dev_layer_visibility_jump.utils import layer_stack

    page_key = str(page.id)
    for index in range(25):
        _add_text(page, f"jump_{index:02d}", page_key)
    target_id = "jump_12"
    target_key = f"{page_key}:{target_id}"
    layer_stack.sync_layer_stack_after_data_change(context)

    before = _stack_index(context, "text", target_key)
    context.scene.bmanga_active_layer_stack_index = before
    result = bpy.ops.bmanga.layer_stack_move_ten(
        "EXEC_DEFAULT",
        direction="UP",
    )
    assert "FINISHED" in result, result
    after_up = _stack_index(context, "text", target_key)
    assert before - after_up == 10, (before, after_up)

    context.scene.bmanga_active_layer_stack_index = after_up
    result = bpy.ops.bmanga.layer_stack_move_ten(
        "EXEC_DEFAULT",
        direction="DOWN",
    )
    assert "FINISHED" in result, result
    after_down = _stack_index(context, "text", target_key)
    assert after_down - after_up == 10, (after_up, after_down)
    assert after_down == before, (before, after_down)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_layer_visibility_jump_"))
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        work_path = temp_root / "LayerVisibilityJump.bmanga"
        result = bpy.ops.bmanga.work_new(filepath=str(work_path))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        from bmanga_dev_layer_visibility_jump.core.work import get_work

        context = bpy.context
        work = get_work(context)
        page = work.pages[0]
        assert len(page.comas) >= 1
        coma = page.comas[0]

        _check_coma_visibility(context, page, coma)
        _check_ten_step_move(context, page)
        print("BMANGA_LAYER_VISIBILITY_JUMP_CHECK_OK")
    finally:
        if module is not None:
            try:
                module.unregister()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


main()
