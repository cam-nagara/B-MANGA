"""見開きページのUID directoryを安全に複製・確定・rollbackする。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Mapping
import uuid

MANIFEST_RELATIVE_PATH = Path("assets") / ".spread-content-manifest.json"
MANIFEST_NAME = MANIFEST_RELATIVE_PATH.as_posix()
_PAGE_CONTROL_NAMES = {"page.blend", "page.json", "comas"}
_DERIVED_PAGE_FILES = {"page_preview.png", "page_preview.detail.png"}


class SpreadContentError(RuntimeError):
    pass


def _require_page_source(page_dir: Path) -> None:
    if not page_dir.is_dir() or page_dir.is_symlink():
        raise SpreadContentError(f"ページフォルダーがありません: {page_dir.name}")
    blend = page_dir / "page.blend"
    if not blend.is_file() or blend.is_symlink():
        raise SpreadContentError(
            f"{page_dir.name} のページ内容ファイルがありません。"
            "元データを保護するため中止しました"
        )


def _is_derived_only_page_dir(page_dir: Path) -> bool:
    """一覧用派生画像だけがある未確定page directoryか。"""

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
        target = destination / item.name
        if item.is_symlink():
            raise SpreadContentError(f"リンクされたページ資産には対応していません: {item.name}")
        if item.is_dir():
            _copytree_without_links(
                item,
                target,
                "ページ資産",
                excluded_relative={MANIFEST_RELATIVE_PATH.relative_to(item.name)}
                if item.name == MANIFEST_RELATIVE_PATH.parts[0]
                else set(),
            )
        elif item.is_file():
            shutil.copy2(item, target)
        else:
            raise SpreadContentError(f"未対応のページ内項目です: {item}")
    (destination / "assets").mkdir(exist_ok=True)
    (destination / "comas").mkdir(exist_ok=True)


def _merge_extra_assets(
    source: Path,
    destination: Path,
    *,
    page_root: bool = True,
    relative: Path = Path(),
) -> None:
    for item in source.iterdir():
        child_relative = relative / item.name
        if page_root and (
            item.name in _PAGE_CONTROL_NAMES or item.name in _DERIVED_PAGE_FILES
        ):
            continue
        if child_relative == MANIFEST_RELATIVE_PATH:
            continue
        if item.is_symlink():
            raise SpreadContentError(f"リンクされたページ資産には対応していません: {item.name}")
        target = destination / item.name
        if not target.exists():
            if item.is_dir():
                _copytree_without_links(
                    item,
                    target,
                    "ページ資産",
                    excluded_relative={
                        MANIFEST_RELATIVE_PATH.relative_to(child_relative)
                    }
                    if child_relative == Path("assets")
                    else set(),
                )
            elif item.is_file():
                shutil.copy2(item, target)
            else:
                raise SpreadContentError(f"未対応のページ内項目です: {item}")
            continue
        if item.is_file() and target.is_file() and _same_file(item, target):
            continue
        if item.is_dir() and target.is_dir():
            _merge_extra_assets(
                item,
                target,
                page_root=False,
                relative=child_relative,
            )
            continue
        raise SpreadContentError(
            f"同名のページ資産を安全に統合できません: {item.name}。原本は変更していません"
        )


def _copy_mapped_comas(
    source: Path,
    destination: Path,
    mapping: Mapping[str, str],
) -> None:
    """source UID -> destination UID対応でNative dataを複製する。"""

    source_root = source / "comas"
    if not source_root.is_dir():
        return
    stored = {
        item.name
        for item in source_root.iterdir()
        if item.is_dir() and _is_coma_uid(item.name)
    }
    if stored - set(mapping):
        raise SpreadContentError(
            f"{source.name} にページ情報と対応しないコマ保存フォルダーがあります: "
            + ", ".join(sorted(stored - set(mapping)))
        )
    target_root = destination / "comas"
    target_root.mkdir(exist_ok=True)
    for source_uid, destination_uid in mapping.items():
        if not _is_coma_uid(source_uid) or not _is_coma_uid(destination_uid):
            raise SpreadContentError(
                f"コマUIDが不正です: {source_uid} -> {destination_uid}"
            )
        source_dir = source_root / source_uid
        if not source_dir.is_dir():
            continue
        if source_dir.is_symlink():
            raise SpreadContentError(
                f"リンクされたコマ保存フォルダーには対応していません: {source_uid}"
            )
        target_dir = target_root / destination_uid
        if target_dir.exists():
            raise SpreadContentError(f"コマ保存先が衝突しました: {destination_uid}")
        _copytree_without_links(source_dir, target_dir, "コマ保存フォルダー")


def _copy_selected_comas(
    source: Path,
    destination: Path,
    mapping: Mapping[str, str],
) -> None:
    _copy_mapped_comas(source, destination, mapping)


def _copytree_without_links(
    source: Path,
    destination: Path,
    label: str,
    *,
    excluded_relative: set[Path] | None = None,
) -> None:
    excluded = excluded_relative or set()
    destination.mkdir(parents=True, exist_ok=False)
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        if any(relative == entry or entry in relative.parents for entry in excluded):
            continue
        if item.is_symlink():
            raise SpreadContentError(f"リンクされた{label}には対応していません: {relative}")
        target = destination / relative
        if item.is_dir():
            target.mkdir(exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
        else:
            raise SpreadContentError(f"未対応の{label}です: {relative}")


def _same_file(first: Path, second: Path) -> bool:
    return (
        first.stat().st_size == second.stat().st_size
        and _sha256(first) == _sha256(second)
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_coma_uid(value: str) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("coma_")
        and len(value) == 37
        and all(character in "0123456789abcdef" for character in value[5:])
    )


def _validate_staged_page(directory: Path, page_uid: str) -> None:
    blend = directory / "page.blend"
    meta = directory / "page.json"
    if not blend.is_file() or not meta.is_file():
        raise SpreadContentError(f"{page_uid} の一時ページが完成していません")
    data = json.loads(meta.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or data.get("schema") != "bmanga.page"
        or data.get("schemaVersion") != 1
        or data.get("pageUid") != page_uid
    ):
        raise SpreadContentError(f"{page_uid} のページ情報を検証できません")


def _write_domain_document(path: Path, document: object) -> None:
    try:
        from ..bmanga_core.domain_model import canonical_json_bytes
    except ImportError:  # pure testで単独ロードする場合
        from bmanga_core.domain_model import canonical_json_bytes

    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        payload = canonical_json_bytes(document)  # type: ignore[arg-type]
        with temp.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _install_directories_and_domain(
    work: Path,
    *,
    removals: tuple[Path, ...],
    additions: tuple[tuple[Path, Path], ...],
    project_document: object,
    fail_phase: str,
) -> None:
    from ..bmanga_core.domain_ids import UIDKind, validate_uid
    from . import domain_runtime, native_tree_transaction
    from .project_file_lock import guard_path_write
    from .save_baseline import (
        record_observed_read,
        record_successful_tree_change,
        record_successful_write,
        restore_baseline_registry,
        snapshot_baseline_registry,
    )

    for root in removals:
        for candidate in root.rglob("*"):
            if candidate.is_file():
                record_observed_read(candidate)
    baseline = snapshot_baseline_registry()
    repository = domain_runtime.repository_for(work)
    # Runtime Repositoryがこのprocessで未初期化でも、既存project.jsonを
    # 「未観測だから競合なし」として上書きしない。取引開始時の世代を必ず
    # 読み、以降のcheckpointでhash競合を検査できる状態にする。
    before_project = repository.load_project()
    before_page_uids = {page.uid for page in before_project.pages}
    after_page_uids = {
        page.uid for page in getattr(project_document, "pages", ())
    }
    removal_plans = []
    for source in removals:
        page_uid = validate_uid(source.name, UIDKind.PAGE)
        removal_plans.append(
            native_tree_transaction.Removal(
                source,
                native_tree_transaction.Owner("page", page_uid),
                before_referenced=page_uid in before_page_uids,
                after_referenced=page_uid in after_page_uids,
            )
        )
    addition_plans = []
    for staged, destination in additions:
        page_uid = validate_uid(destination.name, UIDKind.PAGE)
        addition_plans.append(
            native_tree_transaction.Addition(
                staged,
                destination,
                native_tree_transaction.Owner("page", page_uid),
                before_referenced=page_uid in before_page_uids,
                after_referenced=page_uid in after_page_uids,
            )
        )
    native_transaction = native_tree_transaction.NativeTreeTransaction(
        work,
        repository=repository,
        additions=addition_plans,
        removals=removal_plans,
    )
    with guard_path_write(work):
        native_transaction.prepare()
        try:
            native_transaction.apply_removals()
            _inject_failure(fail_phase, "after_backup")
            native_transaction.apply_additions()
            _inject_failure(fail_phase, "after_directory_install")
            repository.checkpoint(  # type: ignore[arg-type]
                project_document,
            )
            native_transaction.recover()
        except BaseException:
            domain_committed = False
            recovery_complete = False
            try:
                domain_committed = native_transaction.recover()
                recovery_complete = True
            finally:
                if recovery_complete and not domain_committed:
                    restore_baseline_registry(baseline)
            raise
        changed = (*removals, *(destination for _staged, destination in additions))
        record_successful_tree_change(*changed)
        record_successful_write(repository.project_path)


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
