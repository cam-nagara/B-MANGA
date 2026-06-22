"""Grease Pencil パネル — master GP (作品全ページ共通) のレイヤー管理 UI.

新仕様:
- 作品全体で 1 つの master GP オブジェクト (bmanga_master_sketch)
- 各レイヤーは複数ページに横断的に存在 (CSP のレイヤーパネル感覚)
- 「ページ GP 一覧」は廃止 (master GP 1 つだけなので不要)
- レイヤー行の種類アイコンから各種設定ダイアログを開く
- マテリアルは内部実装として隠し、ユーザーにはレイヤー設定だけを見せる
"""

from __future__ import annotations

import bpy
from bpy.types import Panel, UIList

from ..core.mode import MODE_COMA, get_mode
from ..core.work import get_work
from ..utils import gpencil as gp_utils
from ..utils import layer_links
from ..utils import layer_stack as layer_stack_utils
from ..utils import layer_stack_visible
from ..utils import log
from ..utils import page_file_scene
from ..utils.layer_hierarchy import split_child_key
from . import layer_stack_detail_ui

B_NAME_CATEGORY = "B-MANGA"
_GP_OBJECT_TYPE = "GREASEPENCIL"
_GP_PAINT_MODE = "PAINT_GREASE_PENCIL"
_GP_EDIT_MODE = "EDIT"
_GP_OBJECT_MODE = "OBJECT"
_LAYER_STACK_DEFAULT_ROWS = 6
_logger = log.get_logger(__name__)


def _master_gp_object():
    """master GP オブジェクト (なければ None)."""
    return gp_utils.get_master_gpencil()


def _active_gp_layer_target(context):
    scene = getattr(context, "scene", None)
    if scene is None or getattr(scene, "bmanga_active_layer_kind", "") != "gp":
        return None, None
    item = layer_stack_utils.active_stack_item(context)
    if item is None or getattr(item, "kind", "") != "gp":
        return None, None
    resolved = layer_stack_utils.resolve_stack_item(context, item)
    if resolved is None:
        return None, None
    obj = resolved.get("object")
    layer = resolved.get("target")
    if obj is None or layer is None:
        return None, None
    if gp_utils.layer_effectively_hidden(layer) or gp_utils.layer_effectively_locked(layer):
        return None, None
    return obj, layer


def _activate_gp_layer_for_tool(context):
    obj, layer = _active_gp_layer_target(context)
    if obj is None or layer is None:
        return None
    try:
        context.view_layer.objects.active = obj
        obj.select_set(True)
        obj.data.layers.active = layer
        gp_utils.ensure_active_frame(layer)
        gp_utils.ensure_layer_material(obj, layer, activate=True, assign_existing=True)
    except Exception:  # noqa: BLE001
        _logger.exception("activate gp layer for tool failed")
        return None
    return obj


def _get_prefs():
    try:
        from ..preferences import get_preferences

        return get_preferences()
    except Exception:  # noqa: BLE001
        return None


def _indent(row, depth: int) -> None:
    """階層インデント。縦方向に行を広げず、横幅だけを空ける."""
    if depth > 0:
        spacer = row.row(align=True)
        spacer.ui_units_x = 1.0 * depth
        spacer.label(text="")


def _kind_icon(kind: str) -> str:
    return {
        "page": "FILE_BLANK",
        "outside_group": "FILE_FOLDER",
        "coma": "MOD_WIREFRAME",
        "coma_preview": "IMAGE_DATA",
        "gp": "OUTLINER_OB_GREASEPENCIL",
        "gp_folder": "FILE_FOLDER",
        "layer_folder": "FILE_FOLDER",
        "image": "IMAGE_DATA",
        "raster": "BRUSH_DATA",
        "fill": "NODE_TEXTURE",
        "balloon_group": "FILE_FOLDER",
        "balloon": "MOD_FLUID",
        "text": "FONT_DATA",
        "effect": "STROKE",
    }.get(kind, "RENDERLAYERS")


def _show_stack_item_in_layer_list(item) -> bool:
    return True


def _layer_stack_template_rows(visible_rows: int) -> int:
    return max(1, min(int(visible_rows), _LAYER_STACK_DEFAULT_ROWS))


def _is_page_edit_context(context) -> bool:
    scene = getattr(context, "scene", None)
    return bool(scene is not None and page_file_scene.is_page_edit_scene(scene))


def _hide_icon(hidden: bool) -> str:
    return "HIDE_ON" if hidden else "HIDE_OFF"


def _gp_hidden(target) -> bool:
    try:
        return bool(gp_utils.layer_effectively_hidden(target))
    except Exception:  # noqa: BLE001
        return bool(getattr(target, "hide", False))


def _select_icon(row, index: int, icon: str) -> None:
    cell = row.row(align=True)
    cell.ui_units_x = 1.0
    cell.label(text="", icon=icon)


def _visibility_button(row, index: int, hidden: bool) -> None:
    cell = row.row(align=True)
    cell.ui_units_x = 1.0
    op = cell.operator(
        "bmanga.layer_stack_toggle_visibility",
        text="",
        icon=_hide_icon(hidden),
        emboss=False,
    )
    op.index = index


def _balloon_group_hidden(item, target) -> bool:
    if item is None or target is None:
        return False
    _page_key, group_id = split_child_key(str(getattr(item, "key", "") or ""))
    members = [
        entry
        for entry in getattr(target, "balloons", []) or []
        if str(getattr(entry, "merge_group_id", "") or "") == group_id
    ]
    if not members:
        return False
    return not any(bool(getattr(entry, "visible", True)) for entry in members)


def _draw_square_label(row, text: str = "", icon: str = "BLANK1") -> None:
    """1 ui-unit 幅の placeholder ラベル.

    旧実装は ``text`` も ``icon`` も無いケースで `cell.label(text="")` を呼んで
    いたが、空ラベルは描画幅がオペレーターボタンより僅かに小さくなり、同じ
    depth の行同士が左右にズレて見える原因になっていた。常に BLANK1 アイコンを
    指定して `cell.label(text=text, icon=icon)` を通すことで、可視ボタン (例:
    visibility/expand toggle) と同じ幅を保証する。
    """
    cell = row.row(align=True)
    cell.ui_units_x = 1.0
    cell.label(text=text, icon=icon)


def _draw_visibility_slot(row, item, target, index: int) -> None:
    row.alignment = "LEFT"
    if target is None:
        _draw_square_label(row)
    elif item.kind == "coma_preview" and hasattr(target, "paper_visible"):
        _visibility_button(row, index, not bool(target.paper_visible))
    elif item.kind in {"page", "coma"} and hasattr(target, "visible"):
        _visibility_button(row, index, not bool(target.visible))
    elif item.kind in {"image", "raster", "fill"} and hasattr(target, "visible"):
        _visibility_button(row, index, not bool(target.visible))
    elif item.kind == "balloon_group":
        _visibility_button(row, index, _balloon_group_hidden(item, target))
    elif item.kind in {"balloon", "text"} and hasattr(target, "visible"):
        _visibility_button(row, index, not bool(target.visible))
    elif item.kind in {"gp", "gp_folder", "effect"} and hasattr(target, "hide"):
        _visibility_button(row, index, _gp_hidden(target))
    else:
        _draw_square_label(row)


def _draw_selection_slot(row, index: int, selected: bool) -> None:
    """マルチセレクトのトグルボタン.

    通常クリック=単独選択 / Ctrl=トグル / Shift=範囲選択。invoke 側で event の
    修飾キーを見るため operator_context を INVOKE_DEFAULT にする。
    """
    cell = row.row(align=True)
    cell.ui_units_x = 1.0
    cell.operator_context = "INVOKE_DEFAULT"
    op = cell.operator(
        "bmanga.layer_stack_multi_select",
        text="",
        icon="RADIOBUT_ON" if selected else "RADIOBUT_OFF",
        emboss=False,
    )
    op.index = index
    op.anchor_index = int(getattr(bpy.context.scene, "bmanga_active_layer_stack_index", -1))


def _draw_hierarchy_slot(row, item, target, index: int) -> None:
    _indent(row, int(getattr(item, "depth", 0)))
    if target is None:
        return
    if item.kind == "page":
        expanded = bool(getattr(target, "stack_expanded", True))
        cell = row.row(align=True)
        cell.ui_units_x = 1.0
        op = cell.operator(
            "bmanga.layer_stack_toggle_expanded",
            text="",
            emboss=False,
            icon="DISCLOSURE_TRI_DOWN" if expanded else "DISCLOSURE_TRI_RIGHT",
        )
        op.index = index
    elif item.kind == "gp_folder":
        expanded = bool(getattr(target, "is_expanded", True))
        cell = row.row(align=True)
        cell.ui_units_x = 1.0
        op = cell.operator(
            "bmanga.layer_stack_toggle_expanded",
            text="",
            emboss=False,
            icon="DISCLOSURE_TRI_DOWN" if expanded else "DISCLOSURE_TRI_RIGHT",
        )
        op.index = index
    elif item.kind == "layer_folder":
        expanded = bool(getattr(target, "expanded", True))
        cell = row.row(align=True)
        cell.ui_units_x = 1.0
        op = cell.operator(
            "bmanga.layer_stack_toggle_expanded",
            text="",
            emboss=False,
            icon="DISCLOSURE_TRI_DOWN" if expanded else "DISCLOSURE_TRI_RIGHT",
        )
        op.index = index
    elif item.kind == "balloon_group":
        expanded = not layer_stack_visible.is_balloon_group_collapsed(bpy.context, item.key)
        cell = row.row(align=True)
        cell.ui_units_x = 1.0
        op = cell.operator(
            "bmanga.layer_stack_toggle_expanded",
            text="",
            emboss=False,
            icon="DISCLOSURE_TRI_DOWN" if expanded else "DISCLOSURE_TRI_RIGHT",
        )
        op.index = index
    else:
        return


def _draw_type_icon(row, index: int, icon: str) -> None:
    cell = row.row(align=True)
    cell.ui_units_x = 1.0
    cell.operator_context = "INVOKE_DEFAULT"
    op = cell.operator(
        "bmanga.layer_stack_detail",
        text="",
        icon=icon,
        emboss=False,
    )
    op.index = index


def _editable_name_prop(item, target) -> str | None:
    if item is None or target is None:
        return None
    kind = str(getattr(item, "kind", "") or "")
    if kind in {"page", "coma", "coma_preview", "outside_group"}:
        return None
    if kind in {"layer_folder", "image", "raster", "fill", "balloon", "text"} and hasattr(
        target, "title"
    ):
        return "title"
    if kind in {"gp", "gp_folder", "effect"} and hasattr(target, "name"):
        return "name"
    return None


def _draw_inline_name(row, item, target, prop_name: str) -> None:
    cell = row.row(align=True)
    cell.alignment = "LEFT"
    cell.prop(target, prop_name, text="", emboss=False)


def _is_inline_name_editing(item) -> bool:
    scene = getattr(bpy.context, "scene", None)
    if scene is None or item is None:
        return False
    uid = layer_stack_utils.stack_item_uid(item)
    editing_uid = str(getattr(scene, "bmanga_layer_stack_inline_edit_uid", "") or "")
    return bool(uid) and uid == editing_uid


def _is_active_name_row(index: int) -> bool:
    scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return False
    return int(getattr(scene, "bmanga_active_layer_stack_index", -1) or -1) == int(index)


def _select_name(row, index: int, text: str, item=None, target=None) -> None:
    prop_name = _editable_name_prop(item, target)
    if prop_name is not None and _is_inline_name_editing(item):
        _draw_inline_name(row, item, target, prop_name)
        return
    cell = row.row(align=True)
    cell.alignment = "LEFT"
    cell.operator_context = "INVOKE_DEFAULT"
    op = cell.operator(
        "bmanga.layer_stack_multi_select",
        text=str(text or "レイヤー"),
        emboss=False,
        depress=False,
    )
    op.index = index
    op.anchor_index = int(getattr(bpy.context.scene, "bmanga_active_layer_stack_index", -1))


def _select_icon_name(row, index: int, text: str, icon: str, item=None, target=None) -> None:
    _draw_type_icon(row, index, icon)
    _select_name(row, index, text, item=item, target=target)


def _link_state_icon(context, item) -> str:
    if item is None or not layer_links.is_linkable_item(item):
        return ""
    uid = layer_stack_utils.stack_item_uid(item)
    if not uid:
        return ""
    linked = layer_links.linked_uids_for_uid(context, uid)
    return "LINKED" if len(linked) > 1 else ""


def _draw_link_state_icon(row, context, item) -> None:
    icon = _link_state_icon(context, item)
    if not icon:
        return
    cell = row.row(align=True)
    cell.ui_units_x = 0.9
    cell.label(text="", icon=icon)


def _gp_color_style(layer):
    mat = None
    try:
        mat = bpy.data.materials.get(gp_utils._layer_material_name(layer))
    except Exception:  # noqa: BLE001
        mat = None
    return getattr(mat, "grease_pencil", None) if mat is not None else None


def _draw_square_color_prop(row, owner, prop_name: str | None = None) -> None:
    cell = row.row(align=True)
    cell.ui_units_x = 1.0
    if owner is None or prop_name is None or not hasattr(owner, prop_name):
        cell.label(text="")
        return
    cell.prop(owner, prop_name, text="", icon_only=True)


def _draw_square_placeholder(row) -> None:
    cell = row.row(align=True)
    cell.ui_units_x = 1.0
    cell.label(text="")


def _draw_right_aux_lock(row, target, prop_name: str = "lock") -> None:
    if target is None or not hasattr(target, prop_name):
        _draw_square_placeholder(row)
        return
    locked = bool(getattr(target, prop_name))
    cell = row.row(align=True)
    cell.ui_units_x = 1.0
    cell.prop(
        target,
        prop_name,
        text="",
        emboss=False,
        icon="LOCKED" if locked else "UNLOCKED",
    )


def _draw_right_aux_coma_enter(row, index: int) -> None:
    cell = row.row(align=True)
    cell.ui_units_x = 1.0
    op = cell.operator(
        "bmanga.layer_stack_enter_coma",
        text="",
        icon="PLAY",
        emboss=False,
    )
    op.stack_index = index


def _draw_right_controls(row, controls, index: int) -> None:
    if not controls.get("gp_style") and not controls.get("aux"):
        return
    slots = row.row(align=True)
    slots.alignment = "RIGHT"

    gp_style = controls.get("gp_style")
    if gp_style is not None:
        _draw_square_color_prop(slots, gp_style, "color")
        _draw_square_color_prop(slots, gp_style, "fill_color")

    aux = controls.get("aux")
    if aux == "coma_enter":
        _draw_right_aux_coma_enter(slots, index)
    elif aux == "lock":
        _draw_right_aux_lock(slots, controls.get("lock_target"), controls.get("lock_prop", "lock"))


def _draw_stack_gp_row(row, controls, item, resolved, index: int) -> None:
    target = resolved.get("target") if resolved is not None else None
    if target is None:
        _draw_type_icon(row, index, _kind_icon(item.kind))
        _select_name(row, index, item.label or item.name or item.key or "レイヤー", item=item)
        return
    _draw_type_icon(row, index, _kind_icon(item.kind))
    name = item.label if item.kind == "effect" and item.label else target.name
    if not str(name or "").strip():
        name = item.label or item.name or item.key or "レイヤー"
    _select_name(row, index, name, item=item, target=target)
    if item.kind == "gp":
        controls["gp_style"] = _gp_color_style(target)
    if item.kind == "gp" and hasattr(target, "lock"):
        controls["aux"] = "lock"
        controls["lock_target"] = target
        controls["lock_prop"] = "lock"


def _draw_stack_page_row(row, item, resolved, index: int, work=None) -> None:
    target = resolved.get("target") if resolved is not None else None
    if target is None:
        _select_icon_name(row, index, item.label, _kind_icon(item.kind), item=item)
        return
    icon = "DOCUMENTS" if target.spread else "FILE_BLANK"
    label = layer_stack_detail_ui.page_layer_name(target, work)
    title = str(getattr(target, "title", "") or "").strip()
    _select_icon_name(row, index, f"{label} {title}" if title else label, icon, item=item, target=target)


def _draw_stack_coma_row(row, controls, item, resolved, index: int) -> None:
    target = resolved.get("target") if resolved is not None else None
    if target is None:
        _select_icon_name(row, index, item.label, _kind_icon(item.kind), item=item)
        return
    title = str(getattr(target, "title", "") or "").strip()
    _draw_type_icon(row, index, "MOD_WIREFRAME")
    number_cell = row.row(align=True)
    number_cell.ui_units_x = 1.8
    number_cell.prop(target, "coma_number", text="")
    _select_name(row, index, title or "コマ", item=item, target=target)
    controls["aux"] = "coma_enter"


def _draw_stack_data_row(row, controls, item, resolved, index: int) -> None:
    target = resolved.get("target") if resolved is not None else None
    if item.kind == "outside_group":
        _select_icon(row, index, _kind_icon(item.kind))
        _select_name(row, index, item.label or "(ページ外)", item=item)
        return
    if item.kind == "coma_preview":
        _draw_type_icon(row, index, _kind_icon(item.kind))
        _select_name(row, index, item.label or "コマプレビュー", item=item)
        return
    if target is None:
        _draw_type_icon(row, index, _kind_icon(item.kind))
        _select_name(row, index, item.label or item.name or item.key or "レイヤー", item=item)
        return
    if item.kind == "layer_folder":
        _draw_type_icon(row, index, "FILE_FOLDER")
        _select_name(row, index, getattr(target, "title", "") or item.label, item=item, target=target)
    elif item.kind == "balloon_group":
        _draw_type_icon(row, index, "FILE_FOLDER")
        _select_name(row, index, item.label or "フキダシ結合", item=item, target=target)
    elif item.kind == "image":
        _draw_type_icon(row, index, "IMAGE_DATA")
        _select_name(row, index, getattr(target, "title", "") or item.label, item=item, target=target)
        controls["aux"] = "lock"
        controls["lock_target"] = target
        controls["lock_prop"] = "locked"
    elif item.kind == "raster":
        _draw_type_icon(row, index, "BRUSH_DATA")
        _select_name(row, index, getattr(target, "title", "") or item.label, item=item, target=target)
        controls["aux"] = "lock"
        controls["lock_target"] = target
        controls["lock_prop"] = "locked"
    elif item.kind == "fill":
        _draw_type_icon(row, index, "NODE_TEXTURE")
        _select_name(row, index, getattr(target, "title", "") or item.label, item=item, target=target)
        controls["aux"] = "lock"
        controls["lock_target"] = target
        controls["lock_prop"] = "locked"
    elif item.kind == "balloon":
        _draw_type_icon(row, index, "MOD_FLUID")
        _select_name(
            row,
            index,
            getattr(target, "title", "")
            or getattr(target, "id", "")
            or item.label
            or item.name
            or "フキダシ",
            item=item,
            target=target,
        )
    elif item.kind == "text":
        _draw_type_icon(row, index, "FONT_DATA")
        _select_name(
            row,
            index,
            getattr(target, "title", "") or getattr(target, "body", "") or item.label,
            item=item,
            target=target,
        )
    elif item.kind == "effect":
        _draw_stack_gp_row(row, controls, item, resolved, index)
    else:
        _draw_type_icon(row, index, _kind_icon(item.kind))
        _select_name(row, index, item.label or item.name or item.key or "レイヤー", item=item, target=target)


class BMANGA_UL_layer_stack(UIList):
    """統合レイヤーリスト。UIList の実CollectionをD&D並び替え対象にする."""

    bl_idname = "BMANGA_UL_layer_stack"

    def filter_items(self, context, data, propname):
        items = getattr(data, propname, None)
        if items is None:
            return [], []
        if propname == "bmanga_layer_stack_visible":
            return [self.bitflag_filter_item] * len(items), []
        work = get_work(context)
        if work is None or not getattr(work, "loaded", False):
            return [self.bitflag_filter_item] * len(items), []
        active_idx = int(getattr(work, "active_page_index", -1))
        active_page_key = ""
        if 0 <= active_idx < len(work.pages):
            active_page_key = layer_stack_utils.page_stack_key(work.pages[active_idx])
        flags = []
        for item in items:
            kind = getattr(item, "kind", "")
            if kind == "outside_group" or not _show_stack_item_in_layer_list(item):
                flags.append(0)
                continue
            if kind == "page":
                flags.append(0)
                continue
            page_key = layer_stack_utils._stack_item_page_key(item, context)
            flags.append(
                self.bitflag_filter_item
                if active_page_key and page_key == active_page_key
                else 0
            )
        return flags, []

    def draw_item(
        self,
        context,
        layout,
        data,
        item,
        icon,
        active_data,
        active_propname,
        index,
        flt_flag=0,
    ):
        if self.layout_type not in {"DEFAULT", "COMPACT"}:
            layout.label(text=item.label, icon=_kind_icon(item.kind))
            return
        stack = getattr(context.scene, "bmanga_layer_stack", None)
        source_index = layer_stack_utils.find_stack_index_for_item(stack, item)
        if source_index >= 0:
            item = stack[source_index]
            index = source_index
        row = layout.row(align=True)
        row.context_pointer_set("bmanga_layer_stack_item", item)
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        target = resolved.get("target") if resolved is not None else None
        _draw_visibility_slot(row, item, target, index)
        _draw_hierarchy_slot(row, item, target, index)
        _draw_link_state_icon(row, context, item)
        left = row.row(align=True)
        left.alignment = "LEFT"
        controls = {}
        if item.kind == "outside_group":
            _draw_stack_data_row(left, controls, item, resolved, index)
        elif item.kind == "page":
            _draw_stack_page_row(left, item, resolved, index, get_work(context))
        elif item.kind == "coma":
            _draw_stack_coma_row(left, controls, item, resolved, index)
        elif item.kind in {"gp", "gp_folder", "effect"}:
            _draw_stack_gp_row(left, controls, item, resolved, index)
        else:
            _draw_stack_data_row(left, controls, item, resolved, index)
        if controls.get("gp_style") or controls.get("aux"):
            right = row.row(align=True)
            right.alignment = "RIGHT"
            _draw_right_controls(right, controls, index)


class BMANGA_UL_layer_panel_pages(UIList):
    """レイヤーパネル内のページリスト。選択で下のレイヤー一覧を切り替える."""

    bl_idname = "BMANGA_UL_layer_panel_pages"

    def draw_item(
        self,
        context,
        layout,
        data,
        item,
        icon,
        active_data,
        active_propname,
        index,
    ):
        if self.layout_type not in {"DEFAULT", "COMPACT"}:
            layout.label(text=layer_stack_detail_ui.page_layer_name(item, get_work(context)))
            return
        row = layout.row(align=True)
        row.operator_context = "EXEC_DEFAULT"
        selected = int(getattr(data, active_propname, -1)) == int(index)
        icon_name = "IMGDISPLAY" if bool(getattr(item, "spread", False)) else "FILE_BLANK"
        label = layer_stack_detail_ui.page_layer_name(item, get_work(context))
        title = str(getattr(item, "title", "") or "").strip()
        if title:
            label = f"{label} {title}"
        icon_cell = row.row(align=True)
        icon_cell.ui_units_x = 1.0
        icon_cell.label(text="", icon=icon_name)
        name_cell = row.row(align=True)
        name_cell.alignment = "LEFT"
        op = name_cell.operator(
            "bmanga.page_select",
            text=label,
            emboss=False,
            depress=False,
        )
        op.index = index
        open_cell = row.row(align=True)
        open_cell.ui_units_x = 1.0
        open_op = open_cell.operator("bmanga.open_page_file", text="", icon="FILE_BLEND")
        open_op.index = index


def draw_stack_item_detail(layout, context, item, resolved) -> bool:
    return layer_stack_detail_ui.draw_stack_item_detail(layout, context, item, resolved)


def _draw_page_list_box(layout, context) -> None:
    work = get_work(context)
    box = layout.box()
    box.label(text="ページ", icon="FILE_BLANK")
    if work is None or not getattr(work, "loaded", False):
        box.label(text="(ページがありません)", icon="INFO")
        return
    row = box.row(align=True)
    rows = min(6, max(3, len(work.pages)))
    row.template_list(
        BMANGA_UL_layer_panel_pages.bl_idname,
        "",
        work,
        "pages",
        work,
        "active_page_index",
        rows=rows,
    )
    if _is_page_edit_context(context):
        return
    tools = row.column(align=True)
    tools.ui_units_x = 1.25
    tools.operator("bmanga.open_page_file", text="", icon="FILE_BLEND")
    tools.separator()
    tools.operator("bmanga.page_add", text="", icon="ADD")
    tools.operator("bmanga.page_duplicate", text="", icon="DUPLICATE")
    tools.operator("bmanga.page_remove", text="", icon="REMOVE")
    tools.separator()
    op = tools.operator("bmanga.page_move", text="", icon="TRIA_UP")
    op.direction = -1
    op = tools.operator("bmanga.page_move", text="", icon="TRIA_DOWN")
    op.direction = 1
    spread = layout.box()
    spread.label(text="見開き")
    row = spread.row(align=True)
    row.operator("bmanga.pages_merge_spread", text="変更", icon="ARROW_LEFTRIGHT")
    row.operator("bmanga.pages_split_spread", text="解除", icon="UNLINKED")
    idx = work.active_page_index
    if 0 <= idx < len(work.pages) and work.pages[idx].spread:
        spread.label(text=f"間隔 {work.pages[idx].tombo_gap_mm:.2f}mm")


def _draw_layer_stack_box(layout, context) -> None:
    scene = context.scene
    box = layout.box()
    box.label(text="レイヤー", icon="RENDERLAYERS")
    try:
        layer_stack_utils.schedule_layer_stack_draw_maintenance(context)
    except Exception as exc:  # noqa: BLE001
        _logger.exception("layer stack panel draw failed")
        box.label(text="レイヤー一覧を更新できません", icon="ERROR")
        box.label(text=str(exc)[:80])
        return
    stack = getattr(scene, "bmanga_layer_stack", None)
    if stack is None:
        box.label(text="(レイヤーがありません)")
    else:
        layer_area = box.row(align=True)
        visible_stack = getattr(scene, "bmanga_layer_stack_visible", None)
        visible_rows = len(visible_stack) if visible_stack is not None else 0
        rows = _layer_stack_template_rows(visible_rows)
        layer_area.template_list(
            BMANGA_UL_layer_stack.bl_idname,
            "",
            scene,
            "bmanga_layer_stack_visible",
            scene,
            "bmanga_active_layer_stack_visible_index",
            rows=rows,
            maxrows=30,
            sort_lock=True,
        )

        tools = layer_area.column(align=True)
        tools.ui_units_x = 1.25
        add_menu = tools.operator("wm.call_menu", text="", icon="ADD")
        add_menu.name = "BMANGA_MT_layer_stack_add"
        tools.operator("bmanga.layer_stack_duplicate", text="", icon="DUPLICATE")
        tools.operator("bmanga.layer_stack_link_selected", text="", icon="LINKED")
        tools.operator("bmanga.asset_register_layers", text="", icon="ASSET_MANAGER")
        tools.operator("bmanga.layer_stack_delete", text="", icon="REMOVE")
        tools.separator()
        op = tools.operator("bmanga.layer_stack_move", text="", icon="TRIA_UP_BAR")
        op.direction = "FRONT"
        op = tools.operator("bmanga.layer_stack_move", text="", icon="TRIA_UP")
        op.direction = "UP"
        op = tools.operator("bmanga.layer_stack_move", text="", icon="TRIA_DOWN")
        op.direction = "DOWN"
        op = tools.operator("bmanga.layer_stack_move", text="", icon="TRIA_DOWN_BAR")
        op.direction = "BACK"


def _draw_layer_stack_context_menu(self, context) -> None:
    item = getattr(context, "bmanga_layer_stack_item", None)
    stack = getattr(getattr(context, "scene", None), "bmanga_layer_stack", None)
    if stack is None:
        return
    index = -1
    if item is None:
        index = int(getattr(context.scene, "bmanga_active_layer_stack_index", -1))
        if 0 <= index < len(stack):
            item = stack[index]
    else:
        for i, stack_item in enumerate(stack):
            if layer_stack_utils.stack_item_uid(stack_item) == layer_stack_utils.stack_item_uid(item):
                index = i
                break
    if item is None:
        return
    uid = layer_stack_utils.stack_item_uid(item)
    if not uid:
        return
    layout = self.layout
    layout.separator()
    op = layout.operator("bmanga.layer_stack_detail", text="詳細設定", icon="PREFERENCES")
    op.index = index
    op.uid = uid
    op.offset_from_selection = True


def _visible_layer_stack_entries(context, stack) -> list[tuple[int, object]]:
    return layer_stack_utils.visible_layer_stack_entries(context, stack)


def _draw_layer_stack_rows(layout, context, stack) -> None:
    entries = _visible_layer_stack_entries(context, stack)
    if not entries:
        layout.label(text="(レイヤーがありません)", icon="INFO")
        return
    for index, item in entries:
        row = layout.row(align=True)
        row.context_pointer_set("bmanga_layer_stack_item", item)
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        target = resolved.get("target") if resolved is not None else None
        _draw_visibility_slot(row, item, target, index)
        _draw_hierarchy_slot(row, item, target, index)
        _draw_link_state_icon(row, context, item)
        left = row.row(align=True)
        left.alignment = "LEFT"
        controls = {}
        if item.kind == "outside_group":
            _draw_stack_data_row(left, controls, item, resolved, index)
        elif item.kind == "page":
            _draw_stack_page_row(left, item, resolved, index, get_work(context))
        elif item.kind == "coma":
            _draw_stack_coma_row(left, controls, item, resolved, index)
        elif item.kind in {"gp", "gp_folder", "effect"}:
            _draw_stack_gp_row(left, controls, item, resolved, index)
        else:
            _draw_stack_data_row(left, controls, item, resolved, index)
        if controls.get("gp_style") or controls.get("aux"):
            right = row.row(align=True)
            right.alignment = "RIGHT"
            _draw_right_controls(right, controls, index)


class BMANGA_PT_page_list(Panel):
    """作品ファイルでのページリスト。ページ選択・追加・並べ替えを行う."""

    bl_idname = "BMANGA_PT_page_list"
    bl_label = "ページ"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 22

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(
            work and work.loaded
            and get_mode(context) != MODE_COMA
            and not _is_page_edit_context(context)
        )

    def draw(self, context):
        _draw_page_list_box(self.layout, context)


class BMANGA_PT_layer_stack(Panel):
    """統合レイヤーリスト。画像/GP/フキダシ/テキスト/効果線をここに集約する."""

    bl_idname = "BMANGA_PT_layer_stack"
    bl_label = "レイヤー"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 22

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work and work.loaded and _is_page_edit_context(context))

    def draw(self, context):
        layout = self.layout
        work = get_work(context)
        if work is None or not work.loaded:
            layout.label(text="作品を開いてください", icon="INFO")
            return
        _draw_layer_stack_box(layout, context)


class BMANGA_PT_gpencil(Panel):
    """master GP のモード / 描画色管理 UI."""

    bl_idname = "BMANGA_PT_gpencil"
    bl_label = "Grease Pencil"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 23
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return get_mode(context) != MODE_COMA

    def draw(self, context):
        layout = self.layout
        work = get_work(context)

        # --- カーソル追従トグル (active_page_index 追従用) ---
        prefs = _get_prefs()
        if prefs is not None:
            box = layout.box()
            row = box.row(align=True)
            row.label(text="カーソル追従", icon="RESTRICT_SELECT_OFF")
            row.prop(prefs, "gpencil_follow_cursor", text="")
            row.operator("bmanga.gpencil_follow_cursor", text="切替")

        if work is None or not work.loaded:
            layout.label(text="作品を開いてください", icon="INFO")
            return

        # master GP の確保ボタン
        layout.operator(
            "bmanga.gpencil_master_ensure",
            text="マスター GP を用意",
            icon="OUTLINER_OB_GREASEPENCIL",
        )

        obj = _master_gp_object()
        if obj is None:
            layout.label(text="(マスター GP が未生成です)", icon="INFO")
            return

        row = layout.row(align=True)
        row.label(text=obj.name, icon="OUTLINER_OB_GREASEPENCIL")

        # ブラシ (描画モード時のみ)
        if obj.mode == _GP_PAINT_MODE:
            ts = context.tool_settings
            paint = None
            for attr in (
                "gpencil_paint",
                "grease_pencil_paint",
                "gpencil_v3_paint",
            ):
                paint = getattr(ts, attr, None)
                if paint is not None:
                    break
            if paint is not None:
                brush_box = layout.box()
                brush_box.label(text="ブラシ", icon="BRUSH_DATA")
                try:
                    brush_box.template_ID(paint, "brush")
                except Exception:  # noqa: BLE001
                    if getattr(paint, "brush", None) is not None:
                        brush_box.label(text=paint.brush.name)
                brush = getattr(paint, "brush", None)
                if brush is not None:
                    if hasattr(brush, "size"):
                        brush_box.prop(brush, "size")
                    if hasattr(brush, "strength"):
                        brush_box.prop(brush, "strength")


class BMANGA_OT_gpencil_master_ensure(bpy.types.Operator):
    """master GP オブジェクトを ensure (生成 or 既存取得) して active 化."""

    bl_idname = "bmanga.gpencil_master_ensure"
    bl_label = "マスター GP を用意"
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        if scene is None:
            return {"CANCELLED"}
        try:
            obj = gp_utils.ensure_master_gpencil(scene)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"master GP 生成失敗: {exc}")
            return {"CANCELLED"}
        vl = context.view_layer
        if vl is not None and obj is not None:
            try:
                vl.objects.active = obj
                obj.select_set(True)
            except Exception:  # noqa: BLE001
                pass
        return {"FINISHED"}


class BMANGA_OT_gpencil_master_mode_set(bpy.types.Operator):
    """master GP を必ず active 化してからツールを切り替える wrapper.

    UI のモード切替ボタンは ``bpy.ops.object.mode_set`` を直接呼ぶと、
    view_layer.objects.active が master GP でない場合に意図しない
    オブジェクトのモードが切り替わる。この wrapper で必ず master GP を
    active 化してから mode_set を呼ぶ。
    """

    bl_idname = "bmanga.gpencil_master_mode_set"
    bl_label = "B-MANGAツール切替"
    bl_options = {"REGISTER", "INTERNAL"}

    mode: bpy.props.StringProperty(default="OBJECT")  # type: ignore[valid-type]

    @classmethod
    def description(cls, _context, properties):
        mode = getattr(properties, "mode", "OBJECT")
        if mode == "OBJECT":
            return "オブジェクトツールに切り替えます"
        if mode == "PAINT_GREASE_PENCIL":
            return "描画ツールに切り替えます"
        if mode == "EDIT":
            return "グリースペンシル編集モードに切り替えます"
        return "B-MANGAツールを切り替えます"

    def execute(self, context):
        try:
            from ..operators import coma_modal_state

            coma_modal_state.finish_all(context)
            # ラスター Texture Paint / 別 GP 描画中なら、 切替前に確実に退出
            # (PNG 自動保存 + paper_bg 再表示も含む)。 同モードへの再入は no-op。
            if self.mode != _GP_PAINT_MODE:
                coma_modal_state.exit_drawing_mode(context)
            else:
                obj_active = getattr(getattr(context, "view_layer", None), "objects", None)
                obj_active = getattr(obj_active, "active", None) if obj_active is not None else None
                if getattr(obj_active, "mode", "") == "TEXTURE_PAINT":
                    coma_modal_state.exit_drawing_mode(context)
        except Exception:  # noqa: BLE001
            pass
        if self.mode in {_GP_PAINT_MODE, _GP_EDIT_MODE}:
            obj = _activate_gp_layer_for_tool(context)
            if obj is None:
                self.report({"WARNING"}, "グリースペンシルレイヤーを選択してください")
                return {"CANCELLED"}
        else:
            obj = gp_utils.get_master_gpencil()
        if obj is None and self.mode == _GP_OBJECT_MODE:
            try:
                obj = gp_utils.ensure_master_gpencil(context.scene)
            except Exception:  # noqa: BLE001
                return {"CANCELLED"}
        vl = context.view_layer
        if vl is not None:
            try:
                vl.objects.active = obj
                obj.select_set(True)
            except Exception:  # noqa: BLE001
                pass
        try:
            bpy.ops.object.mode_set(mode=self.mode)
        except Exception as exc:  # noqa: BLE001
            self.report({"WARNING"}, f"モード切替失敗: {exc}")
            return {"CANCELLED"}
        if self.mode == _GP_OBJECT_MODE:
            try:
                bpy.ops.bmanga.object_tool("INVOKE_DEFAULT")
            except Exception:  # noqa: BLE001
                pass
        return {"FINISHED"}


_REMOVED_PANEL_CLASSES = (
    BMANGA_PT_gpencil,
)

_CLASSES = (
    BMANGA_OT_gpencil_master_ensure,
    BMANGA_OT_gpencil_master_mode_set,
    BMANGA_UL_layer_panel_pages,
    BMANGA_UL_layer_stack,
    BMANGA_PT_page_list,
    BMANGA_PT_layer_stack,
)


def _unregister_removed_panels() -> None:
    for cls in _REMOVED_PANEL_CLASSES:
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


def register() -> None:
    _unregister_removed_panels()
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    try:
        bpy.types.UI_MT_list_item_context_menu.append(_draw_layer_stack_context_menu)
    except Exception:  # noqa: BLE001
        pass


def unregister() -> None:
    try:
        bpy.types.UI_MT_list_item_context_menu.remove(_draw_layer_stack_context_menu)
    except Exception:  # noqa: BLE001
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _unregister_removed_panels()
