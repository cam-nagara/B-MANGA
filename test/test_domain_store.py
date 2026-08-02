from __future__ import annotations

import pytest

from bmanga_core.domain_ids import UIDKind, derived_uid
from bmanga_core.domain_model import (
    DomainLink,
    DomainNode,
    DomainValidationError,
    PageDocument,
    PageSummary,
    ProjectDocument,
)
from bmanga_core.domain_store import (
    ApplyPagePatch,
    ApplyProjectPatch,
    CommandError,
    DomainStore,
    PagePatch,
    ProjectPatch,
    SetProjectSetting,
    page_patch,
    project_patch,
)


PROJECT_UID = "project_0123456789abcdef0123456789abcdef"
PAGE_UID = derived_uid(UIDKind.PAGE, PROJECT_UID, "one")
ROOT_UID = derived_uid(UIDKind.NODE, PAGE_UID, "root")


def _project() -> ProjectDocument:
    return ProjectDocument(
        project_uid=PROJECT_UID,
        revision=0,
        settings={"paper": {"dpi": 600}},
        pages=[
            PageSummary(
                uid=PAGE_UID,
                display_id="p0001",
                display_number=1,
            )
        ],
    )


def _page() -> PageDocument:
    root = DomainNode(ROOT_UID, "page", "p0001")
    return PageDocument(
        project_uid=PROJECT_UID,
        page_uid=PAGE_UID,
        revision=0,
        root_uid=ROOT_UID,
        settings={},
        nodes={ROOT_UID: root},
        children={ROOT_UID: []},
    )


def test_command_updates_store_once_and_emits_one_event():
    store = DomainStore(_project(), {PAGE_UID: _page()})
    event = store.execute(SetProjectSetting("paper", {"dpi": 1200}))
    assert event.event_type == "project.setting.changed"
    assert event.revision == 1
    assert store.project.settings["paper"]["dpi"] == 1200
    assert store.dirty_project
    assert store.drain_events() == (event,)


def test_command_failure_rolls_back_memory_and_event_queue():
    store = DomainStore(_project(), {PAGE_UID: _page()})
    before = store.project.to_dict()
    with pytest.raises(Exception):
        store.execute(
            ApplyPagePatch(
                PagePatch(
                    "project_ffffffffffffffffffffffffffffffff",
                    PAGE_UID,
                    0,
                )
            )
        )
    assert store.project.to_dict() == before
    assert store.pages[PAGE_UID].to_dict() == _page().to_dict()
    assert not store.dirty_project
    assert not store.dirty_page_uids
    assert store.drain_events() == ()


def test_transaction_rolls_back_all_commands_as_one_unit():
    store = DomainStore(_project(), {PAGE_UID: _page()})
    with pytest.raises(RuntimeError, match="cancel"):
        with store.transaction():
            store.execute(SetProjectSetting("title", "changed"))
            raise RuntimeError("cancel")
    assert "title" not in store.project.settings
    assert store.drain_events() == ()


def test_page_hydration_preserves_store_identity_dirty_state_and_events():
    store = DomainStore(_project(), {PAGE_UID: _page()})
    event = store.execute(SetProjectSetting("title", "dirty project"))
    candidate = _page()
    candidate.settings["loaded"] = "disk"

    store.hydrate_page(candidate)

    assert store.project.settings["title"] == "dirty project"
    assert store.pages[PAGE_UID].settings["loaded"] == "disk"
    assert store.dirty_project
    assert PAGE_UID not in store.dirty_page_uids
    assert store.drain_events() == (event,)


def test_single_member_link_group_remains_valid_for_future_relinking():
    page = _page()
    link_uid = derived_uid(UIDKind.LINK, PAGE_UID, "single")
    page.links[link_uid] = DomainLink(
        link_uid,
        "linked-duplicate",
        (ROOT_UID,),
    )
    page.validate()
    assert PageDocument.from_dict(page.to_dict()).links[link_uid].members == (
        ROOT_UID,
    )


def test_duplicate_coma_native_uid_is_rejected():
    page = _page()
    native_uid = derived_uid(UIDKind.COMA, PAGE_UID, "native")
    first_uid = derived_uid(UIDKind.NODE, PAGE_UID, "coma-one")
    second_uid = derived_uid(UIDKind.NODE, PAGE_UID, "coma-two")
    page.nodes[first_uid] = DomainNode(
        first_uid,
        "coma",
        "c01",
        native_uid=native_uid,
    )
    page.nodes[second_uid] = DomainNode(
        second_uid,
        "coma",
        "c02",
        native_uid=native_uid,
    )
    page.children[ROOT_UID] = [first_uid, second_uid]
    page.children[first_uid] = []
    page.children[second_uid] = []

    with pytest.raises(DomainValidationError, match="duplicate coma native UID"):
        page.validate()


def test_node_cannot_belong_to_multiple_link_groups():
    page = _page()
    first_link_uid = derived_uid(UIDKind.LINK, PAGE_UID, "first")
    second_link_uid = derived_uid(UIDKind.LINK, PAGE_UID, "second")
    page.links[first_link_uid] = DomainLink(
        first_link_uid,
        "linked-duplicate",
        (ROOT_UID,),
    )
    page.links[second_link_uid] = DomainLink(
        second_link_uid,
        "linked-duplicate",
        (ROOT_UID,),
    )

    with pytest.raises(
        DomainValidationError,
        match="node belongs to multiple link groups",
    ):
        page.validate()


def test_project_projection_commit_rejects_stale_revision_without_mutation():
    store = DomainStore(_project(), {PAGE_UID: _page()})
    candidate = _project()
    candidate.settings["title"] = "first"
    patch = project_patch(store.project, candidate)
    event = store.execute(ApplyProjectPatch(patch))
    assert event.revision == 1
    committed = store.project.to_dict()

    with pytest.raises(CommandError, match="stale project patch"):
        store.execute(ApplyProjectPatch(patch))
    assert store.project.to_dict() == committed


def test_identical_projection_is_a_noop_without_revision_or_dirty_state():
    store = DomainStore(_project(), {PAGE_UID: _page()})

    project_event = store.execute(
        ApplyProjectPatch(project_patch(store.project, store.project))
    )
    page_event = store.execute(
        ApplyPagePatch(
            page_patch(store.pages[PAGE_UID], store.pages[PAGE_UID])
        )
    )

    assert project_event.event_type == "project.patch.noop"
    assert page_event.event_type == "page.patch.noop"
    assert project_event.revision == 0
    assert page_event.revision == 0
    assert not store.dirty_project
    assert not store.dirty_page_uids


def test_page_projection_commit_rejects_stale_revision_without_mutation():
    store = DomainStore(_project(), {PAGE_UID: _page()})
    candidate = _page()
    candidate.settings["title"] = "first"
    patch = page_patch(store.pages[PAGE_UID], candidate)
    event = store.execute(ApplyPagePatch(patch))
    assert event.revision == 1
    committed = store.pages[PAGE_UID].to_dict()

    with pytest.raises(CommandError, match="stale page patch"):
        store.execute(ApplyPagePatch(patch))
    assert store.pages[PAGE_UID].to_dict() == committed


def test_explicit_project_patch_preserves_unmentioned_domain_fields():
    project = _project()
    project.settings["domainOnly"] = {"future": True}
    store = DomainStore(project, {PAGE_UID: _page()})
    patch = ProjectPatch(
        PROJECT_UID,
        expected_revision=0,
        settings_upsert={"title": "UI edit"},
    )

    store.execute(ApplyProjectPatch(patch))

    assert store.project.settings["title"] == "UI edit"
    assert store.project.settings["domainOnly"] == {"future": True}


def test_explicit_page_patch_preserves_unmentioned_nodes_and_links():
    page = _page()
    extension_uid = derived_uid(UIDKind.NODE, PAGE_UID, "extension")
    link_uid = derived_uid(UIDKind.LINK, PAGE_UID, "extension")
    page.nodes[extension_uid] = DomainNode(
        extension_uid,
        "extension",
        "future-1",
        settings={"future": True},
    )
    page.children[ROOT_UID].append(extension_uid)
    page.children[extension_uid] = []
    page.links[link_uid] = DomainLink(
        link_uid,
        "future-link",
        (extension_uid,),
    )
    store = DomainStore(_project(), {PAGE_UID: page})

    store.execute(
        ApplyPagePatch(
            PagePatch(
                PROJECT_UID,
                PAGE_UID,
                expected_revision=0,
                settings_upsert={"title": "UI edit"},
            )
        )
    )

    committed = store.pages[PAGE_UID]
    assert committed.nodes[extension_uid].settings == {"future": True}
    assert committed.links[link_uid].members == (extension_uid,)


def test_page_removal_requires_an_explicit_patch_field():
    page = _page()
    extension_uid = derived_uid(UIDKind.NODE, PAGE_UID, "remove")
    page.nodes[extension_uid] = DomainNode(
        extension_uid,
        "extension",
        "future-remove",
    )
    page.children[ROOT_UID].append(extension_uid)
    page.children[extension_uid] = []
    store = DomainStore(_project(), {PAGE_UID: page})

    store.execute(
        ApplyPagePatch(
            PagePatch(
                PROJECT_UID,
                PAGE_UID,
                expected_revision=0,
                nodes_remove=(extension_uid,),
                children_remove=(extension_uid,),
                children_upsert={ROOT_UID: ()},
            )
        )
    )

    assert extension_uid not in store.pages[PAGE_UID].nodes
