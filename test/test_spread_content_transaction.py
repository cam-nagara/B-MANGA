from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

from bmanga_core.domain_ids import UIDKind, derived_uid
from bmanga_core.domain_model import (
    DomainNode,
    PageDocument,
    PageSummary,
    ProjectDocument,
    canonical_json_bytes,
)
from bmanga_core.domain_repository import ProjectRepository
from bmanga_core.faults import FaultPoint, arm_fault, isolated_faults


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_spread_transaction_test"
PROJECT_UID = "project_0123456789abcdef0123456789abcdef"


def _load_module():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules.setdefault(PACKAGE, package)
    core = __import__("bmanga_core", fromlist=["*"])
    sys.modules.setdefault(f"{PACKAGE}.bmanga_core", core)
    for module_name in (
        "domain_ids",
        "domain_model",
        "domain_repository",
        "domain_store",
        "faults",
    ):
        module = __import__(f"bmanga_core.{module_name}", fromlist=["*"])
        sys.modules.setdefault(f"{PACKAGE}.bmanga_core.{module_name}", module)
    io_name = f"{PACKAGE}.io"
    io_package = types.ModuleType(io_name)
    io_package.__path__ = [str(ROOT / "io")]
    sys.modules.setdefault(io_name, io_package)
    name = f"{io_name}.spread_page_content"
    spec = importlib.util.spec_from_file_location(name, ROOT / "io" / "spread_page_content.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SPREAD = _load_module()


class _Block(dict):
    def __init__(self, *args, parent=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.parent = parent


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _page_document(page_uid: str, display_id: str) -> PageDocument:
    root_uid = derived_uid(UIDKind.NODE, page_uid, "root")
    root = DomainNode(root_uid, "page", display_id)
    return PageDocument(
        project_uid=PROJECT_UID,
        page_uid=page_uid,
        revision=0,
        root_uid=root_uid,
        settings={},
        nodes={root_uid: root},
        children={root_uid: []},
    )


def _make_work(tmp_path: Path):
    work = tmp_path / "Rollback.bmanga"
    page_uids = {
        page_id: derived_uid(UIDKind.PAGE, PROJECT_UID, page_id)
        for page_id in ("p0001", "p0002", "p0001-0002")
    }
    old_pages = [
        PageSummary(page_uids["p0001"], "p0001", 1),
        PageSummary(page_uids["p0002"], "p0002", 2),
    ]
    old_project = ProjectDocument(PROJECT_UID, 0, {"name": "old"}, old_pages)
    repository = ProjectRepository(work)
    repository.checkpoint(
        old_project,
        [
            _page_document(page_uids["p0001"], "p0001"),
            _page_document(page_uids["p0002"], "p0002"),
        ],
    )
    for page_id in ("p0001", "p0002"):
        _write(
            repository.page_dir(page_uids[page_id]) / "page.blend",
            f"blend-{page_id}".encode(),
        )
    staged = tmp_path / "staged" / page_uids["p0001-0002"]
    _write(staged / "page.blend", b"merged-blend")
    target_page = _page_document(page_uids["p0001-0002"], "p0001-0002")
    _write(staged / "page.json", canonical_json_bytes(target_page))
    new_project = ProjectDocument(
        PROJECT_UID,
        1,
        {"name": "new"},
        [PageSummary(page_uids["p0001-0002"], "p0001-0002", 1, spread=True)],
    )
    baseline = __import__(
        f"{PACKAGE}.io.save_baseline",
        fromlist=["initialize_new_work_baseline"],
    )
    baseline.capture_loaded_baseline(
        work,
        repository.page_dir(page_uids["p0001"]) / "page.blend",
        page_json_paths=(
            repository.page_path(page_uids["p0001"]),
            repository.page_path(page_uids["p0002"]),
        ),
        content_paths=tuple(path for path in work.rglob("*") if path.is_file()),
    )
    return work, staged, page_uids, new_project


def _snapshot(work: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(work)): path.read_bytes()
        for path in work.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "phase",
    ("after_backup", "after_directory_install"),
)
def test_failed_install_restores_all_directories_and_domain(tmp_path: Path, phase: str):
    work, staged, page_uids, new_project = _make_work(tmp_path)
    before = _snapshot(work)
    with pytest.raises(SPREAD.SpreadContentError, match="強制失敗"):
        SPREAD._install_directories_and_domain(
            work,
            removals=(
                work / "pages" / page_uids["p0001"],
                work / "pages" / page_uids["p0002"],
            ),
            additions=(
                (
                    staged,
                    work / "pages" / page_uids["p0001-0002"],
                ),
            ),
            project_document=new_project,
            fail_phase=phase,
        )
    assert _snapshot(work) == before
    assert not (work / "pages" / page_uids["p0001-0002"]).exists()


def test_repository_install_failure_restores_directories_and_domain(tmp_path: Path):
    work, staged, page_uids, new_project = _make_work(tmp_path)
    before = _snapshot(work)
    with isolated_faults():
        arm_fault(FaultPoint.CHECKPOINT_AFTER_INSTALL, times=1)
        with pytest.raises(Exception, match="injected fault"):
            SPREAD._install_directories_and_domain(
                work,
                removals=(
                    work / "pages" / page_uids["p0001"],
                    work / "pages" / page_uids["p0002"],
                ),
                additions=(
                    (
                        staged,
                        work / "pages" / page_uids["p0001-0002"],
                    ),
                ),
                project_document=new_project,
                fail_phase="",
            )
    assert _snapshot(work) == before


def test_successful_install_replaces_tree_and_domain(tmp_path: Path):
    work, staged, page_uids, new_project = _make_work(tmp_path)
    SPREAD._install_directories_and_domain(
        work,
        removals=(
            work / "pages" / page_uids["p0001"],
            work / "pages" / page_uids["p0002"],
        ),
        additions=(
            (
                staged,
                work / "pages" / page_uids["p0001-0002"],
            ),
        ),
        project_document=new_project,
        fail_phase="",
    )
    assert not (work / "pages" / page_uids["p0001"]).exists()
    assert not (work / "pages" / page_uids["p0002"]).exists()
    target = work / "pages" / page_uids["p0001-0002"]
    assert (target / "page.blend").read_bytes() == b"merged-blend"
    assert ProjectRepository(work).load_project().to_dict() == new_project.to_dict()


def test_page_shell_ignores_only_top_level_derived_preview(tmp_path: Path):
    source = tmp_path / "source"
    _write(source / "page.blend", b"blend")
    _write(source / "page.json", b"{}")
    _write(source / "page_preview.png", b"derived")
    _write(source / "assets" / "page_preview.png", b"user-asset")
    destination = tmp_path / "destination"

    SPREAD._copy_page_shell(source, destination)

    assert not (destination / "page_preview.png").exists()
    assert (destination / "assets" / "page_preview.png").read_bytes() == b"user-asset"


def test_extra_asset_merge_does_not_conflict_on_derived_preview(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    destination = tmp_path / "destination"
    _write(first / "page_preview.png", b"first-preview")
    _write(second / "page_preview.png", b"second-preview")
    _write(first / "assets" / "one.bin", b"one")
    _write(second / "assets" / "two.bin", b"two")
    SPREAD._copy_page_shell(first, destination)

    SPREAD._merge_extra_assets(second, destination)

    assert not (destination / "page_preview.png").exists()
    assert (destination / "assets" / "one.bin").read_bytes() == b"one"
    assert (destination / "assets" / "two.bin").read_bytes() == b"two"


def test_merge_link_maps_separates_equal_group_names_and_records_reverse_map():
    first = {SPREAD.LINK_PROP: json.dumps({"gp:same": "shared-group"})}
    second = {SPREAD.LINK_PROP: json.dumps({"gp:same": "shared-group"})}
    maps = {
        "p0001": {"gp": {"same": "same"}},
        "p0002": {"gp": {"same": "second-gp"}},
    }

    merged, group_maps = SPREAD._merge_link_maps(
        first, second, maps, "p0001", "p0002", "p0001-0002"
    )

    assert set(merged) == {"gp:same", "gp:second-gp"}
    assert len(set(merged.values())) == 2
    assert group_maps["p0001"]["shared-group"] == "shared-group"
    assert group_maps["p0002"]["shared-group"] != "shared-group"


def test_id_remap_preserves_source_ownership_marker():
    block = {
        SPREAD.SOURCE_PAGE_PROP: "p0001",
        "bmanga_parent_key": "p0001:c01",
        "bmanga_title": "c01",
    }
    SPREAD._remap_id_properties(
        block,
        "p0001",
        "p0001-0002",
        {"coma": {"c01": "c02"}},
    )
    assert block[SPREAD.SOURCE_PAGE_PROP] == "p0001"
    assert block["bmanga_parent_key"] == "p0001-0002:c02"
    assert block["bmanga_title"] == "c01"


def test_coma_uid_copy_never_overwrites_existing_directory(tmp_path: Path):
    source_uid = "coma_0123456789abcdef0123456789abcdef"
    target_uid = "coma_fedcba9876543210fedcba9876543210"
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _write(source / "comas" / source_uid / "scene.blend", b"source")
    _write(destination / "comas" / target_uid / "scene.blend", b"existing")

    with pytest.raises(SPREAD.SpreadContentError, match="衝突"):
        SPREAD._copy_mapped_comas(
            source,
            destination,
            {source_uid: target_uid},
        )

    assert (
        source / "comas" / source_uid / "scene.blend"
    ).read_bytes() == b"source"
    assert (
        destination / "comas" / target_uid / "scene.blend"
    ).read_bytes() == b"existing"


def test_nested_linked_page_asset_is_blocked(monkeypatch, tmp_path: Path):
    source = tmp_path / "source"
    nested = source / "assets" / "linked.bin"
    _write(nested, b"outside")
    original = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == nested or original(path),
    )

    with pytest.raises(SPREAD.SpreadContentError, match="リンクされたページ資産"):
        SPREAD._copy_page_shell(source, tmp_path / "destination")


def test_only_regenerated_preview_directory_can_be_replaced_on_split(tmp_path: Path):
    derived = tmp_path / "p0001"
    _write(derived / "page_preview.png", b"preview")
    assert SPREAD._FS._is_derived_only_page_dir(derived) is True

    _write(derived / "user-note.txt", b"keep")
    assert SPREAD._FS._is_derived_only_page_dir(derived) is False


def test_empty_page_directory_is_not_treated_as_regenerated_preview(tmp_path: Path):
    empty = tmp_path / "p0001"
    empty.mkdir()
    assert SPREAD._FS._is_derived_only_page_dir(empty) is False


def test_selected_coma_copy_uses_uid_directory_and_fixed_native_name(tmp_path: Path):
    source_uid = "coma_0123456789abcdef0123456789abcdef"
    target_uid = "coma_fedcba9876543210fedcba9876543210"
    source = tmp_path / "spread"
    destination = tmp_path / "page"
    destination.mkdir()
    _write(source / "comas" / source_uid / "scene.blend", b"coma")
    _write(source / "comas" / source_uid / "preview.png", b"preview")

    SPREAD._copy_selected_comas(
        source,
        destination,
        {source_uid: target_uid},
    )

    target = destination / "comas" / target_uid
    assert (target / "scene.blend").read_bytes() == b"coma"
    assert (target / "preview.png").read_bytes() == b"preview"
    assert not (destination / "comas" / source_uid).exists()


def test_generated_layer_inherits_source_from_parent_marker():
    parent = _Block({SPREAD.SOURCE_PAGE_PROP: "p0002"})
    generated = _Block({"bmanga_balloon_fill_mesh_owner_id": "balloon_0002"}, parent=parent)
    memberships = {"p0001": {}, "p0002": {"balloon": ["balloon_0002"]}}
    assert SPREAD._source_for_block(generated, memberships) == "p0002"


def test_generated_layer_uses_unique_owner_reference_after_regeneration():
    generated = _Block({"bmanga_balloon_fill_mesh_owner_id": "balloon_0002"})
    memberships = {
        "p0001": {"balloon": ["balloon_0001"]},
        "p0002": {"balloon": ["balloon_0002"]},
    }
    assert SPREAD._source_for_block(generated, memberships) == "p0002"


def test_combined_page_helper_is_regenerated_for_each_split_page():
    helper = _Block({"bmanga_paper_bg_page_id": "p0001-0002"})
    assert SPREAD._is_regenerated_page_helper(helper, "p0001-0002") is True
    assert SPREAD._is_regenerated_page_helper(helper, "p0001") is False
