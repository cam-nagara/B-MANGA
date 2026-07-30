"""コマ移動のNative複製をprocess強制終了後もDomain世代へ収束させる。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid
from typing import Mapping

from ..bmanga_core.domain_ids import UIDKind, validate_uid
from ..bmanga_core.domain_repository import ProjectRepository
from ..utils import paths
from .project_file_lock import work_lock


MARKER_FILE_NAME = ".bmanga-coma-move.json"
MARKER_SCHEMA = "bmanga.coma-move"
MARKER_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TRANSACTION_RE = re.compile(r"^[0-9a-f]{32}$")
_MARKER_KEYS = {
    "schema",
    "schemaVersion",
    "transactionUid",
    "sourcePageUid",
    "sourceComaUid",
    "targetPageUid",
    "targetComaUid",
    "treeHash",
}


class ComaMoveRecoveryError(RuntimeError):
    """中断したコマ移動を安全な一世代へ収束できない。"""


def _reject_link(path: Path) -> None:
    is_junction = getattr(path, "is_junction", lambda: False)
    if path.is_symlink() or is_junction():
        raise ComaMoveRecoveryError(
            f"コマ実体にリンクまたはジャンクションがあります: {path}"
        )


def tree_hash(directory: Path) -> str:
    """マーカー自身を除くNative directoryの決定的SHA-256を返す。"""

    root = Path(directory)
    if not root.is_dir():
        raise ComaMoveRecoveryError(f"コマ実体がありません: {root}")
    _reject_link(root)
    digest = hashlib.sha256()
    for candidate in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        _reject_link(candidate)
        relative = candidate.relative_to(root).as_posix()
        if relative == MARKER_FILE_NAME:
            continue
        encoded = relative.encode("utf-8")
        if candidate.is_dir():
            digest.update(b"D\0")
            digest.update(encoded)
            digest.update(b"\0")
        elif candidate.is_file():
            digest.update(b"F\0")
            digest.update(encoded)
            digest.update(b"\0")
            digest.update(str(candidate.stat().st_size).encode("ascii"))
            digest.update(b"\0")
            with candidate.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            raise ComaMoveRecoveryError(
                f"コマ実体に通常ファイル以外があります: {candidate}"
            )
    return digest.hexdigest()


def _atomic_marker(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_name(f".{path.name}.stage-{uuid.uuid4().hex}")
    data = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    try:
        with stage.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(stage, path)
    finally:
        stage.unlink(missing_ok=True)


def publish_native_copy(
    source: Path,
    staged: Path,
    destination: Path,
    *,
    source_page_uid: str,
    source_coma_uid: str,
    target_page_uid: str,
    target_coma_uid: str,
) -> Path:
    """原本を保持した完全複製と復旧マーカーを同じrenameで公開する。"""

    source_page_uid = validate_uid(source_page_uid, UIDKind.PAGE)
    source_coma_uid = validate_uid(source_coma_uid, UIDKind.COMA)
    target_page_uid = validate_uid(target_page_uid, UIDKind.PAGE)
    target_coma_uid = validate_uid(target_coma_uid, UIDKind.COMA)
    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")
    expected_hash = tree_hash(source)
    shutil.copytree(source, staged)
    if tree_hash(staged) != expected_hash:
        raise ComaMoveRecoveryError("コマ実体の複製結果が原本と一致しません")
    marker = staged / MARKER_FILE_NAME
    _atomic_marker(
        marker,
        {
            "schema": MARKER_SCHEMA,
            "schemaVersion": MARKER_SCHEMA_VERSION,
            "transactionUid": uuid.uuid4().hex,
            "sourcePageUid": source_page_uid,
            "sourceComaUid": source_coma_uid,
            "targetPageUid": target_page_uid,
            "targetComaUid": target_coma_uid,
            "treeHash": expected_hash,
        },
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged, destination)
    return destination / MARKER_FILE_NAME


def _load_marker(root: Path, marker: Path) -> dict[str, str]:
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ComaMoveRecoveryError(
            f"コマ移動の復旧記録を読めません: {marker}"
        ) from exc
    if not isinstance(value, dict) or set(value) != _MARKER_KEYS:
        raise ComaMoveRecoveryError("コマ移動の復旧記録形式が不正です")
    if value.get("schema") != MARKER_SCHEMA:
        raise ComaMoveRecoveryError("未知のコマ移動復旧記録です")
    if value.get("schemaVersion") != MARKER_SCHEMA_VERSION:
        raise ComaMoveRecoveryError("未対応のコマ移動復旧記録です")
    transaction_uid = value.get("transactionUid")
    tree_digest = value.get("treeHash")
    if not isinstance(transaction_uid, str) or not _TRANSACTION_RE.fullmatch(
        transaction_uid
    ):
        raise ComaMoveRecoveryError("コマ移動のtransaction UIDが不正です")
    if not isinstance(tree_digest, str) or not _SHA256_RE.fullmatch(tree_digest):
        raise ComaMoveRecoveryError("コマ移動の実体hashが不正です")
    result = {
        "transactionUid": transaction_uid,
        "treeHash": tree_digest,
        "sourcePageUid": validate_uid(value.get("sourcePageUid"), UIDKind.PAGE),
        "sourceComaUid": validate_uid(value.get("sourceComaUid"), UIDKind.COMA),
        "targetPageUid": validate_uid(value.get("targetPageUid"), UIDKind.PAGE),
        "targetComaUid": validate_uid(value.get("targetComaUid"), UIDKind.COMA),
    }
    expected_destination = (
        root
        / paths.PAGES_DIR_NAME
        / result["targetPageUid"]
        / paths.COMAS_DIR_NAME
        / result["targetComaUid"]
        / MARKER_FILE_NAME
    )
    if marker.resolve() != expected_destination.resolve():
        raise ComaMoveRecoveryError("コマ移動復旧記録の配置先が不正です")
    if (
        result["sourcePageUid"] == result["targetPageUid"]
        and result["sourceComaUid"] == result["targetComaUid"]
    ):
        raise ComaMoveRecoveryError("コマ移動元と移動先が同一です")
    return result


def _contains_native(document, native_uid: str) -> bool:
    return any(
        node.kind == "coma" and node.native_uid == native_uid
        for node in document.nodes.values()
    )


def _verify_hash(directory: Path, expected: str, label: str) -> None:
    actual = tree_hash(directory)
    if actual != expected:
        raise ComaMoveRecoveryError(
            f"{label}の内容が復旧記録と一致しません: {directory}"
        )


def _recover_marker(
    root: Path,
    repository: ProjectRepository,
    marker: Path,
) -> Path:
    data = _load_marker(root, marker)
    project = repository.load_project()
    page_uids = {page.uid for page in project.pages}
    source_page_uid = data["sourcePageUid"]
    target_page_uid = data["targetPageUid"]
    if source_page_uid not in page_uids or target_page_uid not in page_uids:
        raise ComaMoveRecoveryError("コマ移動対象ページがDomainにありません")
    source_document = repository.load_page(source_page_uid)
    target_document = repository.load_page(target_page_uid)
    source_native = data["sourceComaUid"]
    target_native = data["targetComaUid"]
    source_referenced = _contains_native(source_document, source_native)
    target_referenced = _contains_native(target_document, target_native)
    source = (
        repository.page_dir(source_page_uid)
        / paths.COMAS_DIR_NAME
        / source_native
    )
    destination = marker.parent
    expected_hash = data["treeHash"]
    _verify_hash(destination, expected_hash, "移動先コマ")
    if target_referenced and not source_referenced:
        if source.exists():
            _verify_hash(source, expected_hash, "移動元コマ")
            shutil.rmtree(source)
        marker.unlink()
        return destination
    if source_referenced and not target_referenced:
        if not source.is_dir():
            raise ComaMoveRecoveryError("未確定移動の原本コマがありません")
        _verify_hash(source, expected_hash, "移動元コマ")
        shutil.rmtree(destination)
        return source
    raise ComaMoveRecoveryError(
        "コマ移動のDomain世代を一意に判定できません"
    )


def recover_interrupted_coma_moves(
    work_dir: Path,
    *,
    repository: ProjectRepository | None = None,
) -> tuple[Path, ...]:
    """Repository journal収束後のDomainを正本に全移動マーカーを復旧する。"""

    root = Path(work_dir).resolve(strict=True)
    current_repository = repository or ProjectRepository(root)
    recovered: list[Path] = []
    pattern = (
        f"{paths.PAGES_DIR_NAME}/page_*/"
        f"{paths.COMAS_DIR_NAME}/coma_*/{MARKER_FILE_NAME}"
    )
    with work_lock(root, blocking=True):
        for marker in sorted(root.glob(pattern)):
            recovered.append(_recover_marker(root, current_repository, marker))
    return tuple(recovered)


__all__ = (
    "ComaMoveRecoveryError",
    "MARKER_FILE_NAME",
    "publish_native_copy",
    "recover_interrupted_coma_moves",
    "tree_hash",
)
