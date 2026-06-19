"""カスタム右クリックコンテキストメニュー.

Outliner / 3D ビュー / 各ツール (フキダシ/テキスト/効果線/Object/枠線) で
右クリック時に B-MANGA レイヤーの「詳細設定」ダイアログを開けるようにする。
active_object の ``bmanga_kind`` / ``bmanga_id`` を見て kind ごとの詳細を
``bmanga.layer_detail_open`` operator で表示する。
"""

from __future__ import annotations

import bpy
from bpy.types import Menu

from ..utils import layer_stack as layer_stack_utils
from ..utils import object_naming as on


def _selected_balloon_count(context) -> int:
    try:
        from ..core.work import get_active_page

        page = get_active_page(context)
        if page is None:
            return 0
        return sum(1 for entry in page.balloons if bool(getattr(entry, "selected", False)))
    except Exception:  # noqa: BLE001
        return 0


def _active_managed_object(context):
    """B-MANGA 管理下のレイヤー Object を解決する.

    優先順位:
        1. ``context.active_object`` (3D ビューでの選択)
        2. ``context.selected_objects`` の最初の管理 Object
        3. ``context.selected_ids`` (Outliner 選択) の最初の管理 Object
        4. ``view_layer.active`` (Outliner の active)
    """
    # 1. 3D ビューの active_object
    obj = getattr(context, "active_object", None)
    if obj is not None and on.is_managed(obj):
        return obj
    # 2. selected_objects (3D ビューや Outliner で選択中)
    selected = getattr(context, "selected_objects", None) or ()
    for o in selected:
        if on.is_managed(o):
            return o
    # 3. selected_ids (Outliner の context で利用可能)
    selected_ids = getattr(context, "selected_ids", None) or ()
    for sid in selected_ids:
        if isinstance(sid, bpy.types.Object) and on.is_managed(sid):
            return sid
    # 4. view_layer.active (Outliner の active)
    view_layer = getattr(context, "view_layer", None)
    if view_layer is not None:
        active = getattr(view_layer, "active", None)
        if active is not None and on.is_managed(active):
            return active
    return None


def _active_plain_curve_object(context):
    obj = getattr(context, "active_object", None)
    if obj is not None and getattr(obj, "type", "") == "CURVE" and not on.is_managed(obj):
        return obj
    selected = getattr(context, "selected_objects", None) or ()
    for obj in selected:
        if getattr(obj, "type", "") == "CURVE" and not on.is_managed(obj):
            return obj
    return None


def _managed_object_matches_stack_item(obj, item) -> bool:
    if obj is None or item is None:
        return False
    kind = str(getattr(item, "kind", "") or "")
    key = str(getattr(item, "key", "") or "")
    obj_kind = on.get_kind(obj)
    obj_id = on.get_bmanga_id(obj)
    if kind == "effect_legacy":
        kind = "effect"
    if obj_kind == "effect_legacy":
        obj_kind = "effect"
    if obj_kind != kind:
        return False
    if obj_id == key:
        return True
    child_id = key.split(":", 1)[1] if ":" in key else key
    return bool(child_id and obj_id == child_id)


def _active_managed_object_for_stack_item(context, item):
    obj = _active_managed_object(context)
    if _managed_object_matches_stack_item(obj, item):
        return obj
    return None


def _active_stack_item_no_sync(context):
    scene = getattr(context, "scene", None)
    stack = getattr(scene, "bmanga_layer_stack", None) if scene is not None else None
    if stack is None:
        return None
    idx = int(getattr(scene, "bmanga_active_layer_stack_index", -1))
    if 0 <= idx < len(stack):
        return stack[idx]
    return None


def _active_stack_target(context):
    from ..utils import layer_stack as _ls
    item = _active_stack_item_no_sync(context)
    resolved = _ls.resolve_stack_item(context, item) if item is not None else None
    return resolved.get("target") if resolved else None, resolved


def selection_command_items(context) -> list[dict]:
    """選択中レイヤー向け右クリックメニュー項目を返す.

    実際の Menu 描画と実機テストの両方で使い、項目の抜けを防ぐ。
    """
    item = _active_stack_item_no_sync(context)
    kind = str(getattr(item, "kind", "") or "")
    has_item = item is not None
    normalized_kind = "effect" if kind == "effect_legacy" else kind
    copyable_kinds = {"balloon", "text", "raster", "gp", "effect"}
    has_layer_clipboard = False
    has_tail_clipboard = False
    balloon_has_tails = False
    try:
        from ..operators import layer_clipboard_op
        from ..utils import layer_links

        has_layer_clipboard = layer_clipboard_op.has_layer_clipboard(context)
        has_tail_clipboard = layer_clipboard_op.has_tail_clipboard(context)
        balloon_has_tails = layer_clipboard_op.active_balloon_has_tails(context)
        selected_linkable_count = layer_links.selected_linkable_count(context)
        selected_any_linked = layer_links.selected_any_linked(context)
    except Exception:  # noqa: BLE001
        selected_linkable_count = 0
        selected_any_linked = False
    detail_operator = "bmanga.layer_stack_detail"
    if kind in {"image", "raster", "fill", "balloon", "text", "gp", "effect", "effect_legacy"}:
        if _active_managed_object_for_stack_item(context, item) is not None:
            detail_operator = "bmanga.layer_detail_open"
    items = [
        {
            "label": "詳細設定",
            "operator": detail_operator,
            "icon": "PREFERENCES",
            "enabled": has_item,
        },
        {
            "label": "コピー",
            "operator": "bmanga.layer_clipboard_copy",
            "icon": "COPYDOWN",
            "enabled": has_item and normalized_kind in copyable_kinds,
        },
        {
            "label": "貼り付け",
            "operator": "bmanga.layer_clipboard_paste",
            "icon": "PASTEDOWN",
            "enabled": has_layer_clipboard,
        },
        {
            "label": "複製",
            "operator": "bmanga.layer_stack_duplicate",
            "icon": "DUPLICATE",
            "enabled": has_item,
        },
        {
            "label": "リンク複製",
            "operator": "bmanga.layer_stack_link_duplicate",
            "icon": "LINKED",
            "enabled": has_item and normalized_kind in {"balloon", "effect"},
        },
        {
            "label": "選択レイヤーをリンク",
            "operator": "bmanga.layer_stack_link_selected",
            "icon": "LINKED",
            "enabled": has_item and selected_linkable_count >= 2,
        },
        {
            "label": "リンクを解除",
            "operator": "bmanga.layer_stack_unlink_selected",
            "icon": "UNLINKED",
            "enabled": has_item and selected_any_linked,
        },
    ]
    if normalized_kind in {"balloon", "effect"}:
        items.insert(
            5,
            {
                "label": "中心点を中心へ戻す",
                "operator": "bmanga.reset_center_point",
                "icon": "PIVOT_BOUNDBOX",
                "enabled": has_item,
            },
        )
    if normalized_kind in {"balloon", "effect"}:
        items.insert(
            6,
            {
                "label": "自由変形",
                "operator": "bmanga.free_transform_mode",
                "icon": "MOD_LATTICE",
                "enabled": has_item,
            },
        )
    if normalized_kind in {"balloon", "text", "effect"}:
        ft_has_transform = False
        if has_item:
            try:
                from ..utils import free_transform as _ft
                from ..operators import object_tool_selection as _ots

                if normalized_kind == "balloon":
                    _target, _resolved = _active_stack_target(context)
                    ft_has_transform = _target is not None and (
                        _ft.entry_enabled(_target)
                        or not _ft.offsets_are_zero(_ft.entry_offsets(_target))
                    )
                elif normalized_kind == "effect":
                    _key = _ots.active_selection_key(context)
                    if _key:
                        _item_id = _key.split(":", 2)[-1] if ":" in _key else ""
                        _obj, _layer = _ots.find_effect_layer(_item_id)
                        _payload = _ft.effect_payload_for_layer(_obj, _layer)
                        ft_has_transform = _ft.effect_payload_enabled(_payload)
                elif normalized_kind == "text":
                    _target, _resolved = _active_stack_target(context)
                    ft_has_transform = _target is not None and (
                        _ft.entry_enabled(_target)
                        or not _ft.offsets_are_zero(_ft.entry_offsets(_target))
                    )
            except Exception:  # noqa: BLE001
                pass
        items.insert(
            7 if normalized_kind in {"balloon", "effect"} else 5,
            {
                "label": "自由変形をリセット",
                "operator": "bmanga.reset_free_transform",
                "icon": "LOOP_BACK",
                "enabled": has_item and ft_has_transform,
            },
        )
    if normalized_kind == "balloon":
        items.insert(
            8,
            {
                "label": "拡大・縮小・回転",
                "operator": "bmanga.balloon_free_transform_scale_rotate",
                "icon": "FULLSCREEN_ENTER",
                "enabled": has_item,
            },
        )
        items.append(
            {
                "label": "フキダシを結合",
                "operator": "bmanga.balloon_merge_selected",
                "icon": "FILE_FOLDER",
                "enabled": _selected_balloon_count(context) >= 2,
            }
        )
        items.extend([
            {
                "label": "しっぽをコピー",
                "operator": "bmanga.balloon_tail_clipboard_copy",
                "icon": "COPYDOWN",
                "enabled": balloon_has_tails,
            },
            {
                "label": "しっぽを貼り付け",
                "operator": "bmanga.balloon_tail_clipboard_paste",
                "icon": "PASTEDOWN",
                "enabled": has_tail_clipboard,
            },
        ])
    items.append(
        {
            "label": "削除",
            "operator": "bmanga.layer_stack_delete",
            "icon": "TRASH",
            "enabled": has_item,
        }
    )
    return items


def _draw_selection_command_items(layout, context) -> bool:
    items = selection_command_items(context)
    if not any(bool(item.get("enabled", False)) for item in items):
        return False
    for item in items:
        label = str(item.get("label", ""))
        op_id = str(item.get("operator", ""))
        icon = str(item.get("icon", "NONE") or "NONE")
        enabled = bool(item.get("enabled", False))
        row = layout.row()
        row.enabled = enabled
        if not op_id:
            row.label(text=label, icon=icon)
            continue
        if op_id == "bmanga.layer_stack_detail":
            row.operator_context = "INVOKE_DEFAULT"
            op = row.operator(op_id, text=label, icon=icon)
            op.index = int(getattr(context.scene, "bmanga_active_layer_stack_index", -1))
            op.offset_from_selection = True
        elif op_id == "bmanga.layer_detail_open":
            row.operator_context = "INVOKE_DEFAULT"
            row.operator(op_id, text=label, icon=icon)
        else:
            row.operator_context = "INVOKE_DEFAULT"
            row.operator(op_id, text=label, icon=icon)
    return True


def _draw_layer_commands(layout, context) -> None:
    """選択中レイヤー Object に対して詳細/複製/削除 等のコマンドを描画."""
    obj = _active_managed_object(context)
    if obj is None:
        plain_curve = _active_plain_curve_object(context)
        if plain_curve is not None:
            layout.label(text=str(getattr(plain_curve, "name", "") or "カーブ"), icon="CURVE_BEZCURVE")
            layout.operator("bmanga.balloon_register_selected_curve", text="選択カーブをフキダシに登録", icon="MOD_CURVE")
            return
        item = _active_stack_item_no_sync(context)
        if item is not None:
            layout.label(text=str(getattr(item, "label", "") or getattr(item, "name", "") or "選択中レイヤー"), icon="RESTRICT_SELECT_OFF")
            layout.separator()
            _draw_selection_command_items(layout, context)
            return
        layout.label(text="B-MANGA レイヤーを選択してください", icon="INFO")
        return
    kind = on.get_kind(obj)
    title = str(obj.get(on.PROP_TITLE, "") or obj.name)
    layout.label(text=title, icon="OBJECT_DATA")
    layout.separator()
    if not _draw_selection_command_items(layout, context):
        detail_row = layout.row()
        detail_row.operator_context = "INVOKE_DEFAULT"
        detail_row.operator(
            "bmanga.layer_detail_open", text="詳細設定", icon="PREFERENCES"
        )
        # フキダシ / 効果線の場合はリンク複製も
        if kind in {"balloon", "effect", "effect_legacy"}:
            link_op = getattr(bpy.ops.bmanga, "layer_stack_link_duplicate", None)
            if link_op is not None:
                layout.operator(
                    "bmanga.layer_stack_link_duplicate",
                    text="リンク複製",
                    icon="LINKED",
                )

    # Outliner D&D で親変更可能であることの案内
    layout.separator()
    layout.label(
        text="親変更は Outliner で D&D してください",
        icon="OUTLINER",
    )


class BMANGA_MT_layer_context(Menu):
    """B-MANGA レイヤー Object 用サブメニュー (3D ビュー / Outliner 共通)."""

    bl_idname = "BMANGA_MT_layer_context"
    bl_label = "B-MANGA"

    def draw(self, context):
        _draw_layer_commands(self.layout, context)


def open_layer_context_menu() -> bool:
    """ツール側の modal operator から呼び出すヘルパ."""
    try:
        bpy.ops.wm.call_menu(name=BMANGA_MT_layer_context.bl_idname)
        return True
    except Exception:  # noqa: BLE001
        return False


# 旧 idname を維持して既存ツール側の呼出を壊さない (内容は新メニューと同じ)
class BMANGA_MT_selection_context(Menu):
    bl_idname = "BMANGA_MT_selection_context"
    bl_label = "B-MANGA"

    def draw(self, context):
        _draw_layer_commands(self.layout, context)


class BMANGA_MT_object_context(Menu):
    """3D ビューの Object 右クリックに append されるサブメニュー."""

    bl_idname = "BMANGA_MT_object_context"
    bl_label = "B-MANGA"

    def draw(self, context):
        layout = self.layout
        _draw_layer_commands(layout, context)
        layout.separator()
        op_link = getattr(bpy.ops.bmanga, "open_link_source", None)
        if op_link is not None:
            layout.operator("bmanga.open_link_source", icon="FILE_BLEND")
        op_record = getattr(bpy.ops.bmanga, "record_asset_link", None)
        if op_record is not None:
            layout.operator("bmanga.record_asset_link", icon="LINKED")


def _draw_in_object_context(self, context):
    """3D ビュー Object 右クリックメニューに B-MANGA サブメニューを差し込む."""
    obj = _active_managed_object(context)
    if obj is None and _active_plain_curve_object(context) is None:
        return
    self.layout.separator()
    self.layout.menu(
        BMANGA_MT_object_context.bl_idname,
        icon="OUTLINER_OB_GROUP_INSTANCE",
    )


def _draw_in_outliner_context(self, context):
    """Outliner 右クリックメニューに B-MANGA サブメニューを差し込む.

    Outliner では Object 未選択でも常にサブメニューを出す (選択中の場合は
    詳細設定が有効、未選択は案内ラベルのみ)。
    """
    self.layout.separator()
    self.layout.menu(
        BMANGA_MT_object_context.bl_idname,
        icon="OUTLINER_OB_GROUP_INSTANCE",
    )


_CLASSES = (
    BMANGA_MT_layer_context,
    BMANGA_MT_selection_context,
    BMANGA_MT_object_context,
)


# Outliner の append 候補メニュー (Blender 5.1 で存在するもののみ append される)
_OUTLINER_MENUS = (
    "OUTLINER_MT_object",
    "OUTLINER_MT_collection",
    "OUTLINER_MT_context_menu",
    "OUTLINER_MT_asset",
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_object_context_menu.append(_draw_in_object_context)
    # Outliner の各種右クリックメニューにも同じサブメニューを差し込む
    for menu_name in _OUTLINER_MENUS:
        menu = getattr(bpy.types, menu_name, None)
        if menu is not None:
            try:
                menu.append(_draw_in_outliner_context)
            except (AttributeError, TypeError):
                pass


def unregister() -> None:
    try:
        bpy.types.VIEW3D_MT_object_context_menu.remove(_draw_in_object_context)
    except (ValueError, AttributeError):
        pass
    for menu_name in _OUTLINER_MENUS:
        menu = getattr(bpy.types, menu_name, None)
        if menu is None:
            continue
        try:
            menu.remove(_draw_in_outliner_context)
        except (ValueError, AttributeError):
            pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
