from __future__ import annotations

import importlib
import importlib.util
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


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_native_tree_test"


def _load_native_module():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules.setdefault(PACKAGE, package)
    core = importlib.import_module("bmanga_core")
    sys.modules.setdefault(f"{PACKAGE}.bmanga_core", core)
    for module_name in ("domain_ids", "domain_model", "domain_repository"):
        module = importlib.import_module(f"bmanga_core.{module_name}")
        sys.modules.setdefault(f"{PACKAGE}.bmanga_core.{module_name}", module)
    io_name = f"{PACKAGE}.io"
    io_package = types.ModuleType(io_name)
    io_package.__path__ = [str(ROOT / "io")]
    sys.modules.setdefault(io_name, io_package)
    for module_name in ("project_file_lock", "native_tree_transaction"):
        name = f"{io_name}.{module_name}"
        spec = importlib.util.spec_from_file_location(
            name,
            ROOT / "io" / f"{module_name}.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[f"{io_name}.native_tree_transaction"]


NATIVE = _load_native_module()
Addition = NATIVE.Addition
NativeTreeTransaction = NATIVE.NativeTreeTransaction
NativeTreeTransactionError = NATIVE.NativeTreeTransactionError
Owner = NATIVE.Owner
Removal = NATIVE.Removal
recover_pending_native_transactions = (
    NATIVE.recover_pending_native_transactions
)


PROJECT_UID = "project_0123456789abcdef0123456789abcdef"
PAGE_UID = derived_uid(UIDKind.PAGE, PROJECT_UID, "native")
ROOT_UID = derived_uid(UIDKind.NODE, PAGE_UID, "root")


def _project(*, page_referenced: bool) -> ProjectDocument:
    pages = [PageSummary(PAGE_UID, "p0001", 1)] if page_referenced else []
    return ProjectDocument(PROJECT_UID, 0, {"name": "native"}, pages)


def _page() -> PageDocument:
    root = DomainNode(ROOT_UID, "page", "p0001")
    return PageDocument(
        PROJECT_UID,
        PAGE_UID,
        0,
        ROOT_UID,
        {},
        {ROOT_UID: root},
        {ROOT_UID: []},
    )


def _write_page_tree(path: Path, payload: bytes) -> None:
    path.mkdir(parents=True)
    (path / "page.json").write_bytes(canonical_json_bytes(_page()))
    (path / "page.blend").write_bytes(payload)


def test_uncommitted_addition_is_removed_after_restart(tmp_path: Path):
    root = tmp_path / "Native.bmanga"
    repository = ProjectRepository(root)
    repository.checkpoint(_project(page_referenced=False))
    staged = tmp_path / "staged"
    _write_page_tree(staged, b"new")
    destination = repository.page_dir(PAGE_UID)
    transaction = NativeTreeTransaction(
        root,
        repository=repository,
        additions=(Addition(staged, destination, Owner("page", PAGE_UID)),),
    )
    transaction.prepare()
    transaction.apply()
    assert destination.is_dir()

    restarted = ProjectRepository(root)
    assert recover_pending_native_transactions(
        root,
        repository=restarted,
    ) == 1
    assert not destination.exists()
    assert not tuple(restarted.journal_dir.iterdir())


def test_committed_removal_is_finalized_after_restart(tmp_path: Path):
    root = tmp_path / "Native.bmanga"
    repository = ProjectRepository(root)
    repository.checkpoint(_project(page_referenced=True), [_page()])
    source = repository.page_dir(PAGE_UID)
    (source / "page.blend").write_bytes(b"old")
    transaction = NativeTreeTransaction(
        root,
        repository=repository,
        removals=(Removal(source, Owner("page", PAGE_UID)),),
    )
    transaction.prepare()
    transaction.apply()
    repository.checkpoint(_project(page_referenced=False))

    restarted = ProjectRepository(root)
    restarted.recover()
    assert recover_pending_native_transactions(
        root,
        repository=restarted,
    ) == 1
    assert not source.exists()
    assert not tuple(restarted.journal_dir.iterdir())


def test_same_path_derived_tree_replacement_is_atomic(tmp_path: Path):
    root = tmp_path / "Native.bmanga"
    repository = ProjectRepository(root)
    repository.checkpoint(_project(page_referenced=False))
    destination = repository.page_dir(PAGE_UID)
    _write_page_tree(destination, b"derived-old")
    staged = tmp_path / "replacement"
    _write_page_tree(staged, b"replacement")
    owner = Owner("page", PAGE_UID)
    transaction = NativeTreeTransaction(
        root,
        repository=repository,
        removals=(
            Removal(
                destination,
                owner,
                before_referenced=False,
                after_referenced=True,
            ),
        ),
        additions=(Addition(staged, destination, owner),),
    )
    transaction.prepare()
    transaction.apply()
    repository.checkpoint(_project(page_referenced=True))
    assert transaction.recover()
    assert (destination / "page.blend").read_bytes() == b"replacement"
    assert not tuple(repository.journal_dir.iterdir())


def test_corrupted_native_generation_fails_closed_and_retains_journal(
    tmp_path: Path,
):
    root = tmp_path / "Native.bmanga"
    repository = ProjectRepository(root)
    repository.checkpoint(_project(page_referenced=False))
    staged = tmp_path / "staged"
    _write_page_tree(staged, b"expected")
    destination = repository.page_dir(PAGE_UID)
    transaction = NativeTreeTransaction(
        root,
        repository=repository,
        additions=(Addition(staged, destination, Owner("page", PAGE_UID)),),
    )
    transaction.prepare()
    transaction.apply()
    (destination / "page.blend").write_bytes(b"tampered")

    with pytest.raises(NativeTreeTransactionError, match="differs"):
        recover_pending_native_transactions(
            root,
            repository=ProjectRepository(root),
        )
    assert transaction.journal.is_file()
    assert destination.is_dir()
