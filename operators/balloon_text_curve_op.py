"""フキダシ Curve + 画像 / テキスト表示 Object 化 operators."""

from __future__ import annotations

import bpy
from mathutils import Matrix, Vector

from ..utils import balloon_curve_object as bco
from ..utils import balloon_curve_source_state
from ..utils import image_real_object as iro
from ..utils import log
from ..utils import text_real_object as tro

_logger = log.get_logger(__name__)


def _find_balloon(context, page_id: str, balloon_id: str):
    work = getattr(context.scene, "bname_work", None)
    if work is None:
        return None, None
    target_page_id = str(page_id or "")
    target_balloon_id = str(balloon_id or "")
    for page in getattr(work, "pages", []) or []:
        if target_page_id and str(getattr(page, "id", "") or "") != target_page_id:
            continue
        for entry in getattr(page, "balloons", []) or []:
            if str(getattr(entry, "id", "") or "") == target_balloon_id:
                return page, entry
    for entry in getattr(work, "shared_balloons", []) or []:
        if str(getattr(entry, "id", "") or "") == target_balloon_id:
            return None, entry
    return None, None


def _curve_world_bounds_and_normalize(obj: bpy.types.Object) -> tuple[float, float, float, float] | None:
    curve = getattr(obj, "data", None)
    if obj is None or getattr(obj, "type", "") != "CURVE" or curve is None:
        return None
    matrix = obj.matrix_world.copy()
    coords: list[Vector] = []
    for spline in getattr(curve, "splines", []) or []:
        if str(getattr(spline, "type", "") or "") == "BEZIER":
            coords.extend(matrix @ point.co for point in getattr(spline, "bezier_points", []) or [])
        else:
            for point in getattr(spline, "points", []) or []:
                co = getattr(point, "co", None)
                if co is not None:
                    coords.append(matrix @ Vector((float(co[0]), float(co[1]), float(co[2]))))
    if not coords:
        return None
    min_x = min(v.x for v in coords)
    min_y = min(v.y for v in coords)
    max_x = max(v.x for v in coords)
    max_y = max(v.y for v in coords)
    origin = Vector((min_x, min_y, float(matrix.translation.z)))
    for spline in getattr(curve, "splines", []) or []:
        if str(getattr(spline, "type", "") or "") == "BEZIER":
            for point in getattr(spline, "bezier_points", []) or []:
                point.co = matrix @ point.co - origin
                point.handle_left = matrix @ point.handle_left - origin
                point.handle_right = matrix @ point.handle_right - origin
            continue
        for point in getattr(spline, "points", []) or []:
            co = getattr(point, "co", None)
            if co is None:
                continue
            normalized = matrix @ Vector((float(co[0]), float(co[1]), float(co[2]))) - origin
            point.co = (normalized.x, normalized.y, normalized.z, float(co[3]))
    obj.matrix_world = Matrix.Translation(origin)
    return min_x, min_y, max_x, max_y


def _page_offset_mm(context, work, page) -> tuple[float, float]:
    if work is None or page is None:
        return 0.0, 0.0
    try:
        from ..utils import page_grid

        page_id = str(getattr(page, "id", "") or "")
        for index, candidate in enumerate(getattr(work, "pages", []) or []):
            if str(getattr(candidate, "id", "") or "") == page_id:
                return page_grid.page_total_offset_mm(work, context.scene, index)
    except Exception:  # noqa: BLE001
        pass
    return 0.0, 0.0


class BNAME_OT_balloons_to_curve_all(bpy.types.Operator):
    bl_idname = "bname.balloons_to_curve_all"
    bl_label = "全フキダシを再生成"
    bl_description = "全 page.balloons を Bezier Curve として再生成します。"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from ..core.work import get_work

        work = get_work(context)
        return bool(work and getattr(work, "loaded", False))

    def execute(self, context):
        from ..core.work import get_work

        scene = context.scene
        work = get_work(context)
        n = 0
        for page in getattr(work, "pages", []):
            for entry in getattr(page, "balloons", []):
                if bco.ensure_balloon_curve_object(scene=scene, entry=entry, page=page):
                    n += 1
        self.report({"INFO"}, f"{n} 件のフキダシ Curve を生成")
        return {"FINISHED"}


class BNAME_OT_balloon_regenerate_keep_edit(bpy.types.Operator):
    bl_idname = "bname.balloon_regenerate_keep_edit"
    bl_label = "手編集を維持して再生成"
    bl_description = "制御点数と順序が一致する場合だけ、手編集差分を維持してフキダシ形状を再生成します。"
    bl_options = {"REGISTER", "UNDO"}

    page_id: bpy.props.StringProperty(name="ページID", default="", options={"HIDDEN"})  # type: ignore[valid-type]
    balloon_id: bpy.props.StringProperty(name="フキダシID", default="", options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        page, entry = _find_balloon(context, self.page_id, self.balloon_id)
        if entry is None:
            self.report({"WARNING"}, "フキダシが見つかりません")
            return {"CANCELLED"}
        obj = bco.ensure_balloon_curve_object(
            scene=context.scene,
            entry=entry,
            page=page,
            force_regenerate=True,
            preserve_manual_delta=True,
        )
        if obj is None:
            self.report({"WARNING"}, "フキダシを再生成できません")
            return {"CANCELLED"}
        self.report({"INFO"}, "フキダシを再生成しました")
        return {"FINISHED"}


class BNAME_OT_balloon_register_selected_curve(bpy.types.Operator):
    bl_idname = "bname.balloon_register_selected_curve"
    bl_label = "選択カーブをフキダシに登録"
    bl_description = "選択中のBlenderカーブを自由形状フキダシとして登録します。"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from ..core.work import get_work
        from ..utils import object_naming as on

        work = get_work(context)
        obj = getattr(context, "active_object", None)
        return bool(work and getattr(work, "loaded", False) and obj is not None and obj.type == "CURVE" and not on.is_managed(obj))

    def execute(self, context):
        from ..operators import balloon_op
        from ..utils import active_target, layer_stack as layer_stack_utils, object_naming as on, object_selection
        from ..utils.geom import m_to_mm

        obj = getattr(context, "active_object", None)
        bounds = _curve_world_bounds_and_normalize(obj)
        if obj is None or bounds is None:
            self.report({"WARNING"}, "登録できるカーブが見つかりません")
            return {"CANCELLED"}
        work = getattr(context.scene, "bname_work", None)
        parent_kind, parent_key, page = active_target.resolve_active_target(context)
        if page is None:
            self.report({"WARNING"}, "ページが選択されていません")
            return {"CANCELLED"}
        min_x, min_y, max_x, max_y = bounds
        offset_x, offset_y = _page_offset_mm(context, work, page)
        with bco.defer_auto_sync():
            entry = page.balloons.add()
            entry.id = balloon_op._allocate_balloon_id(page)
            entry.title = str(obj.name or "フキダシ")
            entry.shape = "custom"
            entry.x_mm = m_to_mm(min_x) - offset_x
            entry.y_mm = m_to_mm(min_y) - offset_y
            entry.width_mm = max(0.1, m_to_mm(max_x - min_x))
            entry.height_mm = max(0.1, m_to_mm(max_y - min_y))
            entry.parent_kind = parent_kind
            entry.parent_key = parent_key
            entry.selected = True
            page.active_balloon_index = len(page.balloons) - 1
        entry_id = str(getattr(entry, "id", "") or "")
        for candidate in getattr(page, "balloons", []) or []:
            if str(getattr(candidate, "id", "") or "") != entry_id and hasattr(candidate, "selected"):
                candidate.selected = False
        on.stamp_identity(
            obj,
            kind="balloon",
            bname_id=entry.id,
            title=entry.title,
            z_index=1000 + len(page.balloons) * 10,
            parent_key=parent_key,
        )
        balloon_curve_source_state.mark_freeform(obj)
        bco.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        object_selection.select_key(context, object_selection.balloon_key(page, entry), mode="single")
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        self.report({"INFO"}, "カーブをフキダシに登録しました")
        return {"FINISHED"}


class BNAME_OT_balloon_regenerate_discard_edit(bpy.types.Operator):
    bl_idname = "bname.balloon_regenerate_discard_edit"
    bl_label = "手編集を破棄して再生成"
    bl_description = "Blenderで直接編集した制御点を破棄し、詳細設定の形状として再生成します。"
    bl_options = {"REGISTER", "UNDO"}

    page_id: bpy.props.StringProperty(name="ページID", default="", options={"HIDDEN"})  # type: ignore[valid-type]
    balloon_id: bpy.props.StringProperty(name="フキダシID", default="", options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        page, entry = _find_balloon(context, self.page_id, self.balloon_id)
        if entry is None:
            self.report({"WARNING"}, "フキダシが見つかりません")
            return {"CANCELLED"}
        obj = bco.ensure_balloon_curve_object(
            scene=context.scene,
            entry=entry,
            page=page,
            force_regenerate=True,
            preserve_manual_delta=False,
        )
        if obj is None:
            self.report({"WARNING"}, "フキダシを再生成できません")
            return {"CANCELLED"}
        self.report({"INFO"}, "フキダシを再生成しました")
        return {"FINISHED"}


class BNAME_OT_images_to_empty_all(bpy.types.Operator):
    """画像レイヤーを実画像平面として登録する互換 Operator."""

    bl_idname = "bname.images_to_empty_all"
    bl_label = "全画像レイヤーを再登録"
    bl_description = "画像レイヤーを透明画像付きの平面として登録します。"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from ..core.work import get_work

        work = get_work(context)
        if not (work and getattr(work, "loaded", False)):
            return False
        return getattr(context.scene, "bname_image_layers", None) is not None

    def execute(self, context):
        from ..core.work import get_work

        scene = context.scene
        work = get_work(context)
        n = iro.sync_all_image_real_objects(scene, work)
        self.report({"INFO"}, f"{n} 件の画像レイヤーを再登録")
        return {"FINISHED"}


class BNAME_OT_texts_to_empty_all(bpy.types.Operator):
    """テキストレイヤーを実体付き表示 Object として登録."""

    bl_idname = "bname.texts_to_empty_all"
    bl_label = "全テキストを再登録"
    bl_description = (
        "テキストを透明画像付きの平面として登録します。"
        "編集用のカーソルや選択範囲は B-Name のオーバーレイで表示します。"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from ..core.work import get_work

        work = get_work(context)
        return bool(work and getattr(work, "loaded", False))

    def execute(self, context):
        from ..core.work import get_work

        scene = context.scene
        work = get_work(context)
        n = 0
        for page in getattr(work, "pages", []):
            for entry in getattr(page, "texts", []):
                if tro.ensure_text_real_object(scene=scene, entry=entry, page=page):
                    n += 1
        self.report({"INFO"}, f"{n} 件のテキスト表示 Object を登録")
        return {"FINISHED"}


# 後方互換: 旧 op 名 bname.texts_to_plane_all を新しい実体版にエイリアス。
# Blender 5.x では Operator サブクラスが親の bl_rna を継承すると
# 「unable to get Python class for RNA struct」警告が連発するため、
# 継承ではなく独立クラスとして定義し、execute は再実装してロジックを共有する。
class BNAME_OT_texts_to_plane_all(bpy.types.Operator):
    bl_idname = "bname.texts_to_plane_all"
    bl_label = "全テキストを再登録 (旧名)"
    bl_description = BNAME_OT_texts_to_empty_all.bl_description
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return BNAME_OT_texts_to_empty_all.poll(context)

    def execute(self, context):
        from ..core.work import get_work

        scene = context.scene
        work = get_work(context)
        n = 0
        for page in getattr(work, "pages", []):
            for entry in getattr(page, "texts", []):
                if tro.ensure_text_real_object(scene=scene, entry=entry, page=page):
                    n += 1
        self.report({"INFO"}, f"{n} 件のテキスト表示 Object を登録")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_balloons_to_curve_all,
    BNAME_OT_balloon_regenerate_keep_edit,
    BNAME_OT_balloon_register_selected_curve,
    BNAME_OT_balloon_regenerate_discard_edit,
    BNAME_OT_images_to_empty_all,
    BNAME_OT_texts_to_empty_all,
    BNAME_OT_texts_to_plane_all,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
