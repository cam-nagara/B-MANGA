"""別ページ受入れをLayer Commandへ確定するための一時状態境界。"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StageCommandSnapshot:
    layer: object
    asset: dict[str, object]
    raster: object
    work_dir: Path
    stage_path: Path
    stage_existed: bool
    stage_data: dict
    scene_props: dict[str, object]
    wm_props: dict[str, object]
    target_props: dict[tuple[str, str], dict[str, object]]


def capture(context) -> StageCommandSnapshot:
    from ..core.work import get_work
    from . import (
        cross_page_link_stage,
        cross_page_stage,
        layer_command_runtime,
        layer_stack_command_runtime,
    )
    from . import asset_instantiation_transaction

    scene_keys = (
        cross_page_stage._ASSET_MANIFEST_PROP,
        cross_page_link_stage.MANIFEST_PROP,
    )
    wm_keys = (cross_page_stage._RUNTIME_KEYS_PROP,)
    work = get_work(context)
    page = _active_page(work)
    if work is None or page is None:
        raise RuntimeError("staged command requires an active page")
    work_dir = Path(str(getattr(work, "work_dir", "") or "")).resolve()
    stage_path = cross_page_stage.staged_path(
        work_dir,
        str(getattr(page, "id", "") or ""),
    )
    stage_existed = stage_path.is_file()
    stage_data = copy.deepcopy(cross_page_stage._read(stage_path))
    if stage_existed:
        from ..io import save_baseline

        save_baseline.record_observed_read(stage_path)
    return StageCommandSnapshot(
        layer=layer_command_runtime.capture(context, ()),
        asset=asset_instantiation_transaction._snapshot(context, page),
        raster=layer_stack_command_runtime._capture_raster_files(  # noqa: SLF001
            context,
            (),
        ),
        work_dir=work_dir,
        stage_path=stage_path,
        stage_existed=stage_existed,
        stage_data=stage_data,
        scene_props=_custom_values(context.scene, scene_keys),
        wm_props=_custom_values(context.window_manager, wm_keys),
        target_props=_target_values(context),
    )


def restore(context, snapshot: StageCommandSnapshot) -> None:
    from ..core.work import get_work
    from . import (
        asset_instantiation_transaction,
        layer_command_runtime,
        layer_stack_command_runtime,
    )

    page = _active_page(get_work(context))
    if page is None:
        raise RuntimeError("staged command rollback page is missing")
    failures: list[tuple[str, BaseException]] = []
    steps = (
        (
            "asset",
            lambda: asset_instantiation_transaction._rollback(
                context,
                page,
                snapshot.asset,
            ),
        ),
        (
            "datablocks-final",
            lambda: asset_instantiation_transaction._rollback_datablocks(
                snapshot.asset,
            ),
        ),
        ("layer", lambda: layer_command_runtime.restore(context, snapshot.layer)),
        (
            "datablocks-final",
            lambda: _rollback_datablocks_preserving_native(
                context,
                snapshot,
            ),
        ),
        (
            "raster",
            lambda: layer_stack_command_runtime._restore_raster_files(  # noqa: SLF001
                snapshot.raster,
            ),
        ),
        ("stage", lambda: _restore_stage_file(snapshot)),
        (
            "scene",
            lambda: _restore_custom_values(context.scene, snapshot.scene_props),
        ),
        (
            "window-manager",
            lambda: _restore_custom_values(
                context.window_manager,
                snapshot.wm_props,
            ),
        ),
        ("targets", lambda: _restore_target_values(context, snapshot.target_props)),
    )
    for label, step in steps:
        try:
            step()
        except BaseException as exc:  # 1段階の失敗でstage復元を止めない
            failures.append((label, exc))
    if failures:
        labels = ", ".join(label for label, _exc in failures)
        raise RuntimeError(
            f"staged command rollbackの{len(failures)}段階に失敗しました: {labels}"
        ) from failures[0][1]


def _rollback_datablocks_preserving_native(
    context,
    snapshot: StageCommandSnapshot,
) -> None:
    from . import asset_instantiation_transaction

    adjusted = dict(snapshot.asset)
    before = snapshot.asset.get("datablocks", {})
    adjusted["datablocks"] = {
        name: set(values)
        for name, values in before.items()
    } if isinstance(before, dict) else {}
    protected = _restored_native_datablocks(context, snapshot.layer)
    for name, pointers in protected.items():
        adjusted["datablocks"].setdefault(name, set()).update(pointers)
    asset_instantiation_transaction._rollback_datablocks(adjusted)


def _restored_native_datablocks(context, layer_snapshot) -> dict[str, set[int]]:
    import bpy

    from . import layer_object_model

    del context
    wanted = {
        "effect": set(getattr(layer_snapshot, "effects", {})),
        "gp": set(getattr(layer_snapshot, "gps", {})),
    }
    page_id = str(getattr(layer_snapshot, "page_id", "") or "")
    result = {
        name: set()
        for name in ("objects", "meshes", "curves", "materials", "images")
    }
    materials = []
    for kind, identities in wanted.items():
        for obj in layer_object_model.iter_layer_objects(kind):
            if layer_object_model.stable_id(obj) not in identities:
                continue
            if layer_object_model.parent_key(obj).split(":", 1)[0] != page_id:
                continue
            result["objects"].add(int(obj.as_pointer()))
            data = getattr(obj, "data", None)
            for name in ("meshes", "curves"):
                if _is_current_datablock(getattr(bpy.data, name), data):
                    result[name].add(int(data.as_pointer()))
            materials.extend(
                slot.material
                for slot in getattr(obj, "material_slots", ())
                if slot.material is not None
            )
    for material in materials:
        if not _is_current_datablock(bpy.data.materials, material):
            continue
        result["materials"].add(int(material.as_pointer()))
        nodes = getattr(getattr(material, "node_tree", None), "nodes", ())
        for node in nodes:
            image = getattr(node, "image", None)
            if _is_current_datablock(bpy.data.images, image):
                result["images"].add(int(image.as_pointer()))
    return result


def _is_current_datablock(collection, value) -> bool:
    if value is None:
        return False
    try:
        return collection.get(value.name) == value
    except (AttributeError, ReferenceError):
        return False


def _restore_stage_file(snapshot: StageCommandSnapshot) -> None:
    from ..io.project_file_lock import work_lock
    from . import cross_page_stage

    with work_lock(snapshot.work_dir, blocking=True):
        if snapshot.stage_existed:
            cross_page_stage._write_or_remove(
                snapshot.stage_path,
                copy.deepcopy(snapshot.stage_data),
            )
        else:
            snapshot.stage_path.unlink(missing_ok=True)


def _custom_values(owner, keys) -> dict[str, object]:
    if owner is None:
        return {}
    return {
        key: owner.get(key) if key in owner else None
        for key in keys
    }


def _restore_custom_values(owner, values: dict[str, object]) -> None:
    if owner is None:
        return
    for key, value in values.items():
        if value is None:
            if key in owner:
                del owner[key]
        else:
            owner[key] = value


def _target_values(context) -> dict[tuple[str, str], dict[str, object]]:
    from . import cross_page_stage

    result = {}
    for kind, target in _iter_targets(context):
        identity = cross_page_stage._asset_identity(target, kind)
        if identity:
            result[(kind, identity)] = _stage_values(target)
    return result


def _stage_values(target) -> dict[str, object]:
    from . import cross_page_stage

    keys = (
        cross_page_stage.STAGE_OBJECT_PROP,
        cross_page_stage.ASSET_STAGE_PROP,
        cross_page_stage.ASSET_STAGE_INDEX_PROP,
        cross_page_stage.ASSET_STAGE_TOKEN_PROP,
    )
    return _custom_values(target, keys)


def _restore_target_values(context, values) -> None:
    from . import cross_page_stage

    for kind, target in _iter_targets(context):
        identity = cross_page_stage._asset_identity(target, kind)
        current = values.get((kind, identity))
        _restore_custom_values(
            target,
            current if current is not None else _empty_stage_values(),
        )


def _empty_stage_values() -> dict[str, object]:
    from . import cross_page_stage

    return {
        cross_page_stage.STAGE_OBJECT_PROP: None,
        cross_page_stage.ASSET_STAGE_PROP: None,
        cross_page_stage.ASSET_STAGE_INDEX_PROP: None,
        cross_page_stage.ASSET_STAGE_TOKEN_PROP: None,
    }


def _iter_targets(context):
    from ..core.work import get_work
    from . import cross_page_stage, layer_object_model

    work = get_work(context)
    index = int(getattr(work, "active_page_index", -1)) if work is not None else -1
    page = work.pages[index] if work is not None and 0 <= index < len(work.pages) else None
    if page is None:
        return
    for kind, collection in cross_page_stage._asset_collections(context, page).items():
        for target in collection:
            yield kind, target
    for kind in ("gp", "effect"):
        for target in layer_object_model.iter_layer_objects(kind):
            if layer_object_model.parent_key(target).split(":", 1)[0] == page.id:
                yield kind, target


def _active_page(work):
    if work is None:
        return None
    index = int(getattr(work, "active_page_index", -1))
    pages = getattr(work, "pages", ())
    return pages[index] if 0 <= index < len(pages) else None


__all__ = ("StageCommandSnapshot", "capture", "restore")
