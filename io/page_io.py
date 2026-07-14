"""ページディレクトリと pages.json の入出力.

計画書 3.3 / 4.5 参照。page.blend の実際のロード/セーブは Blender API
(``bpy.ops.wm.open_mainfile`` / ``bpy.ops.wm.save_as_mainfile``) を
使う必要があるため、ここではディレクトリ構造・pages.json の整合性
管理のみを担う。ファイルロードは operators/ 層で呼び出す。
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from ..utils import json_io, log, paths
from . import schema
from .project_content_migration_lock import guard_path_write
from .project_content_save_baseline import record_successful_tree_change

_logger = log.get_logger(__name__)


# ---------- page.json (個別ページメタ) ----------


# 保存の差分書込: 直近にこのセッションで書いた page.json の内容ハッシュ。
# 一致すればファイル書込をスキップする (55 ページ規模の Ctrl+S 高速化)。
_LAST_WRITTEN_PAGE_JSON: dict[str, str] = {}


def save_page_json(work_dir: Path, page_entry) -> Path:
    import hashlib
    import json as _json

    paths.validate_page_id(page_entry.id)
    out = paths.page_meta_path(Path(work_dir), page_entry.id)
    data = schema.page_to_dict(page_entry)
    digest = hashlib.md5(
        _json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    key = str(out)
    with guard_path_write(out):
        if _LAST_WRITTEN_PAGE_JSON.get(key) == digest and out.is_file():
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        json_io.write_json(out, data)
        _LAST_WRITTEN_PAGE_JSON[key] = digest
    return out


def load_page_json(work_dir: Path, page_entry) -> dict:
    paths.validate_page_id(page_entry.id)
    path = paths.page_meta_path(Path(work_dir), page_entry.id)
    if not path.is_file():
        # page.json がまだ無い新規ページも「詳細読込済み」として扱う
        # (保存時に page.json を新規作成できるようにする)
        page_entry.detail_loaded = True
        return {}
    data = json_io.read_json(path)
    schema.page_from_dict(page_entry, data)
    # 読込成功したページだけ保存対象にする (読込失敗・未読込ページの
    # page.json を空データで上書きしないための実行時フラグ)
    page_entry.detail_loaded = True
    return data


# ---------- pages.json ----------


def save_pages_json(work_dir: Path, work) -> Path:
    data = schema.pages_to_dict(
        work,
        last_modified=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    out = paths.pages_meta_path(Path(work_dir))
    json_io.write_json(out, data)
    _logger.debug("pages.json saved: %s (%d pages)", out, len(work.pages))
    return out


def load_pages_json(work_dir: Path, work) -> dict:
    path = paths.pages_meta_path(Path(work_dir))
    if not path.is_file():
        # 新規作品でまだ pages.json が無い場合は空リストで初期化
        work.pages.clear()
        work.active_page_index = -1
        return {"pages": [], "totalPages": 0}
    data = json_io.read_json(path)
    schema.pages_from_dict(work, data)
    try:
        from ..utils import page_range

        page_entries = data.get("pages", []) if isinstance(data, dict) else []
        has_saved_range_flags = any(
            isinstance(entry, dict) and "inPageRange" in entry
            for entry in page_entries
        )
        if not has_saved_range_flags:
            page_range.sync_end_number_to_existing_pages(work)
        page_range.update_page_range_visibility(work)
    except Exception:  # noqa: BLE001
        _logger.exception("page range visibility sync failed")
    _logger.info("pages.json loaded: %s (%d pages)", path, len(work.pages))
    return data


# ---------- ページディレクトリ操作 ----------


def ensure_page_dir(work_dir: Path, page_id: str) -> Path:
    """pNNNN/ ディレクトリを用意."""
    paths.validate_page_id(page_id)
    page_path = paths.page_dir(Path(work_dir), page_id)
    with guard_path_write(page_path):
        page_path.mkdir(parents=True, exist_ok=True)
    return page_path


def remove_page_dir(work_dir: Path, page_id: str) -> None:
    """pNNNN/ をまるごと削除 (コマ含む)."""
    paths.validate_page_id(page_id)
    page_path = paths.page_dir(Path(work_dir), page_id)
    with guard_path_write(page_path):
        if page_path.exists():
            shutil.rmtree(page_path)
            record_successful_tree_change(page_path)
            _logger.info("page dir removed: %s", page_path)


def copy_page_dir(work_dir: Path, src_id: str, dst_id: str) -> None:
    """ページディレクトリをまるごとコピー (複製)."""
    paths.validate_page_id(src_id)
    paths.validate_page_id(dst_id)
    src = paths.page_dir(Path(work_dir), src_id)
    dst = paths.page_dir(Path(work_dir), dst_id)
    with guard_path_write(dst):
        if not src.exists():
            raise FileNotFoundError(f"source page dir missing: {src}")
        if dst.exists():
            raise FileExistsError(f"destination already exists: {dst}")
        shutil.copytree(src, dst)
        record_successful_tree_change(dst)
    _logger.info("page dir copied: %s -> %s", src, dst)


def rename_page_dir(work_dir: Path, old_id: str, new_id: str) -> None:
    """ページディレクトリを rename."""
    paths.validate_page_id(old_id)
    paths.validate_page_id(new_id)
    src = paths.page_dir(Path(work_dir), old_id)
    dst = paths.page_dir(Path(work_dir), new_id)
    with guard_path_write(dst):
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
    entry.dir_rel = f"{page_id}/"
    entry.spread = False
    entry.coma_count = 0
    # 新規ページはメモリ上のデータが正本 (page.json を新規作成して良い)
    entry.detail_loaded = True
    work.active_page_index = len(work.pages) - 1
    return entry


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
