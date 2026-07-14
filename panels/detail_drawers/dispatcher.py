"""詳細設定の共通外枠、本文振分け、プリセット、リンク欄。"""

from __future__ import annotations

from collections.abc import Mapping

from ...utils.detail_dialog import DetailContractError, normalize_detail_mode
from . import balloon, basic, effect, gp, image, preset_adapters, raster_fill, text
from .basic import prop_if, value


_KIND_LABELS = {
    "page": ("ページ", "FILE_BLANK"),
    "coma": ("コマ", "MOD_WIREFRAME"),
    "gp": ("グリースペンシル", "GREASEPENCIL"),
    "layer_folder": ("フォルダー", "FILE_FOLDER"),
    "image": ("画像", "IMAGE_DATA"),
    "image_path": ("パターンカーブ", "CURVE_BEZCURVE"),
    "raster": ("ラスター", "BRUSH_DATA"),
    "fill": ("囲い塗り", "NODE_TEXTURE"),
    "balloon": ("フキダシ", "MESH_CIRCLE"),
    "text": ("テキスト", "FONT_DATA"),
    "effect": ("効果線", "FORCE_FORCE"),
    "balloon_tail": ("しっぽ", "SHARPCURVE"),
    "balloon_shape": ("フキダシ形状", "MESH_CIRCLE"),
}

_BODY_DRAWERS = {
    "page": basic.draw_page_body,
    "coma": basic.draw_coma_body,
    "gp": gp.draw_gp_body,
    "layer_folder": basic.draw_layer_folder_body,
    "image": image.draw_image_body,
    "image_path": image.draw_image_path_body,
    "raster": raster_fill.draw_raster_body,
    "fill": raster_fill.draw_fill_body,
    "balloon": balloon.draw_balloon_body,
    "text": text.draw_text_body,
    "effect": effect.draw_effect_body,
    "balloon_tail": balloon.draw_tail_body,
    "balloon_shape": balloon.draw_balloon_body,
}


def draw_detail_dialog(
    layout,
    context,
    session,
    mode,
    *,
    description_owner=None,
) -> bool:
    """3入口が共有する唯一の詳細描画API。"""

    layout = basic.classified_layout(layout)
    normalized_mode = normalize_detail_mode(mode)
    _validate_session(session, normalized_mode)
    target = session.target
    drawer = _BODY_DRAWERS.get(target.kind)
    if drawer is None:
        raise DetailContractError(f"detail drawer is not registered: {target.kind}")

    draw_detail_header(layout, target, normalized_mode)
    if normalized_mode.value == "preset" and description_owner is not None:
        layout.prop(description_owner, "description_text")
        layout.separator()
    drawer(layout, context, session, normalized_mode)
    preset_adapters.draw_preset_management(layout, context, session, normalized_mode)
    draw_linked_layers(layout, context, target, normalized_mode)
    return True


def _validate_session(session, mode) -> None:
    target = getattr(session, "target", None)
    layout = getattr(session, "layout", None)
    if target is None or layout is None:
        raise DetailContractError("detail session must contain target and layout")
    session_mode = normalize_detail_mode(getattr(session, "mode", mode))
    if session_mode is not mode:
        raise DetailContractError("draw mode differs from the fixed detail session")
    if target.kind != layout.kind or layout.mode is not mode:
        raise DetailContractError("draw target and fixed layout do not match")
    validator = getattr(session, "validate_target", None)
    if callable(validator):
        validator()


def draw_detail_header(layout, target, mode) -> None:
    label, icon = _label_and_icon(target, mode)
    box = layout.box()
    header_text = f"{label}プリセット" if str(mode.value) == "preset" else label
    box.label(text=header_text, icon=icon)
    if str(mode.value) == "preset":
        return
    _draw_display_name(box, target)
    _draw_visibility_and_lock(box, target)


def _label_and_icon(target, mode) -> tuple[str, str]:
    label, icon = _KIND_LABELS[target.kind]
    if target.kind != "fill":
        return label, icon
    namespace = str(getattr(target, "namespace", "") or "")
    fill_type = str(value(target.data, "fill_type", "solid") or "solid")
    preset_mode = str(getattr(mode, "value", mode)) == "preset"
    if (preset_mode and namespace == "gradient") or (
        not preset_mode and fill_type == "gradient"
    ):
        return "グラデーション", "NODE_TEXTURE"
    return label, icon


def _draw_display_name(layout, target) -> None:
    data = target.data
    if prop_if(layout, data, "title", text="表示名"):
        return
    obj = target.object_ref
    if _has_custom_property(obj, "bmanga_title"):
        layout.prop(obj, '["bmanga_title"]', text="表示名")


def _draw_visibility_and_lock(layout, target) -> None:
    row = layout.row(align=True)
    if target.kind in {"gp", "effect"}:
        obj = target.object_ref
        drew = _custom_prop_if(row, obj, "bmanga_user_visible", text="表示")
        drew = _custom_prop_if(row, obj, "bmanga_user_locked", text="ロック") or drew
    else:
        data = target.data
        drew = prop_if(row, data, "visible", text="表示")
        drew = prop_if(row, data, "locked", text="ロック") or drew
    if not drew:
        row.enabled = False


def _has_custom_property(obj, name: str) -> bool:
    if obj is None:
        return False
    try:
        return name in obj.keys()
    except (AttributeError, TypeError):
        return False


def _custom_prop_if(layout, obj, name: str, *, text: str) -> bool:
    if not _has_custom_property(obj, name):
        return False
    layout.prop(obj, f'["{name}"]', text=text)
    return True


def draw_linked_layers(layout, context, target, mode) -> bool:
    """安定UIDから解決したリンクだけを最下段へ描画する。"""

    if str(mode.value) != "actual" or not target.stack_uid:
        return False
    from ...utils import layer_display, layer_links

    partner_uids = set(layer_links.linked_uids_for_uid(context, target.stack_uid))
    partner_uids.discard(target.stack_uid)
    if target.kind in {"balloon", "text"}:
        page = _target_page(target.params)
        partner_uids.update(
            layer_links.related_uids_for_target(context, target.kind, target.data, page)
        )
        partner_uids.discard(target.stack_uid)
    if not partner_uids:
        return False
    layer_display.draw_linked_layers_box(layout, context, partner_uids)
    return True


def _target_page(params):
    if isinstance(params, Mapping):
        return params.get("page")
    return value(params, "page", None)


__all__ = ["draw_detail_dialog", "draw_detail_header", "draw_linked_layers"]
