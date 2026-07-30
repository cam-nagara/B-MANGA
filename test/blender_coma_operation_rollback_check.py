"""Blender実機: コマ操作のcheckpoint失敗でGP／効果線まで完全復旧する。"""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_coma_operation_rollback_test"


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


def _prepare_case(root: Path, operation: str):
    from bmanga_coma_operation_rollback_test.io import page_io
    from bmanga_coma_operation_rollback_test.operators import coma_op
    from bmanga_coma_operation_rollback_test.utils import (
        gp_object_layer,
        layer_object_model,
    )
    from bmanga_coma_operation_rollback_test.operators import effect_line_op

    work_dir = root / f"ComaRollback-{operation}.bmanga"
    assert bpy.ops.bmanga.work_new(filepath=str(work_dir)) == {"FINISHED"}
    assert bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0) == {"FINISHED"}
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    while len(page.comas) < 2:
        coma_op.create_basic_frame_coma(work, page, work_dir)
    page.comas[0].rect_x_mm = 10.0
    page.comas[1].rect_x_mm = 100.0
    target_index = 1 if operation == "merge" else 0
    target = page.comas[target_index]
    parent_key = f"{page.id}:{target.coma_id}"
    gp_id = f"gp_rollback_{operation}"
    gp_obj = gp_object_layer.create_layer_gp_object(
        scene=bpy.context.scene,
        bmanga_id=gp_id,
        title=f"GP rollback {operation}",
        z_index=210,
        parent_kind="coma",
        parent_key=parent_key,
    )
    assert gp_obj is not None
    effect_obj, effect_layer = effect_line_op._create_effect_layer(
        bpy.context,
        (20.0, 25.0, 35.0, 45.0),
        parent_key=parent_key,
    )
    assert effect_obj is not None and effect_layer is not None
    effect_line_op._write_effect_strokes(
        bpy.context,
        effect_obj,
        effect_layer,
        (20.0, 25.0, 35.0, 45.0),
    )
    effect_id = layer_object_model.stable_id(effect_obj)
    assert effect_id
    parent_key = layer_object_model.parent_key(gp_obj)
    assert parent_key == layer_object_model.parent_key(effect_obj)
    page_io.save_page_json(work_dir, page)
    return work_dir, str(page.id), parent_key, gp_id, effect_id


def _invoke(operation: str, parent_key: str):
    from bmanga_coma_operation_rollback_test.operators import coma_op

    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    target_id = parent_key.split(":", 1)[1]
    target_index = next(
        index
        for index, coma in enumerate(page.comas)
        if str(coma.coma_id) == target_id
    )
    if operation == "delete":
        page.active_coma_index = target_index
        return bpy.ops.bmanga.coma_remove("EXEC_DEFAULT")
    if operation == "template":
        page.active_coma_index = target_index
        return bpy.ops.bmanga.coma_split_template(
            "EXEC_DEFAULT",
            rows=1,
            cols=2,
            clear_existing=True,
            target_page_id=str(page.id),
            target_coma_index=target_index,
        )
    page.active_coma_index = 1 - target_index
    refs = [(0, page, index, page.comas[index]) for index in range(2)]
    original = coma_op.object_selection.selected_coma_refs
    coma_op.object_selection.selected_coma_refs = lambda _context: refs
    try:
        return bpy.ops.bmanga.coma_merge_selected(
            "EXEC_DEFAULT",
            border_mode="merge",
        )
    finally:
        coma_op.object_selection.selected_coma_refs = original


def _exercise(root: Path, operation: str) -> None:
    from bmanga_coma_operation_rollback_test.bmanga_core.domain_model import (
        document_hash,
    )
    from bmanga_coma_operation_rollback_test.io import (
        domain_projection,
        domain_runtime,
    )
    from bmanga_coma_operation_rollback_test.utils import (
        effect_line_object,
        layer_object_model,
    )

    bpy.ops.wm.read_factory_settings(use_empty=True)
    work_dir, page_id, parent_key, gp_id, effect_id = _prepare_case(
        root,
        operation,
    )
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    page_uid = domain_projection.ensure_page_uid(
        page,
        domain_projection.ensure_project_uid(work),
    )
    repository = domain_runtime.repository_for(work_dir)
    before_hash = document_hash(repository.load_page(page_uid))
    original_checkpoint = repository.checkpoint

    def fail_checkpoint(*_args, **_kwargs):
        raise RuntimeError("injected checkpoint failure")

    repository.checkpoint = fail_checkpoint
    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        try:
            result = _invoke(operation, parent_key)
        except RuntimeError as exc:
            assert "injected checkpoint failure" in str(exc)
            result = {"CANCELLED"}
    finally:
        logging.disable(previous_logging_disable)
        repository.checkpoint = original_checkpoint
    assert result == {"CANCELLED"}, (operation, result)
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    assert work.loaded, operation
    assert len(page.comas) == 2, (operation, len(page.comas))
    assert document_hash(repository.load_page(page_uid)) == before_hash
    gp_obj = layer_object_model.find_layer_object("gp", gp_id)
    effect_obj = layer_object_model.find_layer_object("effect", effect_id)
    assert gp_obj is not None, f"{operation}: GP object was not restored"
    assert effect_obj is not None, f"{operation}: effect object was not restored"
    assert layer_object_model.parent_key(gp_obj) == parent_key, (
        operation,
        parent_key,
        layer_object_model.parent_key(gp_obj),
    )
    assert layer_object_model.parent_key(effect_obj) == parent_key, (
        operation,
        parent_key,
        layer_object_model.parent_key(effect_obj),
    )
    assert effect_line_object.find_effect_display_object(effect_obj) is not None
    assert not any(
        obj.name.startswith("__BManga_ComaRollback_")
        for obj in bpy.data.objects
    )
    grease_pencils = getattr(bpy.data, "grease_pencils_v3", None)
    if grease_pencils is None:
        grease_pencils = getattr(bpy.data, "grease_pencils", ())
    assert not any(
        data.name.startswith("__BManga_ComaRollbackData_")
        for data in grease_pencils
    )
    assert not tuple((work_dir / "journal").glob("native-op-*.json"))
    retry = _invoke(operation, parent_key)
    assert retry == {"FINISHED"}, (operation, retry)
    assert not any(
        obj.name.startswith("__BManga_ComaRollback_")
        for obj in bpy.data.objects
    )
    assert not any(
        data.name.startswith("__BManga_ComaRollbackData_")
        for data in grease_pencils
    )


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_rollback_"))
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        for operation in ("delete", "merge", "template"):
            _exercise(temp_root / operation, operation)
        print("BMANGA_COMA_OPERATION_ROLLBACK_OK", flush=True)
    finally:
        if module is not None:
            try:
                module.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    import traceback

    try:
        main()
        os._exit(0)
    except Exception:
        traceback.print_exc()
        os._exit(1)
