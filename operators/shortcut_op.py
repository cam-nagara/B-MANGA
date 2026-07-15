"""キーボードショートカット用の小オペレータ群.

Preferences でキー割当を変更可能なショートカット:
- bmanga.set_mode_object  : O 既定 → アクティブを Object モードへ
- bmanga.set_mode_draw    : P 既定 → アクティブ GP を Draw モードへ
- bmanga.page_next        : COMMA 既定 → 次のページへフォーカス
- bmanga.page_prev        : PERIOD 既定 → 前のページへフォーカス
- bmanga.undo             : Z → Undo
- bmanga.redo             : X → Redo
- bmanga.toggle_eraser_brush : E → Eraser Hard / Eraser Stroke 切替
"""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..core.mode import MODE_PAGE, get_mode
from ..core.work import get_work
from ..utils import gpencil as gp_utils
from ..utils import layer_stack as layer_stack_utils
from ..utils import log, page_range
from ..utils import shortcut_visibility
from ..utils.geom import mm_to_m
from ..utils.page_grid import (
    _resolve_overview_params,
    page_grid_offset_mm,
)
from . import coma_modal_state

_logger = log.get_logger(__name__)

_GP_ERASER_HARD_ASSET = (
    "brushes/essentials_brushes-gp_draw.blend/Brush/Eraser Hard"
)
_GP_ERASER_STROKE_ASSET = (
    "brushes/essentials_brushes-gp_draw.blend/Brush/Eraser Stroke"
)


def _bmanga_work_loaded(context) -> bool:
    work = get_work(context)
    return bool(work is not None and work.loaded)


def _shortcuts_allowed(context) -> bool:
    return shortcut_visibility.shortcuts_allowed(context)


def _active_gp_paint_brush(context):
    obj = context.view_layer.objects.active if context.view_layer else None
    if obj is None or getattr(obj, "type", "") != "GREASEPENCIL":
        return None
    if getattr(obj, "mode", "") != "PAINT_GREASE_PENCIL":
        return None
    paint = getattr(context.tool_settings, "gpencil_paint", None)
    return getattr(paint, "brush", None) if paint is not None else None


def _finish_modal_tools_for_mode_switch(context) -> None:
    coma_modal_state.finish_all(context)


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


def _activate_gp_layer_for_drawing(context):
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
        _logger.exception("activate gp layer for drawing failed")
        return None
    return obj


# ---------- ツール切替 ----------


class BMANGA_OT_set_mode_object(Operator):
    """アクティブオブジェクトを Object ツールへ切替."""

    bl_idname = "bmanga.set_mode_object"
    bl_label = "オブジェクトツール"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.view_layer is not None and _shortcuts_allowed(context)

    def invoke(self, context, event):
        if coma_modal_state.event_blocked_by_inline_text_edit(event):
            return {"CANCELLED"}
        return self.execute(context)

    def execute(self, context):
        try:
            coma_modal_state.activate_object_tool(context)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("set_mode_object failed")
            self.report({"WARNING"}, f"切替不可: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


class BMANGA_OT_set_mode_draw(Operator):
    """選択中の GP レイヤーを描画ツール (PAINT_GREASE_PENCIL) へ切替."""

    bl_idname = "bmanga.set_mode_draw"
    bl_label = "描画ツール"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.view_layer is not None and _shortcuts_allowed(context)

    def invoke(self, context, event):
        if coma_modal_state.event_blocked_by_inline_text_edit(event):
            return {"CANCELLED"}
        return self.execute(context)

    def execute(self, context):
        _finish_modal_tools_for_mode_switch(context)
        if getattr(context.scene, "bmanga_active_layer_kind", "") == "raster":
            try:
                return bpy.ops.bmanga.raster_layer_paint_enter("EXEC_DEFAULT")
            except Exception as exc:  # noqa: BLE001
                self.report({"WARNING"}, f"切替不可: {exc}")
                return {"CANCELLED"}
        obj = _activate_gp_layer_for_drawing(context)
        if obj is None:
            self.report({"WARNING"}, "描画するグリースペンシルレイヤーを選択してください")
            return {"CANCELLED"}
        try:
            if obj.mode != "PAINT_GREASE_PENCIL":
                bpy.ops.object.mode_set(mode="PAINT_GREASE_PENCIL")
        except Exception as exc:  # noqa: BLE001
            _logger.exception("set_mode_draw failed")
            self.report({"WARNING"}, f"切替不可: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


# ---------- ページ移動 ----------


def _focus_view_to_page(context, work, page_index: int) -> None:
    """ビューを指定 page_index の grid 中心へ移動 (距離はキープ)."""
    scene = context.scene
    if scene is None:
        return
    cols, gap_x, gap_y, cw, ch = _resolve_overview_params(scene, work)
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")
    ox_mm, oy_mm = page_grid_offset_mm(
        page_index, cols, gap_x, cw, ch, start_side, read_direction,
        work=work, gap_y_mm=gap_y,
    )
    if 0 <= page_index < len(work.pages):
        add_x = float(getattr(work.pages[page_index], "offset_x_mm", 0.0))
        add_y = float(getattr(work.pages[page_index], "offset_y_mm", 0.0))
        ox_mm += add_x
        oy_mm += add_y
    cx = mm_to_m(ox_mm + cw / 2.0)
    cy = mm_to_m(oy_mm + ch / 2.0)

    moved = 0
    for area in context.screen.areas:
        if area.type != "VIEW_3D":
            continue
        space = area.spaces.active
        if space is None:
            continue
        rv3d = getattr(space, "region_3d", None)
        if rv3d is None:
            continue
        try:
            loc = rv3d.view_location.copy()
            loc.x = cx
            loc.y = cy
            rv3d.view_location = loc
            moved += 1
        except Exception:  # noqa: BLE001
            pass
        area.tag_redraw()
    if moved == 0:
        _logger.debug("focus_view_to_page: no VIEW_3D updated")


class BMANGA_OT_page_next(Operator):
    """active_page_index を +1 してビューをそのページにフォーカス (循環なし)."""

    bl_idname = "bmanga.page_next"
    bl_label = "次のページ"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return (
            work is not None
            and work.loaded
            and len(work.pages) > 0
            and get_mode(context) == MODE_PAGE
            and _shortcuts_allowed(context)
        )

    def execute(self, context):
        work = get_work(context)
        if work is None or len(work.pages) == 0:
            return {"CANCELLED"}
        active = int(getattr(work, "active_page_index", -1))
        new_idx = next(
            (
                i for i in range(max(0, active + 1), len(work.pages))
                if page_range.page_in_range(work.pages[i])
            ),
            -1,
        )
        if new_idx < 0:
            return {"CANCELLED"}
        work.active_page_index = new_idx
        _focus_view_to_page(context, work, new_idx)
        return {"FINISHED"}


class BMANGA_OT_page_prev(Operator):
    """active_page_index を -1 してビューをそのページにフォーカス (循環なし)."""

    bl_idname = "bmanga.page_prev"
    bl_label = "前のページ"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return (
            work is not None
            and work.loaded
            and len(work.pages) > 0
            and get_mode(context) == MODE_PAGE
            and _shortcuts_allowed(context)
        )

    def execute(self, context):
        work = get_work(context)
        if work is None or len(work.pages) == 0:
            return {"CANCELLED"}
        active = int(getattr(work, "active_page_index", -1))
        new_idx = next(
            (
                i for i in range(min(len(work.pages) - 1, active - 1), -1, -1)
                if page_range.page_in_range(work.pages[i])
            ),
            -1,
        )
        if new_idx < 0:
            return {"CANCELLED"}
        work.active_page_index = new_idx
        _focus_view_to_page(context, work, new_idx)
        return {"FINISHED"}


class BMANGA_OT_undo(Operator):
    """B-MANGA 有効時の単独 Z: Undo."""

    bl_idname = "bmanga.undo"
    bl_label = "戻る"
    bl_options = {"REGISTER"}

    def _run(self, context):
        if not bpy.ops.ed.undo.poll():
            return {"CANCELLED"}
        try:
            result = bpy.ops.ed.undo()
        except Exception as exc:  # noqa: BLE001
            _logger.exception("bmanga undo failed")
            self.report({"WARNING"}, f"Undo失敗: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"} if "FINISHED" in result else {"CANCELLED"}

    def invoke(self, context, event):
        if not _shortcuts_allowed(context):
            return {"PASS_THROUGH"}
        if coma_modal_state.event_blocked_by_inline_text_edit(event):
            # 本文入力中の Z は Undo ではなく文字入力 (テキスト編集側の
            # Ctrl+Z 履歴は text_edit_history が処理する)。
            return {"CANCELLED"}
        if not _bmanga_work_loaded(context):
            return {"PASS_THROUGH"}
        return self._run(context)

    def execute(self, context):
        if not _bmanga_work_loaded(context):
            return {"CANCELLED"}
        return self._run(context)


class BMANGA_OT_redo(Operator):
    """B-MANGA 有効時の単独 X: Redo."""

    bl_idname = "bmanga.redo"
    bl_label = "進む"
    bl_options = {"REGISTER"}

    def _run(self, context):
        if not bpy.ops.ed.redo.poll():
            return {"CANCELLED"}
        try:
            result = bpy.ops.ed.redo()
        except Exception as exc:  # noqa: BLE001
            _logger.exception("bmanga redo failed")
            self.report({"WARNING"}, f"Redo失敗: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"} if "FINISHED" in result else {"CANCELLED"}

    def invoke(self, context, event):
        if not _shortcuts_allowed(context):
            return {"PASS_THROUGH"}
        if coma_modal_state.event_blocked_by_inline_text_edit(event):
            return {"CANCELLED"}
        if not _bmanga_work_loaded(context):
            return {"PASS_THROUGH"}
        return self._run(context)

    def execute(self, context):
        if not _bmanga_work_loaded(context):
            return {"CANCELLED"}
        return self._run(context)


class BMANGA_OT_toggle_eraser_brush(Operator):
    """B-MANGA GP描画時の単独 E: Eraser Hard / Eraser Stroke を切替."""

    bl_idname = "bmanga.toggle_eraser_brush"
    bl_label = "消しゴム切替"
    bl_options = {"REGISTER"}

    def _run(self, context):
        brush = _active_gp_paint_brush(context)
        if brush is None:
            return {"CANCELLED"}
        current_name = getattr(brush, "name", "")
        next_asset = (
            _GP_ERASER_STROKE_ASSET
            if current_name == "Eraser Hard"
            else _GP_ERASER_HARD_ASSET
        )
        try:
            result = bpy.ops.brush.asset_activate(
                asset_library_type="ESSENTIALS",
                relative_asset_identifier=next_asset,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("toggle_eraser_brush failed")
            self.report({"WARNING"}, f"消しゴム切替失敗: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"} if "FINISHED" in result else {"CANCELLED"}

    def invoke(self, context, event):
        if not _shortcuts_allowed(context):
            return {"PASS_THROUGH"}
        if coma_modal_state.event_blocked_by_inline_text_edit(event):
            return {"CANCELLED"}
        if not _bmanga_work_loaded(context):
            return {"PASS_THROUGH"}
        brush = _active_gp_paint_brush(context)
        if brush is None:
            return {"PASS_THROUGH"}
        return self._run(context)

    def execute(self, context):
        if not _bmanga_work_loaded(context):
            return {"CANCELLED"}
        return self._run(context)


class BMANGA_OT_toggle_lasso_tool(Operator):
    """L キー: 選択ツールを Lasso ⇔ Box でトグル.

    B-MANGA 作品が開かれている時のみ動作。それ以外は PASS_THROUGH で
    Blender 標準 (Select Linked) に譲る。
    """

    bl_idname = "bmanga.toggle_lasso_tool"
    bl_label = "投げ縄ツール切替"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        if not _shortcuts_allowed(context):
            return {"PASS_THROUGH"}
        work = get_work(context)
        if work is None or not work.loaded:
            return {"PASS_THROUGH"}
        try:
            wm_tools = bpy.context.workspace.tools
            current_tool = None
            for t in wm_tools:
                # アクティブツールの id (mode/space に依存)
                if t.space_type == "VIEW_3D":
                    current_tool = t.idname
                    break
            new_tool = (
                "builtin.select_box"
                if current_tool == "builtin.select_lasso"
                else "builtin.select_lasso"
            )
            bpy.ops.wm.tool_set_by_id(name=new_tool)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("toggle_lasso_tool failed")
            self.report({"WARNING"}, f"ツール切替失敗: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


# ---------- Cut → 別レイヤー化 (Ctrl+X / Ctrl+V 上書き) ----------


# Cut → Paste 別レイヤー化のフラグ (module スコープ).
# scene custom property に置くと .blend に永続化され、Cut した状態で
# 保存→別ファイルを開いた最初の Paste で意図せず新レイヤーが作られる
# 不具合があるため、プロセス内変数として保持する。
_PASTE_TO_NEW_LAYER_FLAG = False


def _try_call_op(op_callable, *args, **kwargs) -> bool:
    """bpy.ops 呼び出しを try する (失敗時 False)."""
    try:
        result = op_callable(*args, **kwargs)
        return "FINISHED" in result
    except Exception:  # noqa: BLE001
        return False


def _gp_cut_to_clipboard(context) -> bool:
    """選択 GP ストロークをクリップボードへコピー + 削除.

    GP v3 / legacy で operator 名が異なるため複数候補を順に試す。
    """
    # クリップボードへコピー
    copied = False
    for op in (
        getattr(bpy.ops.grease_pencil, "copy", None),
        getattr(bpy.ops.gpencil, "copy", None),
    ):
        if op is None:
            continue
        if _try_call_op(op):
            copied = True
            break
    if not copied:
        return False
    # 選択削除
    for op in (
        getattr(bpy.ops.grease_pencil, "delete", None),
        getattr(bpy.ops.gpencil, "delete", None),
    ):
        if op is None:
            continue
        if _try_call_op(op):
            return True
    return True  # 削除に失敗してもコピーは成功


def _gp_paste_clipboard(context) -> bool:
    """クリップボードから GP ストロークを貼付."""
    for op in (
        getattr(bpy.ops.grease_pencil, "paste", None),
        getattr(bpy.ops.gpencil, "paste", None),
    ):
        if op is None:
            continue
        if _try_call_op(op):
            return True
    return False


def _create_gp_object_for_paste(context, source_obj):
    """切り取った線を受ける個別の手描きレイヤーを作る。"""
    from ..utils import gp_object_layer, layer_object_model

    if not layer_object_model.is_layer_object(source_obj, "gp"):
        return None
    parent_key = layer_object_model.parent_key(source_obj)
    folder_id = layer_object_model.folder_id(source_obj)
    obj = gp_object_layer.create_layer_gp_object(
        scene=context.scene,
        bmanga_id=layer_object_model.make_stable_id("gp"),
        title="貼り付け",
        z_index=layer_object_model.z_index(source_obj) + 1,
        parent_kind=(
            "folder"
            if folder_id
            else ("coma" if ":" in parent_key else ("page" if parent_key else "outside"))
        ),
        parent_key=parent_key,
        folder_id=folder_id,
    )
    if obj is None:
        return None
    source_materials = getattr(getattr(source_obj, "data", None), "materials", None)
    target_materials = getattr(getattr(obj, "data", None), "materials", None)
    if source_materials is not None and target_materials is not None:
        try:
            target_materials.clear()
            for material in source_materials:
                target_materials.append(material)
            gp_utils.ensure_unique_object_materials(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("paste_to_new_layer: material copy failed")
    layer = layer_object_model.content_layer(obj)
    if layer is not None:
        obj.data.layers.active = layer
        gp_utils.ensure_active_frame(layer, frame_number=context.scene.frame_current)
        gp_utils.ensure_layer_material(obj, layer, activate=True, assign_existing=True)
    return obj


def _activate_gp_object_for_paste(context, obj, mode: str) -> bool:
    try:
        if getattr(context.object, "mode", "OBJECT") != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for candidate in tuple(getattr(context, "selected_objects", ()) or ()):
            candidate.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        if mode != "OBJECT":
            bpy.ops.object.mode_set(mode=mode)
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("paste_to_new_layer: target activation failed")
        return False


def _discard_failed_paste_target(context, target_obj, source_obj, source_mode: str) -> None:
    from ..utils import layer_object_model

    try:
        if getattr(context.object, "mode", "OBJECT") != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:  # noqa: BLE001
        pass
    layer_object_model.remove_layer_object(target_obj)
    _activate_gp_object_for_paste(context, source_obj, source_mode)


class BMANGA_OT_gp_cut_to_new_layer(Operator):
    """Ctrl+X 上書き: 選択 GP ストロークを切り取り、次の Paste で新レイヤー化フラグを立てる.

    B-MANGA 作品が開かれていない、または GP 編集モードでない場合は
    PASS_THROUGH で標準 Cut に譲る。
    """

    bl_idname = "bmanga.gp_cut_to_new_layer"
    bl_label = "切り取り (新レイヤー予約)"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        if not _shortcuts_allowed(context):
            return {"PASS_THROUGH"}
        work = get_work(context)
        if work is None or not work.loaded:
            return {"PASS_THROUGH"}
        obj = context.view_layer.objects.active if context.view_layer else None
        if obj is None or obj.type != "GREASEPENCIL":
            return {"PASS_THROUGH"}
        if obj.mode not in {"EDIT", "PAINT_GREASE_PENCIL", "SCULPT_GREASE_PENCIL"}:
            return {"PASS_THROUGH"}
        ok = _gp_cut_to_clipboard(context)
        if not ok:
            self.report({"WARNING"}, "Cut 失敗 (選択ストロークがありませんか?)")
            return {"CANCELLED"}
        # 次の Paste で新レイヤー化するフラグ (module 変数: 永続化しない)
        global _PASTE_TO_NEW_LAYER_FLAG
        _PASTE_TO_NEW_LAYER_FLAG = True
        return {"FINISHED"}


class BMANGA_OT_gp_paste_to_new_layer(Operator):
    """Ctrl+V 上書き: フラグが立っていれば新規レイヤーを作成し、そこに paste.

    フラグが無い場合は通常 paste (現在レイヤーへ)。
    """

    bl_idname = "bmanga.gp_paste_to_new_layer"
    bl_label = "貼付 (新レイヤー)"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        if not _shortcuts_allowed(context):
            return {"PASS_THROUGH"}
        work = get_work(context)
        if work is None or not work.loaded:
            return {"PASS_THROUGH"}
        obj = context.view_layer.objects.active if context.view_layer else None
        if obj is None or obj.type != "GREASEPENCIL":
            return {"PASS_THROUGH"}
        scene = context.scene
        global _PASTE_TO_NEW_LAYER_FLAG
        source_obj = None
        source_mode = "OBJECT"
        target_obj = None
        if _PASTE_TO_NEW_LAYER_FLAG:
            source_obj = obj
            source_mode = str(getattr(source_obj, "mode", "OBJECT") or "OBJECT")
            target_obj = _create_gp_object_for_paste(context, source_obj)
            if target_obj is None or not _activate_gp_object_for_paste(
                context,
                target_obj,
                source_mode,
            ):
                if target_obj is not None:
                    _discard_failed_paste_target(
                        context,
                        target_obj,
                        source_obj,
                        source_mode,
                    )
                self.report({"ERROR"}, "貼り付け先の手描きレイヤーを作成できません")
                return {"CANCELLED"}
            obj = target_obj
        ok = _gp_paste_clipboard(context)
        if not ok:
            if source_obj is not None and target_obj is not None:
                _discard_failed_paste_target(
                    context,
                    target_obj,
                    source_obj,
                    source_mode,
                )
            self.report({"WARNING"}, "Paste 失敗 (クリップボード空?)")
            return {"CANCELLED"}
        _PASTE_TO_NEW_LAYER_FLAG = False
        scene.bmanga_active_layer_kind = "gp"
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return {"FINISHED"}


class BMANGA_OT_toggle_asset_shelf(Operator):
    """3D View のブラシアセットシェルフをカーソル位置に表示.

    Blender 5.x の Grease Pencil 描画モードで Space に既定割り当てされている
    ブラシ Asset Shelf を、C キー側に移すための wrapper。
    """

    bl_idname = "bmanga.toggle_asset_shelf"
    bl_label = "アセットシェルフ表示切替"
    bl_options = {"REGISTER"}

    @staticmethod
    def _shelf_name_from_context(context) -> str | None:
        mode_map = {
            "SCULPT": "VIEW3D_AST_brush_sculpt",
            "PAINT_VERTEX": "VIEW3D_AST_brush_vertex_paint",
            "PAINT_WEIGHT": "VIEW3D_AST_brush_weight_paint",
            "PAINT_TEXTURE": "VIEW3D_AST_brush_texture_paint",
            "PAINT_GREASE_PENCIL": "VIEW3D_AST_brush_gpencil_paint",
            "SCULPT_GREASE_PENCIL": "VIEW3D_AST_brush_gpencil_sculpt",
            "WEIGHT_GREASE_PENCIL": "VIEW3D_AST_brush_gpencil_weight",
            "VERTEX_GREASE_PENCIL": "VIEW3D_AST_brush_gpencil_vertex",
        }
        mode = getattr(context, "mode", "")
        if mode in mode_map:
            return mode_map[mode]
        obj = getattr(context, "object", None)
        obj_mode = getattr(obj, "mode", "")
        if obj_mode == "PAINT_GREASE_PENCIL":
            return "VIEW3D_AST_brush_gpencil_paint"
        if obj_mode == "SCULPT_GREASE_PENCIL":
            return "VIEW3D_AST_brush_gpencil_sculpt"
        if obj_mode == "WEIGHT_GREASE_PENCIL":
            return "VIEW3D_AST_brush_gpencil_weight"
        if obj_mode == "VERTEX_GREASE_PENCIL":
            return "VIEW3D_AST_brush_gpencil_vertex"
        return None

    @staticmethod
    def _find_view3d_area_region(context):
        area = context.area if context.area and context.area.type == "VIEW_3D" else None
        if area is None and context.screen is not None:
            for candidate in context.screen.areas:
                if candidate.type == "VIEW_3D":
                    area = candidate
                    break
        if area is None:
            return None, None
        region = context.region if context.region and context.region.type == "WINDOW" else None
        if region is None:
            for candidate in area.regions:
                if candidate.type == "WINDOW":
                    region = candidate
                    break
        return area, region

    def invoke(self, context, event):
        if not _shortcuts_allowed(context):
            return {"PASS_THROUGH"}
        shelf_name = self._shelf_name_from_context(context)
        area, region = self._find_view3d_area_region(context)
        if shelf_name and area is not None and region is not None:
            try:
                with context.temp_override(area=area, region=region):
                    result = bpy.ops.wm.call_asset_shelf_popover(
                        "INVOKE_DEFAULT",
                        name=shelf_name,
                    )
                if "FINISHED" in result:
                    return {"FINISHED"}
            except Exception:  # noqa: BLE001
                _logger.exception("toggle_asset_shelf: popup failed")
        return self.execute(context)

    def execute(self, context):
        area = context.area
        if area is None or area.type != "VIEW_3D":
            for a in context.screen.areas:
                if a.type == "VIEW_3D":
                    area = a
                    break
        if area is None:
            return {"CANCELLED"}
        space = area.spaces.active
        if space is None:
            return {"CANCELLED"}
        # Asset Shelf 領域の表示プロパティをトグル (Blender 5.x)
        for attr in ("show_region_asset_shelf", "show_region_tool_header"):
            if hasattr(space, attr) and attr == "show_region_asset_shelf":
                try:
                    setattr(space, attr, not getattr(space, attr))
                    area.tag_redraw()
                    return {"FINISHED"}
                except Exception:  # noqa: BLE001
                    pass
        # フォールバック: region.alignment 切替で表示/非表示
        for region in area.regions:
            if getattr(region, "type", "") == "ASSET_SHELF":
                try:
                    region.alignment = (
                        "NONE" if region.alignment != "NONE" else "BOTTOM"
                    )
                    area.tag_redraw()
                    return {"FINISHED"}
                except Exception:  # noqa: BLE001
                    pass
        return {"CANCELLED"}


_CLASSES = (
    BMANGA_OT_set_mode_object,
    BMANGA_OT_set_mode_draw,
    BMANGA_OT_page_next,
    BMANGA_OT_page_prev,
    BMANGA_OT_undo,
    BMANGA_OT_redo,
    BMANGA_OT_toggle_eraser_brush,
    BMANGA_OT_toggle_asset_shelf,
    BMANGA_OT_toggle_lasso_tool,
    BMANGA_OT_gp_cut_to_new_layer,
    BMANGA_OT_gp_paste_to_new_layer,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:  # noqa: BLE001
            pass
