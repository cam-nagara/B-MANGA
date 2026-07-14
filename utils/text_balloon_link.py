"""テキストと親フキダシのプリセット連動を固定対象へ適用する。"""

from __future__ import annotations


def _same_entry(left, right) -> bool:
    try:
        return left is right or left == right
    except Exception:
        return left is right


def _text_owner_id(work, entry, page=None, stable_id: str = "") -> str:
    if page is not None:
        return str(getattr(page, "id", "") or "")
    owner_id, separator, _entry_id = str(stable_id or "").partition(":")
    if separator and owner_id:
        return owner_id
    for candidate_page in getattr(work, "pages", ()) or ():
        if any(_same_entry(candidate, entry) for candidate in getattr(candidate_page, "texts", ()) or ()):
            return str(getattr(candidate_page, "id", "") or "")
    if any(_same_entry(candidate, entry) for candidate in getattr(work, "shared_texts", ()) or ()):
        return "outside"
    return ""


def find_linked_balloon(work, entry, *, page=None, stable_id: str = ""):
    """テキストが明示的に参照する親フキダシだけを返す。"""

    balloon_id = str(getattr(entry, "parent_balloon_id", "") or "")
    if work is None or not balloon_id:
        return None
    owner_id = _text_owner_id(work, entry, page=page, stable_id=stable_id)
    if owner_id in {"outside", "__outside__"}:
        balloons = getattr(work, "shared_balloons", ()) or ()
    else:
        owner_page = next(
            (
                candidate
                for candidate in getattr(work, "pages", ()) or ()
                if str(getattr(candidate, "id", "") or "") == owner_id
            ),
            None,
        )
        if owner_page is None:
            return None
        balloons = getattr(owner_page, "balloons", ()) or ()
    for balloon in balloons:
        if str(getattr(balloon, "id", "") or "") == balloon_id:
            return balloon
    return None


def apply_linked_balloon_preset(
    work,
    entry,
    preset_name: str,
    *,
    page=None,
    stable_id: str = "",
):
    """選んだ名前を固定テキストと、その明示的な親だけへ反映する。"""

    name = str(preset_name or "")
    entry.linked_balloon_preset = name
    balloon = find_linked_balloon(work, entry, page=page, stable_id=stable_id)
    if balloon is None:
        return None
    if name:
        balloon.shape = "custom"
        balloon.custom_preset_name = name
    elif str(getattr(balloon, "shape", "") or "") == "none":
        balloon.shape = "rect"
        balloon.custom_preset_name = ""
    return balloon


__all__ = ["apply_linked_balloon_preset", "find_linked_balloon"]
