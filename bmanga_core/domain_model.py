"""Blender非依存のB-MANGA Domain Modelと厳格codec。"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .domain_ids import UIDKind, validate_uid


PROJECT_SCHEMA = "bmanga.project"
PAGE_SCHEMA = "bmanga.page"
SCHEMA_VERSION = 1


class DomainValidationError(ValueError):
    """Domain文書が正規形を満たさない。"""


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{label} must be an object")
    return copy.deepcopy(dict(value))


def _text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise DomainValidationError(f"{label} must be a trimmed string")
    if not allow_empty and not value:
        raise DomainValidationError(f"{label} is required")
    return value


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise DomainValidationError(f"{label} must be an integer >= {minimum}")
    return value


def _schema(payload: Mapping[str, Any], expected: str) -> None:
    if payload.get("schema") != expected:
        raise DomainValidationError(f"unsupported schema: {payload.get('schema')!r}")
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise DomainValidationError(
            f"unsupported {expected} schemaVersion: {payload.get('schemaVersion')!r}"
        )


@dataclass(slots=True)
class PageSummary:
    uid: str
    display_id: str
    display_number: int
    title: str = ""
    spread: bool = False
    source_page_uids: tuple[str, ...] = ()
    source_page_display_ids: tuple[str, ...] = ()
    settings: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        validate_uid(self.uid, UIDKind.PAGE)
        _text(self.display_id, "page display_id")
        _integer(self.display_number, "page display_number", minimum=1)
        _text(self.title, "page title", allow_empty=True)
        for value in self.source_page_uids:
            validate_uid(value, UIDKind.PAGE)
        if len(set(self.source_page_uids)) != len(self.source_page_uids):
            raise DomainValidationError("duplicate source page UID")
        if len(self.source_page_display_ids) != len(self.source_page_uids):
            raise DomainValidationError("source page UID/display ID count mismatch")
        for value in self.source_page_display_ids:
            _text(value, "source page display_id")
        if len(set(self.source_page_display_ids)) != len(
            self.source_page_display_ids
        ):
            raise DomainValidationError("duplicate source page display ID")
        _mapping(self.settings, "page summary settings")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "uid": self.uid,
            "displayId": self.display_id,
            "displayNumber": self.display_number,
            "title": self.title,
            "spread": bool(self.spread),
            "sourcePageUids": list(self.source_page_uids),
            "sourcePageDisplayIds": list(self.source_page_display_ids),
            "settings": copy.deepcopy(self.settings),
        }

    @classmethod
    def from_dict(cls, value: object) -> "PageSummary":
        data = _mapping(value, "page summary")
        result = cls(
            uid=data.get("uid"),
            display_id=data.get("displayId"),
            display_number=data.get("displayNumber"),
            title=data.get("title", ""),
            spread=bool(data.get("spread", False)),
            source_page_uids=tuple(data.get("sourcePageUids", ())),
            source_page_display_ids=tuple(
                data.get("sourcePageDisplayIds", ())
            ),
            settings=_mapping(data.get("settings", {}), "page summary settings"),
        )
        result.validate()
        return result


@dataclass(slots=True)
class DomainNode:
    uid: str
    kind: str
    display_id: str
    title: str = ""
    settings: dict[str, Any] = field(default_factory=dict)
    native_uid: str = ""

    def validate(self) -> None:
        validate_uid(self.uid, UIDKind.NODE)
        _text(self.kind, "node kind")
        _text(self.display_id, "node display_id")
        _text(self.title, "node title", allow_empty=True)
        _mapping(self.settings, "node settings")
        if self.native_uid:
            _text(self.native_uid, "native UID")
            if self.kind == "coma":
                validate_uid(self.native_uid, UIDKind.COMA)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "uid": self.uid,
            "kind": self.kind,
            "displayId": self.display_id,
            "title": self.title,
            "settings": copy.deepcopy(self.settings),
            "nativeUid": self.native_uid,
        }

    @classmethod
    def from_dict(cls, value: object) -> "DomainNode":
        data = _mapping(value, "domain node")
        result = cls(
            uid=data.get("uid"),
            kind=data.get("kind"),
            display_id=data.get("displayId"),
            title=data.get("title", ""),
            settings=_mapping(data.get("settings", {}), "node settings"),
            native_uid=data.get("nativeUid", ""),
        )
        result.validate()
        return result


@dataclass(slots=True)
class DomainLink:
    uid: str
    kind: str
    members: tuple[str, ...]

    def validate(self) -> None:
        validate_uid(self.uid, UIDKind.LINK)
        _text(self.kind, "link kind")
        if not self.members or len(set(self.members)) != len(self.members):
            raise DomainValidationError("link requires at least one unique member")
        for member in self.members:
            validate_uid(member, UIDKind.NODE)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"uid": self.uid, "kind": self.kind, "members": list(self.members)}

    @classmethod
    def from_dict(cls, value: object) -> "DomainLink":
        data = _mapping(value, "domain link")
        result = cls(
            uid=data.get("uid"),
            kind=data.get("kind"),
            members=tuple(data.get("members", ())),
        )
        result.validate()
        return result


@dataclass(slots=True)
class ProjectDocument:
    project_uid: str
    revision: int
    settings: dict[str, Any]
    pages: list[PageSummary] = field(default_factory=list)

    def validate(self) -> None:
        validate_uid(self.project_uid, UIDKind.PROJECT)
        _integer(self.revision, "project revision")
        _mapping(self.settings, "project settings")
        page_uids = [page.uid for page in self.pages]
        display_ids = [page.display_id for page in self.pages]
        if len(page_uids) != len(set(page_uids)):
            raise DomainValidationError("duplicate page UID")
        if len(display_ids) != len(set(display_ids)):
            raise DomainValidationError("duplicate page display ID")
        for page in self.pages:
            page.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": PROJECT_SCHEMA,
            "schemaVersion": SCHEMA_VERSION,
            "projectUid": self.project_uid,
            "revision": self.revision,
            "settings": copy.deepcopy(self.settings),
            "pageOrder": [page.uid for page in self.pages],
            "pages": {page.uid: page.to_dict() for page in self.pages},
        }

    @classmethod
    def from_dict(cls, value: object) -> "ProjectDocument":
        data = _mapping(value, "project document")
        _schema(data, PROJECT_SCHEMA)
        pages = _ordered_pages(data)
        result = cls(
            project_uid=data.get("projectUid"),
            revision=data.get("revision"),
            settings=_mapping(data.get("settings"), "project settings"),
            pages=pages,
        )
        result.validate()
        return result


def _ordered_pages(data: Mapping[str, Any]) -> list[PageSummary]:
    pages = _mapping(data.get("pages"), "pages")
    order = data.get("pageOrder")
    if not isinstance(order, Sequence) or isinstance(order, (str, bytes)):
        raise DomainValidationError("pageOrder must be an array")
    if len(order) != len(set(order)) or set(order) != set(pages):
        raise DomainValidationError("pageOrder/pages mismatch")
    return [PageSummary.from_dict(pages[uid]) for uid in order]


@dataclass(slots=True)
class PageDocument:
    project_uid: str
    page_uid: str
    revision: int
    root_uid: str
    settings: dict[str, Any]
    nodes: dict[str, DomainNode]
    children: dict[str, list[str]]
    links: dict[str, DomainLink] = field(default_factory=dict)

    def validate(self) -> None:
        validate_uid(self.project_uid, UIDKind.PROJECT)
        validate_uid(self.page_uid, UIDKind.PAGE)
        validate_uid(self.root_uid, UIDKind.NODE)
        _integer(self.revision, "page revision")
        _mapping(self.settings, "page settings")
        self._validate_nodes()
        self._validate_tree()
        self._validate_links()

    def _validate_nodes(self) -> None:
        if self.root_uid not in self.nodes:
            raise DomainValidationError("root node is missing")
        if set(self.nodes) != {node.uid for node in self.nodes.values()}:
            raise DomainValidationError("node key/UID mismatch")
        for node in self.nodes.values():
            node.validate()
        native_uids = [
            node.native_uid
            for node in self.nodes.values()
            if node.kind == "coma" and node.native_uid
        ]
        if len(native_uids) != len(set(native_uids)):
            raise DomainValidationError("duplicate coma native UID")

    def _validate_tree(self) -> None:
        if set(self.children) != set(self.nodes):
            raise DomainValidationError("every node must own one child list")
        flat = [uid for values in self.children.values() for uid in values]
        if len(flat) != len(set(flat)):
            raise DomainValidationError("node has multiple parents")
        if set(flat) != set(self.nodes) - {self.root_uid}:
            raise DomainValidationError("tree has orphan or root child reference")
        _assert_acyclic(self.root_uid, self.children)

    def _validate_links(self) -> None:
        if set(self.links) != {link.uid for link in self.links.values()}:
            raise DomainValidationError("link key/UID mismatch")
        linked_members: set[str] = set()
        for link in self.links.values():
            link.validate()
            if not set(link.members) <= set(self.nodes):
                raise DomainValidationError("link references unknown node")
            overlap = linked_members.intersection(link.members)
            if overlap:
                raise DomainValidationError(
                    "node belongs to multiple link groups"
                )
            linked_members.update(link.members)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": PAGE_SCHEMA,
            "schemaVersion": SCHEMA_VERSION,
            "projectUid": self.project_uid,
            "pageUid": self.page_uid,
            "revision": self.revision,
            "settings": copy.deepcopy(self.settings),
            "tree": {
                "rootUid": self.root_uid,
                "nodes": {uid: node.to_dict() for uid, node in self.nodes.items()},
                "children": copy.deepcopy(self.children),
            },
            "links": {uid: link.to_dict() for uid, link in self.links.items()},
        }

    @classmethod
    def from_dict(cls, value: object) -> "PageDocument":
        data = _mapping(value, "page document")
        _schema(data, PAGE_SCHEMA)
        tree = _mapping(data.get("tree"), "page tree")
        nodes = {
            str(uid): DomainNode.from_dict(node)
            for uid, node in _mapping(tree.get("nodes"), "page nodes").items()
        }
        children = _children(tree.get("children"))
        links = {
            str(uid): DomainLink.from_dict(link)
            for uid, link in _mapping(data.get("links"), "page links").items()
        }
        result = cls(
            project_uid=data.get("projectUid"),
            page_uid=data.get("pageUid"),
            revision=data.get("revision"),
            root_uid=tree.get("rootUid"),
            settings=_mapping(data.get("settings"), "page settings"),
            nodes=nodes,
            children=children,
            links=links,
        )
        result.validate()
        return result


def _children(value: object) -> dict[str, list[str]]:
    data = _mapping(value, "tree children")
    result: dict[str, list[str]] = {}
    for parent, children in data.items():
        if not isinstance(children, list) or not all(
            isinstance(child, str) for child in children
        ):
            raise DomainValidationError("children entries must be UID arrays")
        result[str(parent)] = list(children)
    return result


def _assert_acyclic(root_uid: str, children: Mapping[str, Sequence[str]]) -> None:
    seen: set[str] = set()
    active: set[str] = set()

    def visit(uid: str) -> None:
        if uid in active:
            raise DomainValidationError("tree contains a cycle")
        if uid in seen:
            return
        active.add(uid)
        for child in children.get(uid, ()):
            visit(child)
        active.remove(uid)
        seen.add(uid)

    visit(root_uid)
    if seen != set(children):
        raise DomainValidationError("tree contains unreachable nodes")


def canonical_json_bytes(document: ProjectDocument | PageDocument) -> bytes:
    """同一Domainなら常に同じbyte列を返す。"""

    return (
        json.dumps(
            document.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def document_hash(document: ProjectDocument | PageDocument) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


__all__ = (
    "DomainLink",
    "DomainNode",
    "DomainValidationError",
    "PAGE_SCHEMA",
    "PROJECT_SCHEMA",
    "PageDocument",
    "PageSummary",
    "ProjectDocument",
    "SCHEMA_VERSION",
    "canonical_json_bytes",
    "document_hash",
)
