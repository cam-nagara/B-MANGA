from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_spread_transaction_test"


def _load_module():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules.setdefault(PACKAGE, package)
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


def _make_work(tmp_path: Path):
    work = tmp_path / "Rollback.bmanga"
    work.mkdir()
    _write(work / "work.json", b'{"old":"work"}\n')
    _write(work / "pages.json", b'{"old":"pages"}\n')
    for page_id in ("p0001", "p0002"):
        _write(work / page_id / "page.blend", f"blend-{page_id}".encode())
        _write(work / page_id / "page.json", f'{{"id":"{page_id}"}}\n'.encode())
    staged = tmp_path / "staged" / "p0001-0002"
    _write(staged / "page.blend", b"merged-blend")
    _write(staged / "page.json", b'{"id":"p0001-0002"}\n')
    baseline = __import__(
        f"{PACKAGE}.io.project_content_save_baseline",
        fromlist=["initialize_new_work_baseline"],
    )
    baseline.capture_loaded_baseline(
        work,
        work / "p0001" / "page.blend",
        page_json_paths=(work / "p0001" / "page.json", work / "p0002" / "page.json"),
        content_paths=tuple(path for path in work.rglob("*") if path.is_file()),
    )
    return work, staged


def _snapshot(work: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(work)): path.read_bytes()
        for path in work.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "phase",
    ("after_backup", "after_directory_install", "after_json_install"),
)
def test_failed_install_restores_all_directories_and_json(tmp_path: Path, phase: str):
    work, staged = _make_work(tmp_path)
    before = _snapshot(work)
    with pytest.raises(SPREAD.SpreadContentError, match="強制失敗"):
        SPREAD._install_directories_and_json(
            work,
            removals=(work / "p0001", work / "p0002"),
            additions=((staged, work / "p0001-0002"),),
            work_json={"new": "work"},
            pages_json={"new": "pages"},
            fail_phase=phase,
        )
    assert _snapshot(work) == before
    assert not (work / "p0001-0002").exists()


def test_successful_install_replaces_tree_and_json(tmp_path: Path):
    work, staged = _make_work(tmp_path)
    SPREAD._install_directories_and_json(
        work,
        removals=(work / "p0001", work / "p0002"),
        additions=((staged, work / "p0001-0002"),),
        work_json={"new": "work"},
        pages_json={"new": "pages"},
        fail_phase="",
    )
    assert not (work / "p0001").exists()
    assert not (work / "p0002").exists()
    assert (work / "p0001-0002" / "page.blend").read_bytes() == b"merged-blend"
    assert json.loads((work / "work.json").read_text(encoding="utf-8")) == {"new": "work"}
    assert json.loads((work / "pages.json").read_text(encoding="utf-8")) == {"new": "pages"}


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


def test_coma_artifact_rename_never_overwrites_existing_file(tmp_path: Path):
    coma_dir = tmp_path / "c02"
    _write(coma_dir / "c01.blend", b"source")
    _write(coma_dir / "c02.blend", b"existing")

    with pytest.raises(SPREAD.SpreadContentError, match="衝突"):
        SPREAD._FS._rename_coma_artifacts(coma_dir, "c01", "c02")

    assert (coma_dir / "c01.blend").read_bytes() == b"source"
    assert (coma_dir / "c02.blend").read_bytes() == b"existing"


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


def test_selected_coma_copy_restores_directory_and_artifact_ids(tmp_path: Path):
    source = tmp_path / "spread"
    destination = tmp_path / "page"
    destination.mkdir()
    _write(source / "c02" / "c02.blend", b"coma")
    _write(source / "c02" / "c02.json", b"{}")

    SPREAD._copy_selected_comas(source, destination, {"c02": "c01"})

    assert (destination / "c01" / "c01.blend").read_bytes() == b"coma"
    assert (destination / "c01" / "c01.json").read_bytes() == b"{}"
    assert not (destination / "c02").exists()


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
