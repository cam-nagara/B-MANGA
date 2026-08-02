"""Native mainfile保存中だけDomain checkpoint確定を遅延する。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from . import save_recovery_paths


RASTER_SNAPSHOT_JOURNAL = "raster-snapshot-journal.json"
RASTER_SNAPSHOT_VERSION = 1
_TRANSACTION_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{12}$")
_STALE_SNAPSHOT_MAX_AGE = timedelta(hours=24)
MAX_RASTER_SNAPSHOT_DIMENSION = 32_768
MAX_RASTER_SNAPSHOT_CHANNELS = 4
MAX_RASTER_SNAPSHOT_RAW_BYTES = 8 * 1024 * 1024 * 1024


@dataclass(slots=True)
class RasterPixelSnapshot:
    width: int
    height: int
    channels: int
    compressed_path: Path
    compressed_sha256: str
    raw_byte_count: int


@dataclass(frozen=True, slots=True)
class RecoverableRasterSnapshotTransaction:
    """プロセス終了後に現在画面へ戻す未保存ラスター画素。"""

    transaction_id: str
    work_dir: Path
    source_path: Path
    snapshot_dir: Path
    raster_snapshots: dict[str, RasterPixelSnapshot]


@dataclass(slots=True)
class PendingNativeCheckpoint:
    work_dir: Path
    repository_paths: tuple[Path, ...]
    raster_ids: tuple[str, ...]
    project_changed: bool = False
    page_uids: set[str] = field(default_factory=set)
    raster_snapshots: dict[str, RasterPixelSnapshot] = field(
        default_factory=dict
    )
    snapshot_dir: Path | None = None


_PENDING: dict[str, PendingNativeCheckpoint] = {}


def _key(work_dir: str | Path) -> str:
    return os.path.normcase(str(Path(work_dir).resolve(strict=False)))


def begin(
    work_dir: str | Path,
    *,
    repository_paths: Iterable[str | Path],
    raster_ids: Iterable[str],
    snapshot_dir: str | Path | None = None,
) -> PendingNativeCheckpoint:
    """Sidecar書込前に、今回のnative保存確定待ち状態を開始する。"""

    key = _key(work_dir)
    if key in _PENDING:
        raise RuntimeError("native checkpoint is already pending")
    pending = PendingNativeCheckpoint(
        Path(work_dir).resolve(strict=False),
        tuple(
            dict.fromkeys(
                Path(path).resolve(strict=False)
                for path in repository_paths
            )
        ),
        tuple(dict.fromkeys(str(value) for value in raster_ids if str(value))),
        snapshot_dir=(
            Path(snapshot_dir).resolve(strict=False)
            if snapshot_dir is not None
            else None
        ),
    )
    _PENDING[key] = pending
    return pending


def is_pending(work_dir: str | Path) -> bool:
    return _key(work_dir) in _PENDING


def pending_for(
    work_dir: str | Path,
) -> PendingNativeCheckpoint | None:
    return _PENDING.get(_key(work_dir))


def preserve_raster_snapshots(
    work_dir: str | Path,
    snapshots: dict[str, RasterPixelSnapshot],
) -> None:
    pending = _PENDING.get(_key(work_dir))
    if pending is None:
        return
    for raster_id, snapshot in snapshots.items():
        if (
            raster_id
            and snapshot.compressed_path.is_file()
            and snapshot.raw_byte_count > 0
        ):
            pending.raster_snapshots.setdefault(raster_id, snapshot)


def note_domain_write(
    work_dir: str | Path,
    *,
    project_changed: bool,
    page_uids: Iterable[str],
) -> None:
    pending = _PENDING.get(_key(work_dir))
    if pending is None:
        return
    pending.project_changed = pending.project_changed or bool(project_changed)
    pending.page_uids.update(
        str(uid) for uid in page_uids if str(uid)
    )


def take(work_dir: str | Path) -> PendingNativeCheckpoint | None:
    """保存成否確定時に状態を一度だけ取り出す。"""

    return _PENDING.pop(_key(work_dir), None)


def clear() -> None:
    for pending in tuple(_PENDING.values()):
        cleanup_snapshot_transaction(
            pending.work_dir,
            pending.snapshot_dir,
        )
    _PENDING.clear()


def create_snapshot_transaction(
    work_dir: str | Path,
    transaction_id: str,
    *,
    source_path: str | Path | None = None,
) -> Path:
    """今回の未保存画素だけを置くdurableな一時領域を作る。"""

    work = Path(work_dir).resolve(strict=True)
    tx_id = str(transaction_id or "")
    if not _TRANSACTION_ID_RE.fullmatch(tx_id):
        raise RuntimeError("invalid raster snapshot transaction ID")
    base = save_recovery_paths.raster_snapshot_base(work)
    save_recovery_paths.assert_recovery_owned_path(
        work,
        base,
        label="ラスター保存復旧先",
    )
    base.mkdir(parents=True, exist_ok=True)
    tx_dir = base / tx_id
    save_recovery_paths.assert_recovery_owned_path(
        work,
        tx_dir,
        label="ラスター保存復旧先",
    )
    tx_dir.mkdir(exist_ok=False)
    journal = tx_dir / RASTER_SNAPSHOT_JOURNAL
    _write_json_durable(
        journal,
        {
            "journalVersion": RASTER_SNAPSHOT_VERSION,
            "transactionId": tx_id,
            "status": "capturing",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "sourcePath": (
                str(Path(source_path).resolve(strict=False))
                if source_path is not None
                else ""
            ),
            "snapshots": [],
        },
    )
    return tx_dir


def snapshot_path(snapshot_dir: str | Path, raster_id: str) -> Path:
    """安定IDをファイル名へ直接出さず、専用領域内の圧縮先を返す。"""

    directory = Path(snapshot_dir).resolve(strict=True)
    digest = hashlib.sha256(str(raster_id).encode("utf-8")).hexdigest()
    path = directory / f"{digest}.pixels.zlib"
    if path.parent != directory:
        raise RuntimeError("raster snapshot path escaped transaction")
    return path


def seal_snapshot_transaction(
    snapshot_dir: str | Path,
    snapshots: dict[str, RasterPixelSnapshot],
) -> None:
    """全圧縮ファイルを検証できるmanifestをfsyncしてから利用可能にする。"""

    directory = Path(snapshot_dir).resolve(strict=True)
    journal = directory / RASTER_SNAPSHOT_JOURNAL
    payload = json.loads(journal.read_text(encoding="utf-8"))
    payload["status"] = "ready"
    payload["snapshots"] = [
        {
            "rasterId": raster_id,
            "file": snapshot.compressed_path.name,
            "sha256": snapshot.compressed_sha256,
            "rawBytes": snapshot.raw_byte_count,
            "width": snapshot.width,
            "height": snapshot.height,
            "channels": snapshot.channels,
        }
        for raster_id, snapshot in sorted(snapshots.items())
    ]
    _write_json_durable(journal, payload)


def cleanup_snapshot_transaction(
    work_dir: str | Path,
    snapshot_dir: str | Path | None,
) -> None:
    if snapshot_dir is None:
        return
    work = Path(work_dir).resolve(strict=False)
    directory = Path(snapshot_dir)
    save_recovery_paths.assert_recovery_owned_path(
        work,
        directory,
        label="ラスター保存復旧削除対象",
    )
    base = save_recovery_paths.raster_snapshot_base(work)
    if directory.parent.resolve(strict=False) != base.resolve(strict=False):
        raise RuntimeError("raster snapshot transaction is outside its base")
    save_recovery_paths.remove_recovery_tree(
        work,
        directory,
        ignore_errors=True,
    )
    save_recovery_paths.prune_empty_base(work, base)


def cleanup_stale_snapshot_transactions(
    work_dir: str | Path,
) -> tuple[Path, ...]:
    """異常終了で残った24時間超の画素snapshotだけを安全に掃除する。"""

    work = Path(work_dir).resolve(strict=True)
    base = save_recovery_paths.raster_snapshot_base(work)
    if not base.is_dir():
        return ()
    now = datetime.now(timezone.utc)
    removed: list[Path] = []
    for directory in tuple(base.iterdir()):
        try:
            save_recovery_paths.assert_recovery_owned_path(
                work,
                directory,
                label="ラスター保存復旧対象",
            )
            if (
                not directory.is_dir()
                or not _TRANSACTION_ID_RE.fullmatch(directory.name)
            ):
                continue
            journal = directory / RASTER_SNAPSHOT_JOURNAL
            if journal.is_file():
                payload = json.loads(journal.read_text(encoding="utf-8"))
                if str(payload.get("status", "") or "") in {
                    "recoverable",
                    "hydrated",
                }:
                    # 未保存画素の唯一のdurable copy。時間では捨てず、
                    # 同じmainfileの次checkpoint成功時だけ削除する。
                    continue
            age = now - datetime.fromtimestamp(
                directory.stat().st_mtime,
                timezone.utc,
            )
            if age < _STALE_SNAPSHOT_MAX_AGE:
                continue
            cleanup_snapshot_transaction(work, directory)
            if not directory.exists():
                removed.append(directory)
        except Exception:
            continue
    return tuple(removed)


def mark_snapshot_native_result(
    work_dir: str | Path,
    transaction_id: str,
    *,
    committed: bool,
) -> None:
    """Native復旧判断を同じIDの画素snapshotへdurableに反映する。"""

    work = Path(work_dir).resolve(strict=True)
    tx_id = str(transaction_id or "")
    if not _TRANSACTION_ID_RE.fullmatch(tx_id):
        raise RuntimeError("invalid raster snapshot transaction ID")
    directory = save_recovery_paths.raster_snapshot_base(work) / tx_id
    if not directory.exists():
        return
    save_recovery_paths.assert_recovery_owned_path(
        work,
        directory,
        label="ラスター保存復旧対象",
    )
    journal = directory / RASTER_SNAPSHOT_JOURNAL
    if not journal.is_file():
        cleanup_snapshot_transaction(work, directory)
        return
    payload = json.loads(journal.read_text(encoding="utf-8"))
    if (
        int(payload.get("journalVersion", 0) or 0)
        != RASTER_SNAPSHOT_VERSION
        or str(payload.get("transactionId", "") or "") != tx_id
    ):
        raise RuntimeError("raster snapshot journal identity is invalid")
    status = str(payload.get("status", "") or "")
    if committed or status not in {"ready", "recoverable"}:
        cleanup_snapshot_transaction(work, directory)
        return
    payload["status"] = "recoverable"
    payload["nativeRecoveredAt"] = datetime.now(timezone.utc).isoformat()
    _write_json_durable(journal, payload)


def recoverable_snapshot_transactions(
    work_dir: str | Path,
    source_path: str | Path,
) -> tuple[RecoverableRasterSnapshotTransaction, ...]:
    """現在mainfileに属する復旧可能snapshotを厳格検証して返す。"""

    work = Path(work_dir).resolve(strict=True)
    source = Path(source_path).resolve(strict=False)
    try:
        source.relative_to(work)
    except ValueError as exc:
        raise RuntimeError("raster snapshot source is outside work") from exc
    base = save_recovery_paths.raster_snapshot_base(work)
    if not base.is_dir():
        return ()
    recovered: list[RecoverableRasterSnapshotTransaction] = []
    for directory in sorted(base.iterdir()):
        save_recovery_paths.assert_recovery_owned_path(
            work,
            directory,
            label="ラスター保存復旧対象",
        )
        if (
            not directory.is_dir()
            or not _TRANSACTION_ID_RE.fullmatch(directory.name)
        ):
            continue
        journal = directory / RASTER_SNAPSHOT_JOURNAL
        if not journal.is_file():
            continue
        payload = json.loads(journal.read_text(encoding="utf-8"))
        if str(payload.get("status", "") or "") not in {
            "recoverable",
            "hydrated",
        }:
            continue
        if (
            int(payload.get("journalVersion", 0) or 0)
            != RASTER_SNAPSHOT_VERSION
            or str(payload.get("transactionId", "") or "")
            != directory.name
        ):
            raise RuntimeError("raster snapshot journal identity is invalid")
        recorded_source = Path(
            str(payload.get("sourcePath", "") or "")
        ).resolve(strict=False)
        try:
            recorded_source.relative_to(work)
        except ValueError as exc:
            raise RuntimeError(
                "raster snapshot source escaped work"
            ) from exc
        if os.path.normcase(str(recorded_source)) != os.path.normcase(
            str(source)
        ):
            continue
        snapshots: dict[str, RasterPixelSnapshot] = {}
        rows = payload.get("snapshots", [])
        if not isinstance(rows, list):
            raise RuntimeError("raster snapshot manifest is invalid")
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError("raster snapshot entry is invalid")
            raster_id = str(row.get("rasterId", "") or "")
            filename = str(row.get("file", "") or "")
            width = int(row.get("width", 0) or 0)
            height = int(row.get("height", 0) or 0)
            channels = int(row.get("channels", 0) or 0)
            raw_bytes = int(row.get("rawBytes", 0) or 0)
            digest = str(row.get("sha256", "") or "")
            if (
                not raster_id
                or raster_id in snapshots
                or not re.fullmatch(r"[0-9a-f]{64}\.pixels\.zlib", filename)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or width <= 0
                or height <= 0
                or width > MAX_RASTER_SNAPSHOT_DIMENSION
                or height > MAX_RASTER_SNAPSHOT_DIMENSION
                or channels <= 0
                or channels > MAX_RASTER_SNAPSHOT_CHANNELS
                or raw_bytes > MAX_RASTER_SNAPSHOT_RAW_BYTES
                or raw_bytes != width * height * channels * 4
            ):
                raise RuntimeError("raster snapshot entry contract is invalid")
            compressed = directory / filename
            save_recovery_paths.assert_recovery_owned_path(
                work,
                compressed,
                label="ラスター画素snapshot",
            )
            if compressed.parent != directory or not compressed.is_file():
                raise RuntimeError("raster snapshot payload is missing")
            snapshots[raster_id] = RasterPixelSnapshot(
                width,
                height,
                channels,
                compressed,
                digest,
                raw_bytes,
            )
        recovered.append(
            RecoverableRasterSnapshotTransaction(
                directory.name,
                work,
                recorded_source,
                directory,
                snapshots,
            )
        )
    return tuple(recovered)


def mark_recoverable_snapshot_hydrated(
    transaction: RecoverableRasterSnapshotTransaction,
) -> None:
    """画素をRAMへ戻した事実を残すが、次checkpointまではpayloadを保持する。"""

    journal = transaction.snapshot_dir / RASTER_SNAPSHOT_JOURNAL
    payload = json.loads(journal.read_text(encoding="utf-8"))
    if str(payload.get("status", "") or "") not in {
        "recoverable",
        "hydrated",
    }:
        raise RuntimeError("raster snapshot is not recoverable")
    payload["status"] = "hydrated"
    payload["hydratedAt"] = datetime.now(timezone.utc).isoformat()
    _write_json_durable(journal, payload)


def cleanup_recovered_snapshot_transactions(
    work_dir: str | Path,
    source_path: str | Path,
) -> tuple[Path, ...]:
    """同じmainfileの次checkpoint成功後だけ旧復旧snapshotを破棄する。"""

    work = Path(work_dir).resolve(strict=True)
    source_key = os.path.normcase(
        str(Path(source_path).resolve(strict=False))
    )
    base = save_recovery_paths.raster_snapshot_base(work)
    if not base.is_dir():
        return ()
    removed: list[Path] = []
    for directory in tuple(base.iterdir()):
        save_recovery_paths.assert_recovery_owned_path(
            work,
            directory,
            label="ラスター保存復旧対象",
        )
        if (
            not directory.is_dir()
            or not _TRANSACTION_ID_RE.fullmatch(directory.name)
        ):
            continue
        journal = directory / RASTER_SNAPSHOT_JOURNAL
        if not journal.is_file():
            continue
        payload = json.loads(journal.read_text(encoding="utf-8"))
        if str(payload.get("status", "") or "") not in {
            "recoverable",
            "hydrated",
        }:
            continue
        recorded_source = os.path.normcase(
            str(
                Path(
                    str(payload.get("sourcePath", "") or "")
                ).resolve(strict=False)
            )
        )
        if recorded_source != source_key:
            continue
        cleanup_snapshot_transaction(work, directory)
        if not directory.exists():
            removed.append(directory)
    return tuple(removed)


def _write_json_durable(path: Path, payload: dict) -> None:
    temp = path.with_suffix(f"{path.suffix}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        try:
            fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            fd = -1
        if fd >= 0:
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temp.unlink(missing_ok=True)


__all__ = (
    "MAX_RASTER_SNAPSHOT_CHANNELS",
    "MAX_RASTER_SNAPSHOT_DIMENSION",
    "MAX_RASTER_SNAPSHOT_RAW_BYTES",
    "PendingNativeCheckpoint",
    "RecoverableRasterSnapshotTransaction",
    "RasterPixelSnapshot",
    "begin",
    "clear",
    "cleanup_snapshot_transaction",
    "cleanup_stale_snapshot_transactions",
    "cleanup_recovered_snapshot_transactions",
    "create_snapshot_transaction",
    "is_pending",
    "mark_snapshot_native_result",
    "mark_recoverable_snapshot_hydrated",
    "note_domain_write",
    "pending_for",
    "preserve_raster_snapshots",
    "recoverable_snapshot_transactions",
    "seal_snapshot_transaction",
    "snapshot_path",
    "take",
)
