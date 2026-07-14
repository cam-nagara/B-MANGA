"""旧GPフォルダーを作品全体の汎用フォルダーへ統合する処理。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from .project_content_migration_model import (
        MigrationError,
        MigrationIssue,
        PagePlan,
    )
except ImportError:  # 単体テストが移行本体をファイルとして読む場合
    from project_content_migration_model import (  # type: ignore[no-redef]
        MigrationError,
        MigrationIssue,
        PagePlan,
    )


def build_folder_manifest(
    work_data: Mapping[str, Any],
    pages: Sequence[PagePlan],
    work_meta: Path,
) -> tuple[tuple[Mapping[str, Any], ...], tuple[MigrationIssue, ...]]:
    """全ページの旧GPフォルダーを作品全体の一意な定義へ集約する。"""

    issues: list[MigrationIssue] = []
    collected: dict[str, dict[str, Any]] = {}
    existing = existing_folder_definitions(work_data)
    for page in pages:
        raw_manifest = page.inspection_facts.get("folderManifest", ())
        if not isinstance(raw_manifest, (list, tuple)):
            issues.append(_folder_manifest_issue(
                page.page_id,
                work_meta,
                "ページのフォルダー検査結果が壊れています",
            ))
            continue
        for raw in raw_manifest:
            record = canonical_folder_record(raw)
            if record is None:
                issues.append(_folder_manifest_issue(
                    page.page_id,
                    work_meta,
                    "ページのフォルダー定義が不足しています",
                ))
                continue
            folder_id = record["id"]
            previous = collected.get(folder_id)
            if previous is not None and previous != record:
                issues.append(_folder_manifest_issue(
                    page.page_id,
                    work_meta,
                    f"同じフォルダーIDに異なる定義があります: {folder_id}",
                ))
                continue
            existing_record = existing.get(folder_id)
            if existing_record is not None and existing_record != record:
                issues.append(_folder_manifest_issue(
                    page.page_id,
                    work_meta,
                    f"既存フォルダーと移行先が衝突しています: {folder_id}",
                ))
                continue
            collected[folder_id] = record
    # page planはページID順、各folderManifestは旧内部グループのpreorder。
    # この挿入順がレイヤー一覧の並びそのものなので、名前や親IDで再sortしない。
    return tuple(collected.values()), tuple(issues)


def merge_folder_manifest(
    work_data: dict[str, Any],
    folder_manifest: Sequence[Mapping[str, Any]],
) -> None:
    """既存順を保ちながら、検査済みフォルダーをwork.jsonへ統合する。"""

    raw_existing = work_data.get("layer_folders", work_data.get("layerFolders", []))
    if not isinstance(raw_existing, list):
        raise MigrationError("work.json のフォルダー一覧が壊れています")
    merged = [dict(item) for item in raw_existing if isinstance(item, Mapping)]
    index_by_id = {
        str(item.get("id", "") or ""): index
        for index, item in enumerate(merged)
        if str(item.get("id", "") or "")
    }
    for raw in folder_manifest:
        record = canonical_folder_record(raw)
        if record is None:
            raise MigrationError("検査済みフォルダー定義が壊れています")
        index = index_by_id.get(record["id"])
        if index is None:
            index_by_id[record["id"]] = len(merged)
            merged.append(record)
            continue
        current = canonical_folder_record(merged[index])
        if current != record:
            raise MigrationError(
                f"確認後にフォルダー定義が衝突しました: {record['id']}"
            )
    work_data["layer_folders"] = merged
    work_data.pop("layerFolders", None)


def verify_folder_manifest(work_data: Mapping[str, Any], expected_manifest: object) -> None:
    if not isinstance(expected_manifest, list):
        raise MigrationError("移行記録のフォルダー一覧が壊れています")
    actual = existing_folder_definitions(work_data)
    for raw in expected_manifest:
        expected = canonical_folder_record(raw)
        if expected is None or actual.get(expected["id"]) != expected:
            folder_id = "" if expected is None else expected["id"]
            raise MigrationError(
                f"作品全体のフォルダー定義を再読込できません: {folder_id or 'unknown'}"
            )


def existing_folder_definitions(
    work_data: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    raw_items = work_data.get("layer_folders", work_data.get("layerFolders", ()))
    if not isinstance(raw_items, list):
        return {}
    result = {}
    for raw in raw_items:
        record = canonical_folder_record(raw)
        if record is not None:
            result[record["id"]] = record
    return result


def canonical_folder_record(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    folder_id = str(raw.get("id", "") or "").strip()
    title = str(raw.get("title", "") or "").strip()
    if not folder_id or not title:
        return None
    return {
        "id": folder_id,
        "title": title,
        "parentKey": str(raw.get("parentKey", raw.get("parent_key", "")) or ""),
        "expanded": bool(raw.get("expanded", True)),
        "visible": bool(raw.get("visible", True)),
        "locked": bool(raw.get("locked", False)),
    }


def _folder_manifest_issue(page_id: str, work_meta: Path, message: str) -> MigrationIssue:
    return MigrationIssue(
        code="folder_manifest_conflict",
        page_id=str(page_id),
        page_path=str(work_meta),
        message=str(message),
    )
