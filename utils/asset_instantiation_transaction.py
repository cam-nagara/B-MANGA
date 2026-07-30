"""複数レイヤー素材配置を一操作としてrollbackする。"""

from __future__ import annotations

from functools import wraps
from pathlib import Path

from ..core.work import get_active_page, get_work
from . import layer_links


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


def _snapshot(context, page) -> dict[str, object]:
    return {
        "identities": _asset_identities(context, page),
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
    asset_bundle._rollback_instantiated_asset(  # noqa: SLF001
        context,
        page,
        created,
        snapshot["text_parents"],
        snapshot["link_json"],
        snapshot["active_indexes"],
    )


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
        except BaseException:
            _rollback(context, page, snapshot)
            raise

    return wrapped


__all__ = ["atomic_asset_instantiation"]
