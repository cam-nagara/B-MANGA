"""Commandだけが更新できるB-MANGA Domain Store。"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Protocol

from .domain_model import (
    DomainLink,
    DomainNode,
    DomainValidationError,
    PageDocument,
    PageSummary,
    ProjectDocument,
)


class CommandError(RuntimeError):
    """Commandの前提または検証に失敗した。"""


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_type: str
    target_uid: str
    revision: int
    changed_fields: tuple[str, ...] = ()


class DomainCommand(Protocol):
    def apply(self, store: "DomainStore") -> DomainEvent:
        """Storeを更新して確定Eventを返す。"""


@dataclass(frozen=True, slots=True)
class ProjectPatch:
    project_uid: str
    expected_revision: int
    settings_upsert: dict[str, object] = field(default_factory=dict)
    settings_remove: tuple[str, ...] = ()
    pages: tuple[PageSummary, ...] | None = None

    @property
    def is_empty(self) -> bool:
        return not (
            self.settings_upsert
            or self.settings_remove
            or self.pages is not None
        )


@dataclass(frozen=True, slots=True)
class PagePatch:
    project_uid: str
    page_uid: str
    expected_revision: int | None
    root_uid: str | None = None
    settings_upsert: dict[str, object] = field(default_factory=dict)
    settings_remove: tuple[str, ...] = ()
    nodes_upsert: dict[str, DomainNode] = field(default_factory=dict)
    nodes_remove: tuple[str, ...] = ()
    children_upsert: dict[str, tuple[str, ...]] = field(default_factory=dict)
    children_remove: tuple[str, ...] = ()
    links_upsert: dict[str, DomainLink] = field(default_factory=dict)
    links_remove: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (
            self.root_uid is not None
            or self.settings_upsert
            or self.settings_remove
            or self.nodes_upsert
            or self.nodes_remove
            or self.children_upsert
            or self.children_remove
            or self.links_upsert
            or self.links_remove
        )


def project_patch(
    current: ProjectDocument,
    candidate: ProjectDocument,
    *,
    require_candidate_revision: bool = True,
) -> ProjectPatch:
    """二つの文書から、変更したDomainフィールドだけのCommand入力を作る。"""
    current.validate()
    candidate.validate()
    if candidate.project_uid != current.project_uid:
        raise CommandError("project patch belongs to another project")
    if require_candidate_revision and candidate.revision != current.revision:
        raise CommandError("stale project projection")
    upsert, remove = _mapping_delta(current.settings, candidate.settings)
    pages = (
        tuple(copy.deepcopy(candidate.pages))
        if current.pages != candidate.pages
        else None
    )
    return ProjectPatch(
        project_uid=current.project_uid,
        expected_revision=current.revision,
        settings_upsert=upsert,
        settings_remove=remove,
        pages=pages,
    )


def page_patch(
    current: PageDocument | None,
    candidate: PageDocument,
    *,
    require_candidate_revision: bool = True,
) -> PagePatch:
    """一ページの設定・node・tree・linkを明示deltaへ変換する。"""
    candidate.validate()
    if current is None:
        if require_candidate_revision and candidate.revision != 0:
            raise CommandError("new page projection has a stale revision")
        return PagePatch(
            project_uid=candidate.project_uid,
            page_uid=candidate.page_uid,
            expected_revision=None,
            root_uid=candidate.root_uid,
            settings_upsert=copy.deepcopy(candidate.settings),
            nodes_upsert=copy.deepcopy(candidate.nodes),
            children_upsert={
                uid: tuple(children)
                for uid, children in candidate.children.items()
            },
            links_upsert=copy.deepcopy(candidate.links),
        )
    current.validate()
    if (
        candidate.project_uid != current.project_uid
        or candidate.page_uid != current.page_uid
    ):
        raise CommandError("page patch belongs to another page")
    if require_candidate_revision and candidate.revision != current.revision:
        raise CommandError("stale page projection")
    settings_upsert, settings_remove = _mapping_delta(
        current.settings,
        candidate.settings,
    )
    nodes_upsert, nodes_remove = _mapping_delta(current.nodes, candidate.nodes)
    children_upsert, children_remove = _children_delta(
        current.children,
        candidate.children,
    )
    links_upsert, links_remove = _mapping_delta(current.links, candidate.links)
    return PagePatch(
        project_uid=candidate.project_uid,
        page_uid=candidate.page_uid,
        expected_revision=current.revision,
        root_uid=(
            candidate.root_uid
            if candidate.root_uid != current.root_uid
            else None
        ),
        settings_upsert=settings_upsert,
        settings_remove=settings_remove,
        nodes_upsert=nodes_upsert,
        nodes_remove=nodes_remove,
        children_upsert=children_upsert,
        children_remove=children_remove,
        links_upsert=links_upsert,
        links_remove=links_remove,
    )


@dataclass(frozen=True, slots=True)
class ApplyProjectPatch:
    patch: ProjectPatch

    def apply(self, store: "DomainStore") -> DomainEvent:
        patch = self.patch
        current = store._project
        if patch.project_uid != current.project_uid:
            raise CommandError("project patch belongs to another project")
        if patch.expected_revision != current.revision:
            raise CommandError("stale project patch")
        if patch.is_empty:
            return DomainEvent(
                "project.patch.noop",
                current.project_uid,
                current.revision,
            )
        candidate = copy.deepcopy(current)
        _apply_mapping_delta(
            candidate.settings,
            patch.settings_upsert,
            patch.settings_remove,
        )
        changed_fields: list[str] = []
        if patch.settings_upsert or patch.settings_remove:
            changed_fields.append("settings")
        if patch.pages is not None:
            candidate.pages = list(copy.deepcopy(patch.pages))
            changed_fields.append("pages")
        candidate.revision += 1
        candidate.validate()
        store._project = candidate
        allowed = {page.uid for page in candidate.pages}
        store._pages = {
            uid: page for uid, page in store._pages.items() if uid in allowed
        }
        store._dirty_pages.intersection_update(allowed)
        store._dirty_project = True
        return DomainEvent(
            "project.patch.applied",
            candidate.project_uid,
            candidate.revision,
            tuple(changed_fields),
        )


@dataclass(frozen=True, slots=True)
class ApplyPagePatch:
    patch: PagePatch

    def apply(self, store: "DomainStore") -> DomainEvent:
        patch = self.patch
        if patch.project_uid != store._project.project_uid:
            raise CommandError("page patch belongs to another project")
        current = store._pages.get(patch.page_uid)
        if current is None:
            if patch.expected_revision is not None:
                raise CommandError("stale page patch")
            if patch.is_empty:
                raise CommandError("new page patch is empty")
            candidate = PageDocument(
                project_uid=patch.project_uid,
                page_uid=patch.page_uid,
                revision=0,
                root_uid=patch.root_uid or "",
                settings={},
                nodes={},
                children={},
                links={},
            )
        else:
            if patch.expected_revision != current.revision:
                raise CommandError("stale page patch")
            if patch.is_empty:
                return DomainEvent(
                    "page.patch.noop",
                    current.page_uid,
                    current.revision,
                )
            candidate = copy.deepcopy(current)
            candidate.revision += 1
        if patch.root_uid is not None:
            candidate.root_uid = patch.root_uid
        _apply_mapping_delta(
            candidate.settings,
            patch.settings_upsert,
            patch.settings_remove,
        )
        _apply_mapping_delta(
            candidate.nodes,
            patch.nodes_upsert,
            patch.nodes_remove,
        )
        _apply_mapping_delta(
            candidate.children,
            {uid: list(children) for uid, children in patch.children_upsert.items()},
            patch.children_remove,
        )
        _apply_mapping_delta(
            candidate.links,
            patch.links_upsert,
            patch.links_remove,
        )
        candidate.validate()
        store._pages[candidate.page_uid] = candidate
        store._dirty_pages.add(candidate.page_uid)
        changed_fields = tuple(
            label
            for label, changed in (
                ("settings", patch.settings_upsert or patch.settings_remove),
                (
                    "tree",
                    patch.root_uid is not None
                    or patch.nodes_upsert
                    or patch.nodes_remove
                    or patch.children_upsert
                    or patch.children_remove,
                ),
                ("links", patch.links_upsert or patch.links_remove),
            )
            if changed
        )
        return DomainEvent(
            "page.patch.applied",
            candidate.page_uid,
            candidate.revision,
            changed_fields,
        )


def _mapping_delta(before, after):
    upsert = {
        key: copy.deepcopy(value)
        for key, value in after.items()
        if key not in before or before[key] != value
    }
    remove = tuple(key for key in before if key not in after)
    return upsert, remove


def _children_delta(before, after):
    upsert = {
        key: tuple(value)
        for key, value in after.items()
        if key not in before or before[key] != value
    }
    remove = tuple(key for key in before if key not in after)
    return upsert, remove


def _apply_mapping_delta(target, upsert, remove) -> None:
    for key in remove:
        target.pop(key, None)
    for key, value in upsert.items():
        target[key] = copy.deepcopy(value)


@dataclass(frozen=True, slots=True)
class SetProjectSetting:
    key: str
    value: object

    def apply(self, store: "DomainStore") -> DomainEvent:
        key = str(self.key or "").strip()
        if not key:
            raise CommandError("setting key is required")
        candidate = copy.deepcopy(store._project)
        candidate.settings[key] = copy.deepcopy(self.value)
        candidate.revision += 1
        candidate.validate()
        store._project = candidate
        store._dirty_project = True
        return DomainEvent(
            "project.setting.changed",
            candidate.project_uid,
            candidate.revision,
            (key,),
        )


@dataclass(frozen=True, slots=True)
class SetPageLinks:
    page_uid: str
    links: dict[str, DomainLink]

    def apply(self, store: "DomainStore") -> DomainEvent:
        candidate = copy.deepcopy(store.require_page(self.page_uid))
        candidate.links = copy.deepcopy(self.links)
        candidate.revision += 1
        candidate.validate()
        store._pages[candidate.page_uid] = candidate
        store._dirty_pages.add(candidate.page_uid)
        return DomainEvent(
            "page.links.changed",
            candidate.page_uid,
            candidate.revision,
            ("links",),
        )


@dataclass(slots=True)
class _Snapshot:
    project: ProjectDocument
    pages: dict[str, PageDocument]
    dirty_project: bool
    dirty_pages: set[str]
    events: list[DomainEvent]


@dataclass(slots=True)
class DomainStore:
    _project: ProjectDocument
    _pages: dict[str, PageDocument] = field(default_factory=dict)
    _dirty_project: bool = False
    _dirty_pages: set[str] = field(default_factory=set)
    _events: list[DomainEvent] = field(default_factory=list)
    _transaction_depth: int = 0

    def __post_init__(self) -> None:
        self._project = copy.deepcopy(self._project)
        self._pages = copy.deepcopy(self._pages)
        self.validate()

    @property
    def project(self) -> ProjectDocument:
        return copy.deepcopy(self._project)

    @property
    def pages(self) -> dict[str, PageDocument]:
        return copy.deepcopy(self._pages)

    @property
    def dirty_project(self) -> bool:
        return self._dirty_project

    @property
    def dirty_page_uids(self) -> frozenset[str]:
        return frozenset(self._dirty_pages)

    def validate(self) -> None:
        self._project.validate()
        self._validate_index()
        for page in self._pages.values():
            page.validate()

    def _validate_index(self) -> None:
        """Commandが検証済み文書を差し替えた後のStore索引だけを検査する。"""

        expected = {page.uid for page in self._project.pages}
        if not set(self._pages) <= expected:
            raise DomainValidationError("store contains page outside project")
        for uid, page in self._pages.items():
            if uid != page.page_uid:
                raise DomainValidationError("page key/UID mismatch")
            if page.project_uid != self._project.project_uid:
                raise DomainValidationError("page project UID mismatch")

    def require_page(self, page_uid: str) -> PageDocument:
        try:
            return self._pages[page_uid]
        except KeyError as exc:
            raise CommandError(f"unknown page UID: {page_uid}") from exc

    def execute(self, command: DomainCommand) -> DomainEvent:
        snapshot = self._snapshot()
        try:
            event = command.apply(self)
            # 組込みCommandは変更したProject/Pageをapply内で検証し、
            # 既存文書をcopy-on-writeで差し替える。全ページ再検証は
            # 55/80ページの単一ページ編集を二次時間化するため索引に限定する。
            self._validate_index()
        except BaseException:
            self._restore(snapshot)
            raise
        self._events.append(event)
        return event

    @contextmanager
    def transaction(self) -> Iterator["DomainStore"]:
        snapshot = self._snapshot()
        self._transaction_depth += 1
        try:
            yield self
            self._validate_index()
        except BaseException:
            self._restore(snapshot)
            raise
        finally:
            self._transaction_depth -= 1

    def drain_events(self) -> tuple[DomainEvent, ...]:
        events = tuple(self._events)
        self._events.clear()
        return events

    def mark_checkpointed(
        self,
        *,
        project: bool = True,
        page_uids: tuple[str, ...] | None = None,
    ) -> None:
        if project:
            self._dirty_project = False
        if page_uids is None:
            self._dirty_pages.clear()
        else:
            for uid in page_uids:
                self._dirty_pages.discard(uid)

    def hydrate_page(self, page: PageDocument) -> None:
        """読込済み一ページだけをStoreへ載せ、他のdirty/Eventを保つ。"""

        page.validate()
        expected = {summary.uid for summary in self._project.pages}
        if page.page_uid not in expected:
            raise DomainValidationError("hydrated page is outside project")
        if page.project_uid != self._project.project_uid:
            raise DomainValidationError("hydrated page belongs to another project")
        self._pages[page.page_uid] = copy.deepcopy(page)
        self._dirty_pages.discard(page.page_uid)

    def _snapshot(self) -> _Snapshot:
        # 組込みCommandはProject/Pageを必ずcopy-on-writeで差し替える。
        # 参照の浅い退避でrollbackでき、単一ページ変更のたびに全ページを
        # deepcopyする二次時間化を避けられる。
        return _Snapshot(
            project=self._project,
            pages=dict(self._pages),
            dirty_project=self._dirty_project,
            dirty_pages=set(self._dirty_pages),
            events=list(self._events),
        )

    def _restore(self, snapshot: _Snapshot) -> None:
        self._project = snapshot.project
        self._pages = snapshot.pages
        self._dirty_project = snapshot.dirty_project
        self._dirty_pages = snapshot.dirty_pages
        self._events = snapshot.events


__all__ = (
    "ApplyPagePatch",
    "ApplyProjectPatch",
    "CommandError",
    "DomainCommand",
    "DomainEvent",
    "DomainStore",
    "PagePatch",
    "ProjectPatch",
    "SetPageLinks",
    "SetProjectSetting",
    "page_patch",
    "project_patch",
)
