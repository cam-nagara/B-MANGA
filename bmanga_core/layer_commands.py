"""Blender投影を原子的に確定するLayer Command。"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from .domain_model import DomainNode, PageDocument
from .domain_store import CommandError, DomainEvent, DomainStore


@dataclass(frozen=True, slots=True)
class ApplyLayerMutation:
    """UI操作後の投影を、現在Revisionに対して一度だけ確定する。"""

    page_uid: str
    candidate: PageDocument
    expected_revision: int
    operation: str

    def apply(self, store: DomainStore) -> DomainEvent:
        operation = str(self.operation or "")
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]*", operation) is None:
            raise CommandError("invalid layer mutation operation")
        current = _current_page(store, self.page_uid, self.expected_revision)
        candidate = copy.deepcopy(self.candidate)
        _validate_mutation_identity(current, candidate, self.expected_revision)
        candidate.validate()
        event_type = f"layer.mutation.{operation}"
        if candidate == current:
            return DomainEvent(
                f"{event_type}.noop",
                current.page_uid,
                current.revision,
            )
        changed_fields = _mutation_changed_fields(current, candidate)
        candidate.revision = current.revision + 1
        candidate.validate()
        store._pages[candidate.page_uid] = candidate
        store._dirty_pages.add(candidate.page_uid)
        return DomainEvent(
            event_type,
            candidate.page_uid,
            candidate.revision,
            changed_fields,
        )


def _current_page(
    store: DomainStore,
    page_uid: str,
    expected_revision: int,
) -> PageDocument:
    current = store.require_page(page_uid)
    if current.revision != expected_revision:
        raise CommandError("stale page revision")
    return current


def _validate_mutation_identity(
    current: PageDocument,
    candidate: PageDocument,
    expected_revision: int,
) -> None:
    if candidate.revision != expected_revision:
        raise CommandError("stale layer mutation candidate")
    if candidate.project_uid != current.project_uid:
        raise CommandError("layer mutation belongs to another project")
    if candidate.page_uid != current.page_uid:
        raise CommandError("layer mutation belongs to another page")
    if candidate.root_uid != current.root_uid:
        raise CommandError("layer mutation cannot replace the page root")


def _node_structure(node: DomainNode) -> tuple[str, str, str, str]:
    return (node.uid, node.kind, node.display_id, node.native_uid)


def _mutation_changed_fields(
    current: PageDocument,
    candidate: PageDocument,
) -> tuple[str, ...]:
    common = set(current.nodes).intersection(candidate.nodes)
    node_structure_changed = (
        set(current.nodes) != set(candidate.nodes)
        or any(
            _node_structure(current.nodes[uid])
            != _node_structure(candidate.nodes[uid])
            for uid in common
        )
    )
    node_settings_changed = any(
        current.nodes[uid].title != candidate.nodes[uid].title
        or current.nodes[uid].settings != candidate.nodes[uid].settings
        for uid in common
    )
    settings_changed = (
        current.settings != candidate.settings or node_settings_changed
    )
    tree_changed = (
        node_structure_changed or current.children != candidate.children
    )
    fields: list[str] = []
    if settings_changed:
        fields.append("settings")
    if tree_changed:
        fields.extend(("tree", "mask_parent"))
    if current.links != candidate.links:
        fields.append("links")
    return tuple(fields)


__all__ = ("ApplyLayerMutation",)
