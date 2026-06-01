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

import bpy

from ..utils import log, paths

_logger = log.get_logger(__name__)


def _suspend_keymap_visibility_updates(seconds: float = 4.0) -> None:
    try:
        from ..keymap import keymap as _keymap

        _keymap.suspend_visibility_updates(seconds, reason="blend io")
    except Exception:  # noqa: BLE001
        pass


def save_current_as(blend_path: Path) -> bool:
    """現在の mainfile を指定パスに save_as_mainfile で保存する.

    親ディレクトリは自動生成。成功時 True、失敗時 False を返す。
    """
    blend_path = Path(blend_path)
    blend_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        bpy.ops.wm.save_as_mainfile(
            filepath=str(blend_path.resolve()),
            check_existing=False,
            compress=True,
        )
        _logger.info("mainfile saved: %s", blend_path)
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.exception("save_as_mainfile failed: %s (%s)", blend_path, exc)
        return False


def open_mainfile(blend_path: Path) -> bool:
    """指定 .blend を open_mainfile で開く. 存在しなければ False."""
    blend_path = Path(blend_path)
    if not blend_path.is_file():
        _logger.warning("blend file missing: %s", blend_path)
        return False
    try:
        _suspend_keymap_visibility_updates()
        bpy.ops.wm.open_mainfile(filepath=str(blend_path.resolve()))
        _suspend_keymap_visibility_updates()
        _logger.info("mainfile opened: %s", blend_path)
        return True
    except Exception as exc:  # noqa: BLE001
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
    """現在の mainfile を ``<work>.bname/work.blend`` に保存."""
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
