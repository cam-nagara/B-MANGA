"""Domain識別子とlink graphをBlender UI投影へ束縛する。"""

from __future__ import annotations

from ..bmanga_core.domain_model import PageDocument
from ..utils import log
from .domain_projection_ids import NODE_UID_PROP, custom_set


_logger = log.get_logger(__name__)


def bind_projection_node_uids(page, document: PageDocument, *, context=None) -> None:
    by_kind = {
        kind: {
            node.display_id: node.uid
            for node in document.nodes.values()
            if node.kind == kind
        }
        for kind in (
            "coma",
            "balloon",
            "text",
            "folder",
            "raster",
            "image",
            "fill",
            "image_path",
            "gp",
            "effect",
        )
    }

    def bind(collection, kind: str) -> None:
        for entry in collection or ():
            display_id = str(
                getattr(entry, "coma_id", "")
                or getattr(entry, "id", "")
                or ""
            )
            node_uid = by_kind[kind].get(display_id)
            if node_uid:
                custom_set(entry, NODE_UID_PROP, node_uid)

    bind(getattr(page, "comas", ()), "coma")
    bind(getattr(page, "balloons", ()), "balloon")
    bind(getattr(page, "texts", ()), "text")
    scene = getattr(page, "id_data", None)
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    bind(getattr(work, "layer_folders", ()), "folder")
    bind(getattr(scene, "bmanga_raster_layers", ()), "raster")
    bind(getattr(scene, "bmanga_image_layers", ()), "image")
    bind(getattr(scene, "bmanga_fill_layers", ()), "fill")
    bind(getattr(scene, "bmanga_image_path_layers", ()), "image_path")
    if context is None:
        return
    try:
        from ..utils import layer_object_model

        page_id = str(getattr(page, "id", "") or "")
        for kind in ("gp", "effect"):
            for obj in layer_object_model.iter_layer_objects(kind):
                if layer_object_model.parent_key(obj).split(":", 1)[0] != page_id:
                    continue
                node_uid = by_kind[kind].get(layer_object_model.stable_id(obj))
                if node_uid:
                    obj[NODE_UID_PROP] = node_uid
    except Exception:  # noqa: BLE001
        _logger.exception("Domain native node UID projection failed")


def apply_link_projection(page, document: PageDocument, context) -> None:
    if context is None:
        return
    try:
        from ..utils import layer_links

        mapping: dict[str, str] = {}
        for link in document.links.values():
            if link.kind != "linked-duplicate":
                continue
            group = f"domain_{link.uid}"
            for member in link.members:
                node = document.nodes[member]
                mapping[_projection_layer_uid(page, node.kind, node.display_id)] = group
        layer_links._save_map(context, mapping)
    except Exception:  # noqa: BLE001
        _logger.exception("Domain link graph projection failed")


def _projection_layer_uid(page, kind: str, display_id: str) -> str:
    page_id = str(getattr(page, "id", "") or "")
    if kind in {"balloon", "text"}:
        return f"{kind}:{page_id}:{display_id}"
    return f"{kind}:{display_id}"


__all__ = ("apply_link_projection", "bind_projection_node_uids")
