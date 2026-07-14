from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "_bmanga_detail_migration_manifest_test",
    ROOT / "io" / "detail_data_migration_manifest.py",
)
MANIFEST = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MANIFEST)


class Scene(dict):
    pass


class Collection(dict):
    def __init__(self, **values):
        super().__init__(values.pop("properties"))
        self.__dict__.update(values)


class LayerModel:
    def __init__(self, identities):
        self.identities = identities

    def iter_layer_objects(self, kind):
        return [SimpleNamespace(stable_id=value) for value in self.identities[kind]]

    @staticmethod
    def stable_id(obj):
        return obj.stable_id


class ObjectNaming:
    PROP_PARENT_KEY = "bmanga_parent_key"
    PROP_TITLE = "bmanga_title"
    PROP_Z_INDEX = "bmanga_z_index"

    def __init__(self, collections):
        self.collections = collections
        self.parents = {}

    def find_collection_by_bmanga_id(self, stable_id, *, kind):
        if kind == "folder":
            return self.collections.get(stable_id)
        return self.parents.get((kind, stable_id))

    @staticmethod
    def get_kind(collection):
        return str(collection.get("bmanga_kind", ""))

    @staticmethod
    def get_bmanga_id(collection):
        return str(collection.get("bmanga_id", ""))


def _folder_record(stable_id, title, order, *, parent="p0001", hidden=False, locked=False):
    return {
        "stable_id": stable_id,
        "title": title,
        "parent_kind": "page",
        "parent_key": parent,
        "expanded": order == 0,
        "hidden": hidden,
        "locked": locked,
        "z_index": 200 + order * 10,
        "source_object": "legacy-gp",
        "source_group": title,
    }


def _fixture():
    folder_records = [
        _folder_record("folder-a", "人物", 0),
        _folder_record("folder-b", "背景", 1, hidden=True, locked=True),
    ]
    manifest = MANIFEST.build_manifest(
        "p0001",
        "source-sha256",
        current_ids={"gp": ["gp-existing"], "effect": []},
        migrated_records={
            "gp": [{"stable_id": "gp-new"}],
            "effect": [{"stable_id": "effect-new"}],
        },
        folder_records=folder_records,
        links={"gp:gp-new": "link-a", "effect:effect-new": "link-a"},
    )
    scene = Scene()
    scene.bmanga_work = SimpleNamespace(
        layer_folders=[
            SimpleNamespace(
                id=item["stable_id"],
                title=item["title"],
                parent_key=item["parent_key"],
                expanded=item["expanded"],
                visible=not item["hidden"],
                locked=item["locked"],
            )
            for item in folder_records
        ]
    )
    scene["links"] = '{"effect:effect-new":"link-a","gp:gp-new":"link-a"}'
    collections = {
        item["stable_id"]: Collection(
            properties={
                "bmanga_kind": "folder",
                "bmanga_id": item["stable_id"],
                "bmanga_title": item["title"],
                "bmanga_parent_key": item["parent_key"],
                "bmanga_z_index": item["z_index"],
            },
            hide_viewport=item["hidden"],
            hide_render=item["hidden"],
            hide_select=item["locked"],
            children=[],
        )
        for item in folder_records
    }
    page_collection = Collection(
        properties={"bmanga_kind": "page", "bmanga_id": "p0001"},
        hide_viewport=False,
        hide_render=False,
        hide_select=False,
        children=list(collections.values()),
    )
    scene.collection = Collection(
        properties={},
        hide_viewport=False,
        hide_render=False,
        hide_select=False,
        children=[page_collection],
    )
    model = LayerModel({"gp": ["gp-new", "gp-existing"], "effect": ["effect-new"]})
    naming = ObjectNaming(collections)
    naming.parents[("page", "p0001")] = page_collection
    MANIFEST.store_manifest(scene, manifest)
    return scene, model, naming, manifest


def test_manifest_roundtrip_matches_ids_counts_folder_order_state_and_links():
    scene, model, naming, manifest = _fixture()
    result = MANIFEST.validate_manifest(scene, "p0001", model, naming, "links")

    assert MANIFEST.load_manifest(scene) == manifest
    assert result == {
        "sourceSignature": "source-sha256",
        "gpIds": ["gp-existing", "gp-new"],
        "effectIds": ["effect-new"],
        "folderCount": 2,
    }


def test_existing_folder_capture_uses_saved_order_and_entry_state_as_source_of_truth():
    scene, _model, naming, _manifest = _fixture()
    records = MANIFEST.capture_existing_folders(scene, naming)

    assert [item["stable_id"] for item in records] == ["folder-a", "folder-b"]
    assert records[0]["hidden"] is False and records[0]["locked"] is False
    assert records[1]["hidden"] is True and records[1]["locked"] is True
    assert [item["z_index"] for item in records] == [200, 210]


@pytest.mark.parametrize("missing", ("entry", "collection"))
def test_existing_folder_capture_rejects_an_incomplete_full_list(missing):
    scene, _model, naming, _manifest = _fixture()
    if missing == "entry":
        scene.bmanga_work.layer_folders.pop()
    else:
        scene.collection.children[0].children.pop()
    with pytest.raises(ValueError):
        MANIFEST.capture_existing_folders(scene, naming)


@pytest.mark.parametrize(
    "corruption",
    ("id", "folder_order", "folder_state", "folder_extra", "link"),
)
def test_manifest_rejects_every_saved_result_divergence(corruption):
    scene, model, naming, _manifest = _fixture()
    if corruption == "id":
        model.identities["gp"] = ["gp-existing", "gp-other"]
    elif corruption == "folder_order":
        scene.bmanga_work.layer_folders.reverse()
    elif corruption == "folder_state":
        scene.bmanga_work.layer_folders[1].visible = True
    elif corruption == "folder_extra":
        scene.bmanga_work.layer_folders.append(SimpleNamespace(id="folder-extra"))
    else:
        scene["links"] = '{"gp:gp-new":"different"}'

    with pytest.raises(AssertionError):
        MANIFEST.validate_manifest(scene, "p0001", model, naming, "links")


def test_canonical_link_map_uses_explicit_uid_evidence_without_dropping_groups():
    result = MANIFEST.canonical_link_map(
        {"gp:ptr_old": "group-a", "effect:current": "group-b"},
        {"gp:ptr_old": "gp:gp-new", "effect:current": "effect:current"},
    )
    assert result == {"effect:current": "group-b", "gp:gp-new": "group-a"}


def test_outside_folder_parent_kind_is_preserved_explicitly():
    scene, _model, naming, _manifest = _fixture()
    entry = scene.bmanga_work.layer_folders[0]
    entry.parent_key = "__outside__"
    page = scene.collection.children[0]
    outside_child, page_child = page.children
    outside_child["bmanga_parent_key"] = "__outside__"
    outside = Collection(
        properties={"bmanga_kind": "outside", "bmanga_id": "__outside__"},
        hide_viewport=False,
        hide_render=False,
        hide_select=False,
        children=[outside_child],
    )
    page.children = [page_child]
    scene.collection.children = [outside, page]
    naming.parents[("outside", "__outside__")] = outside

    records = MANIFEST.capture_existing_folders(scene, naming)

    assert records[0]["parent_kind"] == "outside"


def test_inspection_facts_keep_page_version_and_parent_first_folder_order():
    records = {
        "gp": [{"stable_id": "gp-new"}],
        "effect": [],
        "groups": [
            _folder_record("folder-parent", "親", 0),
            {
                **_folder_record("folder-child", "子", 1, parent="folder-parent"),
                "parent_kind": "folder",
            },
        ],
    }
    existing = [_folder_record("folder-existing", "既存", 0)]
    facts = MANIFEST.build_inspection_facts(
        "p0001",
        0,
        records,
        {"gp:ptr_old": "gp:gp-new"},
        {"gp": [], "effect": []},
        existing,
        {"gp:gp-new": "group-a"},
    )

    assert facts["pageDetailDataVersion"] == 0
    assert [item["id"] for item in facts["folderManifest"]] == [
        "folder-parent",
        "folder-child",
    ]
    assert [item["id"] for item in facts["migrationManifest"]["folders"]] == [
        "folder-existing",
        "folder-parent",
        "folder-child",
    ]
    assert facts["migrationManifest"]["sourceSignature"] == facts["sourceSignature"]
