"""Domain ModelとBlender PropertyGroupの明示的なUI投影adapter。"""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from typing import Any, Iterable, Mapping

from ..bmanga_core.domain_ids import UIDKind, derived_uid, is_uid
from ..bmanga_core.domain_model import (
    DomainLink,
    DomainNode,
    PageDocument,
    PageSummary,
    ProjectDocument,
)
from ..utils import layer_uid, log
from . import (
    domain_projection_binding,
    domain_projection_ids,
    domain_projection_preservation,
    domain_projection_tree,
    schema,
)
from .domain_projection_ids import (
    COMA_UID_PROP,
    NODE_UID_PROP,
    PAGE_REVISION_PROP,
    PAGE_UID_PROP,
    PROJECT_REVISION_PROP,
    PROJECT_UID_PROP,
    SOURCE_PAGE_UIDS_PROP,
    ensure_coma_uid,
    ensure_page_uid,
    ensure_project_uid,
)


_logger = log.get_logger(__name__)
_custom_get = domain_projection_ids.custom_get
_custom_set = domain_projection_ids.custom_set

_PROJECT_CONTROL_FIELDS = {"schemaVersion"}
_PAGE_OWNED_PROJECT_FIELDS = {
    "raster_layers",
    "image_layers",
    "fill_layers",
    "image_path_layers",
    "layer_folders",
}
_PAGE_ROOT_FIELDS = {
    "schemaVersion",
    "id",
    "title",
    "spread",
    "comas",
    "balloons",
    "texts",
}
_NODE_STRUCTURAL_FIELDS = {
    "id",
    "title",
    "parentKind",
    "parentKey",
    "folderKey",
    "parent_kind",
    "parent_key",
    "folder_key",
    "nodeUid",
}
_PROJECTED_NODE_KINDS = {
    "page",
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
}


def project_document_from_work(work) -> ProjectDocument:
    project_uid = ensure_project_uid(work)
    page_uids = {
        str(getattr(page, "id", "") or ""): ensure_page_uid(page, project_uid)
        for page in getattr(work, "pages", ())
    }
    pages = [
        _page_summary_from_projection(page, page_uids)
        for page in getattr(work, "pages", ())
    ]
    settings = schema.work_to_dict(work)
    for key in _PROJECT_CONTROL_FIELDS | _PAGE_OWNED_PROJECT_FIELDS:
        settings.pop(key, None)
    revision = int(_custom_get(work, PROJECT_REVISION_PROP, 0) or 0)
    return ProjectDocument(project_uid, max(0, revision), settings, pages)


def preserve_project_projection(
    authoritative: ProjectDocument,
    projection: ProjectDocument,
) -> ProjectDocument:
    """UIが表現しないDomain拡張fieldを明示差分の外へ残す。"""
    authoritative.validate()
    projection.validate()
    if authoritative.project_uid != projection.project_uid:
        raise ValueError("project projection UID mismatch")
    result = copy.deepcopy(projection)
    result.settings = {
        **copy.deepcopy(authoritative.settings),
        **copy.deepcopy(result.settings),
    }
    old_pages = {page.uid: page for page in authoritative.pages}
    for page in result.pages:
        old = old_pages.get(page.uid)
        if old is not None:
            page.settings = {
                **copy.deepcopy(old.settings),
                **copy.deepcopy(page.settings),
            }
    result.validate()
    return result


def _page_summary_from_projection(page, page_uids: dict[str, str]) -> PageSummary:
    raw = schema.page_entry_to_dict(page)
    display_id = str(raw.pop("id", "") or "")
    title = str(raw.pop("title", "") or "")
    spread = bool(raw.pop("spread", False))
    raw.pop("dir", None)
    source_display_ids = tuple(
        str(value) for value in raw.pop("originalPages", ()) if str(value)
    )
    try:
        stored_source_uids = tuple(
            json.loads(str(_custom_get(page, SOURCE_PAGE_UIDS_PROP, "[]") or "[]"))
        )
    except (TypeError, ValueError):
        stored_source_uids = ()
    source_pairs = []
    for index, source_display_id in enumerate(source_display_ids):
        stored_uid = (
            str(stored_source_uids[index])
            if index < len(stored_source_uids)
            else ""
        )
        source_uid = (
            stored_uid
            if is_uid(stored_uid, UIDKind.PAGE)
            else page_uids.get(source_display_id, "")
        )
        if source_uid:
            source_pairs.append((source_uid, source_display_id))
    sources = tuple(uid for uid, _display_id in source_pairs)
    source_display_ids = tuple(
        display_id for _uid, display_id in source_pairs
    )
    return PageSummary(
        uid=page_uids[display_id],
        display_id=display_id,
        display_number=domain_projection_tree.display_number(display_id),
        title=title,
        spread=spread,
        source_page_uids=sources,
        source_page_display_ids=source_display_ids,
        settings=raw,
    )


def project_document_from_payload(
    *,
    project_uid: str,
    revision: int,
    work_payload: Mapping[str, Any],
    pages_payload: Mapping[str, Any],
    page_uids: Mapping[str, str],
) -> ProjectDocument:
    """明示的なCommand境界用projection payloadをDomainへ変換する。"""

    settings = copy.deepcopy(dict(work_payload))
    for key in _PROJECT_CONTROL_FIELDS | _PAGE_OWNED_PROJECT_FIELDS:
        settings.pop(key, None)
    raw_pages = pages_payload.get("pages")
    if not isinstance(raw_pages, list):
        raise ValueError("pages payload must contain an array")
    summaries: list[PageSummary] = []
    for raw_page in raw_pages:
        if not isinstance(raw_page, Mapping):
            raise ValueError("page summary payload must be an object")
        raw = copy.deepcopy(dict(raw_page))
        display_id = str(raw.pop("id", "") or "")
        if display_id not in page_uids:
            raise ValueError(f"page UID is missing: {display_id}")
        title = str(raw.pop("title", "") or "")
        spread = bool(raw.pop("spread", False))
        raw.pop("dir", None)
        source_display_ids = tuple(
            str(source_id)
            for source_id in raw.pop("originalPages", ())
            if source_id in page_uids
        )
        source_uids = tuple(page_uids[source_id] for source_id in source_display_ids)
        summaries.append(
            PageSummary(
                uid=page_uids[display_id],
                display_id=display_id,
                display_number=domain_projection_tree.display_number(display_id),
                title=title,
                spread=spread,
                source_page_uids=source_uids,
                source_page_display_ids=source_display_ids,
                settings=raw,
            )
        )
    return ProjectDocument(
        project_uid=project_uid,
        revision=max(0, int(revision)),
        settings=settings,
        pages=summaries,
    )


def page_document_from_payload(
    *,
    project_uid: str,
    page_uid: str,
    revision: int,
    work_payload: Mapping[str, Any],
    page_payload: Mapping[str, Any],
    coma_uids: Mapping[str, str] | None = None,
    native_payloads: Iterable[tuple[str, Iterable[Mapping[str, Any]]]] = (),
    links: Mapping[str, DomainLink] | None = None,
) -> PageDocument:
    """保存操作で確定済みのUI projection payloadをDomain treeへ変換する。"""

    raw = copy.deepcopy(dict(page_payload))
    display_id = str(raw.get("id", "") or "")
    if not display_id:
        raise ValueError("page payload has no display ID")
    settings = {
        key: copy.deepcopy(value)
        for key, value in raw.items()
        if key not in _PAGE_ROOT_FIELDS
    }
    root_uid = derived_uid(UIDKind.NODE, page_uid, "root")
    root = DomainNode(
        root_uid,
        "page",
        display_id,
        str(raw.get("title", "") or ""),
    )
    nodes = {root_uid: root}
    parent_keys: dict[str, str] = {}
    aliases: dict[str, str] = {}
    ordered: list[str] = []
    owned = _owned_work_payloads(dict(work_payload), raw)
    coma_uid_map = dict(coma_uids or {})
    collections: list[tuple[str, Iterable[Mapping[str, Any]]]] = [
        ("coma", raw.get("comas", ())),
        ("balloon", raw.get("balloons", ())),
        ("text", raw.get("texts", ())),
        ("folder", owned["layer_folders"]),
        ("raster", owned["raster_layers"]),
        ("image", owned["image_layers"]),
        ("fill", owned["fill_layers"]),
        ("image_path", owned["image_path_layers"]),
    ]
    collections.extend(native_payloads)
    for kind, values in collections:
        for value in values:
            payload = copy.deepcopy(dict(value))
            if kind == "coma":
                coma_id = str(payload.get("comaId") or payload.get("id") or "")
                if coma_id in coma_uid_map:
                    payload["nativeUid"] = coma_uid_map[coma_id]
            node, parent_key, node_aliases = _node_from_payload(
                page_uid,
                kind,
                payload,
            )
            if node.uid in nodes:
                existing = nodes[node.uid]
                raise ValueError(
                    "duplicate Domain node UID: "
                    f"{node.uid} "
                    f"({existing.kind}:{existing.display_id} / "
                    f"{node.kind}:{node.display_id})"
                )
            for alias in node_aliases:
                existing = aliases.get(alias)
                if existing is not None and existing != node.uid:
                    raise ValueError(f"duplicate Domain node alias: {alias}")
            nodes[node.uid] = node
            parent_keys[node.uid] = parent_key
            aliases.update({alias: node.uid for alias in node_aliases})
            ordered.append(node.uid)
    children = _build_children(root_uid, nodes, parent_keys, aliases, ordered)
    document = PageDocument(
        project_uid=project_uid,
        page_uid=page_uid,
        revision=max(0, int(revision)),
        root_uid=root_uid,
        settings=settings,
        nodes=nodes,
        children=children,
        links=copy.deepcopy(dict(links or {})),
    )
    document.validate()
    return document


def _owned_work_payloads(
    work_payload: Mapping[str, Any],
    page_payload: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    page_id = str(page_payload.get("id", "") or "")
    owner_keys = {page_id}
    owner_keys.update(
        f"{page_id}:{str(coma.get('comaId') or coma.get('id') or '')}"
        for coma in page_payload.get("comas", ())
        if isinstance(coma, Mapping)
    )
    folders = [
        copy.deepcopy(dict(value))
        for value in work_payload.get("layer_folders", ())
        if isinstance(value, Mapping)
    ]
    selected_folders: list[dict[str, Any]] = []
    remaining = folders
    while remaining:
        accepted: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        for folder in remaining:
            parent = _payload_parent_key(folder)
            if parent in owner_keys or parent.split(":", 1)[0] == page_id:
                accepted.append(folder)
            else:
                pending.append(folder)
        if not accepted:
            break
        selected_folders.extend(accepted)
        owner_keys.update(
            str(folder.get("id", "") or "")
            for folder in accepted
            if str(folder.get("id", "") or "")
        )
        remaining = pending

    result = {"layer_folders": selected_folders}
    for key in (
        "raster_layers",
        "image_layers",
        "fill_layers",
        "image_path_layers",
    ):
        result[key] = [
            copy.deepcopy(dict(value))
            for value in work_payload.get(key, ())
            if isinstance(value, Mapping)
            and (
                _payload_parent_key(value) in owner_keys
                or _payload_parent_key(value).split(":", 1)[0] == page_id
                or str(value.get("folderKey", "") or "") in owner_keys
            )
        ]
    return result


def _payload_parent_key(value: Mapping[str, Any]) -> str:
    return str(
        value.get("folderKey")
        or value.get("parentKey")
        or value.get("parent_key")
        or ""
    )


def apply_project_document(work, document: ProjectDocument) -> None:
    document.validate()
    # Domain読込はUIへの一方向投影であり、PropertyGroupのupdate callbackから
    # page追加・Repository書込みを再入させない。ページ要約の再構築まで同じ
    # 停止区間に含め、部分投影をruntime同期へ見せない。
    from ..core.work_info import suppress_page_number_range_update

    with (
        schema._suspend_load_property_side_effects(),
        suppress_page_number_range_update(),
    ):
        _custom_set(work, PROJECT_UID_PROP, document.project_uid)
        _custom_set(work, PROJECT_REVISION_PROP, document.revision)
        payload = copy.deepcopy(document.settings)
        payload["schemaVersion"] = schema.WORK_SCHEMA_VERSION
        schema.work_from_dict(work, payload)
        _apply_page_summaries(work, document.pages)


def bind_project_document(work, document: ProjectDocument) -> None:
    """保存済みDomainの識別子だけを現在のUI投影へ束縛する。

    UI全体の再投影は読込時だけに限定する。保存直後に
    ``schema.work_from_dict`` を呼ぶと、編集中ページの要素を
    Project設定の空collectionで消し得るためである。
    """

    document.validate()
    _custom_set(work, PROJECT_UID_PROP, document.project_uid)
    _custom_set(work, PROJECT_REVISION_PROP, document.revision)
    summaries = {summary.display_id: summary for summary in document.pages}
    for entry in getattr(work, "pages", ()):
        summary = summaries.get(str(getattr(entry, "id", "")))
        if summary is None:
            continue
        _custom_set(entry, PAGE_UID_PROP, summary.uid)
        if hasattr(entry, "dir_rel"):
            entry.dir_rel = f"pages/{summary.uid}/"
        _custom_set(
            entry,
            SOURCE_PAGE_UIDS_PROP,
            json.dumps(list(summary.source_page_uids), separators=(",", ":")),
        )


def bind_page_document(page, document: PageDocument) -> None:
    """保存済みpage/node/native UIDを現在のUI投影へ束縛する。"""

    document.validate()
    _custom_set(page, PAGE_UID_PROP, document.page_uid)
    _custom_set(page, PAGE_REVISION_PROP, document.revision)
    coma_nodes = {
        node.display_id: node
        for node in document.nodes.values()
        if node.kind == "coma"
    }
    for entry in getattr(page, "comas", ()):
        display_id = str(
            getattr(entry, "coma_id", "") or getattr(entry, "id", "") or ""
        )
        node = coma_nodes.get(display_id)
        if node is not None and node.native_uid:
            _custom_set(entry, COMA_UID_PROP, node.native_uid)
    domain_projection_binding.bind_projection_node_uids(page, document)


def page_payload_from_document(
    document: PageDocument,
    *,
    display_id: str,
    title: str = "",
    spread: bool = False,
) -> dict[str, Any]:
    """Domain pageを一時的なBlender UI projection payloadへ変換する。"""

    document.validate()
    payload = copy.deepcopy(document.settings)
    payload.update(
        {
            "schemaVersion": schema.PAGE_SCHEMA_VERSION,
            "id": display_id,
            "title": title,
            "spread": bool(spread),
            "comas": _payloads_for_kind(document, "coma"),
            "balloons": _payloads_for_kind(document, "balloon"),
            "texts": _payloads_for_kind(document, "text"),
        }
    )
    return payload


def replace_page_projection_payload(
    document: PageDocument,
    payload: Mapping[str, Any],
) -> PageDocument:
    """既存Domain pageの非表示Node/Linkを保ったままUI payloadを置換する。"""

    document.validate()
    work_payload = {
        "layer_folders": _payloads_for_kind(document, "folder"),
        "raster_layers": _payloads_for_kind(document, "raster"),
        "image_layers": _payloads_for_kind(document, "image"),
        "fill_layers": _payloads_for_kind(document, "fill"),
        "image_path_layers": _payloads_for_kind(document, "image_path"),
    }
    native_payloads = (
        ("gp", _payloads_for_kind(document, "gp")),
        ("effect", _payloads_for_kind(document, "effect")),
    )
    coma_uids = {
        node.display_id: node.native_uid
        for node in document.nodes.values()
        if node.kind == "coma" and node.native_uid
    }
    return page_document_from_payload(
        project_uid=document.project_uid,
        page_uid=document.page_uid,
        revision=document.revision + 1,
        work_payload=work_payload,
        page_payload=payload,
        coma_uids=coma_uids,
        native_payloads=native_payloads,
        links=document.links,
    )


def _apply_page_summaries(work, summaries: Iterable[PageSummary]) -> None:
    source_display_ids = {
        summary.uid: summary.display_id for summary in summaries
    }
    work.pages.clear()
    for summary in summaries:
        entry = work.pages.add()
        payload = copy.deepcopy(summary.settings)
        payload.update(
            {
                "id": summary.display_id,
                "title": summary.title,
                "spread": summary.spread,
                "originalPages": [
                    display_id
                    for uid, display_id in zip(
                        summary.source_page_uids,
                        summary.source_page_display_ids,
                        strict=True,
                    )
                    if display_id or uid in source_display_ids
                ],
                "dir": f"pages/{summary.uid}/",
            }
        )
        schema.page_entry_from_dict(entry, payload)
        _custom_set(entry, PAGE_UID_PROP, summary.uid)
        _custom_set(entry, PAGE_REVISION_PROP, 0)
        _custom_set(
            entry,
            SOURCE_PAGE_UIDS_PROP,
            json.dumps(list(summary.source_page_uids), separators=(",", ":")),
        )
    work.active_page_index = 0 if len(work.pages) else -1


def page_document_from_projection(
    work,
    page,
    *,
    context=None,
    preserve_document: PageDocument | None = None,
) -> PageDocument:
    project_uid = ensure_project_uid(work)
    page_uid = ensure_page_uid(page, project_uid)
    revision = int(_custom_get(page, PAGE_REVISION_PROP, 0) or 0)
    preserved = preserve_document
    if context is None and preserved is None:
        preserved = domain_projection_preservation.stored_page_document(
            work, page_uid
        )
    if (
        context is None
        and preserved is not None
        and not bool(getattr(page, "detail_loaded", False))
    ):
        raise RuntimeError("Page projection is not loaded")
    coma_uids = {
        str(getattr(entry, "coma_id", "") or getattr(entry, "id", "") or ""):
        ensure_coma_uid(entry, page_uid)
        for entry in getattr(page, "comas", ())
    }
    work_payload = schema.work_to_dict(work)
    if preserved is not None:
        domain_projection_preservation.preserve_work_payloads(
            work_payload,
            preserved,
            _payloads_for_kind,
        )
    native_payloads = domain_projection_preservation.native_layer_payloads(
        context,
        str(getattr(page, "id", "") or ""),
        NODE_UID_PROP,
    )
    if preserved is not None:
        native_by_kind = dict(native_payloads)
        native_payloads = tuple(
            (
                kind,
                domain_projection_preservation.merge_payload_values(
                    native_by_kind.get(kind, ()),
                    _payloads_for_kind(preserved, kind),
                ),
            )
            for kind in ("gp", "effect")
        )
    document = page_document_from_payload(
        project_uid=project_uid,
        page_uid=page_uid,
        revision=max(0, revision),
        work_payload=work_payload,
        page_payload=schema.page_to_dict(page),
        coma_uids=coma_uids,
        native_payloads=native_payloads,
    )
    aliases: dict[str, str] = {}
    for node in document.nodes.values():
        aliases[node.display_id] = node.uid
        aliases[f"{node.kind}:{node.display_id}"] = node.uid
    current_links = _links_from_context(context, page_uid, aliases)
    if preserved is not None and context is None:
        document.links = copy.deepcopy(preserved.links)
    elif preserved is not None:
        document.links = domain_projection_preservation.merge_links(
            preserved.links,
            current_links,
        )
    else:
        document.links = current_links
    document = preserve_page_projection(preserved, document)
    document.validate()
    return document


def preserve_page_projection(
    authoritative: PageDocument | None,
    projection: PageDocument,
) -> PageDocument:
    """既知UI fieldだけを上書きし、Domain専用field/nodeを保持する。"""
    if authoritative is None:
        projection.validate()
        return copy.deepcopy(projection)
    authoritative.validate()
    if (
        authoritative.project_uid != projection.project_uid
        or authoritative.page_uid != projection.page_uid
    ):
        raise ValueError("page projection UID mismatch")
    result = copy.deepcopy(projection)
    result.settings = {
        **copy.deepcopy(authoritative.settings),
        **copy.deepcopy(result.settings),
    }
    for uid, node in result.nodes.items():
        old = authoritative.nodes.get(uid)
        if old is not None:
            node.settings = {
                **copy.deepcopy(old.settings),
                **copy.deepcopy(node.settings),
            }
    unknown_uids = {
        uid
        for uid, node in authoritative.nodes.items()
        if uid not in result.nodes and node.kind not in _PROJECTED_NODE_KINDS
    }
    for uid in unknown_uids:
        result.nodes[uid] = copy.deepcopy(authoritative.nodes[uid])
        result.children[uid] = []
    if unknown_uids:
        for parent_uid, child_uids in authoritative.children.items():
            for child_uid in child_uids:
                if (
                    parent_uid not in result.nodes
                    or child_uid not in result.nodes
                    or (
                        parent_uid not in unknown_uids
                        and child_uid not in unknown_uids
                    )
                ):
                    continue
                for current_children in result.children.values():
                    if child_uid in current_children:
                        current_children.remove(child_uid)
                result.children[parent_uid].append(child_uid)
    result.validate()
    return result


def _node_from_payload(page_uid: str, kind: str, payload: object):
    if not isinstance(payload, dict):
        raise ValueError(f"{kind} payload must be an object")
    data = copy.deepcopy(payload)
    display_id = str(
        (data.get("comaId") if kind == "coma" else data.get("id"))
        or data.get("id")
        or ""
    ).strip()
    if not display_id:
        raise ValueError(f"{kind} entry has no stable display ID")
    candidate_uid = str(data.get("nodeUid", "") or "")
    node_uid = (
        candidate_uid
        if is_uid(candidate_uid, UIDKind.NODE)
        else derived_uid(UIDKind.NODE, page_uid, f"{kind}:{display_id}")
    )
    title = str(data.get("title", "") or "")
    parent_key = str(data.get("folderKey") or data.get("parentKey") or "")
    settings = {
        key: value for key, value in data.items() if key not in _NODE_STRUCTURAL_FIELDS
    }
    native_uid = str(data.get("nativeUid", "") or "")
    node = DomainNode(node_uid, kind, display_id, title, settings, native_uid)
    aliases = (
        display_id,
        str(data.get("id", "") or ""),
        f"{kind}:{display_id}",
        f"{kind}:{data.get('parentKey', '')}:{display_id}",
    )
    return node, parent_key, tuple(alias for alias in aliases if alias)


def _build_children(
    root_uid: str,
    nodes: dict[str, DomainNode],
    parent_keys: dict[str, str],
    aliases: dict[str, str],
    ordered: list[str],
) -> dict[str, list[str]]:
    result = {uid: [] for uid in nodes}
    for uid in ordered:
        parent_uid = _resolve_parent_uid(parent_keys.get(uid, ""), aliases) or root_uid
        if parent_uid == uid:
            parent_uid = root_uid
        result[parent_uid].append(uid)
    return result


def _resolve_parent_uid(parent_key: str, aliases: Mapping[str, str]) -> str:
    if parent_key in aliases:
        return aliases[parent_key]
    parts = str(parent_key or "").split(":")
    for candidate in reversed(parts):
        if candidate in aliases:
            return aliases[candidate]
    return ""


def _links_from_context(context, page_uid: str, aliases: dict[str, str]):
    if context is None:
        return {}
    try:
        from ..utils import layer_links

        mapping = layer_links.load_map_strict(context)
    except Exception as exc:
        raise RuntimeError("Domain link collection failed") from exc
    return _links_from_mapping(page_uid, aliases, mapping)


def _links_from_mapping(
    page_uid: str,
    aliases: Mapping[str, str],
    mapping: Mapping[str, str],
) -> dict[str, DomainLink]:
    groups: dict[str, list[str]] = defaultdict(list)
    for old_uid, group in mapping.items():
        node_uid = _resolve_projection_link_uid(old_uid, aliases)
        if node_uid and node_uid not in groups[group]:
            groups[group].append(node_uid)
    result: dict[str, DomainLink] = {}
    for group, members in groups.items():
        if not members:
            continue
        existing_uid = str(group).removeprefix("domain_")
        link_uid = (
            existing_uid
            if is_uid(existing_uid, UIDKind.LINK)
            else derived_uid(UIDKind.LINK, page_uid, group)
        )
        result[link_uid] = DomainLink(link_uid, "linked-duplicate", tuple(members))
    return result


def domain_links_from_projection_mapping(
    document: PageDocument,
    mapping: Mapping[str, str],
) -> dict[str, DomainLink]:
    """Blender UIのlayer UID mapをDomain link graphへ一方向変換する。"""

    aliases: dict[str, str] = {}
    page_id = document.nodes[document.root_uid].display_id
    for node in document.nodes.values():
        aliases[node.display_id] = node.uid
        aliases[f"{node.kind}:{node.display_id}"] = node.uid
        if node.kind in {"balloon", "text"}:
            aliases[f"{node.kind}:{page_id}:{node.display_id}"] = node.uid
    return _links_from_mapping(document.page_uid, aliases, mapping)


def _resolve_projection_link_uid(
    value: str,
    aliases: Mapping[str, str],
) -> str:
    if value in aliases:
        return aliases[value]
    parts = str(value or "").split(":")
    for candidate in reversed(parts):
        if candidate in aliases:
            return aliases[candidate]
    return ""


def apply_page_document(page, document: PageDocument, *, context=None) -> None:
    document.validate()
    # page本体だけでなくscene-ownedレイヤー、UID、リンクまでを1つの投影
    # 区間として扱う。途中の画像タイトルupdate等からlayer stack/previewが
    # 走ると、同じpage.jsonのオンデマンド再読込が再入して外側のcollection
    # を空にするため、全要素が揃うまで副作用を止める。
    with schema._suspend_load_property_side_effects():
        _custom_set(page, PAGE_UID_PROP, document.page_uid)
        _custom_set(page, PAGE_REVISION_PROP, document.revision)
        payload = copy.deepcopy(document.settings)
        payload.update(
            {
                "schemaVersion": schema.PAGE_SCHEMA_VERSION,
                "id": str(getattr(page, "id", "") or ""),
                "title": str(getattr(page, "title", "") or ""),
                "spread": bool(getattr(page, "spread", False)),
                "comas": _payloads_for_kind(document, "coma"),
                "balloons": _payloads_for_kind(document, "balloon"),
                "texts": _payloads_for_kind(document, "text"),
            }
        )
        schema.page_from_dict(page, payload)
        coma_nodes = {
            node.display_id: node
            for node in document.nodes.values()
            if node.kind == "coma"
        }
        for entry in getattr(page, "comas", ()):
            display_id = str(
                getattr(entry, "coma_id", "") or getattr(entry, "id", "") or ""
            )
            node = coma_nodes.get(display_id)
            if node is not None and node.native_uid:
                _custom_set(entry, COMA_UID_PROP, node.native_uid)
        _apply_page_owned_collections(page, document)
        domain_projection_binding.bind_projection_node_uids(
            page,
            document,
            context=context,
        )
        domain_projection_binding.apply_link_projection(page, document, context)


def _payloads_for_kind(document: PageDocument, kind: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    parents = domain_projection_tree.parent_map(document)
    page_id = document.nodes[document.root_uid].display_id
    for uid in domain_projection_tree.tree_order(document):
        node = document.nodes[uid]
        if node.kind != kind:
            continue
        payload = copy.deepcopy(node.settings)
        payload["id"] = node.display_id
        payload["title"] = node.title
        payload["nodeUid"] = node.uid
        if node.native_uid:
            payload["nativeUid"] = node.native_uid
        parent = document.nodes.get(parents.get(uid, ""))
        if node.kind == "folder":
            parent_kind, parent_key = _projection_parent(
                document, parent, parents, page_id
            )
            payload["parentKind"] = parent_kind
            payload["parentKey"] = (
                parent.display_id
                if parent is not None and parent.kind == "folder"
                else parent_key
            )
        else:
            semantic = parent
            if parent is not None and parent.kind == "folder":
                payload["folderKey"] = parent.display_id
                semantic = _semantic_parent(document, parent, parents)
            parent_kind, parent_key = _projection_parent(
                document, semantic, parents, page_id
            )
            payload["parentKind"] = parent_kind
            payload["parentKey"] = parent_key
        result.append(payload)
    return result


def _semantic_parent(
    document: PageDocument,
    node: DomainNode,
    parents: Mapping[str, str],
) -> DomainNode:
    current = node
    seen: set[str] = set()
    while current.kind == "folder" and current.uid not in seen:
        seen.add(current.uid)
        current = document.nodes.get(parents.get(current.uid, ""), document.nodes[document.root_uid])
    return current


def _projection_parent(
    document: PageDocument,
    parent: DomainNode | None,
    parents: Mapping[str, str],
    page_id: str,
) -> tuple[str, str]:
    if parent is None or parent.uid == document.root_uid or parent.kind == "page":
        return "page", page_id
    if parent.kind == "coma":
        return "coma", f"{page_id}:{parent.display_id}"
    if parent.kind == "folder":
        semantic = _semantic_parent(document, parent, parents)
        return _projection_parent(document, semantic, parents, page_id)
    return "none", ""


def _apply_page_owned_collections(page, document: PageDocument) -> None:
    work = _work_from_page(page)
    if work is not None and hasattr(work, "layer_folders"):
        _replace_collection(
            work.layer_folders,
            _payloads_for_kind(document, "folder"),
            schema.layer_folder_from_dict,
        )
    scene = getattr(page, "id_data", None)
    if scene is None:
        return
    _replace_collection(
        getattr(scene, "bmanga_raster_layers", None),
        _payloads_for_kind(document, "raster"),
        lambda entry, data: schema.raster_layer_from_dict(
            entry, data, opacity_percent=True
        ),
    )
    _replace_collection(
        getattr(scene, "bmanga_image_layers", None),
        _payloads_for_kind(document, "image"),
        lambda entry, data: schema.image_layer_from_dict(
            entry, data, opacity_percent=True
        ),
    )
    _replace_collection(
        getattr(scene, "bmanga_fill_layers", None),
        _payloads_for_kind(document, "fill"),
        lambda entry, data: schema.fill_layer_from_dict(
            entry, data, opacity_percent=True
        ),
    )
    _replace_collection(
        getattr(scene, "bmanga_image_path_layers", None),
        _payloads_for_kind(document, "image_path"),
        lambda entry, data: schema.image_path_layer_from_dict(
            entry, data, opacity_percent=True
        ),
    )


def _replace_collection(collection, payloads, loader) -> None:
    if collection is None:
        return
    collection.clear()
    for payload in payloads:
        entry = collection.add()
        loader(entry, payload)
        node_uid = str(payload.get("nodeUid", "") or "")
        if is_uid(node_uid, UIDKind.NODE):
            _custom_set(entry, NODE_UID_PROP, node_uid)


def _work_from_page(page):
    scene = getattr(page, "id_data", None)
    return getattr(scene, "bmanga_work", None) if scene is not None else None


__all__ = (
    "COMA_UID_PROP",
    "NODE_UID_PROP",
    "PAGE_REVISION_PROP",
    "PAGE_UID_PROP",
    "PROJECT_REVISION_PROP",
    "PROJECT_UID_PROP",
    "apply_page_document",
    "apply_project_document",
    "bind_page_document",
    "bind_project_document",
    "domain_links_from_projection_mapping",
    "ensure_coma_uid",
    "ensure_page_uid",
    "ensure_project_uid",
    "page_payload_from_document",
    "replace_page_projection_payload",
    "page_document_from_payload",
    "page_document_from_projection",
    "preserve_page_projection",
    "preserve_project_projection",
    "project_document_from_payload",
    "project_document_from_work",
)
