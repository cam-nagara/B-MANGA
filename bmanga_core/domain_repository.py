"""project.json/page.jsonの厳格Repositoryとwrite-ahead journal。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable, ContextManager, Iterable, Mapping

from .domain_ids import UIDKind, validate_uid
from .domain_model import (
    PAGE_SCHEMA,
    PROJECT_SCHEMA,
    PageDocument,
    ProjectDocument,
    canonical_json_bytes,
)
from .faults import FaultPoint, check_fault
from .file_identity import (
    ArtifactCommitHook,
    FileIdentity,
    identity_from_written_handle,
    matches_file_identity,
)


PROJECT_FILE_NAME = "project.json"
PAGES_DIR_NAME = "pages"
PAGE_FILE_NAME = "page.json"
JOURNAL_DIR_NAME = "journal"
JOURNAL_SCHEMA_VERSION = 1
MIN_CHECKPOINT_FREE_SPACE_BYTES = 1024 * 1024
_TRANSACTION_UID_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _is_reparse_stat(value: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(int(getattr(value, "st_file_attributes", 0) or 0) & flag)


class RepositoryError(RuntimeError):
    """Repository操作が安全に完了できない。"""


class LegacyFormatError(RepositoryError):
    """旧保存形式を新形式として開こうとした。"""


class RepositoryConflictError(RepositoryError):
    """読込後に別processが保存先を変更した。"""


class JournalRecoveryError(RepositoryError):
    """中断checkpointを完全状態へ戻せない。"""


class JournalState(StrEnum):
    PREPARED = "PREPARED"
    NATIVE_SAVED = "NATIVE_SAVED"
    INSTALLING = "INSTALLING"
    COMMITTED = "COMMITTED"


class SimulatedProcessCrash(BaseException):
    """subprocess相当の中断を決定的に再現するテスト専用例外。"""


PhaseHook = Callable[[JournalState, int], None]
LockFactory = Callable[[], ContextManager[object]]


@dataclass(slots=True)
class _Entry:
    target: Path
    staged: Path
    backup: Path
    before_exists: bool
    before_hash: str
    after_hash: str
    after_identity: FileIdentity | None = None
    installed: bool = False

    def to_dict(self, root: Path) -> dict[str, object]:
        return {
            "target": self.target.relative_to(root).as_posix(),
            "staged": self.staged.relative_to(root).as_posix(),
            "backup": self.backup.relative_to(root).as_posix(),
            "beforeExists": self.before_exists,
            "beforeHash": self.before_hash,
            "afterHash": self.after_hash,
            "installed": self.installed,
        }


class ProjectRepository:
    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        lock_factory: LockFactory | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.project_path = self.root / PROJECT_FILE_NAME
        self.journal_dir = self.root / JOURNAL_DIR_NAME
        self._observed_hashes: dict[Path, str] = {}
        self._lock_factory = lock_factory or nullcontext

    def initialize_layout(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for relative in (
            PAGES_DIR_NAME,
            "presets",
            "cache",
            JOURNAL_DIR_NAME,
            "assets",
        ):
            (self.root / relative).mkdir(exist_ok=True)

    def reject_legacy_layout(self) -> None:
        legacy = [
            path
            for path in (self.root / "work.json", self.root / "pages.json")
            if path.exists()
        ]
        if legacy:
            names = ", ".join(path.name for path in legacy)
            raise LegacyFormatError(
                f"旧形式の作品は開けません（{names}）。新規作品を作成してください"
            )

    def page_dir(self, page_uid: str) -> Path:
        result = (
            self.root
            / PAGES_DIR_NAME
            / validate_uid(page_uid, UIDKind.PAGE)
        )
        self._assert_physical_target(result)
        return result

    def page_path(self, page_uid: str) -> Path:
        result = self.page_dir(page_uid) / PAGE_FILE_NAME
        self._assert_physical_target(result)
        return result

    def load_project(self) -> ProjectDocument:
        with self._lock_factory():
            self._assert_physical_target(self.project_path)
            self.reject_legacy_layout()
            data = self._read_mapping(self.project_path, PROJECT_SCHEMA)
            result = ProjectDocument.from_dict(data)
            self._remember(self.project_path)
            return result

    def load_page(self, page_uid: str) -> PageDocument:
        with self._lock_factory():
            path = self.page_path(page_uid)
            data = self._read_mapping(path, PAGE_SCHEMA)
            result = PageDocument.from_dict(data)
            if result.page_uid != page_uid:
                raise RepositoryError("page path/UID mismatch")
            self._remember(path)
            return result

    def assert_project_page_files(self, project: ProjectDocument) -> None:
        """一覧読込ではpage.jsonの存在と物理境界だけを検査する。

        schemaとproject UIDは対象ページを開く時の``load_page``で検査する。
        一覧表示で全ページ本文を解析するとページ数に比例してopenが遅くなる
        ため、ここでは詳細をRAMへ読み込まない。
        """
        project.validate()
        pages_root = self.root / PAGES_DIR_NAME
        self._assert_physical_target(pages_root)
        expected = {summary.uid for summary in project.pages}
        found: set[str] = set()
        try:
            entries = os.scandir(pages_root)
        except FileNotFoundError as exc:
            raise RepositoryError(
                f"required directory is missing: {pages_root}"
            ) from exc
        with entries:
            for entry in entries:
                if entry.name not in expected:
                    continue
                entry_stat = entry.stat(follow_symlinks=False)
                if (
                    entry.is_symlink()
                    or not entry.is_dir(follow_symlinks=False)
                    or _is_reparse_stat(entry_stat)
                ):
                    raise RepositoryError(
                        "page directory is not a physical directory: "
                        f"{entry.path}"
                    )
                page_path = Path(entry.path) / PAGE_FILE_NAME
                try:
                    page_stat = page_path.lstat()
                except FileNotFoundError as exc:
                    raise RepositoryError(
                        f"required file is missing: {page_path}"
                    ) from exc
                if (
                    page_path.is_symlink()
                    or not stat.S_ISREG(page_stat.st_mode)
                    or _is_reparse_stat(page_stat)
                ):
                    raise RepositoryError(
                        f"page file is not a physical file: {page_path}"
                    )
                found.add(entry.name)
        missing = sorted(expected - found)
        if missing:
            raise RepositoryError(
                "required page files are missing: " + ", ".join(missing)
            )

    def assert_observations_current(
        self,
        paths: Iterable[str | os.PathLike[str]],
    ) -> None:
        """書込み対象外を含むSession観測が外部更新されていないか確認する。"""

        with self._lock_factory():
            targets: dict[Path, bytes] = {}
            for value in paths:
                path = Path(value).resolve(strict=False)
                self._assert_physical_target(path)
                if not self._is_repository_target(path):
                    raise RepositoryError(
                        f"not a repository target: {path}"
                    )
                targets[path] = b""
            self._assert_no_conflicts(targets)

    def checkpoint(
        self,
        project: ProjectDocument,
        pages: Iterable[PageDocument] = (),
        *,
        include_project: bool = True,
        native_checkpoint: Callable[[], bool] | None = None,
        phase_hook: PhaseHook | None = None,
        artifact_commit_hook: ArtifactCommitHook | None = None,
    ) -> str:
        self.initialize_layout()
        with self._lock_factory():
            return self._checkpoint_locked(
                project,
                pages,
                include_project=include_project,
                native_checkpoint=native_checkpoint,
                phase_hook=phase_hook,
                artifact_commit_hook=artifact_commit_hook,
            )

    def _checkpoint_locked(
        self,
        project: ProjectDocument,
        pages: Iterable[PageDocument],
        *,
        include_project: bool,
        native_checkpoint: Callable[[], bool] | None,
        phase_hook: PhaseHook | None,
        artifact_commit_hook: ArtifactCommitHook | None,
    ) -> str:
        project.validate()
        page_list = list(pages)
        self._validate_page_set(project, page_list)
        payloads = (
            {self.project_path: canonical_json_bytes(project)}
            if include_project
            else {}
        )
        payloads.update(
            {self.page_path(page.page_uid): canonical_json_bytes(page) for page in page_list}
        )
        if not payloads:
            return ""
        for target in payloads:
            self._assert_physical_target(target)
        self._assert_no_conflicts(payloads)
        txid = uuid.uuid4().hex
        entries: list[_Entry] | None = None
        journal_path = self.journal_dir / f"checkpoint-{txid}.json"
        try:
            entries, journal_path = self._prepare(txid, payloads)
            self._call_hook(phase_hook, JournalState.PREPARED, 0)
            if native_checkpoint is not None and native_checkpoint() is not True:
                raise RepositoryError("native checkpoint failed")
            self._write_journal(journal_path, txid, JournalState.NATIVE_SAVED, entries)
            self._call_hook(phase_hook, JournalState.NATIVE_SAVED, 0)
            self._install(txid, journal_path, entries, phase_hook)
        except Exception:
            if entries is not None:
                self._rollback(entries)
                self._cleanup(journal_path, entries)
            raise
        self._cleanup(journal_path, entries)
        for path in payloads:
            self._remember(path)
        if artifact_commit_hook is not None:
            for entry in entries:
                identity = entry.after_identity
                if identity is None:
                    raise RepositoryError(
                        "checkpoint生成物の物理IDがありません"
                    )
                artifact_commit_hook(entry.target, identity)
        return txid

    def recover(self) -> int:
        with self._lock_factory():
            return self._recover_locked()

    def accept_recovered_files(self, paths: Iterable[str | os.PathLike[str]]) -> None:
        """排他ロック下で復元したDomainファイルを新しい観測基準にする。

        通常保存の競合検知を迂回するためのAPIではない。呼出側が同じ作品の
        排他ロックを保持し、自身のrollback backupを復元した直後だけ使う。
        """
        with self._lock_factory():
            for value in paths:
                path = Path(value).resolve()
                try:
                    path.relative_to(self.root)
                except ValueError as exc:
                    raise RepositoryError(
                        f"recovered path is outside repository: {path}"
                    ) from exc
                if path == self.project_path or (
                    path.name == PAGE_FILE_NAME
                    and path.parent.parent.parent == self.root
                    and path.parent.parent.name == PAGES_DIR_NAME
                ):
                    self._remember(path)

    def observed_project_hash(self) -> str:
        """最後に厳格読込またはcheckpointしたproject.jsonのSHA-256."""

        return self._observed_hashes.get(self.project_path, "")

    def _recover_locked(self) -> int:
        if not self.journal_dir.is_dir():
            return 0
        recovered = 0
        for journal_path in sorted(self.journal_dir.glob("checkpoint-*.json")):
            data = self._read_raw_mapping(journal_path)
            txid, state, entries = self._validate_journal(journal_path, data)
            if state is JournalState.COMMITTED:
                self._verify_committed(entries)
            else:
                self._validate_rollback_generation(state, entries)
                self._rollback(entries)
            self._cleanup(journal_path, entries)
            recovered += 1
        return recovered

    def _prepare(
        self,
        txid: str,
        payloads: Mapping[Path, bytes],
    ) -> tuple[list[_Entry], Path]:
        self._assert_checkpoint_capacity(payloads)
        entries: list[_Entry] = []
        journal_path = self.journal_dir / f"checkpoint-{txid}.json"
        staged_paths: list[Path] = []
        try:
            for target, payload in sorted(payloads.items(), key=lambda item: str(item[0])):
                target.parent.mkdir(parents=True, exist_ok=True)
                staged = target.with_name(f".{target.name}.stage-{txid}")
                backup = target.with_name(f".{target.name}.backup-{txid}")
                staged_paths.append(staged)
                after_identity = _write_bytes(staged, payload)
                before_exists = target.is_file()
                before_hash = _file_hash(target) if before_exists else ""
                after_hash = _bytes_hash(payload)
                if after_identity.sha256 != after_hash:
                    raise RepositoryError(
                        "checkpoint stageの内容hashが一致しません"
                    )
                entries.append(
                    _Entry(
                        target,
                        staged,
                        backup,
                        before_exists,
                        before_hash,
                        after_hash,
                        after_identity,
                    )
                )
            self._write_journal(journal_path, txid, JournalState.PREPARED, entries)
        except Exception:
            for staged in staged_paths:
                staged.unlink(missing_ok=True)
            journal_path.unlink(missing_ok=True)
            raise
        return entries, journal_path

    def _assert_checkpoint_capacity(self, payloads: Mapping[Path, bytes]) -> None:
        staged_bytes = sum(len(payload) for payload in payloads.values())
        backup_bytes = sum(
            target.stat().st_size for target in payloads if target.is_file()
        )
        # stage、backup、置換後の最終世代を同時保持できる側へ倒す。
        required = (
            staged_bytes * 2
            + backup_bytes
            + MIN_CHECKPOINT_FREE_SPACE_BYTES
        )
        available = shutil.disk_usage(self.root).free
        if available < required:
            raise RepositoryError(
                "checkpoint capacity is insufficient: "
                f"required={required}, available={available}"
            )

    def _install(
        self,
        txid: str,
        journal_path: Path,
        entries: list[_Entry],
        hook: PhaseHook | None,
    ) -> None:
        self._write_journal(journal_path, txid, JournalState.INSTALLING, entries)
        for index, entry in enumerate(entries, start=1):
            if entry.before_exists:
                shutil.copy2(entry.target, entry.backup)
                _fsync_file(entry.backup)
            os.replace(entry.staged, entry.target)
            if (
                entry.after_identity is None
                or not matches_file_identity(
                    entry.target,
                    entry.after_identity,
                )
            ):
                raise RepositoryError(
                    "checkpoint install直後の物理IDが一致しません"
                )
            entry.installed = True
            self._write_journal(journal_path, txid, JournalState.INSTALLING, entries)
            check_fault(FaultPoint.CHECKPOINT_AFTER_INSTALL, path=str(entry.target))
            self._call_hook(hook, JournalState.INSTALLING, index)
        self._write_journal(journal_path, txid, JournalState.COMMITTED, entries)
        check_fault(FaultPoint.CHECKPOINT_AFTER_COMMIT, root=str(self.root))
        self._call_hook(hook, JournalState.COMMITTED, len(entries))

    def _rollback(self, entries: Iterable[_Entry]) -> None:
        entry_list = tuple(entries)
        failures: list[str] = []
        for entry in reversed(entry_list):
            try:
                target_hash = _optional_file_hash(entry.target)
                backup_hash = _optional_file_hash(entry.backup)
                before_matches = (
                    target_hash == entry.before_hash
                    if entry.before_exists
                    else target_hash is None
                )
                if before_matches:
                    entry.backup.unlink(missing_ok=True)
                elif backup_hash == entry.before_hash:
                    os.replace(entry.backup, entry.target)
                elif not entry.before_exists and target_hash == entry.after_hash:
                    entry.target.unlink(missing_ok=True)
                    entry.backup.unlink(missing_ok=True)
                else:
                    failures.append(str(entry.target))
            except OSError:
                failures.append(str(entry.target))
        if failures:
            raise JournalRecoveryError(
                "checkpoint rollback failed: " + ", ".join(failures)
            )

    def _verify_committed(self, entries: Iterable[_Entry]) -> None:
        bad = [
            str(entry.target)
            for entry in entries
            if not entry.target.is_file() or _file_hash(entry.target) != entry.after_hash
        ]
        if bad:
            raise JournalRecoveryError(
                "committed checkpoint is incomplete: " + ", ".join(bad)
            )

    def _cleanup(self, journal_path: Path, entries: Iterable[_Entry]) -> None:
        for entry in entries:
            entry.staged.unlink(missing_ok=True)
            entry.backup.unlink(missing_ok=True)
        journal_path.unlink(missing_ok=True)

    def _write_journal(
        self,
        path: Path,
        txid: str,
        state: JournalState,
        entries: Iterable[_Entry],
    ) -> None:
        payload = {
            "schema": "bmanga.checkpoint-journal",
            "schemaVersion": JOURNAL_SCHEMA_VERSION,
            "transactionUid": txid,
            "state": state.value,
            "files": [entry.to_dict(self.root) for entry in entries],
        }
        _atomic_json(path, payload)

    def _validate_journal(
        self,
        path: Path,
        data: Mapping[str, object],
    ) -> tuple[str, JournalState, list[_Entry]]:
        if data.get("schema") != "bmanga.checkpoint-journal":
            raise JournalRecoveryError("unknown journal schema")
        if data.get("schemaVersion") != JOURNAL_SCHEMA_VERSION:
            raise JournalRecoveryError("unsupported journal version")
        txid = data.get("transactionUid")
        if not isinstance(txid, str) or not _TRANSACTION_UID_RE.fullmatch(txid):
            raise JournalRecoveryError("journal transaction UID is invalid")
        if path != self.journal_dir / f"checkpoint-{txid}.json":
            raise JournalRecoveryError("journal filename/transaction UID mismatch")
        try:
            state = JournalState(data.get("state"))
        except (TypeError, ValueError) as exc:
            raise JournalRecoveryError("journal state is invalid") from exc
        files = data.get("files")
        if not isinstance(files, list) or not files:
            raise JournalRecoveryError("journal files are invalid")
        entries = [self._entry_from_dict(value, txid) for value in files]
        targets = [entry.target for entry in entries]
        if len(set(targets)) != len(targets):
            raise JournalRecoveryError("journal contains duplicate targets")
        return txid, state, entries

    def _entry_from_dict(self, value: object, txid: str) -> _Entry:
        if not isinstance(value, Mapping):
            raise JournalRecoveryError("journal entry is invalid")
        target = _inside(self.root, value.get("target"))
        staged = _inside(self.root, value.get("staged"))
        backup = _inside(self.root, value.get("backup"))
        if not self._is_repository_target(target):
            raise JournalRecoveryError("journal target is not repository-owned")
        if staged != target.with_name(f".{target.name}.stage-{txid}"):
            raise JournalRecoveryError("journal stage path is invalid")
        if backup != target.with_name(f".{target.name}.backup-{txid}"):
            raise JournalRecoveryError("journal backup path is invalid")
        before_exists = value.get("beforeExists")
        installed = value.get("installed")
        if type(before_exists) is not bool or type(installed) is not bool:
            raise JournalRecoveryError("journal boolean field is invalid")
        before_hash = value.get("beforeHash")
        after_hash = value.get("afterHash")
        if not isinstance(before_hash, str) or not isinstance(after_hash, str):
            raise JournalRecoveryError("journal hash field is invalid")
        if before_exists:
            if not _SHA256_RE.fullmatch(before_hash):
                raise JournalRecoveryError("journal before hash is invalid")
        elif before_hash:
            raise JournalRecoveryError("new journal target has a before hash")
        if not _SHA256_RE.fullmatch(after_hash):
            raise JournalRecoveryError("journal after hash is invalid")
        return _Entry(
            target=target,
            staged=staged,
            backup=backup,
            before_exists=before_exists,
            before_hash=before_hash,
            after_hash=after_hash,
            installed=installed,
        )

    def _assert_no_conflicts(self, payloads: Mapping[Path, bytes]) -> None:
        for path in payloads:
            expected = self._observed_hashes.get(path)
            if expected is None:
                if path.is_file():
                    raise RepositoryConflictError(
                        f"未読込の既存作品ファイルへは保存できません: {path.name}"
                    )
                continue
            actual = _file_hash(path) if path.is_file() else ""
            if actual != expected:
                raise RepositoryConflictError(
                    f"別のBlender画面で作品が更新されています: {path.name}"
                )

    def _remember(self, path: Path) -> None:
        self._observed_hashes[path] = _file_hash(path) if path.is_file() else ""

    def _is_repository_target(self, path: Path) -> bool:
        if path == self.project_path:
            return True
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            return False
        return (
            len(relative.parts) == 3
            and relative.parts[0] == PAGES_DIR_NAME
            and relative.parts[2] == PAGE_FILE_NAME
            and _is_uid(relative.parts[1], UIDKind.PAGE)
        )

    def _assert_physical_target(self, path: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(self.root)
        except (OSError, ValueError) as exc:
            raise RepositoryError(
                f"repository target escapes project root: {path}"
            ) from exc

    def _validate_rollback_generation(
        self,
        state: JournalState,
        entries: Iterable[_Entry],
    ) -> None:
        failures: list[str] = []
        for entry in entries:
            stage_hash = _optional_file_hash(entry.staged)
            target_hash = _optional_file_hash(entry.target)
            backup_hash = _optional_file_hash(entry.backup)
            before_matches = (
                target_hash == entry.before_hash
                if entry.before_exists
                else target_hash is None
            )
            if (
                backup_hash is not None
                and backup_hash != entry.before_hash
                and not (
                    state is JournalState.INSTALLING
                    and before_matches
                )
            ):
                failures.append(f"backup:{entry.backup}")
                continue
            after_matches = target_hash == entry.after_hash
            stage_matches = stage_hash == entry.after_hash
            if state in {JournalState.PREPARED, JournalState.NATIVE_SAVED}:
                if not stage_matches or not before_matches or backup_hash is not None:
                    failures.append(str(entry.target))
            elif state is JournalState.INSTALLING:
                if before_matches:
                    if not stage_matches:
                        failures.append(str(entry.target))
                elif after_matches:
                    if entry.before_exists and backup_hash != entry.before_hash:
                        failures.append(str(entry.target))
                    elif not entry.before_exists and backup_hash is not None:
                        failures.append(str(entry.target))
                else:
                    failures.append(str(entry.target))
        if failures:
            raise JournalRecoveryError(
                "checkpoint recovery files are inconsistent: "
                + ", ".join(failures)
            )

    @staticmethod
    def _call_hook(hook: PhaseHook | None, state: JournalState, index: int) -> None:
        if hook is not None:
            hook(state, index)

    @staticmethod
    def _validate_page_set(
        project: ProjectDocument,
        pages: Iterable[PageDocument],
    ) -> None:
        allowed = {page.uid for page in project.pages}
        for page in pages:
            page.validate()
            if page.project_uid != project.project_uid or page.page_uid not in allowed:
                raise RepositoryError("page does not belong to project")

    @staticmethod
    def _read_mapping(path: Path, expected_schema: str) -> dict[str, object]:
        data = ProjectRepository._read_raw_mapping(path)
        if data.get("schema") != expected_schema:
            raise LegacyFormatError(
                f"旧形式または未対応形式です: {path.name}"
            )
        return data

    @staticmethod
    def _read_raw_mapping(path: Path) -> dict[str, object]:
        try:
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise RepositoryError(f"required file is missing: {path}") from exc
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RepositoryError(f"invalid JSON: {path}") from exc
        if not isinstance(data, dict):
            raise RepositoryError(f"JSON root must be an object: {path}")
        return data


def _inside(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative:
        raise JournalRecoveryError("journal path is invalid")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise JournalRecoveryError("journal path escapes project root") from exc
    return resolved


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    raw = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def _write_bytes(path: Path, payload: bytes) -> FileIdentity:
    with open(path, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        return identity_from_written_handle(
            handle,
            _bytes_hash(payload),
        )


def _fsync_file(path: Path) -> None:
    # Windows の os.fsync は読み取り専用 descriptor を EINVAL/EBADF にする。
    with open(path, "r+b") as handle:
        os.fsync(handle.fileno())


def _bytes_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _optional_file_hash(path: Path) -> str | None:
    return _file_hash(path) if path.is_file() else None


def _is_uid(value: str, kind: UIDKind) -> bool:
    try:
        validate_uid(value, kind)
    except ValueError:
        return False
    return True


__all__ = (
    "JOURNAL_DIR_NAME",
    "JournalRecoveryError",
    "JournalState",
    "LegacyFormatError",
    "PAGE_FILE_NAME",
    "PAGES_DIR_NAME",
    "PROJECT_FILE_NAME",
    "ProjectRepository",
    "RepositoryConflictError",
    "RepositoryError",
    "SimulatedProcessCrash",
)
