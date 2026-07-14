"""カスタム右クリックコンテキストメニュー.

Outliner / 3D ビュー / 各ツール (フキダシ/テキスト/効果線/Object/枠線) で
右クリック時に B-MANGA レイヤーの「詳細設定」ダイアログを開けるようにする。
active_object の ``bmanga_kind`` / ``bmanga_id`` を見て kind ごとの詳細を
``bmanga.layer_detail_open`` operator で表示する。
"""

from __future__ import annotations

import bpy
from bpy.types import Menu

from ..utils import detail_target_resolver
from ..utils import layer_stack as layer_stack_utils
from ..utils import object_naming as on
from ..utils import page_file_scene, shortcut_visibility


def _normalize_detail_object(obj):
    if not isinstance(obj, bpy.types.Object):
        return None
    try:
        from ..utils import detail_target_resolver

        obj = detail_target_resolver.normalize_effect_controller_object(obj)
    except Exception:  # メニュー描画を壊さず、通常の管理Object判定へ戻す
        pass
    return obj if obj is not None and on.is_managed(obj) else None


def _detail_operator_identity(context, obj) -> tuple[str, str]:
    """右クリックしたObjectを、ページを含む固定対象IDへ正規化する。"""

    try:
        from ..utils import detail_target_resolver

        target = detail_target_resolver.resolve_target_from_object(context, obj)
        return target.stable_id, target.kind
    except Exception:  # メニュー自体は維持し、実行時の厳密検証に委ねる
        return on.get_bmanga_id(obj), on.get_kind(obj)


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
    obj = _normalize_detail_object(getattr(context, "active_object", None))
    if obj is not None:
        return obj
    # 2. selected_objects (3D ビューや Outliner で選択中)
    selected = getattr(context, "selected_objects", None) or ()
    for candidate in selected:
        obj = _normalize_detail_object(candidate)
        if obj is not None:
            return obj
    # 3. selected_ids (Outliner の context で利用可能)
    selected_ids = getattr(context, "selected_ids", None) or ()
    for candidate in selected_ids:
        obj = _normalize_detail_object(candidate)
        if obj is not None:
            return obj
    # 4. view_layer.active (Outliner の active)
    view_layer = getattr(context, "view_layer", None)
    if view_layer is not None:
        obj = _normalize_detail_object(getattr(view_layer, "active", None))
        if obj is not None:
            return obj
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


def _is_coma_file_context(context) -> bool:
    try:
        role, _page_id, _coma_id = page_file_scene.current_role(context)
        if role == page_file_scene.ROLE_COMA:
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        return shortcut_visibility.current_blend_is_coma_blend()
    except Exception:  # noqa: BLE001
        return False


def _active_or_selected_object(context):
    obj = getattr(context, "active_object", None)
    if isinstance(obj, bpy.types.Object):
        return obj
    selected = getattr(context, "selected_objects", None) or ()
    for obj in selected:
        if isinstance(obj, bpy.types.Object):
            return obj
    selected_ids = getattr(context, "selected_ids", None) or ()
    for sid in selected_ids:
        if isinstance(sid, bpy.types.Object):
            return sid
    view_layer = getattr(context, "view_layer", None)
    active = getattr(view_layer, "active", None) if view_layer is not None else None
    if isinstance(active, bpy.types.Object):
        return active
    return None


def _managed_object_matches_stack_item(obj, item) -> bool:
    if obj is None or item is None:
        return False
    kind = str(getattr(item, "kind", "") or "")
    key = str(getattr(item, "key", "") or "")
    obj_kind = on.get_kind(obj)
    obj_id = on.get_bmanga_id(obj)
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


def _active_page_spread_state(context) -> dict:
    work = getattr(getattr(context, "scene", None), "bmanga_work", None)
    target, resolved = _active_stack_target(context)
    if work is None or not bool(getattr(work, "loaded", False)):
        return {"page": None, "index": -1, "can_merge": False, "can_split": False}
    if not resolved or str(resolved.get("kind", "") or "") != "page" or target is None:
        return {"page": None, "index": -1, "can_merge": False, "can_split": False}
    page_index = int(resolved.get("index", -1))
    pages = getattr(work, "pages", []) or []
    if not (0 <= page_index < len(pages)):
        return {"page": None, "index": -1, "can_merge": False, "can_split": False}
    page = pages[page_index]
    next_page = pages[page_index + 1] if page_index + 1 < len(pages) else None
    can_split = bool(getattr(page, "spread", False))
    can_merge = bool(
        next_page is not None
        and not getattr(page, "spread", False)
        and not getattr(next_page, "spread", False)
    )
    return {
        "page": page,
        "index": page_index,
        "can_merge": can_merge,
        "can_split": can_split,
    }


def selection_command_items(context) -> list[dict]:
    """選択中レイヤー向け右クリックメニュー項目を返す.

    実際の Menu 描画と実機テストの両方で使い、項目の抜けを防ぐ。
    """
    item = _active_stack_item_no_sync(context)
    kind = str(getattr(item, "kind", "") or "")
    has_item = item is not None
    normalized_kind = kind
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
    resolved = layer_stack_utils.resolve_stack_item(context, item) if item is not None else None
    resolved_target = resolved.get("target") if resolved is not None else None
    can_open_detail = detail_target_resolver.can_open_actual_detail(kind, resolved_target)
    detail_operator = "bmanga.layer_stack_detail"
    detail_props = {
        "uid": layer_stack_utils.stack_item_uid(item) if item is not None else "",
    }
    if can_open_detail and kind in {
        "image",
        "image_path",
        "raster",
        "fill",
        "balloon",
        "text",
        "gp",
        "effect",
    }:
        detail_obj = _active_managed_object_for_stack_item(context, item)
        if detail_obj is not None:
            try:
                object_target = detail_target_resolver.resolve_target_from_object(
                    context, detail_obj
                )
            except Exception:  # 実体を固定解決できないObjectには入口を表示しない
                object_target = None
            if detail_target_resolver.can_open_actual_detail(kind, object_target):
                detail_operator = "bmanga.layer_detail_open"
                detail_id, detail_kind = _detail_operator_identity(context, detail_obj)
                detail_props = {
                    "bmanga_id": detail_id,
                    "kind": detail_kind,
                }
    items = []
    if can_open_detail:
        items.append({
            "label": "詳細設定",
            "operator": detail_operator,
            "icon": "PREFERENCES",
            "enabled": True,
            "props": detail_props,
        })
    items.extend([
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
    ])
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
    if normalized_kind == "page":
        spread_state = _active_page_spread_state(context)
        page_index = int(spread_state.get("index", -1))
        items.extend([
            {
                "label": "見開きに変更",
                "operator": "bmanga.pages_merge_spread",
                "icon": "ARROW_LEFTRIGHT",
                "enabled": bool(spread_state.get("can_merge", False)),
                "props": {"left_index": page_index},
            },
            {
                "label": "見開きを解除",
                "operator": "bmanga.pages_split_spread",
                "icon": "UNLINKED",
                "enabled": bool(spread_state.get("can_split", False)),
                "props": {"spread_index": page_index},
            },
        ])
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


def _apply_menu_operator_props(op, item: dict) -> None:
    for prop_name, value in dict(item.get("props", {}) or {}).items():
        try:
            setattr(op, str(prop_name), value)
        except Exception:  # noqa: BLE001
            pass


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
            _apply_menu_operator_props(op, item)
        elif op_id == "bmanga.layer_detail_open":
            row.operator_context = "INVOKE_DEFAULT"
            op = row.operator(op_id, text=label, icon=icon)
            _apply_menu_operator_props(op, item)
        else:
            row.operator_context = "INVOKE_DEFAULT"
            op = row.operator(op_id, text=label, icon=icon)
            _apply_menu_operator_props(op, item)
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
        try:
            detail_target = detail_target_resolver.resolve_target_from_object(context, obj)
        except Exception:  # 管理Objectでも詳細実体が無いものには入口を出さない
            detail_target = None
        if detail_target_resolver.can_open_actual_detail(kind, detail_target):
            detail_row = layout.row()
            detail_row.operator_context = "INVOKE_DEFAULT"
            detail_op = detail_row.operator(
                "bmanga.layer_detail_open", text="詳細設定", icon="PREFERENCES"
            )
            detail_op.bmanga_id, detail_op.kind = _detail_operator_identity(context, obj)
        # フキダシ / 効果線の場合はリンク複製も
        if kind in {"balloon", "effect"}:
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


def _operator_exists(op_id: str):
    try:
        namespace, name = op_id.split(".", 1)
        return getattr(getattr(bpy.ops, namespace), name, None)
    except Exception:  # noqa: BLE001
        return None


def _operator_poll(op_id: str) -> bool:
    op = _operator_exists(op_id)
    if op is None:
        return False
    try:
        return bool(op.poll())
    except Exception:  # noqa: BLE001
        return True


def _draw_link_file_commands(layout, context) -> bool:
    _ = context
    drew = False
    if _operator_exists("bmanga.open_link_source") is not None:
        row = layout.row()
        row.enabled = _operator_poll("bmanga.open_link_source")
        row.operator(
            "bmanga.open_link_source",
            text="リンク元ファイルを開く",
            icon="FILE_BLEND",
        )
        drew = True
    if _operator_exists("bmanga.record_asset_link") is not None:
        row = layout.row()
        row.enabled = _operator_poll("bmanga.record_asset_link")
        row.operator(
            "bmanga.record_asset_link",
            text="このリンクを記録",
            icon="LINKED",
        )
        drew = True
    return drew


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
        if _is_coma_file_context(context):
            _draw_link_file_commands(layout, context)
            return
        _draw_layer_commands(layout, context)
        if (
            _operator_exists("bmanga.open_link_source") is not None
            or _operator_exists("bmanga.record_asset_link") is not None
        ):
            layout.separator()
            _draw_link_file_commands(layout, context)


def _draw_in_object_context(self, context):
    """3D ビュー Object 右クリックメニューに B-MANGA サブメニューを差し込む."""
    if _is_coma_file_context(context):
        if not shortcut_visibility.bmanga_panel_visible(context):
            return
        if _active_or_selected_object(context) is None:
            return
        self.layout.separator()
        self.layout.menu(
            BMANGA_MT_object_context.bl_idname,
            icon="OUTLINER_OB_GROUP_INSTANCE",
        )
        return
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

    Outliner の Object 右クリックメニューにだけ差し込む。コマ用blendファイルでは
    サブメニュー内をリンク操作だけにする。
    """
    if _active_or_selected_object(context) is None:
        return
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


# Outliner の append 先。Object 以外にも同じ関数を入れると、Blender 側で
# 複数メニューが合成された時に B-MANGA サブメニューが二重に出る。
_OUTLINER_APPEND_MENUS = (
    "OUTLINER_MT_object",
)

# 旧バージョンが append していた候補。register/unregister 時に掃除する。
_OUTLINER_CLEANUP_MENUS = (
    "OUTLINER_MT_object",
    "OUTLINER_MT_collection",
    "OUTLINER_MT_context_menu",
    "OUTLINER_MT_asset",
)


def _remove_menu_callback(menu, callback) -> None:
    for _ in range(8):
        try:
            menu.remove(callback)
        except (ValueError, AttributeError, TypeError):
            break


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _remove_menu_callback(bpy.types.VIEW3D_MT_object_context_menu, _draw_in_object_context)
    bpy.types.VIEW3D_MT_object_context_menu.append(_draw_in_object_context)
    for menu_name in _OUTLINER_CLEANUP_MENUS:
        menu = getattr(bpy.types, menu_name, None)
        if menu is not None:
            _remove_menu_callback(menu, _draw_in_outliner_context)
    for menu_name in _OUTLINER_APPEND_MENUS:
        menu = getattr(bpy.types, menu_name, None)
        if menu is not None:
            try:
                menu.append(_draw_in_outliner_context)
            except (AttributeError, TypeError):
                pass


def unregister() -> None:
    _remove_menu_callback(bpy.types.VIEW3D_MT_object_context_menu, _draw_in_object_context)
    for menu_name in _OUTLINER_CLEANUP_MENUS:
        menu = getattr(bpy.types, menu_name, None)
        if menu is None:
            continue
        _remove_menu_callback(menu, _draw_in_outliner_context)
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
