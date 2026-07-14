"""統合レイヤー一覧で使うページ表示名。"""

from __future__ import annotations

import re


def page_layer_name(target, work=None) -> str:
    """ページの表示番号を作品内の並び順から返す。"""

    if work is not None:
        target_id = str(getattr(target, "id", "") or "")
        for index, page in enumerate(getattr(work, "pages", []) or []):
            if page == target or (
                target_id and str(getattr(page, "id", "") or "") == target_id
            ):
                info = getattr(work, "work_info", None)
                try:
                    start = int(getattr(info, "page_number_start", 1) or 1)
                except Exception:  # noqa: BLE001
                    start = 1
                return f"ページ{start + index:03d}"
    target_id = str(getattr(target, "id", "") or "")
    match = re.search(r"(\d+)", target_id)
    if match:
        return f"ページ{int(match.group(1)):03d}"
    return target_id or "ページ000"


__all__ = ["page_layer_name"]
