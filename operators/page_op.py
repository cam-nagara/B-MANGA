"""ページ追加・削除・複製・並び替え・選択の Operator.

ページ一覧ファイルでは、ページ追加・削除・複製・並び替えを行う。
ページの中身編集はページ用blendファイルで行い、コマ編集モード遷移のみ
``operators.mode_op`` で cNN.blend を開閉する。
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import IntProperty
from bpy.types import Operator

from ..core.mode import MODE_PAGE, get_mode, set_mode
from ..core.work import get_work
from ..io import page_io, work_io
from ..utils import gpencil as gp_utils
from ..utils import layer_stack as layer_stack_utils
from ..utils import edge_selection, log, page_browser, page_grid, page_range, paths
from ..utils import page_file_scene
from ..utils import shortcut_visibility

_logger = log.get_logger(__name__)
_PAGE_PICK_LAYER_KINDS = {"text", "balloon", "effect", "image", "gp", "raster"}


def _require_loaded(op: Operator, work) -> bool:
    if work is None or not work.loaded or not work.work_dir:
        op.report({"ERROR"}, "作品が開かれていません")
        return False
    return True


def _sync_layer_stack_after_page_change(context, *, align_page_order: bool = False) -> None:
    try:
        layer_stack_utils.sync_layer_stack(context, align_page_order=align_page_order)
        layer_stack_utils.remember_layer_stack_signature(context)
        layer_stack_utils.tag_view3d_redraw(context)
    except Exception:  # noqa: BLE001
        _logger.exception("page op: layer stack sync failed")


def _sync_page_number_range(work_dir: Path, work) -> None:
    page_range.sync_end_number_to_page_count(work)
    work_io.save_work_json(work_dir, work)


def _set_page_layer_active(context) -> None:
    if hasattr(context.scene, "bname_active_layer_kind"):
        context.scene.bname_active_layer_kind = "page"
    edge_selection.clear_selection(context)


def _page_offsets_by_id(context, work) -> dict[str, tuple[float, float]]:
    return {
        str(page.id): page_grid.page_total_offset_mm(work, context.scene, i)
        for i, page in enumerate(getattr(work, "pages", []) or [])
    }


def _page_offset_for_entry(context, work, target_page) -> tuple[float, float]:
    if work is None:
        return (0.0, 0.0)
    target_id = str(getattr(target_page, "id", "") or "")
    for i, page in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(page, "id", "") or "") == target_id:
            return page_grid.page_total_offset_mm(work, context.scene, i)
    return (0.0, 0.0)


def _translate_layers_for_offset_changes(context, work, old_offsets: dict[str, tuple[float, float]]) -> None:
    for i, page in enumerate(getattr(work, "pages", []) or []):
        old = old_offsets.get(str(getattr(page, "id", "") or ""))
        if old is None:
            continue
        new = page_grid.page_total_offset_mm(work, context.scene, i)
        dx = new[0] - old[0]
        dy = new[1] - old[1]
        if abs(dx) <= 1.0e-6 and abs(dy) <= 1.0e-6:
            continue
        parent_keys = layer_stack_utils.gp_parent_keys_for_page(page)
        layer_stack_utils.translate_gp_layers_for_parent_keys(context, parent_keys, dx, dy)
        layer_stack_utils.translate_effect_layers_for_parent_keys(context, parent_keys, dx, dy)


def _duplicate_page_gp_layers(context, src_page, dst_page) -> None:
    """src_page 配下の GP レイヤー (ページ本体 + 各コマ) を dst_page へ複製する.

    元レイヤーの ``parent_key`` (= ページ/コマの安定 ID) を dst_page 側の
    対応する ``parent_key`` に書き換える。コマ並びは src/dst で同順を前提とする。
    """
    from ..utils import gp_layer_parenting as gp_parent
    from ..utils.layer_hierarchy import coma_stack_key, page_stack_key

    master = gp_utils.get_master_gpencil()
    if master is None:
        return
    layers = getattr(getattr(master, "data", None), "layers", None)
    if layers is None:
        return

    src_page_key = page_stack_key(src_page)
    dst_page_key = page_stack_key(dst_page)
    work = get_work(context)
    src_offset = _page_offset_for_entry(context, work, src_page)
    dst_offset = _page_offset_for_entry(context, work, dst_page)
    dx_mm = dst_offset[0] - src_offset[0]
    dy_mm = dst_offset[1] - src_offset[1]
    key_map = {src_page_key: dst_page_key}
    src_comas = list(getattr(src_page, "comas", []) or [])
    dst_comas = list(getattr(dst_page, "comas", []) or [])
    for src_coma, dst_coma in zip(src_comas, dst_comas):
        key_map[coma_stack_key(src_page, src_coma)] = coma_stack_key(dst_page, dst_coma)

    src_keys = set(key_map.keys())
    layers_to_copy = [layer for layer in list(layers) if gp_parent.parent_key(layer) in src_keys]
    if not layers_to_copy:
        return

    view_layer = getattr(context, "view_layer", None)
    saved_active_obj = (
        view_layer.objects.active if (view_layer is not None and getattr(view_layer, "objects", None) is not None) else None
    )
    saved_active_layer = getattr(layers, "active", None)
    try:
        if view_layer is not None:
            view_layer.objects.active = master
        for src_layer in layers_to_copy:
            old_key = gp_parent.parent_key(src_layer)
            new_key = key_map.get(old_key, dst_page_key)
            try:
                layers.active = src_layer
            except Exception:  # noqa: BLE001
                continue
            try:
                result = bpy.ops.grease_pencil.layer_duplicate("EXEC_DEFAULT", empty_keyframes=False)
            except Exception:  # noqa: BLE001
                _logger.exception("page_duplicate: GP layer duplicate failed")
                continue
            if "FINISHED" not in result:
                continue
            dup_layer = getattr(layers, "active", None)
            if dup_layer is None or dup_layer is src_layer:
                continue
            try:
                gp_parent.set_parent_key(dup_layer, new_key)
                if abs(dx_mm) > 1.0e-6 or abs(dy_mm) > 1.0e-6:
                    gp_parent.translate_layer(dup_layer, dx_mm, dy_mm)
            except Exception:  # noqa: BLE001
                _logger.exception("page_duplicate: set_parent_key failed")
    finally:
        try:
            if saved_active_layer is not None:
                layers.active = saved_active_layer
        except Exception:  # noqa: BLE001
            pass
        try:
            if view_layer is not None and saved_active_obj is not None:
                view_layer.objects.active = saved_active_obj
        except Exception:  # noqa: BLE001
            pass


def _pick_object_layer_at_event(context, event) -> tuple[dict | None, object | None]:
    try:
        from . import object_tool_op

        hit = object_tool_op.hit_object_at_event(context, event)
    except Exception:  # noqa: BLE001
        _logger.exception("page pick: object layer hit failed")
        return None, None
    if hit is None or str(hit.get("kind", "") or "") not in _PAGE_PICK_LAYER_KINDS:
        return None, None
    return hit, object_tool_op


class BNAME_OT_page_add(Operator):
    """新規ページを末尾に追加."""

    bl_idname = "bname.page_add"
    bl_label = "ページを追加"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded and get_mode(context) == MODE_PAGE)

    def execute(self, context):
        work = get_work(context)
        if not _require_loaded(self, work):
            return {"CANCELLED"}
        work_dir = Path(work.work_dir)
        try:
            # 新規ページを登録 (register_new_page が active を新ページへ変更)
            entry = page_io.register_new_page(work)
            page_io.ensure_page_dir(work_dir, entry.id)
            # 基本枠サイズの矩形コマを 1 個自動生成 (クリスタ準拠の初期状態)
            from .coma_op import create_basic_frame_coma

            create_basic_frame_coma(work, entry, work_dir)
            # ページ Collection + GP オブジェクトを生成
            gp_utils.ensure_page_gpencil(context.scene, entry.id)
            # 全ページの Collection transform を grid 位置に再配置
            page_grid.apply_page_collection_transforms(context, work)
            page_io.save_pages_json(work_dir, work)
            _sync_page_number_range(work_dir, work)
            _set_page_layer_active(context)
            _sync_layer_stack_after_page_change(context, align_page_order=True)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("page_add failed")
            self.report({"ERROR"}, f"ページ追加失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"ページ追加: {entry.id}")
        return {"FINISHED"}


class BNAME_OT_page_remove(Operator):
    """選択中のページを削除 (ディレクトリごと)."""

    bl_idname = "bname.page_remove"
    bl_label = "ページを削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(
            w and w.loaded and get_mode(context) == MODE_PAGE
            and 0 <= w.active_page_index < len(w.pages)
        )

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        work = get_work(context)
        if not _require_loaded(self, work):
            return {"CANCELLED"}
        idx = work.active_page_index
        if not (0 <= idx < len(work.pages)):
            self.report({"ERROR"}, "有効なページが選択されていません")
            return {"CANCELLED"}
        page = work.pages[idx]
        page_id = page.id
        work_dir = Path(work.work_dir)

        try:
            old_offsets = _page_offsets_by_id(context, work)
            page_io.remove_page_dir(work_dir, page_id)
            layer_stack_utils.delete_gp_layers_for_parent_keys(
                context, layer_stack_utils.gp_parent_keys_for_page(page)
            )
            layer_stack_utils.delete_effect_layers_for_parent_keys(
                context, layer_stack_utils.gp_parent_keys_for_page(page)
            )
            work.pages.remove(idx)
            _translate_layers_for_offset_changes(context, work, old_offsets)
            # GP オブジェクト / データ / Collection も削除
            gp_utils.remove_page_gpencil(page_id)
            # active index の補正
            if len(work.pages) == 0:
                work.active_page_index = -1
            elif idx >= len(work.pages):
                work.active_page_index = len(work.pages) - 1
            # 残りページの Collection transform を再計算 (index が詰まるため)
            page_grid.apply_page_collection_transforms(context, work)
            page_io.save_pages_json(work_dir, work)
            _sync_page_number_range(work_dir, work)
            _set_page_layer_active(context)
            _sync_layer_stack_after_page_change(context, align_page_order=True)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("page_remove failed")
            self.report({"ERROR"}, f"ページ削除失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"ページ削除: {page_id}")
        return {"FINISHED"}


class BNAME_OT_page_duplicate(Operator):
    """選択中のページを複製 (ディレクトリごとコピー)."""

    bl_idname = "bname.page_duplicate"
    bl_label = "ページを複製"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(
            w and w.loaded and get_mode(context) == MODE_PAGE
            and 0 <= w.active_page_index < len(w.pages)
        )

    def execute(self, context):
        work = get_work(context)
        if not _require_loaded(self, work):
            return {"CANCELLED"}
        idx = work.active_page_index
        src = work.pages[idx]
        work_dir = Path(work.work_dir)
        try:
            old_offsets = _page_offsets_by_id(context, work)
            new_id = page_io.allocate_new_page_id(work)
            page_io.copy_page_dir(work_dir, src.id, new_id)
            copied_page_blend = paths.page_blend_path(work_dir, new_id)
            if copied_page_blend.exists():
                copied_page_blend.unlink()
            new_entry = work.pages.add()
            new_entry.id = new_id
            new_entry.title = ""
            new_entry.dir_rel = f"{new_id}/"
            new_entry.spread = src.spread
            new_entry.tombo_aligned = src.tombo_aligned
            new_entry.tombo_gap_mm = src.tombo_gap_mm
            for ref in src.original_pages:
                ref_new = new_entry.original_pages.add()
                ref_new.page_id = ref.page_id
            page_io.load_page_json(work_dir, new_entry)
            new_entry.id = new_id
            new_entry.title = ""
            new_entry.dir_rel = f"{new_id}/"
            new_entry.spread = src.spread
            new_entry.tombo_aligned = src.tombo_aligned
            new_entry.tombo_gap_mm = src.tombo_gap_mm
            new_entry.offset_x_mm = 0.0
            new_entry.offset_y_mm = 0.0
            new_entry.coma_count = len(new_entry.comas)
            # 直後の位置 (idx+1) に配置
            new_index = len(work.pages) - 1
            if new_index != idx + 1:
                work.pages.move(new_index, idx + 1)
            work.active_page_index = idx + 1
            new_entry = work.pages[work.active_page_index]
            gp_utils.ensure_page_gpencil(context.scene, new_id)
            # 元ページの GP レイヤー (ページ本体 + 各コマ) を新ページへ複製。
            _duplicate_page_gp_layers(context, src, new_entry)
            _translate_layers_for_offset_changes(context, work, old_offsets)
            page_grid.apply_page_collection_transforms(context, work)
            page_io.save_page_json(work_dir, new_entry)
            page_io.save_pages_json(work_dir, work)
            _sync_page_number_range(work_dir, work)
            _set_page_layer_active(context)
            _sync_layer_stack_after_page_change(context, align_page_order=True)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("page_duplicate failed")
            self.report({"ERROR"}, f"ページ複製失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"ページ複製: {src.id} → {new_id}")
        return {"FINISHED"}


class BNAME_OT_page_move(Operator):
    """選択ページを前後に移動."""

    bl_idname = "bname.page_move"
    bl_label = "ページ移動"
    bl_options = {"REGISTER", "UNDO"}

    direction: IntProperty(  # type: ignore[valid-type]
        name="方向",
        description="-1=前, 1=後ろ",
        default=1,
    )

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(
            w and w.loaded and get_mode(context) == MODE_PAGE
            and 0 <= w.active_page_index < len(w.pages)
        )

    def execute(self, context):
        work = get_work(context)
        if not _require_loaded(self, work):
            return {"CANCELLED"}
        idx = work.active_page_index
        step = 1 if self.direction > 0 else -1
        new_idx = idx + step
        while 0 <= new_idx < len(work.pages) and not page_range.page_in_range(work.pages[new_idx]):
            new_idx += step
        if not (0 <= new_idx < len(work.pages)):
            return {"CANCELLED"}  # 端では無効 (エラーにはしない)
        work_dir = Path(work.work_dir)
        try:
            old_offsets = _page_offsets_by_id(context, work)
            page_io.move_page(work, idx, new_idx)
            page_range.update_page_range_visibility(work)
            _translate_layers_for_offset_changes(context, work, old_offsets)
            # 順序が変わったので Collection transform を再計算
            page_grid.apply_page_collection_transforms(context, work)
            page_io.save_pages_json(work_dir, work)
            _set_page_layer_active(context)
            _sync_layer_stack_after_page_change(context, align_page_order=True)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("page_move failed")
            self.report({"ERROR"}, f"ページ移動失敗: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


def _switch_to_page(context, work, work_dir: Path, new_index: int) -> bool:
    """指定ページを active に切替. mainfile は差し替えない (overview 前提).

    Phase 1 以降は 1 つの work.blend が全ページを保持するため、切替は
    ``active_page_index`` を更新するだけ。ビューフィット等は呼び出し側で
    必要に応じて実行する。
    """
    if not (0 <= new_index < len(work.pages)):
        return False
    if not page_range.page_in_range(work.pages[new_index]):
        return False
    work.active_page_index = new_index
    role, _page_id, _coma_id = page_file_scene.current_role(context)
    context.scene.bname_overview_mode = role != page_file_scene.ROLE_PAGE
    set_mode(MODE_PAGE, context)
    context.scene.bname_current_coma_id = ""
    context.scene.bname_current_coma_page_id = ""
    return True


def _tag_screen_redraw(context) -> None:
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        area.tag_redraw()


class BNAME_OT_page_select(Operator):
    """ページ一覧のクリックで active_page_index を設定."""

    bl_idname = "bname.page_select"
    bl_label = "ページ選択"
    bl_options = {"REGISTER"}

    index: IntProperty(default=0)  # type: ignore[valid-type]

    def execute(self, context):
        work = get_work(context)
        if (
            work is None
            or not work.loaded
            or not work.work_dir
            or get_mode(context) != MODE_PAGE
        ):
            return {"CANCELLED"}
        if not (0 <= self.index < len(work.pages)):
            return {"CANCELLED"}
        if not page_range.page_in_range(work.pages[self.index]):
            return {"CANCELLED"}
        role, _page_id, _coma_id = page_file_scene.current_role(context)
        if self.index == work.active_page_index:
            if role != page_file_scene.ROLE_PAGE and not bool(getattr(context.scene, "bname_overview_mode", True)):
                context.scene.bname_overview_mode = True
                _tag_screen_redraw(context)
            _set_page_layer_active(context)
            _sync_layer_stack_after_page_change(context)
            return {"FINISHED"}
        try:
            _switch_to_page(context, work, Path(work.work_dir), self.index)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("page_select failed")
            self.report({"ERROR"}, f"ページ切替失敗: {exc}")
            return {"CANCELLED"}
        _set_page_layer_active(context)
        _sync_layer_stack_after_page_change(context)
        return {"FINISHED"}


class BNAME_OT_page_pick_viewport(Operator):
    """オブジェクトモードのビューポートクリックでページをアクティブ化."""

    bl_idname = "bname.page_pick_viewport"
    bl_label = "ビューポートページ選択"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        in_page_browser = page_browser.is_page_browser_area(context)
        return bool(
            work is not None
            and work.loaded
            and (get_mode(context) == MODE_PAGE or in_page_browser)
            and getattr(context, "mode", "") == "OBJECT"
            and shortcut_visibility.shortcuts_allowed(context)
        )

    def invoke(self, context, event):
        if not shortcut_visibility.shortcuts_allowed(context):
            return {"PASS_THROUGH"}
        if (
            event.value != "PRESS"
            or bool(getattr(event, "alt", False))
            or bool(getattr(event, "oskey", False))
        ):
            return {"PASS_THROUGH"}
        is_ctrl = bool(getattr(event, "ctrl", False))
        is_shift = bool(getattr(event, "shift", False))
        multi_mode = "toggle" if is_ctrl else ("add" if is_shift else "single")
        area = getattr(context, "area", None)
        if area is None or area.type != "VIEW_3D":
            return {"PASS_THROUGH"}
        work = get_work(context)
        if work is None or not work.loaded:
            return {"PASS_THROUGH"}
        is_browser = page_browser.is_page_browser_area(context)
        previous_overview = bool(getattr(context.scene, "bname_overview_mode", False))
        try:
            from . import coma_picker
            from . import coma_edge_move_op

            if is_browser:
                context.scene.bname_overview_mode = True
            layer_hit, object_tool_op = _pick_object_layer_at_event(context, event)
            if layer_hit is not None and object_tool_op is not None:
                object_tool_op.activate_hit(context, layer_hit, mode=multi_mode)
                _tag_screen_redraw(context)
                return {"FINISHED"}
            if multi_mode == "single" and coma_edge_move_op.extend_selected_handle_at_event(context, event):
                return {"FINISHED"}
            edge_hit = coma_picker.find_coma_edge_at_event(context, event)
            panel_hit = None if edge_hit is not None else coma_picker.find_coma_at_event(context, event)
            if edge_hit is not None:
                page_index = int(edge_hit["page"])
                coma_index = int(edge_hit["coma"])
            elif panel_hit is not None:
                page_index, coma_index = panel_hit
            else:
                page_index = coma_picker.find_page_at_event(context, event)
                coma_index = None
        except Exception:  # noqa: BLE001
            _logger.exception("page_pick_viewport failed")
            return {"PASS_THROUGH"}
        finally:
            if is_browser:
                context.scene.bname_overview_mode = previous_overview
        if page_index is None or not (0 <= page_index < len(work.pages)):
            return {"PASS_THROUGH"}
        if not page_range.page_in_range(work.pages[page_index]):
            return {"PASS_THROUGH"}
        role, current_page_id, _coma_id = page_file_scene.current_role(context)
        in_page_file = role == page_file_scene.ROLE_PAGE
        if in_page_file:
            current_page_index = page_file_scene.find_page_index(work, current_page_id)
            if page_index != current_page_index:
                return {"FINISHED"}
        # マルチ選択モード時は object_selection キーセットへトグル/追加するだけで
        # アクティブやページ切替は行わない (CSP/PS のレイヤー複数選択と同じ感覚)
        if multi_mode != "single":
            page = work.pages[page_index]
            from ..utils import object_selection

            if coma_index is not None and 0 <= coma_index < len(page.comas):
                key = object_selection.coma_key(page, page.comas[coma_index])
            else:
                key = object_selection.page_key(page)
            object_selection.select_key(context, key, mode=multi_mode)
            _tag_screen_redraw(context)
            return {"FINISHED"}
        # --- single (修飾キーなし) クリック: 従来動作 ---
        # 単独選択時は他のマルチセレクトをクリアする (CSP / PS と同じ感覚)
        from ..utils import object_selection

        object_selection.clear(context)
        changed = False
        if (
            not is_browser
            and not in_page_file
            and not bool(getattr(context.scene, "bname_overview_mode", True))
        ):
            context.scene.bname_overview_mode = True
            changed = True
        if page_index != work.active_page_index:
            if is_browser:
                work.active_page_index = page_index
            else:
                _switch_to_page(context, work, Path(work.work_dir), page_index)
            changed = True
        if coma_index is not None:
            page = work.pages[page_index]
            if 0 <= coma_index < len(page.comas):
                if page.active_coma_index != coma_index:
                    page.active_coma_index = coma_index
                    changed = True
                if hasattr(context.scene, "bname_active_layer_kind"):
                    context.scene.bname_active_layer_kind = "coma"
                # アクティブコマ自身も object_selection に登録 (リストの RADIO を点灯)
                object_selection.select_key(
                    context,
                    object_selection.coma_key(page, page.comas[coma_index]),
                    mode="single",
                )
                if edge_hit is not None:
                    edge_selection.set_selection(
                        context,
                        "edge",
                        page_index=page_index,
                        coma_index=coma_index,
                        edge_index=int(edge_hit["edge"]),
                    )
                else:
                    edge_selection.set_selection(
                        context,
                        "border",
                        page_index=page_index,
                        coma_index=coma_index,
                    )
        else:
            _set_page_layer_active(context)
        _sync_layer_stack_after_page_change(context)
        if changed:
            _tag_screen_redraw(context)
        # Blender標準のオブジェクト選択は妨げない。
        return {"FINISHED"} if is_browser else {"PASS_THROUGH"}

    def execute(self, context):
        return {"CANCELLED"}


_CLASSES = (
    BNAME_OT_page_add,
    BNAME_OT_page_remove,
    BNAME_OT_page_duplicate,
    BNAME_OT_page_move,
    BNAME_OT_page_select,
    BNAME_OT_page_pick_viewport,
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
