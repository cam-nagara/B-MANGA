"""Blender実機用: 選択ハンドル回転リングのホバーカーソルを全モーダルツールへ配線した確認.

背景:
  「選択ハンドル四隅の少し外側で回転カーソル+中心軸回転ドラッグ」機能のうち、
  ドラッグ(プレス)側は operators/handle_intercept.py の try_intercept_press が
  operators/object_rotation.py の統一判定 (rotation_hit_with_priority) を使う
  ため、オブジェクトツール以外でも既に機能していた。しかし「ホバー時のカーソル
  変化 (SCROLL_XY)」は operators/object_tool_op.py にしか配線されておらず、
  他ツール (フキダシ/テキスト/塗り/グラデーション/コマ編集系など) では選択
  ハンドルが表示中でもリング上でカーソルが変わらなかった。

本テストは以下を確認する:
  1. object_rotation.update_rotation_hover_cursor に restore_cursor を渡すと、
     リング内で SCROLL_XY になり、リング外へ出た時に指定した restore_cursor
     (例: "TEXT") へ戻ること。
  2. restore_cursor を省略した場合は従来どおり "DEFAULT" へ戻ること
     (object_tool_op.py からの既存呼び出しの後方互換)。
  3. handle_intercept.try_intercept_press/update_drag を使う全モーダルツール
     の modal() ソースに update_rotation_hover_cursor の呼び出しが実在する
     こと (inspect.getsource による静的確認)。
  4. テキストツールの縦書きカスタムカーソル描画 (_draw_vertical_cursor) が、
     op._rotate_cursor_active=True の間は gpu 描画へ一切進まず早期returnする
     こと (SCROLL_XY と縦書きIビームの二重表示を避けるため)。gpu/batch_for_shader
     はヘッドレスで実行できないため、フェイクオブジェクトで呼び出しの有無だけ
     を記録して検証する (redraw_timer や rv3d.update() は使わない)。

実行 (--factory-startup 必須。無いとサードパーティ拡張の読込でハングする):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python test\\blender_rotation_hover_wiring_check.py
"""

from __future__ import annotations

import inspect
import importlib.util
import math
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_rotation_hover_wiring"

FAILURES: list[str] = []

# object_rotation の回転リング (表示角からの距離 3.0mm < d <= 8.0mm) の中央値。
_RING_OFFSET_MM = 5.0

# handle_intercept を使う全モーダルツールと、リングから外れた時に戻すべき
# カーソル値、および静的ソース確認用のクラス名。
# (layer_move_tool は自身の基準カーソルも "SCROLL_XY" のため見た目の変化は
#  無いが、_rotate_cursor_active の状態管理を他ツールと揃えるため配線対象。)
WIRED_TOOLS = (
    ("operators.fill_tool_op", "BMANGA_OT_fill_tool", "CROSSHAIR"),
    ("operators.gradient_tool_op", "BMANGA_OT_gradient_tool", "CROSSHAIR"),
    ("operators.image_path_tool_op", "BMANGA_OT_image_path_tool", "CROSSHAIR"),
    ("operators.balloon_op", "BMANGA_OT_balloon_tool", "CROSSHAIR"),
    ("operators.effect_line_op", "BMANGA_OT_effect_line_tool", "CROSSHAIR"),
    ("operators.balloon_tail_tool_op", "BMANGA_OT_balloon_tail_tool", "CROSSHAIR"),
    ("operators.balloon_nurbs_tool_op", "BMANGA_OT_balloon_nurbs_tool", "CROSSHAIR"),
    ("operators.coma_edge_move_op", "BMANGA_OT_coma_edge_move", "CROSSHAIR"),
    ("operators.coma_knife_cut_op", "BMANGA_OT_coma_knife_cut", "CROSSHAIR"),
    ("operators.coma_create_op", "BMANGA_OT_coma_create_tool", "CROSSHAIR"),
    ("operators.coma_vertex_edit_op", "BMANGA_OT_coma_edit_vertices", None),
    ("operators.layer_move_op", "BMANGA_OT_layer_move_tool", "SCROLL_XY"),
    ("operators.text_op", "BMANGA_OT_text_tool", "TEXT"),
)


def _check(condition: bool, message: str) -> None:
    # message は失敗時の説明文として書く (成功時はそれとは分からない短い
    # "ok" のみ出力する。text_vertical_cursor 側のように成功時も同じ文言を
    # 流用すると「ok: ~ありません」のような紛らわしい表示になるため)。
    if not condition:
        FAILURES.append(message)
        print(f"NG: {message}", flush=True)
    else:
        print("ok", flush=True)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _sub(path: str):
    import importlib

    return importlib.import_module(f"{PACKAGE}.{path}")


def _view3d_context():
    wm = bpy.context.window_manager
    for window in wm.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for region in area.regions:
                if region.type == "WINDOW":
                    return window, screen, area, region, area.spaces.active.region_3d
    raise RuntimeError("VIEW_3D が見つかりません")


def _has_view3d_context() -> bool:
    try:
        _view3d_context()
        return True
    except Exception:
        return False


def _view3d_override():
    window, screen, area, region, _rv3d = _view3d_context()
    return bpy.context.temp_override(window=window, screen=screen, area=area, region=region)


def _set_top_view(mm_to_m, center_world_mm: tuple[float, float]) -> None:
    """真上からの正投影ビューへ直接設定する (UI 実行時のみ).

    --background では view 行列を再計算する手段が無い (redraw_timer は poll が
    通らず、rv3d.update() は GPU 行列スタック未初期化でクラッシュすることを
    実機確認済み) ため、--background ではビューを一切変更せず起動時の行列を
    そのまま使う (test/blender_rotation_gate_priority_check.py と同じ方針)。
    """
    if bpy.app.background:
        return
    with _view3d_override():
        space = bpy.context.space_data
        rv3d = space.region_3d
        rv3d.view_perspective = "ORTHO"
        rv3d.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
        rv3d.view_location = Vector((mm_to_m(center_world_mm[0]), mm_to_m(center_world_mm[1]), 0.0))
        rv3d.view_distance = 0.62
        space.overlay.show_floor = False
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=4)


def _make_event(region, px: float, py: float):
    return SimpleNamespace(
        type="MOUSEMOVE",
        value="NOTHING",
        mouse_x=float(region.x) + float(px),
        mouse_y=float(region.y) + float(py),
        ctrl=False,
        shift=False,
        alt=False,
    )


def _screen_event_for_world(mm_to_m, event_world_mm, x_mm: float, y_mm: float):
    """world mm 座標 -> スクリーン座標イベント (Newton法による反復補正付き).

    test/blender_rotation_gate_priority_check.py の同名関数と同じ考え方。
    """
    from bpy_extras.view3d_utils import location_3d_to_region_2d

    _window, _screen, _area, region, rv3d = _view3d_context()
    point = location_3d_to_region_2d(region, rv3d, (mm_to_m(x_mm), mm_to_m(y_mm), 0.0))
    if point is None:
        raise AssertionError(f"画面座標に変換できません: {x_mm}, {y_mm}")

    def world_at(px, py):
        return event_world_mm(bpy.context, _make_event(region, px, py))

    px, py = float(point.x), float(point.y)
    for _ in range(8):
        world = world_at(px, py)
        if world is None:
            break
        err_x = float(x_mm) - float(world[0])
        err_y = float(y_mm) - float(world[1])
        if abs(err_x) + abs(err_y) <= 0.02:
            break
        wx = world_at(px + 1.0, py)
        wy = world_at(px, py + 1.0)
        if wx is None or wy is None:
            break
        j00 = float(wx[0]) - float(world[0])
        j10 = float(wx[1]) - float(world[1])
        j01 = float(wy[0]) - float(world[0])
        j11 = float(wy[1]) - float(world[1])
        det = j00 * j11 - j01 * j10
        if abs(det) < 1.0e-9:
            break
        step_x = (err_x * j11 - j01 * err_y) / det
        step_y = (j00 * err_y - err_x * j10) / det
        px += max(-200.0, min(200.0, step_x))
        py += max(-200.0, min(200.0, step_y))
    return _make_event(region, px, py)


def _diagonal_ring_point(rect_world: tuple[float, float, float, float], outset: float, ring_offset: float):
    """矩形の右上角から見た「表示ハンドル角」とその外側リング上/外の点を返す."""
    x, y, w, h = rect_world
    handle = (x + w + outset, y + h + outset)
    direction = (1.0 / math.sqrt(2.0), 1.0 / math.sqrt(2.0))
    ring_point = (handle[0] + direction[0] * ring_offset, handle[1] + direction[1] * ring_offset)
    outside_point = (handle[0] + direction[0] * 30.0, handle[1] + direction[1] * 30.0)
    return handle, ring_point, outside_point


def _check_hover_restore_cursor(
    context, coma_modal_state, object_rotation, mm_to_m, event_world_mm, ring_point, outside_point,
) -> None:
    """1 & 2: restore_cursor を渡した時 / 省略した時のカーソル復帰値を確認する."""
    cursor_calls: list[str] = []
    original = coma_modal_state.set_modal_cursor

    def _tracking_set_modal_cursor(_ctx, cursor):
        cursor_calls.append(str(cursor))
        return True

    hover_stub = SimpleNamespace(_rotate_cursor_active=False)
    try:
        coma_modal_state.set_modal_cursor = _tracking_set_modal_cursor

        # --- 1. restore_cursor="TEXT" ---
        cursor_calls.clear()
        ev_ring = _screen_event_for_world(mm_to_m, event_world_mm, ring_point[0], ring_point[1])
        object_rotation.update_rotation_hover_cursor(context, ev_ring, hover_stub, restore_cursor="TEXT")
        _check(cursor_calls == ["SCROLL_XY"], f"リング内進入時に SCROLL_XY が設定されません: {cursor_calls!r}")
        _check(bool(hover_stub._rotate_cursor_active), "リング内進入時に _rotate_cursor_active が True になりません")

        cursor_calls.clear()
        ev_outside = _screen_event_for_world(mm_to_m, event_world_mm, outside_point[0], outside_point[1])
        object_rotation.update_rotation_hover_cursor(context, ev_outside, hover_stub, restore_cursor="TEXT")
        _check(cursor_calls == ["TEXT"], f"リング退出時に restore_cursor=TEXT が設定されません: {cursor_calls!r}")
        _check(not hover_stub._rotate_cursor_active, "リング退出時に _rotate_cursor_active が False に戻りません")

        # --- 2. restore_cursor 省略時は従来どおり DEFAULT ---
        hover_stub._rotate_cursor_active = False
        cursor_calls.clear()
        object_rotation.update_rotation_hover_cursor(context, ev_ring, hover_stub)
        _check(cursor_calls == ["SCROLL_XY"], f"restore_cursor省略時もリング内進入でSCROLL_XYになるはず: {cursor_calls!r}")

        cursor_calls.clear()
        object_rotation.update_rotation_hover_cursor(context, ev_outside, hover_stub)
        _check(cursor_calls == ["DEFAULT"], f"restore_cursor省略時はDEFAULTへ戻るはず(後方互換): {cursor_calls!r}")
        _check(not hover_stub._rotate_cursor_active, "restore_cursor省略時もリング退出でFalseへ戻るはず")
    finally:
        coma_modal_state.set_modal_cursor = original


def _check_wired_tools_static(context) -> None:
    """3: 配線対象ツールの modal() ソースに update_rotation_hover_cursor 呼び出しがあること."""
    for module_path, class_name, _restore_cursor in WIRED_TOOLS:
        mod = _sub(module_path)
        cls = getattr(mod, class_name, None)
        _check(cls is not None, f"{module_path}.{class_name} が見つかりません")
        if cls is None:
            continue
        try:
            source = inspect.getsource(cls.modal)
        except Exception as exc:  # noqa: BLE001
            _check(False, f"{module_path}.{class_name}.modal のソース取得に失敗しました: {exc!r}")
            continue
        _check(
            "update_rotation_hover_cursor" in source,
            f"{module_path}.{class_name}.modal に update_rotation_hover_cursor の呼び出しがありません",
        )


class _FakeShader:
    def __init__(self) -> None:
        self.bind_calls = 0
        self.uniform_calls: list[tuple[str, object]] = []

    def bind(self) -> None:
        self.bind_calls += 1

    def uniform_float(self, name, value) -> None:
        self.uniform_calls.append((name, value))


class _FakeBatch:
    def __init__(self, *_args, **_kwargs) -> None:
        self.draw_calls = 0

    def draw(self, _shader) -> None:
        self.draw_calls += 1


class _FakeGPUState:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def blend_set(self, value) -> None:
        self.calls.append(("blend_set", value))

    def line_width_set(self, value) -> None:
        self.calls.append(("line_width_set", value))


class _FakeGPUShaderNS:
    def __init__(self, tracker: list[str]) -> None:
        self._tracker = tracker

    def from_builtin(self, name):
        self._tracker.append(str(name))
        return _FakeShader()


class _FakeGPU:
    def __init__(self) -> None:
        self.shader_calls: list[str] = []
        self.shader = _FakeGPUShaderNS(self.shader_calls)
        self.state = _FakeGPUState()


def _check_vertical_cursor_skips_while_rotating(text_op) -> None:
    """4: _draw_vertical_cursor が _rotate_cursor_active=True の間は gpu 描画へ進まないこと.

    gpu / batch_for_shader はヘッドレスで実 GL コンテキストが無いため、
    フェイクオブジェクトへ差し替えて「呼び出しが実際に起きたか」だけを記録する
    (redraw_timer や rv3d.update() は使わない)。
    """
    fake_gpu = _FakeGPU()

    def _fake_batch_for_shader(*args, **kwargs):
        return _FakeBatch(*args, **kwargs)

    original_gpu = text_op.gpu
    original_batch_for_shader = text_op.batch_for_shader
    try:
        text_op.gpu = fake_gpu
        text_op.batch_for_shader = _fake_batch_for_shader

        # 回転カーソル表示中: 有効な座標があっても gpu 描画へ一切進まない
        stub_active = SimpleNamespace(_rotate_cursor_active=True, _vcur_x=100, _vcur_y=100)
        text_op._draw_vertical_cursor(stub_active)
        _check(
            not fake_gpu.shader_calls,
            f"回転カーソル表示中なのに縦書きカーソルの gpu 描画へ進みました: {fake_gpu.shader_calls!r}",
        )

        # 回転カーソル非表示 + 有効座標: 従来どおり描画へ進む (退行していないこと)
        fake_gpu.shader_calls.clear()
        stub_inactive = SimpleNamespace(_rotate_cursor_active=False, _vcur_x=100, _vcur_y=100)
        text_op._draw_vertical_cursor(stub_inactive)
        _check(
            bool(fake_gpu.shader_calls),
            "回転カーソル非表示時は縦書きカーソルを通常どおり描画するはずです (gpu呼び出しが記録されません)",
        )

        # 回転カーソル非表示 + 非表示座標(-1): 既存仕様どおり描画しない
        fake_gpu.shader_calls.clear()
        stub_hidden = SimpleNamespace(_rotate_cursor_active=False, _vcur_x=-1, _vcur_y=-1)
        text_op._draw_vertical_cursor(stub_hidden)
        _check(not fake_gpu.shader_calls, "非表示座標(-1)では描画しないはずです (既存仕様)")
    finally:
        text_op.gpu = original_gpu
        text_op.batch_for_shader = original_batch_for_shader


def _run_check() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_rotation_hover_wiring_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "RotationHoverWiring.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        balloon_op = _sub("operators.balloon_op")
        coma_modal_state = _sub("operators.coma_modal_state")
        coma_picker = _sub("operators.coma_picker")
        object_rotation = _sub("operators.object_rotation")
        text_op = _sub("operators.text_op")
        coma_plane = _sub("utils.coma_plane")
        layer_hierarchy = _sub("utils.layer_hierarchy")
        object_selection = _sub("utils.object_selection")
        page_grid = _sub("utils.page_grid")
        geom = _sub("utils.geom")
        mm_to_m = geom.mm_to_m

        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]
        outset = object_selection.SELECTION_HANDLE_OUTSET_MM

        panel = page.comas[0]
        panel.shape_type = "rect"
        panel.rect_x_mm = 0.0
        panel.rect_y_mm = 0.0
        panel.rect_width_mm = 300.0
        panel.rect_height_mm = 300.0
        coma_plane.ensure_coma_plane(context.scene, work, page, panel)
        coma_key = layer_hierarchy.coma_stack_key(page, panel)

        BALLOON_RECT = (20.0, 20.0, 40.0, 30.0)
        balloon = balloon_op._create_balloon_entry(
            context, page,
            shape="rect", x=BALLOON_RECT[0], y=BALLOON_RECT[1], w=BALLOON_RECT[2], h=BALLOON_RECT[3],
            parent_kind="coma", parent_key=coma_key,
        )
        BALLOON_RECT = (float(balloon.x_mm), float(balloon.y_mm), float(balloon.width_mm), float(balloon.height_mm))
        balloon_key = object_selection.balloon_key(page, balloon)

        ox_mm, oy_mm = page_grid.page_total_offset_mm(work, context.scene, 0)
        balloon_rect_world = (BALLOON_RECT[0] + ox_mm, BALLOON_RECT[1] + oy_mm, BALLOON_RECT[2], BALLOON_RECT[3])
        _handle_point, ring_point, outside_point = _diagonal_ring_point(balloon_rect_world, outset, _RING_OFFSET_MM)

        object_selection.select_key(context, balloon_key, mode="single")
        _check(bool(getattr(balloon, "selected", False)), "select_key後もballoon.selectedがTrueになりません")

        # --- 3. 静的配線確認 (VIEW_3D 不要) ---
        _check_wired_tools_static(context)

        # --- 4. 縦書きカスタムカーソルの早期return (VIEW_3D 不要) ---
        _check_vertical_cursor_skips_while_rotating(text_op)

        # --- 1 & 2. ホバー判定の restore_cursor (VIEW_3D 必須) ---
        if _has_view3d_context():
            content_center_world = (ox_mm + 75.0, oy_mm + 75.0)
            _set_top_view(mm_to_m, content_center_world)
            event_world_mm = coma_picker._event_world_mm

            edge_selection = _sub("utils.edge_selection")
            edge_selection.clear_selection(context)
            page.active_coma_index = -1

            _check_hover_restore_cursor(
                context, coma_modal_state, object_rotation, mm_to_m, event_world_mm, ring_point, outside_point,
            )
        else:
            FAILURES.append("SKIPPED: VIEW_3D が無いため restore_cursor の実機検証を実行できません")
            print("SKIP: restore_cursor 実機検証には VIEW_3D が必要です", flush=True)

        if FAILURES:
            for f in FAILURES:
                print(f"FAIL: {f}", flush=True)
            raise AssertionError(f"{len(FAILURES)} 件の検証失敗/未実行があります")
        print("BMANGA_ROTATION_HOVER_WIRING_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


def _main() -> None:
    # bpy.app.timers は --background ではメインループが回らず処理されない
    # (タイマー任せにすると検証コードが一切実行されない) ことを実機確認済み
    # のため、同期的に直接 _run_check() を呼ぶ。VIEW_3D は既定の起動 .blend の
    # レイアウトに最初から存在する。
    try:
        _run_check()
        sys.stdout.flush()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        try:
            bpy.ops.wm.quit_blender()
        except Exception:
            pass
        sys.exit(1)
    try:
        bpy.ops.wm.quit_blender()
    except Exception:
        pass


if __name__ == "__main__":
    _main()
