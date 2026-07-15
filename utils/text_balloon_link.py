"""テキストと親フキダシのプリセット連動を固定対象へ適用する。"""

from __future__ import annotations


def _custom_preset_name(reference: str) -> str:
    value = str(reference or "")
    return value.split(":", 1)[1] if value.startswith("custom:") else value


def apply_balloon_preset_reference(balloon, reference: str, *, preset=None) -> None:
    """組み込み形状または保存済みフキダシプリセットを1件へ適用する。"""

    from ..io import balloon_presets

    value = str(reference or "")
    if value.startswith("shape:"):
        shape = value.split(":", 1)[1]
        balloon.shape = shape
        balloon.custom_preset_name = ""
        return
    name = _custom_preset_name(value)
    if not name:
        if str(getattr(balloon, "shape", "") or "") == "none":
            balloon.shape = "rect"
        balloon.custom_preset_name = ""
        return
    loaded = preset if preset is not None else balloon_presets.load_preset_by_name(name)
    if loaded is None:
        raise LookupError(f"フキダシプリセットが見つかりません: {name}")
    balloon_presets.apply_linked_text_settings(balloon, getattr(loaded, "data", None))
    balloon.shape = "custom"
    balloon.custom_preset_name = name


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
    apply_balloon_preset_reference(balloon, name)
    fit_linked_balloon_to_text(entry, balloon)
    return balloon


def fit_linked_balloon_to_text(text, balloon):
    """テキスト領域を固定し、保存済み余白・位置補正で親フキダシを合わせる。"""

    if text is None or balloon is None:
        return None
    from ..typography import ruby as ruby_layout

    ruby_pad = max(0.0, float(ruby_layout.render_pad_mm_for_entry(text, minimum=0.0)))
    padding_x = max(0.0, float(getattr(balloon, "linked_text_padding_x_mm", 6.0)))
    padding_y = max(0.0, float(getattr(balloon, "linked_text_padding_y_mm", 6.0)))
    offset_x = float(getattr(balloon, "linked_text_offset_x_mm", 0.0) or 0.0)
    offset_y = float(getattr(balloon, "linked_text_offset_y_mm", 0.0) or 0.0)
    text_width = max(0.1, float(getattr(text, "width_mm", 0.1)))
    text_height = max(0.1, float(getattr(text, "height_mm", 0.1)))
    balloon.width_mm = text_width + (padding_x + ruby_pad) * 2.0
    balloon.height_mm = text_height + (padding_y + ruby_pad) * 2.0
    text_center_x = float(getattr(text, "x_mm", 0.0)) + text_width * 0.5
    text_center_y = float(getattr(text, "y_mm", 0.0)) + text_height * 0.5
    balloon.x_mm = text_center_x + offset_x - float(balloon.width_mm) * 0.5
    balloon.y_mm = text_center_y + offset_y - float(balloon.height_mm) * 0.5
    return balloon


__all__ = [
    "apply_balloon_preset_reference",
    "apply_linked_balloon_preset",
    "find_linked_balloon",
    "fit_linked_balloon_to_text",
]
