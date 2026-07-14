"""見開きページ用のファイル複製・確定・rollback。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Mapping
import uuid


MANIFEST_NAME = "spread-content-manifest.json"
_PAGE_CONTROL_NAMES = {"page.blend", "page.json", MANIFEST_NAME}
_DERIVED_PAGE_FILES = {"page_preview.png"}


class SpreadContentError(RuntimeError):
    pass


def _require_page_source(page_dir: Path) -> None:
    if not page_dir.is_dir() or page_dir.is_symlink():
        raise SpreadContentError(f"ページフォルダーがありません: {page_dir.name}")
    blend = page_dir / "page.blend"
    if not blend.is_file() or blend.is_symlink():
        raise SpreadContentError(
            f"{page_dir.name} のページ内容ファイルがありません。元データを保護するため中止しました"
        )


def _is_derived_only_page_dir(page_dir: Path) -> bool:
    """Return whether a target dir contains only regenerated overview previews."""

    if not page_dir.is_dir() or page_dir.is_symlink():
        return False
    items = list(page_dir.iterdir())
    if not items:
        return False
    return all(
        item.name in _DERIVED_PAGE_FILES
        and item.is_file()
        and not item.is_symlink()
        for item in items
    )


def _copy_page_shell(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for item in source.iterdir():
        if item.name in _PAGE_CONTROL_NAMES or item.name in _DERIVED_PAGE_FILES:
            continue
        if item.is_symlink():
            raise SpreadContentError(f"リンクされたページ資産には対応していません: {item.name}")
        if item.is_dir() and _is_coma_id(item.name):
            continue
        target = destination / item.name
        if item.is_dir():
            _copytree_without_links(item, target, "ページ資産")
        elif item.is_file():
            shutil.copy2(item, target)
        else:
            raise SpreadContentError(f"未対応のページ内項目です: {item}")


def _merge_extra_assets(
    source: Path,
    destination: Path,
    *,
    page_root: bool = True,
) -> None:
    for item in source.iterdir():
        if item.name in _PAGE_CONTROL_NAMES:
            continue
        if page_root and item.name in _DERIVED_PAGE_FILES:
            continue
        if item.is_symlink():
            raise SpreadContentError(f"リンクされたページ資産には対応していません: {item.name}")
        if item.is_dir() and _is_coma_id(item.name):
            continue
        target = destination / item.name
        if not target.exists():
            if item.is_dir():
                _copytree_without_links(item, target, "ページ資産")
            elif item.is_file():
                shutil.copy2(item, target)
            else:
                raise SpreadContentError(f"未対応のページ内項目です: {item}")
            continue
        if item.is_file() and target.is_file() and _same_file(item, target):
            continue
        if item.is_dir() and target.is_dir():
            _merge_extra_assets(item, target, page_root=False)
            continue
        raise SpreadContentError(
            f"同名のページ資産を安全に統合できません: {item.name}。原本は変更していません"
        )


def _copy_mapped_comas(source: Path, destination: Path, mapping: Mapping[str, str]) -> None:
    stored = {
        item.name for item in source.iterdir()
        if item.is_dir() and _is_coma_id(item.name)
    }
    if stored - set(mapping):
        raise SpreadContentError(
            f"{source.name} にページ情報と対応しないコマ保存フォルダーがあります: "
            + ", ".join(sorted(stored - set(mapping)))
        )
    for old_id, new_id in mapping.items():
        source_dir = source / old_id
        if not source_dir.is_dir():
            continue
        if source_dir.is_symlink():
            raise SpreadContentError(f"リンクされたコマ保存フォルダーには対応していません: {old_id}")
        target_dir = destination / new_id
        if target_dir.exists():
            raise SpreadContentError(f"コマ保存先が衝突しました: {new_id}")
        _copytree_without_links(source_dir, target_dir, "コマ保存フォルダー")
        _rename_coma_artifacts(target_dir, old_id, new_id)


def _copy_selected_comas(
    source: Path,
    destination: Path,
    mapping: Mapping[str, str],
) -> None:
    for stored_id, restored_id in sorted(mapping.items()):
        if not _is_coma_id(stored_id) or not _is_coma_id(restored_id):
            raise SpreadContentError(
                f"コマ保存フォルダー名が不正です: {stored_id} -> {restored_id}"
            )
        source_dir = source / stored_id
        if not source_dir.is_dir():
            continue
        if source_dir.is_symlink():
            raise SpreadContentError(
                f"リンクされたコマ保存フォルダーには対応していません: {stored_id}"
            )
        target_dir = destination / restored_id
        if target_dir.exists() or target_dir.is_symlink():
            raise SpreadContentError(f"解除後のコマ保存先が衝突しました: {restored_id}")
        _copytree_without_links(
            source_dir,
            target_dir,
            "コマ保存フォルダー",
        )
        _rename_coma_artifacts(target_dir, stored_id, restored_id)


def _copytree_without_links(source: Path, destination: Path, label: str) -> None:
    for item in source.rglob("*"):
        if item.is_symlink():
            raise SpreadContentError(
                f"リンクされた{label}には対応していません: {item.relative_to(source)}"
            )
    shutil.copytree(source, destination)


def _rename_coma_artifacts(directory: Path, old_id: str, new_id: str) -> None:
    if old_id == new_id:
        return
    for suffix in (".blend", ".json", "_thumb.png", "_preview.png"):
        source = directory / f"{old_id}{suffix}"
        if source.exists():
            destination = directory / f"{new_id}{suffix}"
            if destination.exists() or destination.is_symlink():
                raise SpreadContentError(
                    f"コマ保存ファイルが衝突しました: {destination.name}"
                )
            os.replace(source, destination)


def _write_coma_jsons(directory: Path, page_json: Mapping[str, Any]) -> None:
    for coma in page_json.get("comas", []):
        if not isinstance(coma, Mapping):
            continue
        coma_id = str(coma.get("comaId", "") or "")
        coma_dir = directory / coma_id
        if _is_coma_id(coma_id) and coma_dir.is_dir():
            _write_json(coma_dir / f"{coma_id}.json", coma)


def _same_file(first: Path, second: Path) -> bool:
    return first.stat().st_size == second.stat().st_size and _sha256(first) == _sha256(second)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_coma_id(value: str) -> bool:
    return len(value) == 3 and value[0] == "c" and value[1:].isdigit()


def _validate_staged_page(directory: Path, page_id: str) -> None:
    if not (directory / "page.blend").is_file() or not (directory / "page.json").is_file():
        raise SpreadContentError(f"{page_id} の一時ページが完成していません")
    data = json.loads((directory / "page.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or str(data.get("id", "")) != page_id:
        raise SpreadContentError(f"{page_id} のページ情報を検証できません")


def _install_directories_and_json(
    work: Path,
    *,
    removals: tuple[Path, ...],
    additions: tuple[tuple[Path, Path], ...],
    work_json: Mapping[str, Any],
    pages_json: Mapping[str, Any],
    fail_phase: str,
) -> None:
    from .project_content_migration_lock import guard_path_write
    from .project_content_save_baseline import (
        record_observed_read,
        record_successful_tree_change,
        record_successful_write,
    )
    from .project_content_sidecar_save_guard import (
        begin_sidecar_save,
        commit_sidecars,
        mark_sidecar_writes_started,
        restore_sidecars,
    )

    for root in removals:
        for candidate in root.rglob("*"):
            if candidate.is_file():
                record_observed_read(candidate)
    json_paths = (work / "work.json", work / "pages.json")
    token = None
    tx = uuid.uuid4().hex
    backups = [(source, work / f".{source.name}.{tx}.spread-backup") for source in removals]
    installed: list[Path] = []
    with guard_path_write(work):
        try:
            token = begin_sidecar_save(work, json_paths)
            for source, backup in backups:
                os.replace(source, backup)
            _inject_failure(fail_phase, "after_backup")
            for staged, destination in additions:
                os.replace(staged, destination)
                installed.append(destination)
            _inject_failure(fail_phase, "after_directory_install")
            mark_sidecar_writes_started(token)
            _write_json(work / "work.json", work_json)
            _write_json(work / "pages.json", pages_json)
            _inject_failure(fail_phase, "after_json_install")
            commit_sidecars(token)
            token = None
        except BaseException:
            for destination in reversed(installed):
                if destination.exists():
                    shutil.rmtree(destination)
            for source, backup in reversed(backups):
                if backup.exists():
                    os.replace(backup, source)
            if token is not None:
                restore_sidecars(token)
            raise
        for _source, backup in backups:
            shutil.rmtree(backup, ignore_errors=True)
        changed = (*removals, *(destination for _staged, destination in additions))
        record_successful_tree_change(*changed)
        record_successful_write(work / "work.json")
        record_successful_write(work / "pages.json")


def _inject_failure(requested: str, phase: str) -> None:
    if requested and requested == phase:
        raise SpreadContentError(f"強制失敗: {phase}")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(value), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
