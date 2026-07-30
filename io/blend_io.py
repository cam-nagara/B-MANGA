"""Blender mainfile (.blend) の save/open ラッパ.

Phase 1 (overview 再設計): モデル変更あり。
- **work.blend** — ページ一覧専用の軽量 .blend。ページの並びとコマ枠を載せる。
- **page.blend** — 各ページの 2D 編集用 .blend。フキダシ・テキスト・効果線等を載せる。
- **cNN.blend** — 各コマの 3D シーン。コマ編集モード時のみ mainfile。

モード遷移は「現在の mainfile を save_as_mainfile で当該 .blend として保存」
→「切替先の .blend を open_mainfile で開く」の 2 段で行う。
"""

from __future__ import annotations

from pathlib import Path
import tempfile

import bpy

from ..bmanga_core.faults import FaultInjectedError, FaultPoint, check_fault
from ..bmanga_core.observability import observed_operation
from ..utils import log, paths
from .project_file_lock import guard_path_write
from .save_baseline import (
    record_observed_read,
    record_successful_write,
    restore_baseline_registry,
    snapshot_baseline_registry,
)

_logger = log.get_logger(__name__)


def _suspend_keymap_visibility_updates(seconds: float = 4.0) -> None:
    try:
        from ..keymap import keymap as _keymap

        _keymap.suspend_visibility_updates(seconds, reason="blend io")
    except Exception:  # noqa: BLE001
        pass


def _remove_open_checkpoint(checkpoint: Path | None) -> None:
    if checkpoint is None:
        return
    try:
        checkpoint.unlink(missing_ok=True)
        checkpoint.parent.rmdir()
    except OSError:
        _logger.warning("open rollback checkpoint cleanup failed: %s", checkpoint)


def _prepare_open_rollback_source() -> tuple[Path | None, Path | None]:
    """現在状態を失わずにopen失敗から戻れるsourceを用意する。"""

    original = Path(str(getattr(bpy.data, "filepath", "") or ""))
    if original.is_file() and not bool(getattr(bpy.data, "is_dirty", False)):
        return original, None
    checkpoint_dir = Path(tempfile.mkdtemp(prefix="bmanga-open-rollback-"))
    checkpoint = checkpoint_dir / "unsaved-session.blend"
    try:
        result = bpy.ops.wm.save_as_mainfile(
            filepath=str(checkpoint),
            check_existing=False,
            compress=True,
            copy=True,
        )
        if "FINISHED" not in result or not checkpoint.is_file():
            raise RuntimeError("未保存セッションのrollback checkpointを作成できません")
    except BaseException:
        _remove_open_checkpoint(checkpoint)
        raise
    return checkpoint, checkpoint


def _restore_after_open_failure(
    blend_path: Path,
    rollback_source: Path | None,
    baseline_state,
) -> bool:
    """targetを既に開いた場合は元セッションへ戻し、baselineも復元する。"""

    current = Path(str(getattr(bpy.data, "filepath", "") or ""))
    opened_target = current.is_file() and current.resolve() == blend_path.resolve()
    try:
        if opened_target:
            if rollback_source is None or not rollback_source.is_file():
                raise RuntimeError("open_mainfile確定後のrollback元がありません")
            bpy.ops.wm.open_mainfile(filepath=str(rollback_source.resolve()))
            _suspend_keymap_visibility_updates()
    finally:
        restore_baseline_registry(baseline_state)
    return opened_target


def save_current_as(blend_path: Path) -> bool:
    """現在の mainfile を指定パスに save_as_mainfile で保存する.

    親ディレクトリは自動生成。成功時 True、失敗時 False を返す。
    """
    blend_path = Path(blend_path)
    try:
        with guard_path_write(blend_path):
            blend_path.parent.mkdir(parents=True, exist_ok=True)
            from ..utils import handlers

            with handlers.trusted_native_save_target(blend_path):
                result = bpy.ops.wm.save_as_mainfile(
                    filepath=str(blend_path.resolve()),
                    check_existing=False,
                    compress=True,
                )
            if "FINISHED" not in result or not blend_path.is_file():
                raise RuntimeError("Blender本体の保存が完了しませんでした")
            from . import native_save_outcome

            native_result = native_save_outcome.consume(blend_path)
            work_root = _work_root_for_path(blend_path)
            if work_root is not None and native_result is not True:
                raise RuntimeError(
                    "作品情報とBlenderファイルを同じ世代で保存できませんでした"
                )
            record_successful_write(blend_path)
        _logger.info("mainfile saved: %s", blend_path)
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.exception("save_as_mainfile failed: %s (%s)", blend_path, exc)
        return False


def _work_root_for_path(path: Path) -> Path | None:
    current = Path(path).resolve(strict=False).parent
    for _ in range(8):
        if (
            current.suffix == paths.BMANGA_DIR_SUFFIX
            and (current / paths.PROJECT_META_NAME).is_file()
        ):
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


@observed_operation("open.mainfile", failure_result=lambda result: result is False)
def open_mainfile(blend_path: Path) -> bool:
    """指定 .blend を open_mainfile で開く. 存在しなければ False."""
    blend_path = Path(blend_path)
    if not blend_path.is_file():
        _logger.warning("blend file missing: %s", blend_path)
        return False
    check_fault(FaultPoint.OPEN_MAINFILE, path=str(blend_path))
    baseline_state = snapshot_baseline_registry()
    rollback_source: Path | None = None
    checkpoint: Path | None = None
    try:
        # 明示的に開くファイルは、この画面が内容を観測してからメモリへ
        # 読み込む。load_post前の保存処理でも未追跡扱いにならないようにする。
        record_observed_read(blend_path)
        _suspend_keymap_visibility_updates()
        check_fault(
            FaultPoint.OPEN_MAINFILE_AFTER_STAGE,
            path=str(blend_path),
        )
        rollback_source, checkpoint = _prepare_open_rollback_source()
        bpy.ops.wm.open_mainfile(filepath=str(blend_path.resolve()))
        _suspend_keymap_visibility_updates()
        check_fault(
            FaultPoint.OPEN_MAINFILE_AFTER_COMMIT,
            path=str(blend_path),
        )
        _remove_open_checkpoint(checkpoint)
        _logger.info("mainfile opened: %s", blend_path)
        return True
    except FaultInjectedError:
        opened_target = _restore_after_open_failure(
            blend_path,
            rollback_source,
            baseline_state,
        )
        if checkpoint is not None and not opened_target:
            _remove_open_checkpoint(checkpoint)
        raise
    except Exception as exc:  # noqa: BLE001
        try:
            opened_target = _restore_after_open_failure(
                blend_path,
                rollback_source,
                baseline_state,
            )
            if checkpoint is not None and not opened_target:
                _remove_open_checkpoint(checkpoint)
        except Exception as rollback_exc:  # noqa: BLE001
            raise RuntimeError(
                f"open_mainfile失敗後のrollbackに失敗しました: {blend_path}"
            ) from rollback_exc
        _logger.exception("open_mainfile failed: %s (%s)", blend_path, exc)
        return False


def read_homefile() -> bool:
    """空の mainfile 状態に戻す (factory startup でなく user startup)."""
    try:
        _suspend_keymap_visibility_updates()
        bpy.ops.wm.read_homefile()
        _suspend_keymap_visibility_updates()
        _logger.info("mainfile reset to homefile")
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.exception("read_homefile failed: %s", exc)
        return False


# ---------- work.blend (マスター) ----------


def save_work_blend(work_dir: Path) -> bool:
    """現在の mainfile を ``<work>.bmanga/work.blend`` に保存."""
    return save_current_as(paths.work_blend_path(Path(work_dir)))


def open_work_blend(work_dir: Path) -> bool:
    return open_mainfile(paths.work_blend_path(Path(work_dir)))


def work_blend_exists(work_dir: Path) -> bool:
    return paths.work_blend_path(Path(work_dir)).is_file()


# ---------- page.blend (ページ 2D) ----------


def save_page_blend(work_dir: Path, page_id: str) -> bool:
    if not paths.is_valid_page_id(page_id):
        return False
    return save_current_as(paths.page_blend_path(Path(work_dir), page_id))


def open_page_blend(work_dir: Path, page_id: str) -> bool:
    if not paths.is_valid_page_id(page_id):
        return False
    return open_mainfile(paths.page_blend_path(Path(work_dir), page_id))


def page_blend_exists(work_dir: Path, page_id: str) -> bool:
    if not paths.is_valid_page_id(page_id):
        return False
    return paths.page_blend_path(Path(work_dir), page_id).is_file()


# ---------- cNN.blend (コマ 3D) ----------


def save_coma_blend(work_dir: Path, page_id: str, coma_id: str) -> bool:
    if not paths.is_valid_page_id(page_id) or not paths.is_valid_coma_id(coma_id):
        return False
    return save_current_as(paths.coma_blend_path(Path(work_dir), page_id, coma_id))


def open_coma_blend(work_dir: Path, page_id: str, coma_id: str) -> bool:
    if not paths.is_valid_page_id(page_id) or not paths.is_valid_coma_id(coma_id):
        return False
    return open_mainfile(paths.coma_blend_path(Path(work_dir), page_id, coma_id))


def coma_blend_exists(work_dir: Path, page_id: str, coma_id: str) -> bool:
    if not paths.is_valid_page_id(page_id) or not paths.is_valid_coma_id(coma_id):
        return False
    return paths.coma_blend_path(Path(work_dir), page_id, coma_id).is_file()


def current_mainfile_path() -> Path | None:
    """現在開いている mainfile の絶対パス. 未保存なら None."""
    p = bpy.data.filepath
    if not p:
        return None
    return Path(p).resolve()
