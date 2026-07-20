"""レイヤー種別を横断したロック状態のアクセサ.

レイヤー一覧 (``panels/gpencil_panel.py``) やロック用オペレーター
(``operators/layer_stack_lock_op.py``) から、``item.kind`` ごとに異なる
ロック実体 (PropertyGroup の ``locked`` / GP・効果線オブジェクトの
``bmanga_user_locked``) を意識せず読み書きできるようにする。

対応する種別ごとの正本:

- balloon / text / image / image_path / raster / fill / layer_folder / coma:
  ``target.locked`` (PropertyGroup の BoolProperty)
- gp / effect: Object のカスタムプロパティ ``bmanga_user_locked``
  (``utils/layer_object_model.py`` の ``user_locked`` / ``set_user_locked``
  経由。GP layer の ``lock`` と Object の ``hide_select`` にも連動する)

それ以外の種別 (page / coma_preview / balloon_group / outside_group 等) は
ロック非対応として扱う。
"""

from __future__ import annotations

# ``target.locked`` プロパティを持つ種別 (レイヤー一覧の resolve_stack_item
# が返す ``resolved["target"]`` が直接 BoolProperty ``locked`` を持つ)。
_ENTRY_LOCK_KINDS = frozenset(
    {
        "balloon",
        "text",
        "image",
        "image_path",
        "raster",
        "fill",
        "layer_folder",
        "coma",
    }
)

# Object のカスタムプロパティ (bmanga_user_locked) 経由でロックする種別。
_OBJECT_LOCK_KINDS = frozenset({"gp", "effect"})

LOCKABLE_KINDS = _ENTRY_LOCK_KINDS | _OBJECT_LOCK_KINDS


def is_lockable_kind(kind: str) -> bool:
    """``kind`` がロック機能に対応する種別かどうかを返す."""

    return str(kind or "") in LOCKABLE_KINDS


def is_lockable(item, resolved) -> bool:
    """``item``/``resolved`` の組がロック可能な実体を持つかどうかを返す."""

    kind = str(getattr(item, "kind", "") or "")
    if kind in _ENTRY_LOCK_KINDS:
        target = resolved.get("target") if resolved is not None else None
        return target is not None and hasattr(target, "locked")
    if kind in _OBJECT_LOCK_KINDS:
        obj = resolved.get("object") if resolved is not None else None
        return obj is not None
    return False


def get_locked(item, resolved) -> bool:
    """現在のロック状態を返す。ロック非対応/未解決なら ``False``."""

    kind = str(getattr(item, "kind", "") or "")
    if kind in _ENTRY_LOCK_KINDS:
        target = resolved.get("target") if resolved is not None else None
        if target is None or not hasattr(target, "locked"):
            return False
        return bool(getattr(target, "locked", False))
    if kind in _OBJECT_LOCK_KINDS:
        from . import layer_object_model

        obj = resolved.get("object") if resolved is not None else None
        return layer_object_model.user_locked(obj)
    return False


def set_locked(item, resolved, value: bool) -> bool:
    """ロック状態を書き込む。実際に書き込めたら ``True``."""

    kind = str(getattr(item, "kind", "") or "")
    if kind in _ENTRY_LOCK_KINDS:
        target = resolved.get("target") if resolved is not None else None
        if target is None or not hasattr(target, "locked"):
            return False
        target.locked = bool(value)
        return True
    if kind in _OBJECT_LOCK_KINDS:
        from . import layer_object_model

        obj = resolved.get("object") if resolved is not None else None
        return layer_object_model.set_user_locked(obj, bool(value))
    return False
