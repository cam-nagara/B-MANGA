"""Grease Pencil v3 関連 Operator。

通常経路では「1 GP Object = 1 B-MANGAレイヤー」を正本にする。古い専用
オペレーターIDは既存キーマップ互換の薄い転送として残すが、内部レイヤーや
マスターGPは新規生成しない。
"""

from __future__ import annotations

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..utils import gpencil as gp_utils
from ..utils import detail_popup, geom, layer_object_model, layer_stack as layer_stack_utils, log, page_grid
from . import object_rotation_gp  # noqa: F401 (import時にgp回転ハンドラーを登録)

_logger = log.get_logger(__name__)

_GP_OBJECT_TYPE = "GREASEPENCIL"  # Blender 5.x の GP v3 オブジェクトタイプ
_GP_PAINT_MODE = "PAINT_GREASE_PENCIL"


def _active_gp_object(context):
    obj = context.active_object
    if obj is not None and obj.type == _GP_OBJECT_TYPE:
        return obj
    return None


def _target_gp_object(context):
    obj = _active_gp_object(context)
    if layer_object_model.is_layer_object(obj, "gp"):
        return obj
    item = layer_stack_utils.active_stack_item(context)
    if item is None or str(getattr(item, "kind", "") or "") != "gp":
        return None
    resolved = layer_stack_utils.resolve_stack_item(context, item)
    candidate = resolved.get("object") if resolved is not None else None
    return candidate if layer_object_model.is_layer_object(candidate, "gp") else None


def _create_gp_for_active_page(context, title: str = "レイヤー"):
    from ..utils import gp_object_layer
    from ..utils.layer_hierarchy import page_stack_key

    page = get_active_page(context)
    if page is None:
        return None
    parent_key = page_stack_key(page)
    bmanga_id = layer_object_model.make_stable_id("gp")
    return gp_object_layer.create_layer_gp_object(
        scene=context.scene,
        bmanga_id=bmanga_id,
        title=title,
        z_index=210,
        parent_kind="page",
        parent_key=parent_key,
    )


def _set_view_layer_active(context, obj) -> None:
    """view_layer.objects.active を安全に切替."""
    vl = context.view_layer
    if vl is None or obj is None:
        return
    try:
        for o in list(context.selected_objects):
            if o is not obj:
                o.select_set(False)
    except Exception:  # noqa: BLE001
        pass
    try:
        vl.objects.active = obj
    except Exception:  # noqa: BLE001
        _logger.exception("set active failed: %s", obj.name)
    try:
        obj.select_set(True)
    except Exception:  # noqa: BLE001
        pass


class BMANGA_OT_gpencil_page_ensure(Operator):
    """アクティブページの GP オブジェクトを確保して active 化.

    - ページ Collection が無ければ生成
    - GP オブジェクトが無ければ生成 + 既定レイヤー追加
    - view_layer の active を当該 GP に設定
    描画モードには切替しない (ユーザーの意図を尊重)。
    """

    bl_idname = "bmanga.gpencil_page_ensure"
    bl_label = "ページ用 GP を用意"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return work is not None and work.loaded and get_active_page(context) is not None

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            self.report({"ERROR"}, "ページが選択されていません")
            return {"CANCELLED"}
        try:
            candidates = [
                obj
                for obj in layer_object_model.iter_layer_objects("gp")
                if layer_object_model.parent_key(obj).split(":", 1)[0] == page.id
            ]
            obj = candidates[0] if candidates else _create_gp_for_active_page(context)
            if obj is None:
                raise RuntimeError("手描きレイヤーを作成できませんでした")
            work = get_work(context)
            if work is not None:
                page_grid.apply_page_collection_transforms(context, work)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("gpencil_page_ensure failed")
            self.report({"ERROR"}, f"GP 作成失敗: {exc}")
            return {"CANCELLED"}
        _set_view_layer_active(context, obj)
        self.report({"INFO"}, "手描きレイヤーを選択しました")
        return {"FINISHED"}


class BMANGA_OT_gpencil_layer_add(Operator):
    """個別の手描きレイヤーを追加する互換入口。"""

    bl_idname = "bmanga.gpencil_layer_add"
    bl_label = "レイヤー追加"
    bl_options = {"REGISTER", "UNDO"}

    layer_name: StringProperty(name="レイヤー名", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work is not None and work.loaded and get_active_page(context) is not None)

    def execute(self, context):
        result = bpy.ops.bmanga.layer_stack_add("EXEC_DEFAULT", kind="gp")
        if "FINISHED" not in result:
            return result
        obj = _target_gp_object(context)
        if obj is not None and self.layer_name.strip():
            layer_object_model.set_display_title(obj, self.layer_name.strip())
        return result


class BMANGA_OT_gpencil_layer_remove(Operator):
    """アクティブレイヤーを削除."""

    bl_idname = "bmanga.gpencil_layer_remove"
    bl_label = "レイヤー削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = _target_gp_object(context)
        if obj is None:
            return False
        return layer_object_model.is_layer_object(obj, "gp")

    def invoke(self, context, event):
        return detail_popup.invoke_confirm(context, event, self)

    def execute(self, context):
        obj = _target_gp_object(context)
        if obj is None:
            return {"CANCELLED"}
        _set_view_layer_active(context, obj)
        if hasattr(context.scene, "bmanga_active_layer_kind"):
            context.scene.bmanga_active_layer_kind = "gp"
        if not layer_object_model.remove_layer_object(obj):
            self.report({"ERROR"}, "手描きレイヤーを削除できませんでした")
            return {"CANCELLED"}
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return {"FINISHED"}


class BMANGA_OT_gpencil_layer_select(Operator):
    """安定IDまたは表示名で個別の手描きレイヤーを選択。"""

    bl_idname = "bmanga.gpencil_layer_select"
    bl_label = "レイヤー選択"
    bl_options = {"REGISTER"}

    layer_name: StringProperty(default="")  # type: ignore[valid-type]
    stable_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        obj = layer_object_model.find_layer_object("gp", self.stable_id)
        if obj is None and self.layer_name:
            obj = next(
                (
                    candidate
                    for candidate in layer_object_model.iter_layer_objects("gp")
                    if layer_object_model.display_title(candidate) == self.layer_name
                ),
                None,
            )
        if obj is None:
            return {"CANCELLED"}
        _set_view_layer_active(context, obj)
        if hasattr(context.scene, "bmanga_active_layer_kind"):
            context.scene.bmanga_active_layer_kind = "gp"
        layer = layer_object_model.content_layer(obj)
        if layer is None:
            return {"CANCELLED"}
        try:
            obj.data.layers.active = layer
        except Exception:  # noqa: BLE001
            return {"CANCELLED"}
        try:
            gp_utils.ensure_active_frame(layer)
            gp_utils.ensure_layer_material(obj, layer, activate=True, assign_existing=True)
        except Exception:  # noqa: BLE001
            _logger.exception("layer material setup failed")
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return {"FINISHED"}


class BMANGA_OT_gpencil_folder_add(Operator):
    """汎用レイヤーフォルダーを追加する互換入口。"""

    bl_idname = "bmanga.gpencil_folder_add"
    bl_label = "レイヤーフォルダ追加"
    bl_options = {"REGISTER", "UNDO"}

    parent_folder_name: StringProperty(default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work is not None and work.loaded)

    def execute(self, context):
        return bpy.ops.bmanga.layer_stack_add("EXEC_DEFAULT", kind="layer_folder")


class BMANGA_OT_gpencil_folder_remove(Operator):
    """選択中の汎用レイヤーフォルダーを削除する互換入口。"""

    bl_idname = "bmanga.gpencil_folder_remove"
    bl_label = "レイヤーフォルダ削除"
    bl_options = {"REGISTER", "UNDO"}

    folder_name: StringProperty(default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        item = layer_stack_utils.active_stack_item(context)
        return item is not None and getattr(item, "kind", "") == "layer_folder"

    def invoke(self, context, event):
        return detail_popup.invoke_confirm(context, event, self)

    def execute(self, context):
        return bpy.ops.bmanga.layer_stack_delete("EXEC_DEFAULT")


class BMANGA_OT_gpencil_layer_move_to_folder(Operator):
    """個別の手描きレイヤーを汎用フォルダーへ移す互換入口。"""

    bl_idname = "bmanga.gpencil_layer_move_to_folder"
    bl_label = "レイヤーをフォルダへ移動"
    bl_options = {"REGISTER", "UNDO"}

    layer_name: StringProperty(default="")  # type: ignore[valid-type]
    folder_name: StringProperty(default="")  # type: ignore[valid-type]
    stable_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    folder_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _target_gp_object(context) is not None

    def execute(self, context):
        from ..utils import layer_folder

        obj = layer_object_model.find_layer_object("gp", self.stable_id) or _target_gp_object(context)
        if obj is None:
            return {"CANCELLED"}
        folder_key = self.folder_id or self.folder_name
        if folder_key and layer_folder.find_folder(get_work(context), folder_key) is None:
            self.report({"ERROR"}, "移動先フォルダーが見つかりません")
            return {"CANCELLED"}
        layer_object_model.set_folder_id(obj, folder_key)
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return {"FINISHED"}


# ---------- cursor follow watcher (modal) ----------

# 切替のデッドゾーン (mm). これより短い距離で別ページ境界を跨いだときは
# 切替を行わない (境界近傍でのハンチング防止)。
_FOLLOW_DEAD_ZONE_MM = 3.0
# 更新スロットリング (秒). MOUSEMOVE イベント毎ではなく間引く。
_FOLLOW_THROTTLE_SEC = 0.1


_follow_state: dict = {
    "running": False,
    "last_update_time": 0.0,
    "last_x_mm": None,
    "last_y_mm": None,
    "last_page_id": None,
}


def _update_follow_from_event(context, event) -> None:
    """event の mouse 位置から active page + GP を逆引きして切替."""
    from bpy_extras.view3d_utils import region_2d_to_location_3d

    import time

    now = time.monotonic()
    if now - _follow_state["last_update_time"] < _FOLLOW_THROTTLE_SEC:
        return
    _follow_state["last_update_time"] = now

    scene = context.scene
    if scene is None or not getattr(scene, "bmanga_overview_mode", False):
        return
    try:
        from ..preferences import get_preferences

        prefs = get_preferences()
        if prefs is not None and not bool(prefs.gpencil_follow_cursor):
            return
    except Exception:  # noqa: BLE001
        pass

    work = get_work(context)
    if work is None or not work.loaded or len(work.pages) == 0:
        return

    screen = getattr(context, "screen", None)
    if screen is None:
        return
    mx = event.mouse_x
    my = event.mouse_y
    target_region = None
    target_rv3d = None
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type != "WINDOW":
                continue
            if not (
                region.x <= mx < region.x + region.width
                and region.y <= my < region.y + region.height
            ):
                continue
            space = area.spaces.active
            rv3d = getattr(space, "region_3d", None)
            if rv3d is None:
                continue
            target_region = region
            target_rv3d = rv3d
            break
        if target_region is not None:
            break
    if target_region is None:
        return

    local_mx = mx - target_region.x
    local_my = my - target_region.y
    loc = region_2d_to_location_3d(
        target_region, target_rv3d, (local_mx, local_my), (0.0, 0.0, 0.0)
    )
    if loc is None:
        return
    x_mm = geom.m_to_mm(loc.x)
    y_mm = geom.m_to_mm(loc.y)

    last_x = _follow_state["last_x_mm"]
    last_y = _follow_state["last_y_mm"]
    if last_x is not None and last_y is not None:
        if (
            abs(x_mm - last_x) < _FOLLOW_DEAD_ZONE_MM
            and abs(y_mm - last_y) < _FOLLOW_DEAD_ZONE_MM
        ):
            return
    _follow_state["last_x_mm"] = x_mm
    _follow_state["last_y_mm"] = y_mm

    page_idx = page_grid.page_index_at_world_mm(work, scene, x_mm, y_mm)
    if page_idx is None or not (0 <= page_idx < len(work.pages)):
        return
    page = work.pages[page_idx]
    if page.id == _follow_state["last_page_id"]:
        if work.active_page_index != page_idx:
            work.active_page_index = page_idx
        return
    _follow_state["last_page_id"] = page.id
    work.active_page_index = page_idx
    # 新仕様 (master GP) ではページ単位 GP の active 切替は不要。
    # active_page_index の更新だけで「現在のページ」UI は追従する。


class BMANGA_OT_gpencil_follow_modal(Operator):
    """カーソル追従 watcher の内部モーダルオペレータ.

    ユーザーは直接呼び出さない。``_follow_start()`` が起動する。
    - マウス移動 (MOUSEMOVE) でカーソル下のページをアクティブにする
    - 常に PASS_THROUGH を返して他のオペレータを邪魔しない

    注意: 以前は ``event_timer_add`` で 0.1 秒ごとの TIMER も拾っていたが、
    常駐 TIMER は「描画していなくても」「ウィンドウが非アクティブでも」毎 tick
    ビューポートを再描画させ続け、用紙ガイド線・効果線などの細線がずっと点滅
    する原因になっていた (TIMER イベントの type 読み取りで Event enum 警告も
    大量出力)。ページ追従は MOUSEMOVE だけで十分 (1 ストロークは 1 ページ内に
    収まるため描画中の追従は不要) なので、常駐 TIMER は撤去した。
    """

    bl_idname = "bmanga.gpencil_follow_modal"
    bl_label = "B-MANGA: GP 追従"
    bl_options = {"INTERNAL"}

    def modal(self, context, event):
        if not _follow_state["running"]:
            return {"CANCELLED"}
        if event.type == "MOUSEMOVE":
            try:
                _update_follow_from_event(context, event)
            except Exception:  # noqa: BLE001
                _logger.exception("follow modal tick failed")
        return {"PASS_THROUGH"}

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}


def _follow_start() -> None:
    if _follow_state["running"]:
        return
    _follow_state["running"] = True
    _follow_state["last_update_time"] = 0.0
    _follow_state["last_x_mm"] = None
    _follow_state["last_y_mm"] = None
    _follow_state["last_page_id"] = None
    # context に応じた invoke 呼出. window が無い場合はスキップ
    # (起動直後 register 時にこのパスを通る場合があるので無害)。
    try:
        if bpy.context.window is not None:
            bpy.ops.bmanga.gpencil_follow_modal("INVOKE_DEFAULT")
    except Exception:  # noqa: BLE001
        _logger.exception("follow_start: invoke failed")


def _follow_stop() -> None:
    _follow_state["running"] = False


class BMANGA_OT_gpencil_follow_cursor(Operator):
    """マウス位置追従 watcher の ON/OFF トグル.

    preferences.gpencil_follow_cursor に状態を保存する。
    """

    bl_idname = "bmanga.gpencil_follow_cursor"
    bl_label = "カーソル追従 GP"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            from ..preferences import get_preferences

            prefs = get_preferences()
        except Exception:  # noqa: BLE001
            prefs = None
        if prefs is not None:
            # prefs の update コールバックが _follow_start / _follow_stop を
            # 呼ぶため、ここでは値を書き換えるだけで十分。
            new_state = not bool(prefs.gpencil_follow_cursor)
            prefs.gpencil_follow_cursor = new_state
        else:
            # prefs が取得できないフォールバック: セッション内フラグで直接制御
            new_state = not _follow_state["running"]
            if new_state:
                _follow_start()
            else:
                _follow_stop()
        self.report({"INFO"}, f"カーソル追従 GP: {'ON' if new_state else 'OFF'}")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_gpencil_page_ensure,
    BMANGA_OT_gpencil_follow_modal,
    BMANGA_OT_gpencil_follow_cursor,
    BMANGA_OT_gpencil_layer_add,
    BMANGA_OT_gpencil_layer_remove,
    BMANGA_OT_gpencil_layer_select,
    BMANGA_OT_gpencil_folder_add,
    BMANGA_OT_gpencil_folder_remove,
    BMANGA_OT_gpencil_layer_move_to_folder,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    # register 時の自動起動は行わない。
    # 理由: モーダルオペレータを register 直後に起動してしまうと、アドオン
    # 無効化時 (unregister) にモーダルが動作中のままクラス解除が発生し、
    # Blender が C レベルでクラッシュする (Phase 2 実装で発生確認済)。
    # ユーザーは N パネル > Grease Pencil > 「切替」ボタンで任意に起動する。


def unregister() -> None:
    # 1) モーダル停止フラグを立てる (次の event tick で CANCELLED 終了する)
    _follow_stop()
    # 2) BMANGA_OT_gpencil_follow_modal は最後に unregister し、例外を握り潰す
    #    (走行中のモーダルが残っていても Blender がクラッシュしないよう防御)
    modal_cls = None
    other_classes = []
    for cls in _CLASSES:
        if cls.__name__ == "BMANGA_OT_gpencil_follow_modal":
            modal_cls = cls
        else:
            other_classes.append(cls)
    for cls in reversed(other_classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    if modal_cls is not None:
        try:
            bpy.utils.unregister_class(modal_cls)
        except Exception:  # noqa: BLE001 - Blender 内部エラー全般を握り潰し
            _logger.exception("follow_modal unregister skipped")
