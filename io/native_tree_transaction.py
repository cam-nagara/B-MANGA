"""Domain checkpointとNative tree追加・隔離を強制終了後も一世代へ収束する。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import uuid
from typing import Iterable, Mapping

from ..bmanga_core.domain_ids import UIDKind, validate_uid
from ..bmanga_core.domain_repository import ProjectRepository
from .project_file_lock import work_lock


JOURNAL_SCHEMA = "bmanga.native-tree-transaction"
JOURNAL_VERSION = 1
JOURNAL_PREFIX = "native-op-"
_TX_RE = re.compile(r"^[0-9a-f]{32}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TOP_KEYS = {"schema", "schemaVersion", "transactionUid", "operations"}
_OP_KEYS = {
    "action",
    "path",
    "quarantine",
    "stage",
    "contentKind",
    "contentHash",
    "ownerKind",
    "pageUid",
    "comaUid",
    "beforeReferenced",
    "afterReferenced",
}
_PAGE_META_NAME = "page.json"


class NativeTreeTransactionError(RuntimeError):
    """Native tree取引を安全に復旧できない。"""


@dataclass(frozen=True, slots=True)
class Owner:
    kind: str
    page_uid: str
    coma_uid: str = ""

    def validate(self) -> None:
        if self.kind not in {"page", "coma"}:
            raise NativeTreeTransactionError("Native owner kind is invalid")
        validate_uid(self.page_uid, UIDKind.PAGE)
        if self.kind == "coma":
            validate_uid(self.coma_uid, UIDKind.COMA)
        elif self.coma_uid:
            raise NativeTreeTransactionError("page owner cannot have coma UID")


@dataclass(frozen=True, slots=True)
class Addition:
    staged: Path
    destination: Path
    owner: Owner
    before_referenced: bool = False
    after_referenced: bool = True


@dataclass(frozen=True, slots=True)
class Removal:
    source: Path
    owner: Owner
    before_referenced: bool = True
    after_referenced: bool = False


@dataclass(slots=True)
class _Operation:
    action: str
    path: Path
    quarantine: Path | None
    stage: Path | None
    content_kind: str
    content_hash: str
    owner: Owner
    before_referenced: bool
    after_referenced: bool

    def to_dict(self, root: Path) -> dict[str, object]:
        return {
            "action": self.action,
            "path": self.path.relative_to(root).as_posix(),
            "quarantine": (
                self.quarantine.relative_to(root).as_posix()
                if self.quarantine is not None
                else ""
            ),
            "stage": (
                self.stage.relative_to(root).as_posix()
                if self.stage is not None
                else ""
            ),
            "contentKind": self.content_kind,
            "contentHash": self.content_hash,
            "ownerKind": self.owner.kind,
            "pageUid": self.owner.page_uid,
            "comaUid": self.owner.coma_uid,
            "beforeReferenced": self.before_referenced,
            "afterReferenced": self.after_referenced,
        }


def _is_link(path: Path) -> bool:
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _hash_content(path: Path, *, page_owner: bool) -> tuple[str, str]:
    if _is_link(path):
        raise NativeTreeTransactionError(f"Native path is linked: {path}")
    if path.is_file():
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return "file", digest.hexdigest()
    if not path.is_dir():
        raise NativeTreeTransactionError(f"Native path is missing: {path}")
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*"), key=lambda value: value.as_posix()):
        if _is_link(item):
            raise NativeTreeTransactionError(f"Native child is linked: {item}")
        relative = item.relative_to(path).as_posix()
        if page_owner and (
            relative == _PAGE_META_NAME
            or relative.startswith(f".{_PAGE_META_NAME}.stage-")
            or relative.startswith(f".{_PAGE_META_NAME}.backup-")
        ):
            continue
        encoded = relative.encode("utf-8")
        if item.is_dir():
            digest.update(b"D\0" + encoded + b"\0")
        elif item.is_file():
            digest.update(b"F\0" + encoded + b"\0")
            digest.update(str(item.stat().st_size).encode("ascii") + b"\0")
            with item.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            raise NativeTreeTransactionError(
                f"Native child has unsupported type: {item}"
            )
    return "directory", digest.hexdigest()


def _inside(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise NativeTreeTransactionError("Native journal path is invalid")
    candidate = (root / Path(value)).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise NativeTreeTransactionError(
            "Native journal path escapes the work"
        ) from exc
    return candidate


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    stage = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = (
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    try:
        with stage.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(stage, path)
    finally:
        stage.unlink(missing_ok=True)


def _owner_referenced(repository: ProjectRepository, owner: Owner) -> bool:
    project = repository.load_project()
    if owner.page_uid not in {page.uid for page in project.pages}:
        return False
    if owner.kind == "page":
        return True
    page = repository.load_page(owner.page_uid)
    return any(
        node.kind == "coma" and node.native_uid == owner.coma_uid
        for node in page.nodes.values()
    )


def _verify(operation: _Operation, path: Path) -> None:
    kind, digest = _hash_content(
        path,
        page_owner=operation.owner.kind == "page",
    )
    if kind != operation.content_kind or digest != operation.content_hash:
        raise NativeTreeTransactionError(
            f"Native content differs from its recovery journal: {path}"
        )


def _rollback_operation(operation: _Operation) -> None:
    if operation.action == "add":
        if operation.path.exists():
            _verify(operation, operation.path)
            if operation.path.is_dir():
                shutil.rmtree(operation.path)
            else:
                operation.path.unlink()
        if operation.stage is not None and operation.stage.exists():
            _verify(operation, operation.stage)
            if operation.stage.is_dir():
                shutil.rmtree(operation.stage)
            else:
                operation.stage.unlink()
        return
    quarantine = operation.quarantine
    if quarantine is None:
        raise NativeTreeTransactionError("removal quarantine is missing")
    source_exists = operation.path.exists()
    quarantine_exists = quarantine.exists()
    if source_exists and quarantine_exists:
        raise NativeTreeTransactionError("Native source and quarantine both exist")
    if quarantine_exists:
        _verify(operation, quarantine)
        operation.path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(quarantine, operation.path)
    elif source_exists:
        _verify(operation, operation.path)
    else:
        raise NativeTreeTransactionError("Native removal lost both generations")


def _finalize_operation(
    operation: _Operation,
    *,
    replaced_paths: set[Path],
) -> None:
    if operation.action == "add":
        if not operation.path.exists():
            raise NativeTreeTransactionError("Committed Native addition is missing")
        _verify(operation, operation.path)
        if operation.stage is not None and operation.stage.exists():
            raise NativeTreeTransactionError(
                "Committed Native addition still has staged content"
            )
        return
    quarantine = operation.quarantine
    if operation.path.exists() and operation.path not in replaced_paths:
        raise NativeTreeTransactionError("Committed Native removal still has source")
    if quarantine is not None and quarantine.exists():
        _verify(operation, quarantine)
        if quarantine.is_dir():
            shutil.rmtree(quarantine)
        else:
            quarantine.unlink()


def _load_journal(
    root: Path,
    journal: Path,
) -> tuple[str, list[_Operation]]:
    try:
        raw = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NativeTreeTransactionError(
            f"Native journal cannot be read: {journal}"
        ) from exc
    if not isinstance(raw, dict) or set(raw) != _TOP_KEYS:
        raise NativeTreeTransactionError("Native journal fields are invalid")
    if raw.get("schema") != JOURNAL_SCHEMA or raw.get(
        "schemaVersion"
    ) != JOURNAL_VERSION:
        raise NativeTreeTransactionError("Native journal schema is unsupported")
    txid = raw.get("transactionUid")
    if not isinstance(txid, str) or not _TX_RE.fullmatch(txid):
        raise NativeTreeTransactionError("Native transaction UID is invalid")
    if journal != root / "journal" / f"{JOURNAL_PREFIX}{txid}.json":
        raise NativeTreeTransactionError("Native journal filename is invalid")
    values = raw.get("operations")
    if not isinstance(values, list) or not values:
        raise NativeTreeTransactionError("Native journal has no operations")
    operations: list[_Operation] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict) or set(value) != _OP_KEYS:
            raise NativeTreeTransactionError("Native operation fields are invalid")
        action = value.get("action")
        content_kind = value.get("contentKind")
        content_hash = value.get("contentHash")
        if action not in {"add", "remove"}:
            raise NativeTreeTransactionError("Native operation action is invalid")
        if content_kind not in {"file", "directory"}:
            raise NativeTreeTransactionError("Native content kind is invalid")
        if not isinstance(content_hash, str) or not _HASH_RE.fullmatch(
            content_hash
        ):
            raise NativeTreeTransactionError("Native content hash is invalid")
        before = value.get("beforeReferenced")
        after = value.get("afterReferenced")
        if not isinstance(before, bool) or not isinstance(after, bool):
            raise NativeTreeTransactionError("Native owner states are invalid")
        owner = Owner(
            str(value.get("ownerKind", "")),
            validate_uid(value.get("pageUid"), UIDKind.PAGE),
            str(value.get("comaUid", "")),
        )
        owner.validate()
        target = _inside(root, value.get("path"))
        quarantine_text = value.get("quarantine")
        quarantine = (
            _inside(root, quarantine_text)
            if isinstance(quarantine_text, str) and quarantine_text
            else None
        )
        stage_text = value.get("stage")
        stage = (
            _inside(root, stage_text)
            if isinstance(stage_text, str) and stage_text
            else None
        )
        expected_quarantine = (
            root / "journal" / f"{JOURNAL_PREFIX}{txid}" / f"{index:04d}"
        )
        expected_stage = (
            root
            / "journal"
            / f"{JOURNAL_PREFIX}{txid}"
            / f"{index:04d}.stage"
        )
        if action == "remove" and quarantine != expected_quarantine:
            raise NativeTreeTransactionError("Native quarantine path is invalid")
        if action == "add" and quarantine is not None:
            raise NativeTreeTransactionError("Native addition has quarantine")
        if action == "add" and stage != expected_stage:
            raise NativeTreeTransactionError("Native addition stage is invalid")
        if action == "remove" and stage is not None:
            raise NativeTreeTransactionError("Native removal has a stage")
        operations.append(
            _Operation(
                action,
                target,
                quarantine,
                stage,
                str(content_kind),
                content_hash,
                owner,
                before,
                after,
            )
        )
    by_path: dict[Path, list[_Operation]] = {}
    for operation in operations:
        by_path.setdefault(operation.path, []).append(operation)
    for path_operations in by_path.values():
        actions = [operation.action for operation in path_operations]
        if actions not in (["add"], ["remove"], ["remove", "add"]):
            raise NativeTreeTransactionError(
                "Native journal has invalid same-path operations"
            )
    return txid, operations


def _recover_one(
    root: Path,
    repository: ProjectRepository,
    journal: Path,
) -> bool:
    txid, operations = _load_journal(root, journal)
    by_path: dict[Path, list[_Operation]] = {}
    for operation in operations:
        by_path.setdefault(operation.path, []).append(operation)
    owner_states: dict[Owner, bool] = {}
    for operation in operations:
        owner_states.setdefault(
            operation.owner,
            _owner_referenced(repository, operation.owner),
        )
    before = all(
        owner_states[operation.owner] == operation.before_referenced
        for operation in operations
    )
    after = all(
        owner_states[operation.owner] == operation.after_referenced
        for operation in operations
    )
    if before == after:
        raise NativeTreeTransactionError(
            "Native transaction Domain generation is ambiguous"
        )
    if before:
        for operation in reversed(operations):
            _rollback_operation(operation)
    else:
        replaced_paths = {
            path
            for path, path_operations in by_path.items()
            if len(path_operations) == 2
        }
        for operation in sorted(
            operations,
            key=lambda value: value.action != "add",
        ):
            _finalize_operation(
                operation,
                replaced_paths=replaced_paths,
            )
    transaction_dir = root / "journal" / f"{JOURNAL_PREFIX}{txid}"
    if transaction_dir.exists():
        transaction_dir.rmdir()
    journal.unlink()
    return after


class NativeTreeTransaction:
    """Native tree変更を先行適用し、Domain checkpoint後に確定する。"""

    def __init__(
        self,
        work_dir: Path,
        *,
        repository: ProjectRepository,
        additions: Iterable[Addition] = (),
        removals: Iterable[Removal] = (),
    ) -> None:
        self.root = Path(work_dir).resolve(strict=True)
        self.repository = repository
        self.txid = uuid.uuid4().hex
        self.journal = (
            self.root / "journal" / f"{JOURNAL_PREFIX}{self.txid}.json"
        )
        self._staged: dict[Path, Path] = {}
        self._operations: list[_Operation] = []
        transaction_dir = (
            self.root / "journal" / f"{JOURNAL_PREFIX}{self.txid}"
        )
        for value in removals:
            value.owner.validate()
            source = Path(value.source).resolve(strict=True)
            kind, digest = _hash_content(
                source,
                page_owner=value.owner.kind == "page",
            )
            self._operations.append(
                _Operation(
                    "remove",
                    source,
                    transaction_dir / f"{len(self._operations):04d}",
                    None,
                    kind,
                    digest,
                    value.owner,
                    value.before_referenced,
                    value.after_referenced,
                )
            )
        for value in additions:
            value.owner.validate()
            staged = Path(value.staged).resolve(strict=True)
            destination = Path(value.destination).resolve(strict=False)
            kind, digest = _hash_content(
                staged,
                page_owner=value.owner.kind == "page",
            )
            self._staged[destination] = staged
            self._operations.append(
                _Operation(
                    "add",
                    destination,
                    None,
                    transaction_dir
                    / f"{len(self._operations):04d}.stage",
                    kind,
                    digest,
                    value.owner,
                    value.before_referenced,
                    value.after_referenced,
                )
            )
        if not self._operations:
            raise NativeTreeTransactionError("Native transaction is empty")
        for operation in self._operations:
            try:
                operation.path.relative_to(self.root)
            except ValueError as exc:
                raise NativeTreeTransactionError(
                    "Native target is outside the work"
                ) from exc

    def prepare(self) -> None:
        self.repository.recover()
        recover_pending_native_transactions(
            self.root,
            repository=self.repository,
        )
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        (self.root / "journal" / f"{JOURNAL_PREFIX}{self.txid}").mkdir()
        _atomic_json(
            self.journal,
            {
                "schema": JOURNAL_SCHEMA,
                "schemaVersion": JOURNAL_VERSION,
                "transactionUid": self.txid,
                "operations": [
                    operation.to_dict(self.root)
                    for operation in self._operations
                ],
            },
        )
        try:
            for operation in self._operations:
                if operation.action != "add":
                    continue
                stage = operation.stage
                if stage is None:
                    raise NativeTreeTransactionError(
                        "Native addition stage is missing"
                    )
                os.replace(self._staged[operation.path], stage)
                _verify(operation, stage)
        except BaseException:
            _recover_one(self.root, self.repository, self.journal)
            raise

    def apply_removals(self) -> None:
        for operation in self._operations:
            if operation.action != "remove":
                continue
            quarantine = operation.quarantine
            if quarantine is None or operation.path.exists() is False:
                raise NativeTreeTransactionError("Native removal source vanished")
            os.replace(operation.path, quarantine)

    def apply_additions(self) -> None:
        for operation in self._operations:
            if operation.action != "add":
                continue
            staged = operation.stage
            if staged is None or not staged.exists():
                raise NativeTreeTransactionError(
                    "Native staged addition vanished"
                )
            if operation.path.exists():
                raise FileExistsError(operation.path)
            operation.path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, operation.path)

    def apply(self) -> None:
        self.apply_removals()
        self.apply_additions()

    def recover(self) -> bool:
        self.repository.recover()
        return _recover_one(self.root, self.repository, self.journal)


def recover_pending_native_transactions(
    work_dir: Path,
    *,
    repository: ProjectRepository | None = None,
) -> int:
    """Repository journal収束後に全Native tree journalを復旧する。"""

    root = Path(work_dir).resolve(strict=True)
    current = repository or ProjectRepository(root)
    journal_dir = root / "journal"
    if not journal_dir.is_dir():
        return 0
    recovered = 0
    with work_lock(root, blocking=True):
        for journal in sorted(journal_dir.glob(f"{JOURNAL_PREFIX}*.json")):
            _recover_one(root, current, journal)
            recovered += 1
        for stage_root in journal_dir.glob(".coma-operation-*"):
            if (
                stage_root.is_dir()
                and not _is_link(stage_root)
                and not any(stage_root.iterdir())
            ):
                stage_root.rmdir()
    return recovered


__all__ = (
    "Addition",
    "NativeTreeTransaction",
    "NativeTreeTransactionError",
    "Owner",
    "Removal",
    "recover_pending_native_transactions",
)
