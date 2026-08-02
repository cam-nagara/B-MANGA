"""新規作品作成時の保存先所有権と失敗回収を管理する。"""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping

from ..bmanga_core.file_identity import (
    FileIdentity,
    capture_file_identity,
)
from . import log, paths


MARKER_NAME = ".bmanga-new-work-transaction"
_MARKER_SCHEMA = "bmanga.new-work-transaction"
_MARKER_VERSION = 3

_logger = log.get_logger(__name__)


def _is_link(path: Path) -> bool:
    return path.is_symlink() or getattr(
        path,
        "is_junction",
        lambda: False,
    )()


def _artifact_record(path: Path) -> dict[str, object]:
    if _is_link(path):
        raise RuntimeError("新規作品の生成物にリンクを使用できません")
    stat = path.stat()
    common = {
        "device": int(stat.st_dev),
        "inode": int(stat.st_ino),
    }
    if path.is_dir():
        return {"kind": "directory", **common}
    if path.is_file():
        return capture_file_identity(path).to_record()
    raise RuntimeError("新規作品の生成物が通常ファイルではありません")


def _marker_payload(
    token: str,
    artifacts: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    return {
        "schema": _MARKER_SCHEMA,
        "schemaVersion": _MARKER_VERSION,
        "token": token,
        "artifacts": {
            name: dict(artifacts[name])
            for name in sorted(artifacts)
        },
    }


def _write_marker(
    marker: Path,
    token: str,
    artifacts: Mapping[str, Mapping[str, object]],
    *,
    exclusive: bool = False,
) -> None:
    mode = "x" if exclusive else "w"
    with marker.open(mode, encoding="utf-8", newline="\n") as handle:
        json.dump(
            _marker_payload(token, artifacts),
            handle,
            ensure_ascii=True,
            sort_keys=True,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _valid_relative_name(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError("未完成作品の生成物記録が壊れています")
    relative = PurePosixPath(value)
    if (
        "\\" in value
        or relative.is_absolute()
        or value != relative.as_posix()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise RuntimeError("未完成作品の生成物記録が保存先外です")
    return value


def _valid_record(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError("未完成作品の生成物記録が壊れています")
    kind = value.get("kind")
    if kind == "marker":
        return {"kind": "marker"}
    if kind not in {"file", "directory"}:
        raise RuntimeError("未完成作品の生成物種別が不正です")
    if type(value.get("device")) is not int or type(value.get("inode")) is not int:
        raise RuntimeError("未完成作品の物理IDが不正です")
    result = {
        "kind": kind,
        "device": int(value["device"]),
        "inode": int(value["inode"]),
    }
    if kind == "file":
        size = value.get("size")
        mtime_ns = value.get("mtimeNs")
        sha256 = value.get("sha256")
        if (
            type(size) is not int
            or int(size) < 0
            or type(mtime_ns) is not int
            or int(mtime_ns) < 0
            or not isinstance(sha256, str)
            or len(sha256) != 64
        ):
            raise RuntimeError("未完成作品のファイル指紋が不正です")
        result.update(
            {
                "size": int(size),
                "mtimeNs": int(mtime_ns),
                "sha256": sha256,
            }
        )
    return result


def _read_marker(
    marker: Path,
    token: str,
) -> dict[str, dict[str, object]]:
    data = json.loads(marker.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or data.get("schema") != _MARKER_SCHEMA
        or data.get("schemaVersion") != _MARKER_VERSION
        or data.get("token") != token
    ):
        raise RuntimeError("未完成作品の所有権を確認できません")
    values = data.get("artifacts")
    if not isinstance(values, dict):
        raise RuntimeError("未完成作品の生成物記録が壊れています")
    records = {
        _valid_relative_name(name): _valid_record(value)
        for name, value in values.items()
    }
    if records.get(MARKER_NAME) != {"kind": "marker"}:
        raise RuntimeError("未完成作品の所有権記録がありません")
    return records


def _assert_owned_path(work_dir: Path, path: Path) -> Path:
    lexical_root = Path(work_dir).absolute()
    candidate = Path(path).absolute()
    try:
        relative = candidate.relative_to(lexical_root)
    except ValueError as exc:
        raise RuntimeError("新規作品の生成物が保存先外です") from exc
    current = lexical_root
    for part in relative.parts:
        current /= part
        if current.exists() and _is_link(current):
            raise RuntimeError(
                "新規作品の生成物にリンクを使用できません"
            )
    try:
        candidate.resolve(strict=False).relative_to(
            lexical_root.resolve(strict=True)
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError("新規作品の生成物が保存先外です") from exc
    return Path(path)


def _relative_name(work_dir: Path, artifact: Path) -> str:
    _assert_owned_path(work_dir, artifact)
    try:
        relative = Path(artifact).absolute().relative_to(
            work_dir.absolute()
        )
    except ValueError as exc:
        raise RuntimeError("新規作品の生成物が保存先外です") from exc
    if not relative.parts:
        raise RuntimeError("作品フォルダー全体は削除対象にできません")
    return _valid_relative_name(relative.as_posix())


def _record_captured_artifacts(
    marker: Path,
    token: str,
    artifacts: tuple[tuple[Path, Mapping[str, object]], ...],
) -> None:
    """生成時の指紋と現在の実体が一致する場合だけ所有権を確定する。"""

    work_dir = marker.parent.resolve(strict=True)
    records = _read_marker(marker, token)
    captured: list[tuple[str, dict[str, object]]] = []
    for artifact, captured_record in artifacts:
        path = Path(artifact)
        relative = _relative_name(work_dir, path)
        record = _valid_record(dict(captured_record))
        if record.get("kind") == "marker":
            raise RuntimeError("markerを生成物として登録できません")
        if _artifact_record(path) != record:
            raise RuntimeError(
                "新規作品の生成物が所有権記録前に変更されました"
            )
        captured.append((relative, record))
    for relative, record in captured:
        records[relative] = record
    _write_marker(marker, token, records)


def record_committed_artifact(
    marker: Path,
    token: str,
    artifact: Path,
    identity: FileIdentity,
) -> None:
    """保存処理内で確定した実体だけを、再照合して所有権へ記録する。"""

    path = Path(artifact)
    _record_captured_artifacts(
        marker,
        token,
        ((path, identity.to_record()),),
    )


def committed_artifact_recorder(
    marker: Path,
    token: str,
) -> Callable[[Path, FileIdentity], None]:
    """Domain/native保存の確定点から呼ぶ所有権記録callbackを返す。"""

    def _record(path: Path, identity: FileIdentity) -> None:
        record_committed_artifact(
            marker,
            token,
            Path(path),
            identity,
        )

    return _record


def skeleton_directories(work_dir: Path) -> tuple[Path, ...]:
    assets = paths.assets_dir(work_dir)
    return tuple(
        work_dir / name
        for name in ("pages", "presets", "cache", "journal")
    ) + (
        assets,
        *(assets / name for name in (
            paths.ASSETS_BRUSHES_DIR,
            paths.ASSETS_TEMPLATES_DIR,
            paths.ASSETS_MODELS_DIR,
            paths.ASSETS_BALLOONS_DIR,
            paths.ASSETS_EFFECTS_DIR,
        )),
        paths.scenario_dir(work_dir),
        paths.exports_dir(work_dir),
        paths.raster_dir(work_dir),
        paths.raster_trash_dir(work_dir),
    )


def create_directories(
    marker: Path,
    token: str,
    directories: tuple[Path, ...],
) -> None:
    """各directoryを排他的に作り、成功した実体だけを記録する。"""

    for directory in sorted(
        directories,
        key=lambda path: len(Path(path).parts),
    ):
        path = Path(directory)
        _assert_owned_path(marker.parent, path.parent)
        path.mkdir(exist_ok=False)
        captured = _artifact_record(path)
        _record_captured_artifacts(
            marker,
            token,
            ((path, captured),),
        )


def claim_directory(work_dir: Path, token: str) -> Path:
    """作品directoryを排他的に確保し、失敗回収用markerを作る。"""

    work_dir.parent.mkdir(parents=True, exist_ok=True)
    parent = work_dir.parent.resolve(strict=True)
    work_dir.mkdir(exist_ok=False)
    try:
        if (
            _is_link(work_dir)
            or work_dir.resolve(strict=True).parent != parent
        ):
            raise RuntimeError("新規作品の保存先が選択フォルダー外です")
        marker = work_dir / MARKER_NAME
        _write_marker(
            marker,
            token,
            {MARKER_NAME: {"kind": "marker"}},
            exclusive=True,
        )
        return marker
    except BaseException:
        try:
            if (
                work_dir.is_dir()
                and not _is_link(work_dir)
                and work_dir.resolve(strict=True).parent == parent
            ):
                work_dir.rmdir()
        except OSError:
            pass
        raise


def _scan_entries(
    work_dir: Path,
) -> tuple[tuple[Path, str, bool, bool], ...]:
    result: list[tuple[Path, str, bool, bool]] = []
    pending = [work_dir]
    while pending:
        parent = pending.pop()
        with os.scandir(parent) as entries:
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(work_dir).as_posix()
                unsafe = entry.is_symlink() or _is_link(path)
                is_dir = entry.is_dir(follow_symlinks=False) and not unsafe
                result.append((path, relative, is_dir, unsafe))
                if is_dir:
                    pending.append(path)
    return tuple(result)


def _matches_record(
    marker: Path,
    token: str,
    path: Path,
    relative: str,
    record: Mapping[str, object],
) -> bool:
    if record.get("kind") == "marker":
        if path != marker or relative != MARKER_NAME:
            return False
        try:
            _read_marker(marker, token)
        except (OSError, ValueError, RuntimeError):
            return False
        return True
    try:
        return _artifact_record(path) == dict(record)
    except (OSError, RuntimeError):
        return False


def _remove_owned_entries(
    marker: Path,
    token: str,
    entries: tuple[tuple[Path, str, bool, bool], ...],
    records: Mapping[str, Mapping[str, object]],
    *,
    tolerate_nonempty: bool,
) -> None:
    ordered = sorted(
        entries,
        key=lambda item: (
            len(item[0].parts),
            item[1] != MARKER_NAME,
        ),
        reverse=True,
    )
    for path, relative, is_dir, unsafe in ordered:
        record = records.get(relative)
        if (
            unsafe
            or record is None
            or not _matches_record(
                marker,
                token,
                path,
                relative,
                record,
            )
        ):
            continue
        try:
            path.rmdir() if is_dir else path.unlink(missing_ok=True)
        except OSError:
            if not tolerate_nonempty:
                raise


def cleanup_failed_work(
    work_dir: Path,
    marker: Path,
    token: str,
) -> tuple[str, ...]:
    """同じ物理実体・hashの生成物だけを消し、他者所有物を残す。"""

    if (
        not work_dir.is_dir()
        or _is_link(work_dir)
        or marker.parent.resolve(strict=True) != work_dir.resolve(strict=True)
    ):
        raise RuntimeError("未完成作品の所有権を確認できません")
    records = _read_marker(marker, token)
    entries = _scan_entries(work_dir)
    unknown = tuple(
        sorted(
            relative
            for path, relative, _is_dir, unsafe in entries
            if (
                unsafe
                or relative not in records
                or not _matches_record(
                    marker,
                    token,
                    path,
                    relative,
                    records[relative],
                )
            )
        )
    )
    _remove_owned_entries(
        marker,
        token,
        entries,
        records,
        tolerate_nonempty=bool(unknown),
    )
    if unknown:
        _logger.warning(
            "work_new preserved directory with unknown entries: %s (%s)",
            work_dir,
            ", ".join(unknown),
        )
        return unknown
    work_dir.rmdir()
    return ()


def release_marker(marker: Path, token: str) -> None:
    """成功した作成処理のmarkerだけを削除する。"""

    _read_marker(marker, token)
    marker.unlink()


__all__ = (
    "MARKER_NAME",
    "claim_directory",
    "cleanup_failed_work",
    "committed_artifact_recorder",
    "create_directories",
    "record_committed_artifact",
    "release_marker",
    "skeleton_directories",
)
