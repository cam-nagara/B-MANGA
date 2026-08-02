"""UID page directoryとpage.jsonのDomain adapter。"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..bmanga_core.file_identity import ArtifactCommitHook
from ..bmanga_core.domain_store import (
    ApplyPagePatch,
    ApplyProjectPatch,
    page_patch,
    project_patch,
)
from ..utils import log, paths
from . import domain_projection, domain_runtime, native_checkpoint_runtime
from .save_baseline import record_successful_tree_change, record_successful_write

_logger = log.get_logger(__name__)


# ---------- page.json (個別ページメタ) ----------


def invalidate_page_json_write_cache(
    paths_to_invalidate: list[Path] | tuple[Path, ...],
) -> None:
    """Repositoryはhash基準を持つため旧差分cacheの破棄は不要。"""


def save_page_json(
    work_dir: Path,
    page_entry,
    *,
    on_committed: ArtifactCommitHook | None = None,
) -> Path:
    work = _work_from_page(page_entry)
    if work is None:
        raise RuntimeError("page projection has no owning work")
    kwargs = (
        {}
        if on_committed is None
        else {"on_committed": on_committed}
    )
    _project_path, page_path = save_work_projection(
        work_dir,
        work,
        page_entry=page_entry,
        **kwargs,
    )
    if page_path is None:
        raise RuntimeError("page projection was not checkpointed")
    return page_path


def save_work_projection(
    work_dir: Path,
    work,
    *,
    page_entry=None,
    on_committed: ArtifactCommitHook | None = None,
) -> tuple[Path, Path | None]:
    """projectと編集中pageを同じRepository checkpointで確定する。"""

    repository = domain_runtime.repository_for(work_dir)
    observation_paths = [repository.project_path]
    projected_project = domain_projection.project_document_from_work(work)
    store = domain_runtime.store_for(
        work_dir,
        initial_project=projected_project,
    )
    page_uid = ""
    if page_entry is not None:
        page_uid = domain_projection.ensure_page_uid(
            page_entry,
            projected_project.project_uid,
        )
        observation_paths.append(repository.page_path(page_uid))
        if (
            page_uid not in store.pages
            and repository.page_path(page_uid).is_file()
        ):
            store = domain_runtime.hydrate_page(
                work_dir,
                repository.load_page(page_uid),
            )
    repository.assert_observations_current(observation_paths)
    projected_project = domain_projection.preserve_project_projection(
        store.project,
        projected_project,
    )
    project_missing = not repository.project_path.is_file()
    project_delta = project_patch(store.project, projected_project)
    project_changed = (
        project_missing
        or store.dirty_project
        or not project_delta.is_empty
    )
    projected_page = None
    page_delta = None
    page_changed = False
    if page_entry is not None:
        current_page = store.pages.get(page_uid)
        projected_page = domain_projection.page_document_from_projection(
            work,
            page_entry,
            context=_context_for_page(page_entry),
            preserve_document=current_page,
        )
        projected_page = domain_projection.preserve_page_projection(
            current_page,
            projected_page,
        )
        page_delta = page_patch(current_page, projected_page)
        page_changed = (
            current_page is None
            or not repository.page_path(page_uid).is_file()
            or page_uid in store.dirty_page_uids
            or not page_delta.is_empty
        )
    with store.transaction():
        if not project_delta.is_empty:
            store.execute(ApplyProjectPatch(project_delta))
        if page_delta is not None and not page_delta.is_empty:
            store.execute(ApplyPagePatch(page_delta))
        project = store.project
        page = (
            store.pages[projected_page.page_uid]
            if projected_page is not None
            else None
        )
        if project_changed or page_changed:
            repository.checkpoint(
                project,
                [page] if page is not None and page_changed else (),
                include_project=project_changed,
                artifact_commit_hook=on_committed,
            )
    if project_changed:
        record_successful_write(repository.project_path)
    page_path = None
    if page is not None:
        page_path = repository.page_path(page.page_uid)
        if page_changed:
            record_successful_write(page_path)
    # 保存はUI projectionからDomainへの一方向Commandである。ここでDomainを
    # scene全体へ再投影すると、別ページへ移した直後のscene-ownedレイヤーが
    # 「保存したページに属さない」という理由で消える。保存済みUID/revisionだけ
    # を現在のprojectionへ束縛し、全再投影は明示的な読込時に限定する。
    if page_entry is not None and page is not None:
        domain_projection.bind_page_document(page_entry, page)
    domain_projection.bind_project_document(work, project)
    checkpoint_page_uids = (
        (page.page_uid,) if page is not None and page_changed else ()
    )
    if native_checkpoint_runtime.is_pending(work_dir):
        native_checkpoint_runtime.note_domain_write(
            work_dir,
            project_changed=project_changed,
            page_uids=checkpoint_page_uids,
        )
    else:
        store.mark_checkpointed(
            project=project_changed,
            page_uids=checkpoint_page_uids,
        )
    return repository.project_path, page_path


def reconcile_work_projection(
    work_dir: Path,
    work,
    *,
    page_entry=None,
    context=None,
) -> None:
    """Undo/Redo後のBlender状態をdiskへ書かずDomainへCommand反映する。"""

    projected_project = domain_projection.project_document_from_work(work)
    store = domain_runtime.store_for(
        work_dir,
        initial_project=projected_project,
    )
    projected_project = domain_projection.preserve_project_projection(
        store.project,
        projected_project,
    )
    project_delta = project_patch(
        store.project,
        projected_project,
        require_candidate_revision=False,
    )
    page_delta = None
    if page_entry is not None:
        page_uid = domain_projection.ensure_page_uid(
            page_entry,
            projected_project.project_uid,
        )
        current_page = store.pages.get(page_uid)
        projected_page = domain_projection.page_document_from_projection(
            work,
            page_entry,
            context=context,
            preserve_document=current_page,
        )
        projected_page = domain_projection.preserve_page_projection(
            current_page,
            projected_page,
        )
        page_delta = page_patch(
            current_page,
            projected_page,
            require_candidate_revision=False,
        )
    with store.transaction():
        if not project_delta.is_empty:
            store.execute(ApplyProjectPatch(project_delta))
        if page_delta is not None and not page_delta.is_empty:
            store.execute(ApplyPagePatch(page_delta))
    project = store.project
    domain_projection.bind_project_document(work, project)
    if page_entry is not None:
        page = store.pages[
            domain_projection.ensure_page_uid(
                page_entry,
                project.project_uid,
            )
        ]
        domain_projection.bind_page_document(page_entry, page)


def load_page_json(
    work_dir: Path,
    page_entry,
    *,
    allow_missing: bool = False,
) -> dict:
    project_uid = domain_projection.ensure_project_uid(_work_from_page(page_entry))
    page_uid = domain_projection.ensure_page_uid(page_entry, project_uid)
    repository = domain_runtime.repository_for(work_dir)
    path = repository.page_path(page_uid)
    if not path.is_file():
        if not allow_missing:
            raise FileNotFoundError(
                f"required Domain page is missing: {path}"
            )
        # page.json がまだ無い新規ページも「詳細読込済み」として扱う
        # (保存時に page.json を新規作成できるようにする)
        page_entry.detail_loaded = True
        return {}
    document = repository.load_page(page_uid)
    domain_runtime.hydrate_page(work_dir, document)
    # update callbackがページプレビューを要求しても、同じpage.jsonの読込を
    # 再入させない。失敗時は部分投影を破棄して未読込へ戻す。
    page_entry.detail_loaded = True
    try:
        domain_projection.apply_page_document(
            page_entry,
            document,
            context=_context_for_page(page_entry),
        )
    except Exception:
        from ..utils import page_detail

        page_detail.clear_page_detail(page_entry)
        raise
    return document.to_dict()


# ---------- pages.json ----------


def save_pages_json(
    work_dir: Path,
    work,
    *,
    on_committed: ArtifactCommitHook | None = None,
) -> Path:
    repository = domain_runtime.repository_for(work_dir)
    repository.assert_observations_current((repository.project_path,))
    projected = domain_projection.project_document_from_work(work)
    store = domain_runtime.store_for(work_dir, initial_project=projected)
    projected = domain_projection.preserve_project_projection(
        store.project,
        projected,
    )
    project_missing = not repository.project_path.is_file()
    patch = project_patch(store.project, projected)
    project_changed = (
        project_missing
        or store.dirty_project
        or not patch.is_empty
    )
    with store.transaction():
        if not patch.is_empty:
            store.execute(ApplyProjectPatch(patch))
        project = store.project
        if project_changed:
            repository.checkpoint(
                project,
                artifact_commit_hook=on_committed,
            )
    if project_changed:
        record_successful_write(repository.project_path)
    domain_projection.bind_project_document(work, project)
    store.mark_checkpointed(project=project_changed, page_uids=())
    _logger.debug("project page order saved: %s (%d pages)", repository.project_path, len(work.pages))
    return repository.project_path


def load_pages_json(work_dir: Path, work) -> dict:
    repository = domain_runtime.repository_for(work_dir)
    document = repository.load_project()
    domain_runtime.install_store(work_dir, document)
    domain_projection.apply_project_document(work, document)
    try:
        from ..utils import page_range

        page_range.update_page_range_visibility(work)
    except Exception:  # noqa: BLE001
        _logger.exception("page range visibility sync failed")
    _logger.info("project page order loaded: %s (%d pages)", repository.project_path, len(work.pages))
    return document.to_dict()


# ---------- ページディレクトリ操作 ----------


def ensure_page_dir(work_dir: Path, page_ref) -> Path:
    """UID page directoryを用意する。"""
    if isinstance(page_ref, str):
        stable_ref = page_ref
    else:
        work = _work_from_page(page_ref)
        project_uid = domain_projection.ensure_project_uid(work)
        stable_ref = domain_projection.ensure_page_uid(page_ref, project_uid)
    page_path = paths.page_dir(Path(work_dir), stable_ref)
    page_path.mkdir(parents=True, exist_ok=True)
    paths.page_assets_dir(Path(work_dir), stable_ref).mkdir(exist_ok=True)
    paths.page_comas_dir(Path(work_dir), stable_ref).mkdir(exist_ok=True)
    return page_path


def remove_page_dir(work_dir: Path, page_id: str) -> None:
    """pNNNN/ をまるごと削除 (コマ含む)."""
    page_path = paths.page_dir(Path(work_dir), page_id)
    if page_path.exists():
        shutil.rmtree(page_path)
        record_successful_tree_change(page_path)
        _logger.info("page dir removed: %s", page_path)


def copy_page_dir(work_dir: Path, src_id: str, dst_id: str) -> None:
    """ページディレクトリをまるごとコピー (複製)."""
    src = paths.page_dir(Path(work_dir), src_id)
    dst = paths.page_dir(Path(work_dir), dst_id)
    if not src.exists():
        raise FileNotFoundError(f"source page dir missing: {src}")
    if dst.exists():
        raise FileExistsError(f"destination already exists: {dst}")
    shutil.copytree(src, dst)
    record_successful_tree_change(dst)
    _logger.info("page dir copied: %s -> %s", src, dst)


def rename_page_dir(work_dir: Path, old_id: str, new_id: str) -> None:
    """ページディレクトリを rename."""
    src = paths.page_dir(Path(work_dir), old_id)
    dst = paths.page_dir(Path(work_dir), new_id)
    if not src.exists():
        raise FileNotFoundError(f"source page dir missing: {src}")
    if dst.exists():
        raise FileExistsError(f"destination already exists: {dst}")
    src.rename(dst)
    record_successful_tree_change(src, dst)
    _logger.info("page dir renamed: %s -> %s", src, dst)


# ---------- 新規ページ採番 ----------


def allocate_new_page_id(work) -> str:
    """既存ページ ID から空き番号の最小値を採番して 4 桁 ID を返す."""
    existing = [p.id for p in work.pages]
    idx = paths.next_available_page_index(existing)
    return paths.format_page_id(idx)


def register_new_page(work, title: str = "") -> object:
    """CollectionProperty に新規ページエントリを追加し、返す.

    ディレクトリ作成・pages.json の保存は呼び出し側の責務。
    """
    page_id = allocate_new_page_id(work)
    entry = work.pages.add()
    entry.id = page_id
    entry.title = str(title or "")
    project_uid = domain_projection.ensure_project_uid(work)
    page_uid = domain_projection.ensure_page_uid(entry, project_uid)
    entry.dir_rel = f"pages/{page_uid}/"
    entry.spread = False
    entry.coma_count = 0
    # 新規ページはメモリ上のデータが正本 (page.json を新規作成して良い)
    entry.detail_loaded = True
    work.active_page_index = len(work.pages) - 1
    return entry


def _work_from_page(page_entry):
    scene = getattr(page_entry, "id_data", None)
    return getattr(scene, "bmanga_work", None) if scene is not None else None


def _context_for_page(page_entry):
    try:
        import bpy

        context = bpy.context
        scene = getattr(context, "scene", None)
        if scene is not getattr(page_entry, "id_data", None):
            return None
        work = getattr(scene, "bmanga_work", None)
        work_dir_text = str(getattr(work, "work_dir", "") or "")
        blend_path_text = str(getattr(bpy.data, "filepath", "") or "")
        if not work_dir_text or not blend_path_text:
            return None
        from ..utils import page_file_scene

        role, page_id, _coma_id = page_file_scene.role_from_path(
            Path(blend_path_text),
            Path(work_dir_text),
        )
        if (
            role == page_file_scene.ROLE_PAGE
            and page_id == str(getattr(page_entry, "id", "") or "")
        ):
            return context
    except Exception:
        return None
    return None


# ---------- 並び替え ----------


def move_page(work, from_index: int, to_index: int) -> None:
    """pages コレクション内で要素を移動."""
    n = len(work.pages)
    if not (0 <= from_index < n):
        raise IndexError(f"from_index out of range: {from_index}")
    if not (0 <= to_index < n):
        raise IndexError(f"to_index out of range: {to_index}")
    if from_index == to_index:
        return
    work.pages.move(from_index, to_index)
    # アクティブページ追随
    if work.active_page_index == from_index:
        work.active_page_index = to_index
    elif from_index < work.active_page_index <= to_index:
        work.active_page_index -= 1
    elif to_index <= work.active_page_index < from_index:
        work.active_page_index += 1
