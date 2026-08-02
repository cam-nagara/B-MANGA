"""Blender process内でRepository観測基準を作品ごとに保持する。"""

from __future__ import annotations

from pathlib import Path

from ..bmanga_core.domain_repository import ProjectRepository
from ..bmanga_core.domain_store import DomainStore
from ..bmanga_core.domain_model import PageDocument, ProjectDocument
from .project_file_lock import work_lock


_REPOSITORIES: dict[str, ProjectRepository] = {}
_STORES: dict[str, DomainStore] = {}


def _key(work_dir: str | Path) -> str:
    return str(Path(work_dir).resolve()).casefold()


def repository_for(work_dir: str | Path) -> ProjectRepository:
    root = Path(work_dir).resolve()
    key = _key(root)
    repository = _REPOSITORIES.get(key)
    if repository is None:
        repository = ProjectRepository(
            root,
            lock_factory=lambda: work_lock(root, blocking=True),
        )
        _REPOSITORIES[key] = repository
    return repository


def install_store(
    work_dir: str | Path,
    project: ProjectDocument,
    pages: tuple[PageDocument, ...] = (),
) -> DomainStore:
    store = DomainStore(project, {page.page_uid: page for page in pages})
    _STORES[_key(work_dir)] = store
    return store


def store_for(
    work_dir: str | Path,
    *,
    initial_project: ProjectDocument | None = None,
) -> DomainStore:
    key = _key(work_dir)
    store = _STORES.get(key)
    if store is None:
        if initial_project is None:
            raise RuntimeError("Domain Store is not hydrated")
        store = install_store(work_dir, initial_project)
    return store


def hydrate_page(work_dir: str | Path, page: PageDocument) -> DomainStore:
    current = store_for(work_dir)
    current.hydrate_page(page)
    return current


def forget_repository(work_dir: str | Path) -> None:
    key = _key(work_dir)
    _REPOSITORIES.pop(key, None)
    _STORES.pop(key, None)


def clear_runtime() -> None:
    _REPOSITORIES.clear()
    _STORES.clear()


__all__ = (
    "clear_runtime",
    "forget_repository",
    "hydrate_page",
    "install_store",
    "repository_for",
    "store_for",
)
