from __future__ import annotations

import copy

import pytest

from bmanga_core.domain_ids import UIDKind, derived_uid
from bmanga_core.domain_model import (
    DomainLink,
    DomainNode,
    PageDocument,
    PageSummary,
    ProjectDocument,
)
from bmanga_core.domain_store import CommandError, DomainStore
from bmanga_core.layer_commands import ApplyLayerMutation


PROJECT = "project_0123456789abcdef0123456789abcdef"
PAGE1 = derived_uid(UIDKind.PAGE, PROJECT, "page1")
PAGE2 = derived_uid(UIDKind.PAGE, PROJECT, "page2")


def _uid(kind: UIDKind, scope: str, key: str) -> str:
    return derived_uid(kind, scope, key)


def _page(page_uid: str, display: str) -> PageDocument:
    root = _uid(UIDKind.NODE, page_uid, "root")
    balloon = _uid(UIDKind.NODE, page_uid, "balloon")
    text = _uid(UIDKind.NODE, page_uid, "text")
    link_uid = _uid(UIDKind.LINK, page_uid, "balloon-text")
    return PageDocument(
        PROJECT,
        page_uid,
        0,
        root,
        {},
        {
            root: DomainNode(root, "page", display),
            balloon: DomainNode(balloon, "balloon", f"{display}-balloon"),
            text: DomainNode(text, "text", f"{display}-text"),
        },
        {root: [balloon, text], balloon: [], text: []},
        {
            link_uid: DomainLink(
                link_uid,
                "balloon-text",
                (balloon, text),
            )
        },
    )


def _store() -> DomainStore:
    pages = [
        PageSummary(PAGE1, "p0001", 1),
        PageSummary(PAGE2, "p0002", 2),
    ]
    project = ProjectDocument(PROJECT, 0, {}, pages)
    return DomainStore(
        project,
        {PAGE1: _page(PAGE1, "p0001"), PAGE2: _page(PAGE2, "p0002")},
    )


def _node_by_kind(page: PageDocument, kind: str) -> DomainNode:
    return next(node for node in page.nodes.values() if node.kind == kind)


def test_apply_layer_mutation_success_and_noop():
    store = _store()
    candidate = store.pages[PAGE1]
    _node_by_kind(candidate, "text").title = "changed"
    event = store.execute(
        ApplyLayerMutation(PAGE1, candidate, 0, "text.update")
    )
    assert event.changed_fields == ("settings",)
    assert event.event_type == "layer.mutation.text.update"
    committed = store.pages[PAGE1]
    noop = store.execute(
        ApplyLayerMutation(PAGE1, committed, 1, "text.update")
    )
    assert noop.event_type == "layer.mutation.text.update.noop"
    assert store.pages[PAGE1].revision == 1


def test_apply_layer_mutation_reports_tree_link_and_settings_changes():
    store = _store()
    candidate = store.pages[PAGE1]
    balloon = _node_by_kind(candidate, "balloon")
    text = _node_by_kind(candidate, "text")
    candidate.children[candidate.root_uid] = [text.uid, balloon.uid]
    candidate.links = {}
    text.title = "updated"
    event = store.execute(
        ApplyLayerMutation(PAGE1, candidate, 0, "layer.update")
    )
    assert event.changed_fields == (
        "settings",
        "tree",
        "mask_parent",
        "links",
    )


def test_apply_layer_mutation_rejects_stale_page_root_and_operation():
    store = _store()
    current = store.pages[PAGE1]
    with pytest.raises(CommandError, match="stale"):
        store.execute(
            ApplyLayerMutation(PAGE1, current, 1, "tree.move")
        )
    other_page = copy.deepcopy(current)
    other_page.page_uid = PAGE2
    with pytest.raises(CommandError, match="another page"):
        store.execute(
            ApplyLayerMutation(PAGE1, other_page, 0, "tree.move")
        )
    changed_root = copy.deepcopy(current)
    changed_root.root_uid = _node_by_kind(current, "balloon").uid
    with pytest.raises(CommandError, match="page root"):
        store.execute(
            ApplyLayerMutation(PAGE1, changed_root, 0, "tree.move")
        )
    with pytest.raises(CommandError, match="invalid"):
        store.execute(
            ApplyLayerMutation(PAGE1, current, 0, "Invalid Operation")
        )
