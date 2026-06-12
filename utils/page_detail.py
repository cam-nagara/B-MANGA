"""ページ詳細データ (コマ・フキダシ・テキスト) の読込状態管理.

ファイルの役割ごとに、メモリへ持つページ詳細を絞る:

- 作品ファイル (work.blend): ページ一覧だけを扱うため、詳細は持たない
- ページ用 blend: 全ページの詳細を持つ (ID 採番・出力・見開きで他ページを参照)
- コマ用 blend: 自分が属するページの詳細だけを持つ

詳細未読込のページの page.json は保存時に書き出さない (空上書き防止)。
プレビュー再生成・ページ出力・見開き結合など詳細が必要な処理は、
``ensure_page_detail`` でその場で読み込む。
"""

from __future__ import annotations

from pathlib import Path

from . import log

_logger = log.get_logger(__name__)


def clear_page_detail(page_entry) -> None:
    """ページ詳細 (コマ・フキダシ・テキスト) をメモリから破棄する."""
    try:
        page_entry.comas.clear()
        page_entry.balloons.clear()
        page_entry.texts.clear()
        page_entry.active_coma_index = -1
        page_entry.active_balloon_index = -1
        page_entry.active_text_index = -1
        page_entry.detail_loaded = False
    except Exception:  # noqa: BLE001
        _logger.exception("page detail clear failed: %s", getattr(page_entry, "id", ""))


def ensure_page_detail(work, page_entry) -> bool:
    """詳細未読込なら page.json から読み込む。読み込みを実行したら True."""
    if page_entry is None or bool(getattr(page_entry, "detail_loaded", False)):
        return False
    work_dir = str(getattr(work, "work_dir", "") or "")
    if not work_dir or not getattr(page_entry, "id", ""):
        return False
    try:
        from ..io import page_io

        page_io.load_page_json(Path(work_dir), page_entry)
        return True
    except Exception:  # noqa: BLE001
        _logger.warning(
            "page detail on-demand load failed: %s", getattr(page_entry, "id", ""), exc_info=True
        )
        return False
