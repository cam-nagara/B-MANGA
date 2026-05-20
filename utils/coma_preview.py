"""cNN の表示用プレビュー画像解決ヘルパ."""

from __future__ import annotations

from pathlib import Path

from . import paths


def coma_id_from_entry(entry) -> str:
    """ComaEntry から cNN ID を取り出す."""
    coma_id = str(getattr(entry, "coma_id", "") or getattr(entry, "id", "") or "")
    return coma_id if paths.is_valid_coma_id(coma_id) else ""


def coma_preview_source_path(work_dir: Path, page_id: str, entry) -> Path | None:
    """表示・書き出しに使う ``thumb.png`` を返す."""
    coma_id = coma_id_from_entry(entry)
    if not coma_id:
        return None
    thumb = paths.coma_thumb_path(Path(work_dir), page_id, coma_id)
    return thumb if thumb.is_file() else None
