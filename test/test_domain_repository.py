from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

import bmanga_core.domain_repository as domain_repository
from bmanga_core.domain_ids import UIDKind, derived_uid
from bmanga_core.domain_model import DomainNode, PageDocument, PageSummary, ProjectDocument
from bmanga_core.domain_repository import (
    JournalRecoveryError,
    JournalState,
    LegacyFormatError,
    ProjectRepository,
    RepositoryConflictError,
    RepositoryError,
    SimulatedProcessCrash,
)
from bmanga_core.faults import FaultPoint, arm_fault, isolated_faults


PROJECT_UID = "project_0123456789abcdef0123456789abcdef"
PAGE_UID = derived_uid(UIDKind.PAGE, PROJECT_UID, "one")
ROOT_UID = derived_uid(UIDKind.NODE, PAGE_UID, "root")


def _documents(revision: int = 0):
    project = ProjectDocument(
        project_uid=PROJECT_UID,
        revision=revision,
        settings={"name": "作品"},
        pages=[PageSummary(PAGE_UID, "p0001", 1)],
    )
    root = DomainNode(ROOT_UID, "page", "p0001")
    page = PageDocument(
        project_uid=PROJECT_UID,
        page_uid=PAGE_UID,
        revision=revision,
        root_uid=ROOT_UID,
        settings={"offsetXMm": 0.0},
        nodes={ROOT_UID: root},
        children={ROOT_UID: []},
    )
    return project, page


def test_new_repository_roundtrip_is_exact_and_uses_uid_paths(tmp_path):
    repository = ProjectRepository(tmp_path / "Work.bmanga")
    project, page = _documents()
    repository.checkpoint(project, [page])
    assert repository.project_path.is_file()
    assert repository.page_path(PAGE_UID).is_file()
    assert not (repository.root / "work.json").exists()
    assert not (repository.root / "pages.json").exists()
    assert repository.load_project().to_dict() == project.to_dict()
    assert repository.load_page(PAGE_UID).to_dict() == page.to_dict()
    assert list(repository.journal_dir.iterdir()) == []


def test_observed_project_hash_tracks_loaded_and_checkpointed_generation(tmp_path):
    repository = ProjectRepository(tmp_path / "Work.bmanga")
    project, page = _documents()
    assert repository.observed_project_hash() == ""
    repository.checkpoint(project, [page])
    expected = hashlib.sha256(repository.project_path.read_bytes()).hexdigest()
    assert repository.observed_project_hash() == expected

    project.settings["name"] = "更新"
    project.revision += 1
    repository.checkpoint(project, [page])
    updated = hashlib.sha256(repository.project_path.read_bytes()).hexdigest()
    assert updated != expected
    assert repository.observed_project_hash() == updated


def test_page_only_checkpoint_does_not_rewrite_unchanged_project(tmp_path):
    repository = ProjectRepository(tmp_path / "Work.bmanga")
    project, page = _documents()
    repository.checkpoint(project, [page])
    project_before = repository.project_path.read_bytes()
    project_mtime_before = repository.project_path.stat().st_mtime_ns
    page.settings["offsetXMm"] = 42.0
    page.revision += 1

    repository.checkpoint(project, [page], include_project=False)

    assert repository.project_path.read_bytes() == project_before
    assert repository.project_path.stat().st_mtime_ns == project_mtime_before
    assert repository.load_page(PAGE_UID).settings["offsetXMm"] == 42.0


def test_read_only_observation_check_detects_external_project_update(tmp_path):
    repository = ProjectRepository(tmp_path / "Work.bmanga")
    project, page = _documents()
    repository.checkpoint(project, [page])
    repository.load_project()
    repository.project_path.write_bytes(
        repository.project_path.read_bytes() + b" "
    )

    with pytest.raises(RepositoryConflictError, match="別のBlender画面"):
        repository.assert_observations_current((repository.project_path,))


def test_old_layout_is_rejected_with_an_explicit_error(tmp_path):
    root = tmp_path / "Legacy.bmanga"
    root.mkdir()
    (root / "work.json").write_text("{}", encoding="utf-8")
    with pytest.raises(LegacyFormatError, match="旧形式"):
        ProjectRepository(root).load_project()


def test_native_checkpoint_failure_preserves_previous_generation(tmp_path):
    repository = ProjectRepository(tmp_path / "Work.bmanga")
    project, page = _documents()
    repository.checkpoint(project, [page])
    before = repository.project_path.read_bytes()
    project.settings["name"] = "変更後"
    with pytest.raises(RepositoryError, match="native checkpoint failed"):
        repository.checkpoint(project, [page], native_checkpoint=lambda: False)
    assert repository.project_path.read_bytes() == before
    assert list(repository.journal_dir.iterdir()) == []


def test_capacity_is_checked_before_any_stage_is_written(tmp_path, monkeypatch):
    repository = ProjectRepository(tmp_path / "Work.bmanga")
    project, page = _documents()
    repository.checkpoint(project, [page])
    before_project = repository.project_path.read_bytes()
    before_page = repository.page_path(PAGE_UID).read_bytes()
    monkeypatch.setattr(
        domain_repository.shutil,
        "disk_usage",
        lambda _path: domain_repository.shutil._ntuple_diskusage(0, 0, 1),
    )

    with pytest.raises(RepositoryError, match="capacity is insufficient"):
        repository.checkpoint(project, [page])

    assert repository.project_path.read_bytes() == before_project
    assert repository.page_path(PAGE_UID).read_bytes() == before_page
    assert not list(repository.root.rglob("*.stage-*"))
    assert list(repository.journal_dir.iterdir()) == []


def test_partial_prepare_failure_removes_all_stages(tmp_path, monkeypatch):
    repository = ProjectRepository(tmp_path / "Work.bmanga")
    project, page = _documents()
    repository.checkpoint(project, [page])
    before_project = repository.project_path.read_bytes()
    before_page = repository.page_path(PAGE_UID).read_bytes()
    original_write = domain_repository._write_bytes
    calls = 0

    def fail_second_stage(path, payload):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(payload[:8])
            raise OSError("prepare failed")
        return original_write(path, payload)

    monkeypatch.setattr(domain_repository, "_write_bytes", fail_second_stage)
    with pytest.raises(OSError, match="prepare failed"):
        repository.checkpoint(project, [page])

    assert repository.project_path.read_bytes() == before_project
    assert repository.page_path(PAGE_UID).read_bytes() == before_page
    assert not list(repository.root.rglob("*.stage-*"))
    assert list(repository.journal_dir.iterdir()) == []


def test_partial_backup_failure_preserves_old_generation(tmp_path, monkeypatch):
    repository = ProjectRepository(tmp_path / "Work.bmanga")
    project, page = _documents()
    repository.checkpoint(project, [page])
    before_project = repository.project_path.read_bytes()
    before_page = repository.page_path(PAGE_UID).read_bytes()

    def fail_backup(_source, destination):
        Path(destination).write_bytes(b"partial-backup")
        raise OSError("backup failed")

    monkeypatch.setattr(domain_repository.shutil, "copy2", fail_backup)
    with pytest.raises(OSError, match="backup failed"):
        repository.checkpoint(project, [page])

    assert repository.project_path.read_bytes() == before_project
    assert repository.page_path(PAGE_UID).read_bytes() == before_page
    assert not list(repository.root.rglob("*.backup-*"))
    assert not list(repository.root.rglob("*.stage-*"))
    assert list(repository.journal_dir.iterdir()) == []


def test_process_crash_during_partial_backup_recovers_old_generation(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "Work.bmanga"
    repository = ProjectRepository(root)
    project, page = _documents()
    repository.checkpoint(project, [page])
    before_project = repository.project_path.read_bytes()
    before_page = repository.page_path(PAGE_UID).read_bytes()

    def crash_during_backup(_source, destination):
        Path(destination).write_bytes(b"partial-backup")
        raise SimulatedProcessCrash()

    monkeypatch.setattr(
        domain_repository.shutil,
        "copy2",
        crash_during_backup,
    )
    with pytest.raises(SimulatedProcessCrash):
        repository.checkpoint(project, [page])

    restarted = ProjectRepository(root)
    assert restarted.recover() == 1
    assert restarted.project_path.read_bytes() == before_project
    assert restarted.page_path(PAGE_UID).read_bytes() == before_page
    assert not list(root.rglob("*.backup-*"))
    assert not list(root.rglob("*.stage-*"))
    assert list(restarted.journal_dir.iterdir()) == []


def test_install_failure_rolls_back_every_target(tmp_path):
    repository = ProjectRepository(tmp_path / "Work.bmanga")
    project, page = _documents()
    repository.checkpoint(project, [page])
    before_project = repository.project_path.read_bytes()
    before_page = repository.page_path(PAGE_UID).read_bytes()
    project.settings["name"] = "変更後"
    page.settings["offsetXMm"] = 42.0
    with isolated_faults():
        arm_fault(FaultPoint.CHECKPOINT_AFTER_INSTALL, times=1)
        with pytest.raises(Exception, match="injected fault"):
            repository.checkpoint(project, [page])
    assert repository.project_path.read_bytes() == before_project
    assert repository.page_path(PAGE_UID).read_bytes() == before_page
    assert list(repository.journal_dir.iterdir()) == []


def test_process_crash_journal_recovers_last_complete_generation(tmp_path):
    root = tmp_path / "Work.bmanga"
    repository = ProjectRepository(root)
    project, page = _documents()
    repository.checkpoint(project, [page])
    before_project = repository.project_path.read_bytes()
    before_page = repository.page_path(PAGE_UID).read_bytes()
    project.settings["name"] = "未確定"
    page.settings["offsetXMm"] = 99.0

    def crash(state, index):
        if state is JournalState.INSTALLING and index == 1:
            raise SimulatedProcessCrash()

    with pytest.raises(SimulatedProcessCrash):
        repository.checkpoint(project, [page], phase_hook=crash)
    assert any(repository.journal_dir.glob("checkpoint-*.json"))
    restarted = ProjectRepository(root)
    assert restarted.recover() == 1
    assert restarted.project_path.read_bytes() == before_project
    assert restarted.page_path(PAGE_UID).read_bytes() == before_page
    assert list(restarted.journal_dir.iterdir()) == []


def test_external_update_is_not_overwritten(tmp_path):
    repository = ProjectRepository(tmp_path / "Work.bmanga")
    project, page = _documents()
    repository.checkpoint(project, [page])
    repository.load_project()
    payload = json.loads(repository.project_path.read_text(encoding="utf-8"))
    payload["settings"]["name"] = "別画面"
    repository.project_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    project.settings["name"] = "この画面"
    with pytest.raises(RepositoryConflictError, match="別のBlender画面"):
        repository.checkpoint(project, [page])


def test_unobserved_existing_target_is_never_overwritten(tmp_path):
    root = tmp_path / "Work.bmanga"
    writer = ProjectRepository(root)
    project, page = _documents()
    writer.checkpoint(project, [page])
    before = writer.project_path.read_bytes()

    unopened = ProjectRepository(root)
    project.settings["name"] = "未読込画面"
    with pytest.raises(RepositoryConflictError, match="未読込"):
        unopened.checkpoint(project, [page])
    assert writer.project_path.read_bytes() == before


def test_page_junction_cannot_redirect_repository_outside_root(tmp_path):
    root = tmp_path / "Work.bmanga"
    repository = ProjectRepository(root)
    project, page = _documents()
    repository.checkpoint(project, [page])
    original = repository.page_path(PAGE_UID)
    payload = original.read_bytes()
    original.unlink()
    original.parent.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_page = outside / "page.json"
    outside_page.write_bytes(payload)

    if os.name == "nt":
        result = subprocess.run(
            [
                "cmd",
                "/c",
                "mklink",
                "/J",
                str(original.parent),
                str(outside),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
    else:
        os.symlink(outside, original.parent, target_is_directory=True)

    with pytest.raises(RepositoryError, match="escapes project root"):
        repository.load_page(PAGE_UID)
    assert outside_page.read_bytes() == payload


def test_recovery_rejects_corrupted_backup_without_touching_generation(tmp_path):
    root = tmp_path / "Work.bmanga"
    repository = ProjectRepository(root)
    project, page = _documents()
    repository.checkpoint(project, [page])
    project.settings["name"] = "未確定"
    page.settings["offsetXMm"] = 99.0

    def crash(state, index):
        if state is JournalState.INSTALLING and index == 1:
            raise SimulatedProcessCrash()

    with pytest.raises(SimulatedProcessCrash):
        repository.checkpoint(project, [page], phase_hook=crash)
    backup = next(root.rglob("*.backup-*"))
    journal = next(repository.journal_dir.glob("checkpoint-*.json"))
    data = json.loads(journal.read_text(encoding="utf-8"))
    record = next(item for item in data["files"] if root / item["backup"] == backup)
    target = root / record["target"]
    before_target = target.read_bytes()
    backup.write_bytes(b"corrupted-backup")

    with pytest.raises(JournalRecoveryError, match="inconsistent"):
        ProjectRepository(root).recover()
    assert target.read_bytes() == before_target
    assert backup.read_bytes() == b"corrupted-backup"
    assert journal.is_file()


def test_recovery_rejects_corrupted_stage_and_journal_metadata(tmp_path):
    root = tmp_path / "Work.bmanga"
    repository = ProjectRepository(root)
    project, page = _documents()
    repository.checkpoint(project, [page])
    before = repository.project_path.read_bytes()
    project.settings["name"] = "未確定"

    def crash(state, _index):
        if state is JournalState.PREPARED:
            raise SimulatedProcessCrash()

    with pytest.raises(SimulatedProcessCrash):
        repository.checkpoint(project, [page], phase_hook=crash)
    stage = next(root.rglob("*.stage-*"))
    journal = next(repository.journal_dir.glob("checkpoint-*.json"))
    stage.write_bytes(b"corrupted-stage")
    with pytest.raises(JournalRecoveryError, match="inconsistent"):
        ProjectRepository(root).recover()
    assert repository.project_path.read_bytes() == before
    assert stage.is_file() and journal.is_file()

    data = json.loads(journal.read_text(encoding="utf-8"))
    data["schemaVersion"] = 999
    journal.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(JournalRecoveryError, match="version"):
        ProjectRepository(root).recover()
    assert stage.is_file() and journal.is_file()


def test_explicit_recovery_refreshes_only_repository_owned_observations(tmp_path):
    root = tmp_path / "Work.bmanga"
    repository = ProjectRepository(root)
    project, page = _documents()
    repository.checkpoint(project, [page])
    before_project = repository.project_path.read_bytes()
    before_page = repository.page_path(PAGE_UID).read_bytes()

    project.settings["name"] = "一時保存"
    page.settings["offsetXMm"] = 12.0
    repository.checkpoint(project, [page])
    repository.project_path.write_bytes(before_project)
    repository.page_path(PAGE_UID).write_bytes(before_page)

    repository.accept_recovered_files(
        (repository.project_path, repository.page_path(PAGE_UID))
    )
    project.settings["name"] = "復元後"
    page.settings["offsetXMm"] = 24.0
    repository.checkpoint(project, [page])
    assert repository.load_project().settings["name"] == "復元後"
    assert repository.load_page(PAGE_UID).settings["offsetXMm"] == 24.0

    with pytest.raises(RepositoryError, match="outside repository"):
        repository.accept_recovered_files((tmp_path / "outside.json",))
