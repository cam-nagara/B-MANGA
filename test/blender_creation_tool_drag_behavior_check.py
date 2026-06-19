"""Blender実機用: 効果線/フキダシのドラッグ作成挙動を確認."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from types import MethodType, SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_creation_drag",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_creation_drag"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _event(x: float, y: float):
    return SimpleNamespace(world_x=float(x), world_y=float(y), type="MOUSEMOVE", value="NOTHING")


def _effect_layer_count():
    from bmanga_dev_creation_drag.utils import object_naming

    return sum(1 for obj in bpy.data.objects if str(obj.get(object_naming.PROP_KIND, "") or "") == "effect")


def _assert_rect_close(rect, expected, label: str) -> None:
    if rect is None:
        raise AssertionError(f"{label} がありません")
    for actual, want in zip(rect, expected):
        if abs(float(actual) - float(want)) > 0.05:
            raise AssertionError(f"{label} が範囲通りではありません: actual={rect}, expected={expected}")


def _text_by_id(page, text_id: str):
    for entry in page.texts:
        if str(getattr(entry, "id", "") or "") == str(text_id):
            return entry
    return None


def _operator_proxy(cls, method_names: tuple[str, ...], **attrs):
    proxy = SimpleNamespace(**attrs)
    for name in method_names:
        setattr(proxy, name, MethodType(getattr(cls, name), proxy))
    return proxy


def main() -> None:
    mod = None
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_creation_drag_"))
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "CreationDrag.bmanga"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")

        from bmanga_dev_creation_drag.operators import balloon_op, effect_line_op, text_op
        from bmanga_dev_creation_drag.ui import overlay_creation_range
        from bmanga_dev_creation_drag.utils import coma_border_object, layer_hierarchy, layer_object_sync
        from bmanga_dev_creation_drag.utils import layer_stack as layer_stack_utils
        from bmanga_dev_creation_drag.utils import object_naming, page_grid

        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]
        coma = page.comas[0]
        coma.shape_type = "rect"
        coma.rect_x_mm = 20.0
        coma.rect_y_mm = 20.0
        coma.rect_width_mm = 120.0
        coma.rect_height_mm = 160.0
        coma.border.visible = True
        border = coma_border_object.ensure_coma_border_object(context.scene, work, page, coma)
        if border is None:
            raise AssertionError("コマ枠線の実体がありません")
        page_key = layer_hierarchy.page_stack_key(page)
        coma_key = layer_hierarchy.coma_stack_key(page, coma)
        page_ox, page_oy = page_grid.page_total_offset_mm(work, context.scene, 0)

        def _page_local(world_x: float, world_y: float) -> tuple[float, float]:
            return float(world_x) - page_ox, float(world_y) - page_oy

        original_effect_event = effect_line_op._event_world_xy_mm
        original_balloon_event = balloon_op._event_world_xy_mm
        effect_line_op._event_world_xy_mm = lambda _ctx, ev: (float(ev.world_x), float(ev.world_y))
        balloon_op._event_world_xy_mm = lambda _ctx, ev: (float(ev.world_x), float(ev.world_y))
        try:
            effect_tool = _operator_proxy(
                effect_line_op.BMANGA_OT_effect_line_tool,
                (
                    "_clear_drag_state",
                    "_start_create_preview",
                    "_update_drag",
                    "_finish_drag",
                    "_local_xy_for_parent_key",
                ),
                _push_undo_step=lambda _message: None,
            )
            effect_tool._clear_drag_state()
            before_layers = _effect_layer_count()
            sx, sy = _page_local(10.0, 10.0)
            effect_tool._start_create_preview(sx, sy, 10.0, 10.0, page_key)
            if overlay_creation_range.current_bounds() is None:
                raise AssertionError("効果線のドラッグ開始範囲が表示されません")
            effect_tool._finish_drag(context)
            if _effect_layer_count() != before_layers:
                raise AssertionError("効果線ツールのクリックだけで効果線が作成されています")
            if overlay_creation_range.current_bounds() is not None:
                raise AssertionError("効果線クリック終了後に範囲表示が残っています")

            sx, sy = _page_local(10.0, 10.0)
            effect_tool._start_create_preview(sx, sy, 10.0, 10.0, page_key)
            effect_tool._update_drag(context, _event(42.0, 34.0))
            if _effect_layer_count() != before_layers:
                raise AssertionError("効果線ドラッグ中に実体が先に作成されています")
            _assert_rect_close(overlay_creation_range.current_bounds(), (10.0, 10.0, 32.0, 24.0), "効果線ドラッグ範囲")
            effect_tool._finish_drag(context)
            if _effect_layer_count() != before_layers + 1:
                raise AssertionError("効果線ドラッグ終了時に効果線が作成されません")
            if overlay_creation_range.current_bounds() is not None:
                raise AssertionError("効果線作成後に範囲表示が残っています")

            effect_tool._start_create_preview(220.0, 230.0, 220.0, 230.0, layer_hierarchy.OUTSIDE_STACK_KEY)
            effect_tool._update_drag(context, _event(250.0, 260.0))
            effect_tool._finish_drag(context)
            if _effect_layer_count() != before_layers + 2:
                raise AssertionError("ページ外の効果線作成ができません")

            balloon_tool = _operator_proxy(
                balloon_op.BMANGA_OT_balloon_tool,
                (
                    "_clear_drag_state",
                    "_clear_tail_polyline_state",
                    "_start_create_preview",
                    "_update_create_preview",
                    "_finish_create_preview",
                    "_drag_page_for_create",
                    "_local_xy_for_page",
                ),
                _push_undo_step=lambda _message: None,
                report=lambda _types, _message: None,
            )
            balloon_tool._clear_drag_state()
            before_balloons = len(page.balloons)
            sx, sy = _page_local(18.0, 18.0)
            balloon_tool._start_create_preview(page, sx, sy, 18.0, 18.0, "page", page_key)
            balloon_tool._finish_create_preview(context)
            if len(page.balloons) != before_balloons:
                raise AssertionError("フキダシツールのクリックだけでフキダシが作成されています")

            text_x, text_y = _page_local(24.0, 22.0)
            enclosed_text, _missing = text_op._create_text_entry(
                context,
                page,
                body="囲まれるテキスト",
                speaker_type="normal",
                x_mm=text_x,
                y_mm=text_y,
                width_mm=14.0,
                height_mm=8.0,
                parent_kind="page",
                parent_key=page_key,
            )
            enclosed_text_id = str(getattr(enclosed_text, "id", "") or "")
            text_x, text_y = _page_local(56.0, 24.0)
            outside_text, _missing = text_op._create_text_entry(
                context,
                page,
                body="外側テキスト",
                speaker_type="normal",
                x_mm=text_x,
                y_mm=text_y,
                width_mm=14.0,
                height_mm=8.0,
                parent_kind="page",
                parent_key=page_key,
            )
            outside_text_id = str(getattr(outside_text, "id", "") or "")
            sx, sy = _page_local(18.0, 18.0)
            balloon_tool._start_create_preview(page, sx, sy, 18.0, 18.0, "page", page_key)
            balloon_tool._update_create_preview(context, _event(50.0, 40.0))
            if len(page.balloons) != before_balloons:
                raise AssertionError("フキダシドラッグ中に実体が先に作成されています")
            _assert_rect_close(overlay_creation_range.current_bounds(), (18.0, 18.0, 32.0, 22.0), "フキダシドラッグ範囲")
            balloon_tool._finish_create_preview(context)
            if len(page.balloons) != before_balloons + 1:
                raise AssertionError("フキダシドラッグ終了時にフキダシが作成されません")
            created = page.balloons[-1]
            if str(getattr(created, "parent_kind", "")) != "page":
                raise AssertionError("コマ外のフキダシがページ前面のレイヤーとして作成されていません")
            enclosed_text = _text_by_id(page, enclosed_text_id)
            outside_text = _text_by_id(page, outside_text_id)
            if enclosed_text is None or outside_text is None:
                raise AssertionError("確認用テキストが見つかりません")
            if str(getattr(enclosed_text, "parent_balloon_id", "") or "") != str(getattr(created, "id", "") or ""):
                raise AssertionError("囲まれたテキストがフキダシに紐付いていません")
            if str(getattr(outside_text, "parent_balloon_id", "") or ""):
                raise AssertionError("範囲外のテキストまでフキダシに紐付いています")
            old_text_xy = (float(enclosed_text.x_mm), float(enclosed_text.y_mm))
            balloon_op._move_balloon_with_texts(page, created, float(created.x_mm) + 5.0, float(created.y_mm) + 3.0)
            if (
                abs(float(enclosed_text.x_mm) - (old_text_xy[0] + 5.0)) > 0.05
                or abs(float(enclosed_text.y_mm) - (old_text_xy[1] + 3.0)) > 0.05
            ):
                raise AssertionError("紐付いたテキストがフキダシ移動に追従していません")
            balloon_op._move_balloon_with_texts(page, created, float(created.x_mm) - 5.0, float(created.y_mm) - 3.0)
            first_balloon_id = str(getattr(created, "id", "") or "")
            sx, sy = _page_local(16.0, 16.0)
            balloon_tool._start_create_preview(page, sx, sy, 16.0, 16.0, "page", page_key)
            balloon_tool._update_create_preview(context, _event(54.0, 44.0))
            balloon_tool._finish_create_preview(context)
            enclosed_text = _text_by_id(page, enclosed_text_id)
            if enclosed_text is None:
                raise AssertionError("確認用テキストが見つかりません")
            if str(getattr(enclosed_text, "parent_balloon_id", "") or "") != first_balloon_id:
                raise AssertionError("既に紐付いたテキストの親フキダシが上書きされています")

            before_shared = len(work.shared_balloons)
            balloon_tool._start_create_preview(
                None,
                240.0,
                250.0,
                240.0,
                250.0,
                "outside",
                layer_hierarchy.OUTSIDE_STACK_KEY,
            )
            balloon_tool._update_create_preview(context, _event(270.0, 280.0))
            balloon_tool._finish_create_preview(context)
            if len(work.shared_balloons) != before_shared + 1:
                raise AssertionError("ページ外のフキダシ作成ができません")

            layer_object_sync.assign_per_page_z_ranks(context.scene, work)
            page_balloon_obj = object_naming.find_object_by_bmanga_id(created.id, kind="balloon")
            if page_balloon_obj is None:
                raise AssertionError("作成したフキダシの実体がありません")
            if not (float(page_balloon_obj.location.z) > float(border.location.z)):
                raise AssertionError("コマ外フキダシがすべてのコマより前面にありません")

            stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
            empty_rows = [
                (i, getattr(item, "kind", ""), getattr(item, "label", ""))
                for i, item in enumerate(stack)
                if not str(getattr(item, "kind", "") or "").strip()
                or not str(getattr(item, "label", "") or getattr(item, "name", "") or "").strip()
            ]
            if empty_rows:
                raise AssertionError(f"レイヤーリストに空白行が残っています: {empty_rows}")

            blank = stack.add()
            blank.kind = "gp"
            blank.key = ""
            blank.label = ""
            blank.name = ""
            stack.move(len(stack) - 1, min(1, max(0, len(stack) - 1)))
            if not layer_stack_utils._stack_has_placeholder_rows(stack):
                raise AssertionError("保存済みの空白行を検出できません")
            if not layer_stack_utils.schedule_layer_stack_draw_maintenance(context):
                raise AssertionError("レイヤー一覧の空白行修復が予約されません")
            stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
            empty_rows = [
                (i, getattr(item, "kind", ""), getattr(item, "label", ""))
                for i, item in enumerate(stack)
                if not str(getattr(item, "kind", "") or "").strip()
                or not str(getattr(item, "label", "") or getattr(item, "name", "") or "").strip()
            ]
            if empty_rows:
                raise AssertionError(f"同期後もレイヤーリストに空白行が残っています: {empty_rows}")

            world_bounds = (
                float(created.x_mm) + page_ox,
                float(created.y_mm) + page_oy,
                float(created.width_mm),
                float(created.height_mm),
            )
            _assert_rect_close(world_bounds, (18.0, 18.0, 32.0, 22.0), "作成されたフキダシ")
        finally:
            effect_line_op._event_world_xy_mm = original_effect_event
            balloon_op._event_world_xy_mm = original_balloon_event

        print("BMANGA_CREATION_TOOL_DRAG_BEHAVIOR_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()


if __name__ == "__main__":
    main()
