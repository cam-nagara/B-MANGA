"""UIDコマdirectory (scene.blend / preview.png) のI/O。

表示用cNNはパスから分離する。採番・他ページへの移動・複製を担当し、
scene.blendの実ロード/セーブはoperators層で
bpy.ops.wm.* を呼ぶ。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..bmanga_core.file_identity import ArtifactCommitHook
from ..bmanga_core.domain_ids import UIDKind, derived_uid
from ..utils import log, paths
from . import domain_projection, domain_runtime, page_io, schema
from .project_file_lock import guard_path_write
from .save_baseline import record_successful_tree_change

_logger = log.get_logger(__name__)


# ---------- 採番 ----------


def existing_coma_ids(work_dir: Path, page_id: str) -> list[str]:
    """page.jsonのDomain treeから既存の表示用cNN IDを列挙する。"""

    page_uid = paths.resolve_page_uid(work_dir, page_id)
    repository = domain_runtime.repository_for(work_dir)
    if not repository.page_path(page_uid).is_file():
        return []
    document = repository.load_page(page_uid)
    return sorted(
        node.display_id
        for node in document.nodes.values()
        if node.kind == "coma" and paths.is_valid_coma_id(node.display_id)
    )


def page_data_coma_ids(page) -> set[str]:
    """ページデータ上のコマIDの集合 (ディスクにフォルダが無いコマも含む)."""
    ids: set[str] = set()
    for coma in getattr(page, "comas", []) or []:
        for attr in ("coma_id", "id"):
            value = str(getattr(coma, attr, "") or "")
            if value:
                ids.add(value)
    return ids


def allocate_new_coma_id(work_dir: Path, page_id: str, *, page=None) -> str:
    """新規コマIDを採番する.

    ディスク上の cNN フォルダに加え、``page`` を渡された場合はページデータ上の
    コマIDも使用済みとして扱う。フォルダだけを見ると、まだ一度も保存されて
    いないコマ (データのみのコマ) と同じIDを払い出してしまい、ID重複で
    マスク・親子付け・ファイルが同名衝突する (枠線カットで実際に発生)。
    """
    existing = set(existing_coma_ids(work_dir, page_id))
    if page is not None:
        existing.update(page_data_coma_ids(page))
    idx = paths.next_available_coma_index(sorted(existing))
    return paths.format_coma_id(idx)


# ---------- page.json内のコマDomain ----------


def save_coma_meta(
    work_dir: Path,
    page_id: str,
    entry,
    *,
    on_committed: ArtifactCommitHook | None = None,
) -> Path:
    """コマ単独sidecarを作らず、所有pageのDomain checkpointへ集約する。"""

    page = _owning_page(entry, page_id)
    if on_committed is None:
        return page_io.save_page_json(Path(work_dir), page)
    return page_io.save_page_json(
        Path(work_dir),
        page,
        on_committed=on_committed,
    )


def load_coma_meta(work_dir: Path, page_id: str, coma_id: str, entry) -> dict:
    page_uid = paths.resolve_page_uid(work_dir, page_id)
    repository = domain_runtime.repository_for(work_dir)
    if not repository.page_path(page_uid).is_file():
        return {}
    document = repository.load_page(page_uid)
    node = next(
        (
            candidate
            for candidate in document.nodes.values()
            if candidate.kind == "coma" and candidate.display_id == coma_id
        ),
        None,
    )
    if node is None:
        return {}
    data = dict(node.settings)
    data.update({"id": node.display_id, "comaId": node.display_id, "title": node.title})
    schema.coma_entry_from_dict(entry, data)
    coma_uid = node.native_uid or derived_uid(UIDKind.COMA, page_uid, coma_id)
    entry[domain_projection.COMA_UID_PROP] = coma_uid
    return data


# ---------- ファイル移動/複製 ----------


def _coma_artifact_files(work_dir: Path, page_id: str, coma_id: str) -> list[Path]:
    """UIDコマに関連するNative dataとpreviewを列挙する。"""

    pd = paths.coma_dir(Path(work_dir), page_id, coma_id)
    candidates = [pd / paths.COMA_BLEND_NAME, pd / paths.COMA_PREVIEW_NAME]
    return [p for p in candidates if p.exists()]


def _rename_coma_artifacts(coma_path: Path, old_id: str, new_id: str) -> list[Path]:
    del old_id, new_id
    return [
        path
        for path in (
            coma_path / paths.COMA_BLEND_NAME,
            coma_path / paths.COMA_PREVIEW_NAME,
        )
        if path.exists()
    ]


def move_coma_files(
    work_dir: Path,
    src_page_id: str,
    dst_page_id: str,
    src_coma_id: str,
    dst_coma_id: str,
) -> list[Path]:
    """コマディレクトリ一式を別ページへ移動."""
    paths.validate_page_id(src_page_id)
    paths.validate_page_id(dst_page_id)
    paths.validate_coma_id(src_coma_id)
    paths.validate_coma_id(dst_coma_id)
    src_dir = paths.coma_dir(Path(work_dir), src_page_id, src_coma_id)
    dst_dir = paths.coma_dir(Path(work_dir), dst_page_id, dst_coma_id)
    with guard_path_write(dst_dir):
        if not src_dir.exists():
            return []
        if dst_dir.exists():
            raise FileExistsError(f"destination already exists: {dst_dir}")
        dst_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_dir), str(dst_dir))
        moved = _rename_coma_artifacts(dst_dir, src_coma_id, dst_coma_id)
        moved.append(dst_dir)
        record_successful_tree_change(src_dir, dst_dir)
    _logger.info(
        "coma moved: %s/%s -> %s/%s (%d paths)",
        src_page_id, src_coma_id, dst_page_id, dst_coma_id, len(moved),
    )
    return moved


def copy_coma_files(
    work_dir: Path,
    src_page_id: str,
    dst_page_id: str,
    src_coma_id: str,
    dst_coma_id: str,
) -> list[Path]:
    """コマディレクトリ一式を別ページへコピー."""
    paths.validate_page_id(src_page_id)
    paths.validate_page_id(dst_page_id)
    paths.validate_coma_id(src_coma_id)
    paths.validate_coma_id(dst_coma_id)
    src_dir = paths.coma_dir(Path(work_dir), src_page_id, src_coma_id)
    dst_dir = paths.coma_dir(Path(work_dir), dst_page_id, dst_coma_id)
    with guard_path_write(dst_dir):
        if not src_dir.exists():
            return []
        if dst_dir.exists():
            raise FileExistsError(f"destination already exists: {dst_dir}")
        dst_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_dir, dst_dir)
        copied = _rename_coma_artifacts(dst_dir, src_coma_id, dst_coma_id)
        copied.append(dst_dir)
        record_successful_tree_change(dst_dir)
    _logger.info(
        "coma copied: %s/%s -> %s/%s (%d paths)",
        src_page_id, src_coma_id, dst_page_id, dst_coma_id, len(copied),
    )
    return copied


def remove_coma_files(work_dir: Path, page_id: str, coma_id: str) -> int:
    paths.validate_page_id(page_id)
    paths.validate_coma_id(coma_id)
    coma_path = paths.coma_dir(Path(work_dir), page_id, coma_id)
    with guard_path_write(coma_path):
        if not coma_path.exists():
            return 0
        shutil.rmtree(coma_path)
        record_successful_tree_change(coma_path)
    _logger.info("coma removed: %s/%s", page_id, coma_id)
    return 1


def _owning_page(entry, expected_page_id: str):
    scene = getattr(entry, "id_data", None)
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if work is None:
        raise RuntimeError("coma projection has no owning work")
    for page in getattr(work, "pages", ()):
        if str(getattr(page, "id", "") or "") != expected_page_id:
            continue
        if any(candidate == entry for candidate in getattr(page, "comas", ())):
            return page
    raise RuntimeError(f"coma is not owned by page: {expected_page_id}")
