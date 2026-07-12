"""テキストレイヤー (kind="text") をオブジェクトツールの回転コアへ登録する.

operators/object_rotation.py 本体は並行セッションと共有のため直接編集しない。
代わりに ``object_rotation.register_rotation_handler`` を呼んでテキスト用の
capture/apply 関数を登録する (balloon/image/effect と同じレジストリ方式)。
このモジュールは import されるだけで登録が完了する (副作用による登録)。
"""

from __future__ import annotations

from ..core.work import get_work
from ..utils import object_selection
from ..utils.layer_hierarchy import OUTSIDE_STACK_KEY
from . import object_rotation, object_tool_selection


def _capture_text_rotation(context, key: str) -> dict | None:
    _kind, page_id, item_id = object_selection.parse_key(key)
    work = get_work(context)
    if work is None:
        return None
    if page_id == OUTSIDE_STACK_KEY:
        _idx, entry = object_tool_selection.find_shared_text_by_key(work, item_id)
    else:
        _pi, _page, _idx, entry = object_tool_selection.find_text_by_key(work, page_id, item_id)
    if entry is None:
        return None
    return {"entry": entry, "base_rotation_deg": float(getattr(entry, "rotation_deg", 0.0))}


def _apply_text_rotation(context, snapshot: dict, rotation_deg: float) -> None:
    entry = snapshot.get("entry")
    if entry is not None:
        entry.rotation_deg = float(rotation_deg)


object_rotation.register_rotation_handler("text", _capture_text_rotation, _apply_text_rotation)
