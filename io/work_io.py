"""新Domain作品のproject.json入出力adapter。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..bmanga_core.domain_store import (
    ApplyProjectPatch,
    project_patch,
)
from ..utils import log, paths
from . import (
    coma_move_recovery,
    domain_projection,
    domain_runtime,
    native_tree_transaction,
)
from .save_baseline import (
    initialize_new_work_baseline,
    record_successful_write,
)

_logger = log.get_logger(__name__)


# ---------- 新規作成 ----------


def create_bmanga_skeleton(work_dir: Path) -> None:
    """新形式だけを含む作品directory骨格を作る。"""
    work_dir = Path(work_dir)
    repository = domain_runtime.repository_for(work_dir)
    repository.initialize_layout()
    assets = paths.assets_dir(work_dir)
    for name in (
        paths.ASSETS_BRUSHES_DIR,
        paths.ASSETS_TEMPLATES_DIR,
        paths.ASSETS_MODELS_DIR,
        paths.ASSETS_BALLOONS_DIR,
        paths.ASSETS_EFFECTS_DIR,
    ):
        (assets / name).mkdir(exist_ok=True)
    paths.scenario_dir(work_dir).mkdir(exist_ok=True)
    paths.exports_dir(work_dir).mkdir(exist_ok=True)
    paths.raster_dir(work_dir).mkdir(exist_ok=True)
    paths.raster_trash_dir(work_dir).mkdir(exist_ok=True)
    _logger.info("bmanga skeleton created: %s", work_dir)


# ---------- project.json ----------


def save_work_json(work_dir: Path, work) -> Path:
    """UI投影をCommand境界でDomainへ取り込み、project.jsonへ確定する。"""
    work_dir = Path(work_dir)
    repository = domain_runtime.repository_for(work_dir)
    is_new = not repository.project_path.exists()
    if is_new:
        initialize_new_work_baseline(work_dir)
    projected = domain_projection.project_document_from_work(work)
    store = domain_runtime.store_for(work_dir, initial_project=projected)
    projected = domain_projection.preserve_project_projection(
        store.project,
        projected,
    )
    with store.transaction():
        store.execute(
            ApplyProjectPatch(project_patch(store.project, projected))
        )
        document = store.project
        repository.checkpoint(document)
    record_successful_write(repository.project_path)
    domain_projection.bind_project_document(work, document)
    store.mark_checkpointed(project=True, page_uids=())
    _logger.debug("project.json saved: %s", repository.project_path)
    return repository.project_path


def load_work_json(work_dir: Path, work) -> dict[str, Any]:
    """project.jsonを厳格読込し、PropertyGroupへ一方向投影する。"""
    work.loaded = False
    repository = domain_runtime.repository_for(work_dir)
    repository.recover()
    native_tree_transaction.recover_pending_native_transactions(
        work_dir,
        repository=repository,
    )
    coma_move_recovery.recover_interrupted_coma_moves(
        work_dir,
        repository=repository,
    )
    document = repository.load_project()
    repository.validate_project_pages(document)
    domain_runtime.install_store(work_dir, document)
    domain_projection.apply_project_document(work, document)
    work.work_dir = str(Path(work_dir).resolve())
    work.loaded = True
    _logger.info("project.json loaded: %s", repository.project_path)
    return document.to_dict()
