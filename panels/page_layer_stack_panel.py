"""ページ毎レイヤースタック UI.

仕様:
- ページ選択ドロップダウン: 切替えるとアクティブページ + Outliner 選択も同期
- リストには「種別関係なく一列」 でレイヤーを表示
- 上下ボタンで重なり順を入替 (D&D は Blender API では Outliner 同等の体験を
  実装不可のため不採用)
- 並び順変更は ``bname_z_index`` の入替で永続化、 ``assign_per_page_z_ranks``
  経由で 3D ビューポートの Z 順に即反映する
"""

from __future__ import annotations

from typing import List, Tuple

import bpy
from bpy.props import EnumProperty, IntProperty
from bpy.types import Operator, Panel, UIList

from ..core.mode import MODE_COMA, get_mode
from ..core.work import get_work
from ..utils import object_naming as on
from ..utils import outliner_model as om

B_NAME_CATEGORY = "B-Name"


KIND_ICON = {
    "balloon": "MESH_CIRCLE",
    "effect": "LIGHT",
    "text": "FONT_DATA",
    "gp": "GREASEPENCIL",
    "image": "IMAGE_DATA",
    "raster": "TEXTURE",
    "coma": "MESH_PLANE",
    "folder": "FILE_FOLDER",
}


def _page_items_for_dropdown(_self, context):
    """ページドロップダウンの項目: 全ページ。"""
    work = get_work(context)
    items: list[tuple[str, str, str]] = []
    if work is None:
        return [("", "(作品なし)", "")]
    for i, page in enumerate(getattr(work, "pages", []) or []):
        page_id = str(getattr(page, "id", "") or "")
        title = str(getattr(page, "title", "") or page_id)
        if not page_id:
            continue
        items.append((page_id, f"{i + 1:04d} {title}", title))
    if not items:
        items.append(("", "(ページなし)", ""))
    return items


def _on_page_dropdown_changed(self, context):
    """ページドロップダウン変更時: アクティブページ + Outliner 選択を同期."""
    page_id = str(getattr(self, "bname_layer_panel_page_id", "") or "")
    if not page_id:
        return
    work = get_work(context)
    if work is None:
        return
    for i, page in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(page, "id", "") or "") == page_id:
            try:
                work.active_page_index = i
            except Exception:  # noqa: BLE001
                pass
            break
    # Outliner 側のページ Collection をアクティブにする
    coll = on.find_collection_by_bname_id(page_id, kind="page")
    if coll is None:
        return
    layer_coll = _find_layer_collection(context.view_layer.layer_collection, coll)
    if layer_coll is not None:
        try:
            context.view_layer.active_layer_collection = layer_coll
        except Exception:  # noqa: BLE001
            pass


def _find_layer_collection(root, target):
    if root is None or target is None:
        return None
    if root.collection is target:
        return root
    for child in getattr(root, "children", []) or []:
        hit = _find_layer_collection(child, target)
        if hit is not None:
            return hit
    return None


def _iter_page_layer_objects(page_id: str) -> List[bpy.types.Object]:
    """指定ページに属する管理 Object を Z 順 (上 = 手前) で並べて返す."""
    if not page_id:
        return []
    items: list[Tuple[int, int, bpy.types.Object]] = []
    from ..utils.layer_object_sync import _kind_base_z

    for obj in bpy.data.objects:
        if not bool(obj.get(on.PROP_MANAGED, False)):
            continue
        parent_key = str(obj.get(on.PROP_PARENT_KEY, "") or "")
        owner_page = parent_key.split(":", 1)[0] if parent_key else ""
        if owner_page != page_id:
            continue
        kind_base = _kind_base_z(obj)
        z_index = int(obj.get(on.PROP_Z_INDEX, 0) or 0)
        items.append((kind_base, z_index, obj))
    # ユーザー視点では「上 = 手前」なので、 base+z_index の大きい順で並べる
    items.sort(key=lambda x: (-x[0], -x[1], x[2].name))
    return [obj for _kb, _zi, obj in items]


class BNAME_UL_page_layers(UIList):
    """ページ内の全レイヤーを種別混在で 1 列に表示."""

    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_propname, _index):
        obj = getattr(item, "obj", None) if hasattr(item, "obj") else item
        if obj is None:
            layout.label(text="(なし)")
            return
        kind = str(obj.get(on.PROP_KIND, "") or "")
        title = str(obj.get(on.PROP_TITLE, "") or "") or obj.name
        icon = KIND_ICON.get(kind, "OBJECT_DATA")
        row = layout.row(align=True)
        row.label(text=title, icon=icon)


class BNAME_PT_page_layer_stack(Panel):
    """ページ毎のレイヤー順序制御パネル."""

    bl_idname = "BNAME_PT_page_layer_stack"
    bl_label = "ページレイヤー順"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 13

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        if not (work and getattr(work, "loaded", False)):
            return False
        return get_mode(context) != MODE_COMA

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        work = get_work(context)
        if work is None:
            return

        # ページドロップダウン
        row = layout.row(align=True)
        row.label(text="ページ", icon="DOCUMENTS")
        row.prop(scene, "bname_layer_panel_page_id", text="")

        page_id = str(getattr(scene, "bname_layer_panel_page_id", "") or "")
        if not page_id:
            # 初期値: アクティブページに自動設定
            active_idx = int(getattr(work, "active_page_index", -1))
            if 0 <= active_idx < len(work.pages):
                page_id = str(getattr(work.pages[active_idx], "id", "") or "")
                if page_id:
                    try:
                        scene.bname_layer_panel_page_id = page_id
                    except Exception:  # noqa: BLE001
                        pass

        # レイヤーリスト + 上下ボタン
        objs = _iter_page_layer_objects(page_id)
        # UIList の入力は CollectionProperty 必須 (動的 list 不可) のため、
        # template_list の代替として直接 layout で行を描画する。
        active_index = int(getattr(scene, "bname_layer_panel_active_index", -1))

        body = layout.row()
        col_list = body.column(align=True)
        if not objs:
            col_list.label(text="(レイヤーなし)")
        else:
            for i, obj in enumerate(objs):
                kind = str(obj.get(on.PROP_KIND, "") or "")
                title = str(obj.get(on.PROP_TITLE, "") or "") or obj.name
                icon = KIND_ICON.get(kind, "OBJECT_DATA")
                row = col_list.row(align=True)
                op = row.operator(
                    "bname.page_layer_select",
                    text=title,
                    icon=icon,
                    depress=(i == active_index),
                    emboss=(i == active_index),
                )
                op.layer_index = i
                op.page_id = page_id

        col_btns = body.column(align=True)
        col_btns.operator("bname.page_layer_move_up", text="", icon="TRIA_UP")
        col_btns.operator("bname.page_layer_move_down", text="", icon="TRIA_DOWN")


def _swap_layer_order(obj_a, obj_b) -> None:
    """隣接 2 レイヤーの並び順を入れ替える.

    種別関係なく入替可能にするため、 ``bname_z_kind_base`` (種別 base) と
    ``bname_z_index`` (種別内 rank) の両方を完全交換する。 これにより
    `_iter_page_layer_objects` のソート (kind_base, z_index) で位置が逆転
    する。
    """
    from ..utils.layer_object_sync import _kind_base_z, PROP_Z_KIND_BASE

    base_a = _kind_base_z(obj_a)
    base_b = _kind_base_z(obj_b)
    za = int(obj_a.get(on.PROP_Z_INDEX, 0) or 0)
    zb = int(obj_b.get(on.PROP_Z_INDEX, 0) or 0)
    if base_a == base_b and za == zb:
        # 完全同値は決定的並びのため、 b 側を 1 増やして分離
        zb = za + 1
    obj_a[PROP_Z_KIND_BASE] = base_b
    obj_b[PROP_Z_KIND_BASE] = base_a
    obj_a[on.PROP_Z_INDEX] = zb
    obj_b[on.PROP_Z_INDEX] = za


def _resync_layer_z(context) -> None:
    """Z 値 (location.z) を再計算 + viewport redraw."""
    from ..utils import layer_object_sync as los

    work = get_work(context)
    if work is None:
        return
    try:
        los.assign_per_page_z_ranks(context.scene, work)
    except Exception:  # noqa: BLE001
        pass
    for area in context.screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


class BNAME_OT_page_layer_select(Operator):
    bl_idname = "bname.page_layer_select"
    bl_label = "レイヤー選択"
    bl_options = {"INTERNAL"}

    layer_index: IntProperty(default=-1)  # type: ignore[valid-type]
    page_id: bpy.props.StringProperty(default="")  # type: ignore[valid-type]

    def execute(self, context):
        scene = context.scene
        scene.bname_layer_panel_active_index = int(self.layer_index)
        objs = _iter_page_layer_objects(self.page_id)
        if 0 <= self.layer_index < len(objs):
            obj = objs[self.layer_index]
            try:
                for o in context.selected_objects:
                    o.select_set(False)
                obj.select_set(True)
                context.view_layer.objects.active = obj
            except Exception:  # noqa: BLE001
                pass
        return {"FINISHED"}


class BNAME_OT_page_layer_move_up(Operator):
    bl_idname = "bname.page_layer_move_up"
    bl_label = "上へ移動"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(getattr(context.scene, "bname_layer_panel_page_id", ""))

    def execute(self, context):
        scene = context.scene
        page_id = str(getattr(scene, "bname_layer_panel_page_id", "") or "")
        idx = int(getattr(scene, "bname_layer_panel_active_index", -1))
        objs = _iter_page_layer_objects(page_id)
        if not (0 <= idx < len(objs)) or idx == 0:
            return {"CANCELLED"}
        # リスト上「上 = 手前 = 大 z」なので idx-1 が手前。 上ボタン = 手前へ。
        _swap_layer_order(objs[idx], objs[idx - 1])
        scene.bname_layer_panel_active_index = idx - 1
        _resync_layer_z(context)
        return {"FINISHED"}


class BNAME_OT_page_layer_move_down(Operator):
    bl_idname = "bname.page_layer_move_down"
    bl_label = "下へ移動"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(getattr(context.scene, "bname_layer_panel_page_id", ""))

    def execute(self, context):
        scene = context.scene
        page_id = str(getattr(scene, "bname_layer_panel_page_id", "") or "")
        idx = int(getattr(scene, "bname_layer_panel_active_index", -1))
        objs = _iter_page_layer_objects(page_id)
        if not (0 <= idx < len(objs)) or idx >= len(objs) - 1:
            return {"CANCELLED"}
        _swap_layer_order(objs[idx], objs[idx + 1])
        scene.bname_layer_panel_active_index = idx + 1
        _resync_layer_z(context)
        return {"FINISHED"}


_CLASSES = (
    BNAME_UL_page_layers,
    BNAME_OT_page_layer_select,
    BNAME_OT_page_layer_move_up,
    BNAME_OT_page_layer_move_down,
    BNAME_PT_page_layer_stack,
)


def register() -> None:
    bpy.types.Scene.bname_layer_panel_page_id = EnumProperty(
        name="ページ",
        description="レイヤーリストの対象ページ",
        items=_page_items_for_dropdown,
        update=_on_page_dropdown_changed,
    )
    bpy.types.Scene.bname_layer_panel_active_index = IntProperty(
        name="選択中のレイヤー (パネル内)",
        default=-1,
    )
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    try:
        del bpy.types.Scene.bname_layer_panel_page_id
    except AttributeError:
        pass
    try:
        del bpy.types.Scene.bname_layer_panel_active_index
    except AttributeError:
        pass
