"""プリセット UIList 表示用のプロパティとクラス."""

from __future__ import annotations

import bpy
from bpy.props import CollectionProperty, IntProperty, StringProperty
from bpy.types import PropertyGroup, UIList


class BMANGA_PresetListItem(PropertyGroup):
    identifier: StringProperty()  # type: ignore[valid-type]


_TYPE_ICON = {
    "border": "MESH_PLANE",
    "balloon": "MESH_CIRCLE",
    "text": "FONT_DATA",
    "effect_line": "FORCE_FORCE",
    "fill": "SNAP_FACE",
    "gradient": "NODE_TEXTURE",
    "image_path": "CURVE_BEZCURVE",
    "tail": "SHARPCURVE",
}


def _preset_type_from_active_property(active_property: str) -> str:
    s = active_property
    if s.startswith("bmanga_"):
        s = s[7:]
    if s.endswith("_preset_list_index"):
        s = s[:-18]
    return s


class BMANGA_UL_presets(UIList):
    bl_idname = "BMANGA_UL_presets"

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            preset_type = _preset_type_from_active_property(active_property)
            tool_icon = _TYPE_ICON.get(preset_type, "PREFERENCES")
            row = layout.row(align=True)
            row.label(text=item.name)
            preset_name = str(item.identifier or "")
            if preset_type == "balloon":
                # フキダシの一覧は「次に作る形状」セレクタ用の合成キー
                # (DEFAULT / mode:* / shape:* / custom:実名) を identifier に
                # 使うため、保存済みプリセットの実名へ変換できる行 (custom:)
                # だけ詳細編集ボタンを出す。組み込み形状の行には編集できる
                # プリセット実体が無い。
                preset_name = (
                    preset_name.split(":", 1)[1]
                    if preset_name.startswith("custom:")
                    else ""
                )
            if preset_name and preset_name not in {"NONE", "DEFAULT"}:
                op = row.operator(
                    "bmanga.preset_detail_edit",
                    text="",
                    icon=tool_icon,
                    emboss=False,
                )
                op.preset_type = preset_type
                op.preset_name = preset_name
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text="", icon="PRESET")


_SUPPRESS_INDEX_UPDATE: set[str] = set()

_SELECTOR_ATTRS = {
    "border": "bmanga_border_preset_selector",
    "balloon": "bmanga_balloon_tool_preset_selector",
    "text": "bmanga_text_tool_preset_selector",
    "effect_line": "bmanga_effect_line_tool_preset_selector",
    "fill": "bmanga_fill_tool_preset_selector",
    "gradient": "bmanga_gradient_tool_preset_selector",
    "image_path": "bmanga_image_path_tool_preset_selector",
    "tail": "bmanga_tail_preset_selector",
}

_ENUM_ITEM_GETTERS: dict | None = None


def _ensure_getters():
    global _ENUM_ITEM_GETTERS
    if _ENUM_ITEM_GETTERS is not None:
        return
    from ..operators import balloon_tail_detail_op, effect_line_preset_op, preset_op

    _ENUM_ITEM_GETTERS = {
        "border": preset_op._border_preset_enum_items,
        "balloon": preset_op._balloon_tool_preset_enum_items,
        "text": preset_op._text_preset_enum_items,
        "fill": preset_op._fill_tool_preset_enum_items,
        "gradient": preset_op._gradient_tool_preset_enum_items,
        "image_path": preset_op._image_path_tool_preset_enum_items,
        "effect_line": effect_line_preset_op._effect_line_tool_preset_enum_items,
        "tail": balloon_tail_detail_op._tail_preset_enum_items,
    }


def _get_enum_items(context, preset_type: str) -> list:
    _ensure_getters()
    assert _ENUM_ITEM_GETTERS is not None
    cb = _ENUM_ITEM_GETTERS.get(preset_type)
    if cb is None:
        return []
    try:
        return list(cb(None, context))
    except Exception:  # noqa: BLE001
        return []


def _make_index_update(preset_type: str, selector_attr: str):
    def _update(self, context):
        if preset_type in _SUPPRESS_INDEX_UPDATE:
            return
        col = getattr(self, f"bmanga_{preset_type}_preset_list", None)
        idx = getattr(self, f"bmanga_{preset_type}_preset_list_index", -1)
        if col is None or not (0 <= idx < len(col)):
            return
        ident = col[idx].identifier
        try:
            setattr(self, selector_attr, ident)
        except TypeError:
            pass

    return _update


def refresh_preset_list(context, preset_type: str) -> None:
    wm = getattr(context, "window_manager", None)
    if wm is None:
        return
    selector_attr = _SELECTOR_ATTRS.get(preset_type)
    if not selector_attr or not hasattr(wm, selector_attr):
        return

    col_attr = f"bmanga_{preset_type}_preset_list"
    idx_attr = f"bmanga_{preset_type}_preset_list_index"
    col = getattr(wm, col_attr, None)
    if col is None:
        return

    items = _get_enum_items(context, preset_type)

    current_ids = [it.identifier for it in col]
    new_ids = [it[0] for it in items]
    needs_rebuild = current_ids != new_ids

    if needs_rebuild:
        _SUPPRESS_INDEX_UPDATE.add(preset_type)
        try:
            col.clear()
            for enum_item in items:
                entry = col.add()
                entry.name = str(enum_item[1])
                entry.identifier = str(enum_item[0])
        finally:
            _SUPPRESS_INDEX_UPDATE.discard(preset_type)

    current_value = str(getattr(wm, selector_attr, "") or "")
    current_index = getattr(wm, idx_attr, -1)
    target_index = -1
    for i, it in enumerate(col):
        if it.identifier == current_value:
            target_index = i
            break
    if target_index >= 0 and target_index != current_index:
        _SUPPRESS_INDEX_UPDATE.add(preset_type)
        try:
            setattr(wm, idx_attr, target_index)
        finally:
            _SUPPRESS_INDEX_UPDATE.discard(preset_type)


_CLASSES = (BMANGA_PresetListItem, BMANGA_UL_presets)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    for ptype, sel_attr in _SELECTOR_ATTRS.items():
        setattr(
            bpy.types.WindowManager,
            f"bmanga_{ptype}_preset_list",
            CollectionProperty(type=BMANGA_PresetListItem),
        )
        setattr(
            bpy.types.WindowManager,
            f"bmanga_{ptype}_preset_list_index",
            IntProperty(update=_make_index_update(ptype, sel_attr)),
        )


def unregister():
    for ptype in _SELECTOR_ATTRS:
        for suffix in ("_preset_list_index", "_preset_list"):
            try:
                delattr(bpy.types.WindowManager, f"bmanga_{ptype}{suffix}")
            except AttributeError:
                pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
