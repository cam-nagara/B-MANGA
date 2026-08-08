"""複数レイヤー素材配置を一操作としてrollbackする。"""

from __future__ import annotations

from functools import wraps
from pathlib import Path

from ..core.work import get_active_page, get_work
from . import layer_links


class AssetInstantiationRollbackError(RuntimeError):
    """素材生成失敗に加えてrollbackも完遂できなかった。"""

    def __init__(
        self,
        operation_error: BaseException,
        rollback_error: BaseException,
    ) -> None:
        self.operation_error = operation_error
        self.rollback_error = rollback_error
        super().__init__(
            "素材生成失敗後のrollbackを完遂できませんでした: "
            f"{type(rollback_error).__name__}: {rollback_error}"
        )


def _identity(kind: str, target) -> str:
    if kind in {"gp", "effect"}:
        from . import layer_object_model

        return layer_object_model.stable_id(target)
    return str(
        getattr(target, "coma_id", "") or getattr(target, "id", "") or ""
    )


def _asset_identities(context, page) -> set[tuple[str, str]]:
    from . import cross_page_stage, layer_object_model

    identities = {
        (kind, _identity(kind, target))
        for kind, collection in cross_page_stage._asset_collections(  # noqa: SLF001
            context,
            page,
        ).items()
        for target in collection
        if _identity(kind, target)
    }
    for kind in ("effect", "gp"):
        identities.update(
            (kind, _identity(kind, target))
            for target in layer_object_model.iter_layer_objects(kind)
            if _identity(kind, target)
        )
    return identities


def _datablock_pointers() -> dict[str, set[int]]:
    import bpy

    return {
        name: {int(item.as_pointer()) for item in getattr(bpy.data, name)}
        for name in ("objects", "meshes", "curves", "materials", "images")
    }


def _snapshot(context, page) -> dict[str, object]:
    return {
        "identities": _asset_identities(context, page),
        "datablocks": _datablock_pointers(),
        "text_parents": {
            str(getattr(text, "id", "") or ""): str(
                getattr(text, "parent_balloon_id", "") or ""
            )
            for text in getattr(page, "texts", ()) or ()
        },
        "link_json": str(context.scene.get(layer_links.LINK_PROP, "") or ""),
        "active_indexes": (
            int(getattr(page, "active_coma_index", -1)),
            int(getattr(page, "active_balloon_index", -1)),
            int(getattr(page, "active_text_index", -1)),
        ),
    }


def _rollback_datablocks(snapshot: dict[str, object]) -> None:
    import bpy

    before = snapshot.get("datablocks", {})
    if not isinstance(before, dict):
        return
    failures: list[str] = []
    for name in ("objects", "meshes", "curves", "materials", "images"):
        collection = getattr(bpy.data, name)
        preserved = before.get(name, set())
        for item in tuple(collection):
            try:
                pointer = int(item.as_pointer())
            except ReferenceError:
                continue
            if pointer in preserved:
                continue
            try:
                collection.remove(item, do_unlink=True)
            except TypeError:
                try:
                    collection.remove(item)
                except (ReferenceError, RuntimeError):
                    failures.append(name)
            except (ReferenceError, RuntimeError):
                failures.append(name)
    if failures:
        raise RuntimeError(
            "生成Datablockのrollbackに失敗しました: "
            + ", ".join(sorted(set(failures)))
        )


def _rollback(context, page, snapshot: dict[str, object]) -> None:
    from . import asset_bundle

    before = snapshot["identities"]
    creation_order = {
        "coma": 0,
        "layer_folder": 1,
        "balloon": 2,
        "text": 3,
        "fill": 4,
        "image": 5,
        "image_path": 6,
        "raster": 7,
        "gp": 8,
        "effect": 9,
    }
    created = sorted(
        _asset_identities(context, page) - before,
        key=lambda item: creation_order.get(item[0], 100),
    )
    failures: list[BaseException] = []
    try:
        asset_bundle._rollback_instantiated_asset(  # noqa: SLF001
            context,
            page,
            created,
            snapshot["text_parents"],
            snapshot["link_json"],
            snapshot["active_indexes"],
        )
    except BaseException as exc:  # rollbackは後続掃除を必ず続行する
        failures.append(exc)
    try:
        _rollback_datablocks(snapshot)
    except BaseException as exc:  # rollbackは全Datablock種別を必ず試行する
        failures.append(exc)
    if failures:
        raise RuntimeError(
            f"素材生成rollbackの{len(failures)}段階に失敗しました"
        ) from failures[0]


def _persist(context, page) -> None:
    from ..io import page_io

    work = get_work(context)
    if work is None or not getattr(work, "work_dir", ""):
        raise RuntimeError("素材の保存先がありません")
    page_io.save_page_json(Path(work.work_dir), page)


def atomic_asset_instantiation(function):
    """生成・Domain checkpointの全成功後だけ素材配置を確定する。"""

    @wraps(function)
    def wrapped(context, payload, *args, **kwargs):
        page = kwargs.get("target_page") or get_active_page(context)
        if page is None:
            return function(context, payload, *args, **kwargs)
        snapshot = _snapshot(context, page)
        try:
            result = function(context, payload, *args, **kwargs)
            if not bool(result.get("staged", False)) and not str(
                kwargs.get("stage_id", "") or ""
            ):
                _persist(context, page)
            return result
        except BaseException as operation_error:
            try:
                _rollback(context, page, snapshot)
            except BaseException as rollback_error:
                raise AssetInstantiationRollbackError(
                    operation_error,
                    rollback_error,
                ) from rollback_error
            raise

    return wrapped


__all__ = [
    "AssetInstantiationRollbackError",
    "atomic_asset_instantiation",
]
