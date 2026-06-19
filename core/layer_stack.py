"""統合レイヤーリスト用の軽量 PropertyGroup."""

from __future__ import annotations

import bpy
from bpy.props import CollectionProperty, EnumProperty, IntProperty, StringProperty

from ..utils import log

_logger = log.get_logger(__name__)
_active_index_update_depth = 0
_visible_index_update_depth = 0

LAYER_KIND_ITEMS = (
    ("page", "ページ", ""),
    ("outside_group", "ページ外", ""),
    ("coma", "コマ", ""),
    ("coma_preview", "コマプレビュー", ""),
    ("gp", "グリースペンシル", ""),
    ("gp_folder", "フォルダ", ""),
    ("layer_folder", "汎用フォルダ", ""),
    ("image", "画像", ""),
    ("raster", "ラスター", ""),
    ("fill", "塗り", ""),
    ("balloon_group", "フキダシフォルダ", ""),
    ("balloon", "フキダシ", ""),
    ("text", "テキスト", ""),
    ("effect", "効果線", ""),
)

ACTIVE_LAYER_KIND_ITEMS = (
    ("page", "ページ", ""),
    ("coma", "コマ", ""),
    ("gp", "グリースペンシル", ""),
    ("gp_folder", "フォルダ", ""),
    ("layer_folder", "汎用フォルダ", ""),
    ("image", "画像", ""),
    ("raster", "ラスター", ""),
    ("fill", "塗り", ""),
    ("balloon", "フキダシ", ""),
    ("text", "テキスト", ""),
    ("effect", "効果線", ""),
)


class BMangaLayerStackItem(bpy.types.PropertyGroup):
    """統合レイヤーリストの 1 行。

    実データは GP / 画像 / ページ要素側に保持し、この行は参照キーと
    表示階層だけを持つ。前面→背面の表示順はこの CollectionProperty の
    並びで管理する。
    """

    kind: EnumProperty(name="種別", items=LAYER_KIND_ITEMS, default="gp")  # type: ignore[valid-type]
    name: StringProperty(name="名前", default="")  # type: ignore[valid-type]
    key: StringProperty(name="参照キー", default="")  # type: ignore[valid-type]
    label: StringProperty(name="表示名", default="")  # type: ignore[valid-type]
    parent_key: StringProperty(name="親キー", default="")  # type: ignore[valid-type]
    depth: IntProperty(name="階層", default=0, min=0)  # type: ignore[valid-type]


_CLASSES = (BMangaLayerStackItem,)
_visible_index_select_suppress_count = 0


def suppress_next_visible_index_select(count: int = 1) -> None:
    global _visible_index_select_suppress_count
    _visible_index_select_suppress_count = max(
        _visible_index_select_suppress_count,
        max(1, int(count)),
    )


def _on_active_layer_stack_index_changed(_self, context) -> None:
    """UIList の通常クリック/D&D選択を実データの選択状態へ反映する."""
    global _active_index_update_depth

    if _active_index_update_depth > 0:
        return
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    stack = getattr(scene, "bmanga_layer_stack", None)
    idx = int(getattr(scene, "bmanga_active_layer_stack_index", -1))
    if stack is None or not (0 <= idx < len(stack)):
        return
    try:
        from ..utils import layer_stack as layer_stack_utils
    except Exception:  # noqa: BLE001
        _logger.exception("layer stack utils import failed")
        return
    active_uid = ""
    try:
        active_uid = layer_stack_utils.stack_item_uid(stack[idx])
    except Exception:  # noqa: BLE001
        active_uid = ""
    _active_index_update_depth += 1
    try:
        order_changed = layer_stack_utils.apply_stack_order_if_ui_changed(
            context,
            moved_uid=active_uid,
        )
        if order_changed and active_uid:
            stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
            if stack is not None:
                for i, item in enumerate(stack):
                    if layer_stack_utils.stack_item_uid(item) == active_uid:
                        layer_stack_utils.select_stack_index(context, i)
                        return
        layer_stack_utils.select_stack_index(context, idx)
    except Exception:  # noqa: BLE001
        _logger.exception("active layer stack index update failed")
    finally:
        _active_index_update_depth -= 1


def _on_active_layer_stack_visible_index_changed(_self, context) -> None:
    """表示用一覧の選択を、実レイヤー一覧の選択へ反映する."""
    global _visible_index_select_suppress_count, _visible_index_update_depth

    if _visible_index_update_depth > 0:
        return
    if _visible_index_select_suppress_count > 0:
        _visible_index_select_suppress_count = max(0, _visible_index_select_suppress_count - 1)
        return
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    visible = getattr(scene, "bmanga_layer_stack_visible", None)
    idx = int(getattr(scene, "bmanga_active_layer_stack_visible_index", -1))
    if visible is None or not (0 <= idx < len(visible)):
        return
    try:
        from ..utils import layer_stack as layer_stack_utils
    except Exception:  # noqa: BLE001
        _logger.exception("visible layer stack utils import failed")
        return
    stack = getattr(scene, "bmanga_layer_stack", None)
    source_index = layer_stack_utils.find_stack_index_for_item(stack, visible[idx])
    if source_index < 0:
        return
    _visible_index_update_depth += 1
    try:
        layer_stack_utils.select_stack_index(context, source_index)
    except Exception:  # noqa: BLE001
        _logger.exception("visible layer stack index update failed")
    finally:
        _visible_index_update_depth -= 1


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bmanga_layer_stack = CollectionProperty(type=BMangaLayerStackItem)
    bpy.types.Scene.bmanga_active_layer_stack_index = IntProperty(
        default=-1,
        min=-1,
        update=_on_active_layer_stack_index_changed,
    )
    bpy.types.Scene.bmanga_layer_stack_visible = CollectionProperty(type=BMangaLayerStackItem)
    bpy.types.Scene.bmanga_active_layer_stack_visible_index = IntProperty(
        default=-1,
        min=-1,
        update=_on_active_layer_stack_visible_index_changed,
    )
    bpy.types.Scene.bmanga_active_layer_kind = EnumProperty(
        name="アクティブレイヤー種別",
        items=ACTIVE_LAYER_KIND_ITEMS,
        default="gp",
    )
    bpy.types.Scene.bmanga_active_gp_folder_key = StringProperty(default="")
    bpy.types.Scene.bmanga_active_layer_folder_key = StringProperty(default="")
    bpy.types.Scene.bmanga_active_effect_layer_name = StringProperty(default="")
    bpy.types.Scene.bmanga_layer_stack_inline_edit_uid = StringProperty(default="")
    bpy.types.Scene.bmanga_collapsed_balloon_group_keys = StringProperty(default="", options={"HIDDEN"})
    _logger.debug("layer_stack registered")


def unregister() -> None:
    for attr in (
        "bmanga_layer_stack_inline_edit_uid",
        "bmanga_collapsed_balloon_group_keys",
        "bmanga_active_effect_layer_name",
        "bmanga_active_layer_folder_key",
        "bmanga_active_gp_folder_key",
        "bmanga_active_layer_kind",
        "bmanga_active_layer_stack_visible_index",
        "bmanga_layer_stack_visible",
        "bmanga_active_layer_stack_index",
        "bmanga_layer_stack",
    ):
        try:
            delattr(bpy.types.Scene, attr)
        except AttributeError:
            pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
