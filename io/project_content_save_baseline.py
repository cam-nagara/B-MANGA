"""同一詳細データ版の別Blender画面による上書きを検出する基準hash。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Iterable

try:
    from .project_content_migration_lock import find_work_root
except ImportError:  # ファイル単体でロードする純Pythonテスト用
    from project_content_migration_lock import find_work_root  # type: ignore


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    exists: bool
    digest: str
    size: int = 0
    mtime_ns: int = 0


_registry_lock = threading.RLock()
_baselines: dict[str, dict[str, FileFingerprint]] = {}
_PROTECTED_SUFFIXES = {".blend", ".json", ".png"}
_DERIVED_CACHE_NAMES = {"page_preview.png"}


class SaveBaselineUnavailableError(RuntimeError):
    pass


class SaveBaselineConflictError(RuntimeError):
    def __init__(self, paths: Iterable[Path]):
        self.paths = tuple(paths)
        names = ", ".join(path.name for path in self.paths)
        super().__init__(f"別のBlender画面で作品データが更新されています: {names}")


def _work_key(work: Path) -> str:
    return os.path.normcase(str(work.resolve(strict=False)))


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _is_derived_cache(path: Path) -> bool:
    """別画面で再生成されてもユーザーデータ競合にならない派生物か。"""

    return path.name.casefold() in _DERIVED_CACHE_NAMES


def _canonical_json_bytes(path: Path, raw: bytes) -> bytes:
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw
    if isinstance(data, dict):
        data = dict(data)
        # 通常保存ごとに変わる時刻だけを比較対象から外す。内容本体の変更は
        # 引き続き検出するため、ファイルごとの既知キーに限定する。
        if path.name == "work.json":
            data.pop("lastSaved", None)
        elif path.name == "pages.json":
            data.pop("lastModified", None)
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def fingerprint(path: str | os.PathLike[str]) -> FileFingerprint:
    target = Path(path)
    if not target.exists():
        return FileFingerprint(False, "")
    if target.is_symlink() or not target.is_file():
        return FileFingerprint(True, "invalid-path")
    stat = target.stat()
    if target.suffix.casefold() == ".json":
        payload = _canonical_json_bytes(target, target.read_bytes())
        digest = hashlib.sha256(payload).hexdigest()
    else:
        hasher = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
    return FileFingerprint(True, digest, stat.st_size, stat.st_mtime_ns)


def _matches_fingerprint(path: Path, expected: FileFingerprint) -> bool:
    if not path.exists():
        return not expected.exists
    if not expected.exists or path.is_symlink() or not path.is_file():
        return False
    stat = path.stat()
    if stat.st_size == expected.size and stat.st_mtime_ns == expected.mtime_ns:
        return True
    actual = fingerprint(path)
    return actual.exists == expected.exists and actual.digest == expected.digest


def _default_paths(work: Path, blend: Path) -> set[Path]:
    paths = {work / "work.json", work / "pages.json", blend}
    try:
        rel = blend.resolve(strict=False).relative_to(work.resolve(strict=True))
    except ValueError:
        return paths
    parts = rel.parts
    if parts and len(parts[0]) == 5 and parts[0].startswith("p"):
        paths.add(work / parts[0] / "page.json")
    return paths


def capture_loaded_baseline(
    work_dir: str | os.PathLike[str],
    blend_path: str | os.PathLike[str],
    *,
    page_json_paths: Iterable[str | os.PathLike[str]] = (),
    content_paths: Iterable[str | os.PathLike[str]] = (),
) -> None:
    """load_post完了時点の作品・現在ページ・blendを新しい基準にする。"""

    work = Path(work_dir).resolve(strict=True)
    blend = Path(blend_path).resolve(strict=False)
    paths = _default_paths(work, blend)
    paths.update(Path(path).resolve(strict=False) for path in page_json_paths)
    paths.update(Path(path).resolve(strict=False) for path in content_paths)
    snapshot = {_path_key(path): fingerprint(path) for path in paths}
    with _registry_lock:
        _baselines[_work_key(work)] = snapshot


def initialize_new_work_baseline(work_dir: str | os.PathLike[str]) -> None:
    """work.json作成前の新規作品だけ、存在しない初期値を登録する。"""

    work = Path(work_dir).resolve(strict=True)
    if (work / "work.json").exists():
        raise SaveBaselineUnavailableError("既存作品を新規作品として基準化できません")
    paths = {work / "work.json", work / "pages.json"}
    with _registry_lock:
        _baselines[_work_key(work)] = {
            _path_key(path): fingerprint(path) for path in paths
        }


def conflicting_paths(
    work_dir: str | os.PathLike[str],
    blend_path: str | os.PathLike[str] | None = None,
    *,
    ignore_paths: Iterable[str | os.PathLike[str]] = (),
) -> tuple[Path, ...]:
    """基準取得後に別書込みで変化した追跡対象を返す。"""

    work = Path(work_dir).resolve(strict=True)
    work_key = _work_key(work)
    with _registry_lock:
        snapshot = _baselines.get(work_key)
        if snapshot is None:
            raise SaveBaselineUnavailableError(
                "作品の読込基準がありません。作品を開き直してください"
            )
        items = tuple(snapshot.items())
    ignored = {_path_key(Path(path)) for path in ignore_paths}
    conflicts = []
    for key, expected in items:
        if key in ignored:
            continue
        path = Path(key)
        if _is_derived_cache(path):
            continue
        if not _matches_fingerprint(path, expected):
            conflicts.append(path)
    return tuple(conflicts)


def assert_no_external_changes(
    work_dir: str | os.PathLike[str],
    *,
    ignore_paths: Iterable[str | os.PathLike[str]] = (),
) -> None:
    conflicts = conflicting_paths(work_dir, ignore_paths=ignore_paths)
    if conflicts:
        raise SaveBaselineConflictError(conflicts)


def assert_existing_target_tracked(
    work_dir: str | os.PathLike[str],
    target_path: str | os.PathLike[str],
) -> None:
    """既存PNG等を初回成功書込みで無条件採用する経路を閉じる。"""

    work = Path(work_dir).resolve(strict=True)
    target = Path(target_path).resolve(strict=False)
    if _is_derived_cache(target):
        return
    if not target.is_file():
        return
    with _registry_lock:
        snapshot = _baselines.get(_work_key(work))
        if snapshot is None:
            raise SaveBaselineUnavailableError("作品の読込基準がありません")
        tracked = _path_key(target) in snapshot
    if not tracked and target.suffix.casefold() in {".json", ".png", ".blend"}:
        raise SaveBaselineConflictError((target,))


def record_observed_read(path: str | os.PathLike[str]) -> None:
    """未追跡sidecarは、破壊書込み時でなく読込み成功時にだけ基準へ加える。"""

    target = Path(path).resolve(strict=False)
    work = find_work_root(target)
    if work is None:
        return
    with _registry_lock:
        snapshot = _baselines.get(_work_key(work))
        if snapshot is None:
            return
        snapshot.setdefault(_path_key(target), fingerprint(target))


def record_successful_write(path: str | os.PathLike[str]) -> None:
    """このプロセス自身が成功させた書込みだけ基準へ反映する。"""

    target = Path(path).resolve(strict=False)
    work = find_work_root(target)
    if work is None:
        return
    work_key = _work_key(work)
    with _registry_lock:
        snapshot = _baselines.get(work_key)
        if snapshot is None:
            raise SaveBaselineUnavailableError(
                "作品の読込基準がないため保存結果を基準化できません"
            )
        snapshot[_path_key(target)] = fingerprint(target)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _protected_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        if root.suffix.casefold() in _PROTECTED_SUFFIXES:
            yield root
        return
    if not root.is_dir() or root.is_symlink():
        return
    for candidate in root.rglob("*"):
        if (
            candidate.is_file()
            and not candidate.is_symlink()
            and candidate.suffix.casefold() in _PROTECTED_SUFFIXES
        ):
            yield candidate


def record_successful_tree_change(
    *changed_roots: str | os.PathLike[str],
) -> None:
    """このプロセスが成功させた移動・複製・削除をまとめて基準へ反映する。

    ディレクトリ移動では、移動元にあった追跡済みファイルの「消滅」と、
    移動先に現れた保護対象ファイルの両方を同じ成功点で記録する。
    """

    roots = tuple(Path(path).resolve(strict=False) for path in changed_roots)
    if not roots:
        return
    works = {find_work_root(root) for root in roots}
    works.discard(None)
    if not works:
        return
    if len(works) != 1:
        raise SaveBaselineUnavailableError("複数作品をまたぐ変更は基準化できません")
    work = next(iter(works))
    assert work is not None
    work_key = _work_key(work)
    with _registry_lock:
        snapshot = _baselines.get(work_key)
        if snapshot is None:
            raise SaveBaselineUnavailableError(
                "作品の読込基準がないため変更結果を基準化できません"
            )
        tracked_paths = tuple(Path(key) for key in snapshot)
        for tracked in tracked_paths:
            if any(_is_within(tracked, root) for root in roots):
                snapshot[_path_key(tracked)] = fingerprint(tracked)
        for root in roots:
            for candidate in _protected_files(root):
                snapshot[_path_key(candidate)] = fingerprint(candidate)


def forget_baseline(work_dir: str | os.PathLike[str]) -> None:
    with _registry_lock:
        _baselines.pop(_work_key(Path(work_dir)), None)


def tracked_paths(work_dir: str | os.PathLike[str]) -> tuple[Path, ...]:
    with _registry_lock:
        snapshot = dict(_baselines.get(_work_key(Path(work_dir)), {}))
    return tuple(Path(path) for path in snapshot)


__all__ = [
    "FileFingerprint",
    "SaveBaselineConflictError",
    "SaveBaselineUnavailableError",
    "assert_no_external_changes",
    "assert_existing_target_tracked",
    "capture_loaded_baseline",
    "conflicting_paths",
    "fingerprint",
    "forget_baseline",
    "initialize_new_work_baseline",
    "record_successful_write",
    "record_successful_tree_change",
    "record_observed_read",
    "tracked_paths",
]
