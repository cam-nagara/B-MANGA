"""Blender実機用: 回転リングの「ゲート問題」「幾何の重なり」「per-layer化」の回帰確認.

v0.6.301 で実装された「選択矩形の角の少し外側をドラッグして中心軸で回転する」
機能は、以下の理由でほぼ到達不能だった:

1. ゲート問題: _handle_left_press は hit_object_at_event が None の場合にのみ
   回転ゾーン判定を呼んでいた。コマ/ページは矩形内部全体でヒットを返すため、
   コマ内・ページ上のオブジェクトでは回転判定へ絶対に到達できなかった。
2. 幾何の重なり: 回転リングの基準点 (旧: 実矩形の角) と、リサイズ用角ハンドル
   の描画位置 (本体境界の外側 SELECTION_HANDLE_OUTSET_MM=3mm) がほぼ重なり、
   リング内をクリックしてもリサイズが先に勝っていた。
3. カーソルと動作の不一致: ホバー時の回転カーソル表示はゲートを通さず判定
   していたため、「カーソルは回転なのにドラッグすると別の動作」になっていた。
4. kind別ロジックの重複: balloon/effect/image の回転スナップショット取得・
   適用処理が3箇所 (object_tool_free_transform / object_tool_op /
   handle_intercept) に重複していた。
5. 効果線の誤回転: 効果線の回転は「シーン単一のアクティブレイヤー用バッファ」
   (scene.bmanga_effect_line_params) 経由で保存されるため、複数の効果線
   レイヤーを選択して非アクティブ側の回転リングをドラッグすると、別レイヤー
   (シーン側の「アクティブ」レイヤー) が回転してしまうバグがあった。

これらは operators/object_rotation.py への集約 (統一判定関数
rotation_hit_with_priority + kind別ハンドラーレジストリ) で修正した。
本テストはヘッドレスで以下を確認する:

  1. コマ内部のフキダシを選択し、ハンドル表示角の外側リング内の座標で
     統一判定関数が rot_hit を返す (hit=コマ でも回転が優先される)。
  2. 同座標で実際の press 分岐 (_handle_left_press の実コード) が回転ドラッグを
     開始する (bpy の Operator は直接インスタンス化できないため、クラス辞書の
     Python 関数を移植した純Pythonハーネスで実分岐を呼ぶ)。
  3. 同じリング座標でも、同一キーの精密ハンドル (リサイズ角) がヒットして
     いれば rot_hit は None になる (リサイズ優先)。
  4. 回転の適用 (capture/apply) で entry.rotation_deg が変わり、
     キャンセル (restore) で元に戻る。
  5. 効果線レイヤーを2つ作成し、非アクティブ側のキーで回転を開始すると
     「そのレイヤー」の保存値だけが変わり、もう一方は変わらないこと。
  6. ホバー判定 (update_rotation_hover_cursor) とプレス判定が、リング内・
     ハンドル上・リング外の3点で同一の結果を返すこと。

追記 (敵対的レビューで確認された優先順位/can_rotateの欠陥修正):
  7. 複数選択中の2フキダシで、片方の角リサイズハンドル座標がもう片方の
     回転リング圏内にあっても、キーが異なるという理由だけで回転が横取り
     しないこと (新ルール1: 精密ハンドルはキーに関係なく最優先)。
  8. 未選択の別フキダシの本体クリックが、選択中フキダシの回転リング圏内
     にあっても、そちらへの通常クリックが優先されること (新ルール2)。
  9. コマ本体上のリング座標では、ルール1/2追加後も従来どおり rot_hit が
     返ること (退行防止)。
  10. can_rotate プローブ: 端点指定グラデーション塗りはリング自体が無効化
      され、fill_type を solid へ変更すると (use_gradient_endpoints が
      残留していても) 回転が復活すること。
  11. _start_rotation_drag: capture_rotation_snapshot が None を返す場合に
      回転ドラッグ状態が一切セットされないこと (空ドラッグ/空Undo防止)。

実行 (--factory-startup 必須。無いとサードパーティ拡張の読込でハングする):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python test\\blender_rotation_gate_priority_check.py
"""

from __future__ import annotations

import importlib.util
import inspect
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
PACKAGE = "bmanga_dev_rotation_gate_priority"

FAILURES: list[str] = []

# object_rotation の回転リング (表示角からの距離 3.0mm < d <= 8.0mm) の
# 中央値。ハンドルヒット (±2.5〜3mm) ともリング外側とも干渉しない位置。
_RING_OFFSET_MM = 5.0


def _check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)
        print(f"NG: {message}", flush=True)


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
    通らず、rv3d.update() は GPU 行列スタック未初期化で EXCEPTION_ACCESS_VIOLATION
    でクラッシュすることを実機確認した)。一方、起動 .blend 由来の view 行列は
    ヘッドレスでもそのまま有効で、screen<->world 変換 (region_2d_to_location_3d /
    location_3d_to_region_2d) は同じ行列を共有して双方向とも一貫するため、
    --background ではビューを一切変更せず起動時の行列をそのまま使う
    (どのビューかはテストの座標検証に影響しない。イベント座標は float 精度)。
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


def _make_event(region, px: float, py: float, *, ctrl: bool = False, shift: bool = False, alt: bool = False):
    # 実イベントの mouse_x/y は int だが、--background の既定ビューは
    # 1px あたり十数mm と粗く、int 量子化だとリング (幅5mm) を狙えない。
    # 検証対象の判定コードは float 座標をそのまま受け付けるため、
    # ここでは丸めずに float 精度で渡す。
    return SimpleNamespace(
        type="LEFTMOUSE",
        value="PRESS",
        mouse_x=float(region.x) + float(px),
        mouse_y=float(region.y) + float(py),
        ctrl=ctrl,
        shift=shift,
        alt=alt,
    )


def _screen_event_for_world(mm_to_m, event_world_mm, x_mm: float, y_mm: float, **kwargs):
    """world mm 座標 -> スクリーン座標イベント (Newton法による反復補正付き).

    test/blender_object_handle_hit_check.py の同名関数と同じ考え方。
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
    return _make_event(region, px, py, **kwargs)


def _diagonal_ring_point(rect_world: tuple[float, float, float, float], outset: float, ring_offset: float):
    """矩形の右上角から見た「表示ハンドル角」とその外側リング上の点を返す."""
    x, y, w, h = rect_world
    handle = (x + w + outset, y + h + outset)
    direction = (1.0 / math.sqrt(2.0), 1.0 / math.sqrt(2.0))
    ring_point = (handle[0] + direction[0] * ring_offset, handle[1] + direction[1] * ring_offset)
    outside_point = (handle[0] + direction[0] * 30.0, handle[1] + direction[1] * 30.0)
    return handle, ring_point, outside_point


def _make_press_harness(object_tool_op, object_tool_balloon_tail):
    """BMANGA_OT_object_tool の実メソッドをそのまま使う純Pythonハーネスを作る.

    bpy の Operator サブクラスはテストから直接インスタンス化できない
    (bpy_struct.__new__ が拒否する) ため、クラス辞書の Python 関数を移植した
    ハーネスで _handle_left_press / _update_drag / _cancel_drag の実分岐を
    そのまま検証する。invoke が設定する内部状態も同じ形で初期化する。
    """
    attrs = {
        name: value
        for name, value in object_tool_op.BMANGA_OT_object_tool.__dict__.items()
        if inspect.isfunction(value)
    }
    harness_cls = type("_ObjectToolPressHarness", (), attrs)
    op = harness_cls()
    op._externally_finished = False
    op._cursor_modal_set = False
    op._clear_drag_state()
    op._clear_click_state()
    op._ft_mode = False
    op._ft_snapshot = None
    op._ft_key = ""
    object_tool_balloon_tail.clear_pending(op)
    return op


def _run_check() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_rotation_gate_priority_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "RotationGatePriority.bmanga"))
        assert "FINISHED" in result, result
        # 現行仕様: コマ・効果線の編集実体はページファイル側にある (作品ファイル
        # はページ一覧のみ)。コマ Collection が実在しないと効果線の親リンクが
        # outside へフォールバックし Python 参照も無効化されるため、ページ
        # ファイルを開いてから検証する (test/blender_effect_line_mask_visibility_check.py と同じ)。
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        from bmanga_dev_rotation_gate_priority.operators import (
            balloon_op,
            coma_modal_state,
            coma_picker,
            effect_line_op,
            object_rotation,
            object_tool_balloon_tail,
            object_tool_op,
            object_tool_selection,
        )
        from bmanga_dev_rotation_gate_priority.utils import (
            coma_plane,
            fill_real_object,
            layer_hierarchy,
            layer_stack as layer_stack_utils,
            object_selection,
            page_grid,
        )
        from bmanga_dev_rotation_gate_priority.utils.geom import mm_to_m

        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]
        outset = object_selection.SELECTION_HANDLE_OUTSET_MM

        # ページ全域を覆う「下敷き」のコマ。 v0.6.301 のバグはまさに
        # 「コマ内部のオブジェクトの回転リングへ到達できない」症状だった。
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
        # プリセット適用等で作成時に値が動く可能性があるため、実際の保存値を使う
        BALLOON_RECT = (float(balloon.x_mm), float(balloon.y_mm), float(balloon.width_mm), float(balloon.height_mm))
        balloon_key = object_selection.balloon_key(page, balloon)

        ox_mm, oy_mm = page_grid.page_total_offset_mm(work, context.scene, 0)
        balloon_rect_world = (BALLOON_RECT[0] + ox_mm, BALLOON_RECT[1] + oy_mm, BALLOON_RECT[2], BALLOON_RECT[3])
        handle_point, ring_point, outside_point = _diagonal_ring_point(balloon_rect_world, outset, _RING_OFFSET_MM)

        object_selection.select_key(context, balloon_key, mode="single")
        _check(bool(getattr(balloon, "selected", False)), "select_key後もballoon.selectedがTrueになりません")

        # --- 1. コマヒットが渡されても、選択中フキダシの回転リングが優先される ---
        # (旧実装は hit is None ゲートの内側でしか判定しなかったため、
        #  コマヒットがある時点で回転判定へ絶対に到達できなかった)
        fake_coma_hit = {"kind": "coma", "part": "body", "key": coma_key, "page": 0, "coma": 0}
        rot_hit = object_rotation.rotation_hit_with_priority(
            context, SimpleNamespace(), lambda _ctx, _ev: ring_point, hit=fake_coma_hit,
        )
        _check(
            rot_hit is not None and rot_hit.get("key") == balloon_key,
            f"コマヒットがあるため回転リングが無視されました (v0.6.301のゲート問題再現): {rot_hit!r}",
        )

        # hit=None (どこにもヒットしない座標扱い) でも同様に機能すること
        rot_hit_plain = object_rotation.rotation_hit_with_priority(
            context, SimpleNamespace(), lambda _ctx, _ev: ring_point, hit=None,
        )
        _check(
            rot_hit_plain is not None and rot_hit_plain.get("key") == balloon_key,
            f"hit=None でも回転リングが機能しません: {rot_hit_plain!r}",
        )

        # リング外 (角から30mm) では回転しないこと
        rot_hit_outside = object_rotation.rotation_hit_with_priority(
            context, SimpleNamespace(), lambda _ctx, _ev: outside_point, hit=None,
        )
        _check(rot_hit_outside is None, f"リング外なのに回転ヒットしました: {rot_hit_outside!r}")

        # --- 3. 同一キーの精密ハンドル (リサイズ角) がヒットしていれば回転より優先 ---
        fake_resize_hit = {"kind": "balloon", "part": "top_right", "key": balloon_key}
        rot_hit_excluded = object_rotation.rotation_hit_with_priority(
            context, SimpleNamespace(), lambda _ctx, _ev: ring_point, hit=fake_resize_hit,
        )
        _check(
            rot_hit_excluded is None,
            f"同一キーのリサイズハンドルヒットがあるのに回転が優先されました: {rot_hit_excluded!r}",
        )
        # 別キー (コマ辺) の精密ハンドルヒットも、キーに関係なく回転より優先
        # されるべきである (敵対的レビューで確認された欠陥(a)の修正: 新ルール1。
        # グラデーション端点ハンドルのように精密ハンドルのキー形式が調査中
        # キーと異なる場合でも、実際のクリックがどこかの精密ハンドルに当たって
        # いる以上、そのハンドル操作を優先しなければならない。旧実装は
        # 「precise_hit.key==調査中キー」の場合だけ排他していたため、
        # このケースでは誤ってリングが横取りしていた)。
        fake_other_key_handle_hit = {"kind": "coma_edge", "part": "edge", "key": coma_key}
        rot_hit_other_key_handle = object_rotation.rotation_hit_with_priority(
            context, SimpleNamespace(), lambda _ctx, _ev: ring_point, hit=fake_other_key_handle_hit,
        )
        _check(
            rot_hit_other_key_handle is None,
            "新ルール1: 別キーの精密ハンドルヒットでもキーに関係なく回転より優先"
            f"されるべきですが、回転が返りました (欠陥(a)の再現): {rot_hit_other_key_handle!r}",
        )

        # --- 近接する2フキダシの優先順位 (敵対的レビューで確認された欠陥(b)) ---
        # b-1: 複数選択中、片方 (A) の角リサイズハンドル座標がもう片方 (B) の
        #      回転リング圏内にある場合、Bの回転ではなくAのリサイズが優先される。
        _diag = 1.0 / math.sqrt(2.0)
        # A の表示ハンドル角 (handle_point, Aからの距離0) から見て、Bの
        # 「表示ハンドル角」がリング圏内 (3mm<d<=8mm) に来るよう配置する
        # (5mm*sqrt(2)≒7.07mm はリング範囲内)。
        b_handle_world = (handle_point[0] - 5.0 * _diag, handle_point[1] - 5.0 * _diag)
        BALLOON_B_RECT_WORLD = (
            b_handle_world[0] + outset,
            b_handle_world[1] + outset,
            40.0,
            30.0,
        )
        balloon_b = balloon_op._create_balloon_entry(
            context, page,
            shape="rect",
            x=BALLOON_B_RECT_WORLD[0] - ox_mm, y=BALLOON_B_RECT_WORLD[1] - oy_mm,
            w=BALLOON_B_RECT_WORLD[2], h=BALLOON_B_RECT_WORLD[3],
            parent_kind="coma", parent_key=coma_key,
        )
        # プリセット適用等で値が動くと本テストの前提 (A/Bの相対位置) が崩れる
        # ため、作成後に意図した幾何関係を直接保証する。
        balloon_b.x_mm = BALLOON_B_RECT_WORLD[0] - ox_mm
        balloon_b.y_mm = BALLOON_B_RECT_WORLD[1] - oy_mm
        balloon_b.width_mm = BALLOON_B_RECT_WORLD[2]
        balloon_b.height_mm = BALLOON_B_RECT_WORLD[3]
        balloon_key_b = object_selection.balloon_key(page, balloon_b)

        object_selection.select_key(context, balloon_key, mode="single")
        object_selection.select_key(context, balloon_key_b, mode="add")

        fake_resize_hit_a = {"kind": "balloon", "part": "top_right", "key": balloon_key}
        rot_hit_two_balloons = object_rotation.rotation_hit_with_priority(
            context, SimpleNamespace(), lambda _ctx, _ev: handle_point, hit=fake_resize_hit_a,
        )
        _check(
            rot_hit_two_balloons is None,
            "近接2フキダシ: Aのリサイズハンドルヒットなのに、Bの回転リングが"
            f"優先されました (欠陥(b)-1の再現): {rot_hit_two_balloons!r}",
        )

        # B は幾何検証専用の一時オブジェクトなので、後続の実クリック検証
        # (VIEW_3D press/hover) がAだけのシーンを前提にしていることを崩さない
        # よう、ここで削除しておく (Bの実体がAのリング座標に近接したまま残る
        # と、以降のリアルヒット判定がBを拾ってしまい別の検証が壊れる)。
        balloon_op._delete_balloon_by_id(context, str(getattr(page, "id", "")), str(balloon_b.id))
        balloon_b = None

        # b-2: 未選択の別フキダシの本体 (part=="move") へのクリックが、選択中
        #      フキダシの回転リング圏内にあっても、そちらへのクリックが優先
        #      される (回転にすり替わらない)。
        object_selection.select_key(context, balloon_key, mode="single")
        other_balloon_key = object_selection.make_key(
            "balloon", str(getattr(page, "id", "") or ""), "unselected_other_balloon",
        )
        fake_body_hit_other = {"kind": "balloon", "part": "move", "key": other_balloon_key}
        rot_hit_unselected_body = object_rotation.rotation_hit_with_priority(
            context, SimpleNamespace(), lambda _ctx, _ev: ring_point, hit=fake_body_hit_other,
        )
        _check(
            rot_hit_unselected_body is None,
            "未選択の別フキダシ本体へのクリックが、選択中フキダシの回転リングに"
            f"すり替わりました (欠陥(b)-2の再現): {rot_hit_unselected_body!r}",
        )

        # --- コマ本体上のリング座標では従来どおり rot_hit が返ること (退行防止) ---
        # ルール1/2 を追加した後も、コンテナ系 (コマ/ページ) へのヒットは
        # 引き続き無視されてリングが勝つこと (v0.6.301 の到達不能バグ修正の
        # 本体なので絶対に退行させてはならない)。
        rot_hit_container_regression = object_rotation.rotation_hit_with_priority(
            context, SimpleNamespace(), lambda _ctx, _ev: ring_point, hit=fake_coma_hit,
        )
        _check(
            rot_hit_container_regression is not None and rot_hit_container_regression.get("key") == balloon_key,
            "新ルール1/2適用後、コマ本体ヒットでリングが退行して無視されなくなり"
            f"ました: {rot_hit_container_regression!r}",
        )

        # --- 4. 回転の適用 (capture/apply) とキャンセル (restore) ---
        snapshot = object_rotation.capture_rotation_snapshot(context, balloon_key)
        _check(snapshot is not None, "フキダシの回転スナップショットが作成できません")
        if snapshot is not None:
            base_rot = float(getattr(balloon, "rotation_deg", 0.0))
            _check(
                abs(float(snapshot.get("base_rotation_deg", -999.0)) - base_rot) < 1e-6,
                "スナップショットのbase_rotation_degが現在値と一致しません",
            )
            object_rotation.apply_rotation_snapshot(context, snapshot, base_rot + 30.0)
            _check(
                abs(float(balloon.rotation_deg) - (base_rot + 30.0)) < 1e-6,
                f"回転適用後の値が一致しません: {balloon.rotation_deg}",
            )
            object_rotation.restore_rotation_snapshot(context, snapshot)
            _check(
                abs(float(balloon.rotation_deg) - base_rot) < 1e-6,
                f"回転キャンセル後に元の値へ戻りません: {balloon.rotation_deg}",
            )

        # --- 5. 効果線per-layer化: 非アクティブ側を回してももう一方は変わらない ---
        # 効果線オブジェクトの Python 参照・ポインタ由来キーは、同期処理による
        # 再構築で無効化されうる (既知の別件バグ。test/blender_object_handle_hit_check.py
        # 参照)。そのため参照やキーを保持せず、メタデータに保存された境界の
        # x 座標で「毎回 bpy.data から引き直す」方式で対象レイヤーを特定する。
        EFFECT_A_X = 20.0
        EFFECT_B_X = 120.0

        def _live_effect_layer(x_expect: float):
            for eobj in layer_stack_utils._iter_effect_objects():
                layers = getattr(getattr(eobj, "data", None), "layers", None) or []
                for elayer in layers:
                    entry = effect_line_op._effect_meta(eobj).get(effect_line_op._layer_meta_key(elayer))
                    if isinstance(entry, dict) and abs(float(entry.get("x", -1.0e9)) - x_expect) < 0.5:
                        return eobj, elayer
            return None, None

        def _stored_effect_rotation(x_expect: float) -> float | None:
            eobj, elayer = _live_effect_layer(x_expect)
            if eobj is None or elayer is None:
                return None
            entry = effect_line_op._effect_meta(eobj).get(effect_line_op._layer_meta_key(elayer), {})
            params = entry.get("params", {}) if isinstance(entry, dict) else {}
            if "rotation_deg" not in params:
                return None
            return float(params["rotation_deg"])

        def _live_effect_key(x_expect: float) -> str:
            _eobj, elayer = _live_effect_layer(x_expect)
            return object_selection.effect_key(elayer) if elayer is not None else ""

        created_a = effect_line_op._create_effect_layer(
            context, (EFFECT_A_X, 100.0, 40.0, 30.0), parent_key=coma_key,
        )
        assert created_a[0] is not None and created_a[1] is not None
        created_b = effect_line_op._create_effect_layer(
            context, (EFFECT_B_X, 100.0, 40.0, 30.0), parent_key=coma_key,
        )
        assert created_b[0] is not None and created_b[1] is not None
        created_a = created_b = None  # 参照を保持しない (上記の再構築バグ対策)

        base_rot_a = _stored_effect_rotation(EFFECT_A_X)
        base_rot_b = _stored_effect_rotation(EFFECT_B_X)
        _check(base_rot_a is not None, "効果線Aの初期回転値を読み取れません")
        _check(base_rot_b is not None, "効果線Bの初期回転値を読み取れません")

        # 2つとも選択した上で、わざと A を「シーン側アクティブレイヤー」に
        # してから B のキーで回転を開始する
        # (旧バグ: B の回転リングをドラッグしても A が回ってしまっていた)。
        object_selection.select_key(context, _live_effect_key(EFFECT_A_X), mode="single")
        object_selection.select_key(context, _live_effect_key(EFFECT_B_X), mode="add")
        obj_a, layer_a = _live_effect_layer(EFFECT_A_X)
        effect_line_op._select_effect_layer(context, obj_a, layer_a)
        obj_a = layer_a = None

        effect_key_b = _live_effect_key(EFFECT_B_X)
        _check(bool(effect_key_b), "効果線Bのキーを再解決できません")
        effect_snapshot_b = object_rotation.capture_rotation_snapshot(context, effect_key_b)
        _check(effect_snapshot_b is not None, "効果線Bの回転スナップショットが作成できません")
        if effect_snapshot_b is not None and base_rot_b is not None and base_rot_a is not None:
            _check(
                abs(float(effect_snapshot_b.get("base_rotation_deg", -999.0)) - base_rot_b) < 1e-6,
                f"Bを対象にcaptureしたのにAの値が読まれています: "
                f"snapshot={effect_snapshot_b.get('base_rotation_deg')!r} 期待={base_rot_b!r}",
            )
            object_rotation.apply_rotation_snapshot(context, effect_snapshot_b, base_rot_b + 40.0)
            new_rot_b = _stored_effect_rotation(EFFECT_B_X)
            new_rot_a = _stored_effect_rotation(EFFECT_A_X)
            _check(
                new_rot_b is not None and abs(new_rot_b - (base_rot_b + 40.0)) < 1e-6,
                f"Bの保存値が更新されていません: {new_rot_b!r}",
            )
            _check(
                new_rot_a is not None and abs(new_rot_a - base_rot_a) < 1e-6,
                f"Bを回転させたのにAの保存値が変化しました (per-layer化バグ再現): {new_rot_a!r} (元={base_rot_a!r})",
            )
            object_rotation.restore_rotation_snapshot(context, effect_snapshot_b)
            new_rot_b_restored = _stored_effect_rotation(EFFECT_B_X)
            _check(
                new_rot_b_restored is not None and abs(new_rot_b_restored - base_rot_b) < 1e-6,
                f"Bのキャンセル復元に失敗しました: {new_rot_b_restored!r}",
            )

        # --- 4. can_rotate プローブ: 端点指定グラデーション塗りはリング自体が
        #     無効化されること。fill_type を solid へ変更すると (use_gradient_
        #     endpoints が残留していても) 回転が復活すること (欠陥修正の整合
        #     性確認: utils/fill_real_object.is_gradient_endpoint_rotation_locked
        #     への判定条件統一) ---
        fill_entry = context.scene.bmanga_fill_layers.add()
        fill_entry.id = "rotation_gate_priority_fill_gradient"
        fill_entry.title = "端点グラデ回転ゲート確認用"
        fill_entry.fill_type = "gradient"
        fill_entry.gradient_type = "linear"
        fill_entry.use_gradient_endpoints = True
        fill_entry.use_region = True
        fill_entry.region_x_mm = 120.0
        fill_entry.region_y_mm = 120.0
        fill_entry.region_width_mm = 40.0
        fill_entry.region_height_mm = 30.0
        fill_entry.gradient_start_x_mm = 120.0
        fill_entry.gradient_start_y_mm = 120.0
        fill_entry.gradient_end_x_mm = 160.0
        fill_entry.gradient_end_y_mm = 150.0
        fill_obj = fill_real_object.ensure_fill_real_object(scene=context.scene, entry=fill_entry, page=page)
        _check(fill_obj is not None, "端点グラデ塗りの実体オブジェクトが作成できません")
        fill_key = object_selection.fill_key(fill_entry)
        object_selection.select_key(context, fill_key, mode="single")

        fill_rect = object_tool_selection.selection_bounds_for_key(context, fill_key)
        _check(fill_rect is not None, "端点グラデ塗りの選択矩形が取得できません")
        if fill_rect is not None:
            fill_rect_world = (fill_rect.x, fill_rect.y, fill_rect.width, fill_rect.height)
            _fill_handle, fill_ring_point, _fill_outside = _diagonal_ring_point(
                fill_rect_world, outset, _RING_OFFSET_MM,
            )
            rot_hit_fill_grad = object_rotation.rotation_hit_with_priority(
                context, SimpleNamespace(), lambda _ctx, _ev: fill_ring_point, hit=None,
            )
            _check(
                rot_hit_fill_grad is None,
                "端点指定グラデーション塗りはcan_rotateでリング自体が無効化される"
                f"はずですが、回転が返りました: {rot_hit_fill_grad!r}",
            )

            fill_entry.fill_type = "solid"  # use_gradient_endpoints は意図的に残す
            _check(
                bool(getattr(fill_entry, "use_gradient_endpoints", False)),
                "use_gradient_endpointsがテスト前提通り残留していません (fill_type変更で"
                "リセットされる仕様に変わった場合はこのテストの前提を見直すこと)",
            )
            rot_hit_fill_solid = object_rotation.rotation_hit_with_priority(
                context, SimpleNamespace(), lambda _ctx, _ev: fill_ring_point, hit=None,
            )
            _check(
                rot_hit_fill_solid is not None and rot_hit_fill_solid.get("key") == fill_key,
                "fill_typeをsolidへ変更したのに、use_gradient_endpoints残留により回転が"
                f"無反応のままです (判定条件統一の回帰): {rot_hit_fill_solid!r}",
            )

        # --- 2 & 6. 実際の press 分岐 / hover 判定 (VIEW_3D 必須) ---
        if _has_view3d_context():
            content_center_world = (ox_mm + 75.0, oy_mm + 75.0)
            _set_top_view(mm_to_m, content_center_world)
            event_world_mm = coma_picker._event_world_mm

            object_selection.select_key(context, balloon_key, mode="single")
            # --background では view 行列を変更できず、既定ビューは 1px≈16mm と
            # 極端に広角なため、px 基準のコマ辺延長 ▲ ハンドル判定
            # (coma_edge_move_op.find_selected_handle_at_event) がページ全域を
            # 覆ってしまう (実機で確認)。▲ はアクティブコマにのみ表示される
            # ため、コマの active 状態と辺選択を外して ▲ 自体を出さない。
            # 回転ゲート問題の本体である「コマ本体ヒット」は幾何 (world mm)
            # 判定なので、この措置の影響を受けず検証できる。
            from bmanga_dev_rotation_gate_priority.utils import edge_selection

            edge_selection.clear_selection(context)
            page.active_coma_index = -1

            # --- 6. ホバー判定 (update_rotation_hover_cursor) ---
            hover_stub = SimpleNamespace(_rotate_cursor_active=False)

            def _hover_is_rotate(world_xy: tuple[float, float]) -> bool:
                hover_stub._rotate_cursor_active = False
                ev = _screen_event_for_world(mm_to_m, event_world_mm, world_xy[0], world_xy[1])
                object_rotation.update_rotation_hover_cursor(context, ev, hover_stub)
                return bool(getattr(hover_stub, "_rotate_cursor_active", False))

            _check(_hover_is_rotate(ring_point) is True, "ホバー: リング内なのに回転カーソルになりません")
            _check(_hover_is_rotate(handle_point) is False, "ホバー: ハンドル中心で回転カーソルになってしまいます")
            _check(_hover_is_rotate(outside_point) is False, "ホバー: リング外で回転カーソルになってしまいます")

            # --- 2. 実際の _handle_left_press の実コードで回転ドラッグが始まる ---
            op = _make_press_harness(object_tool_op, object_tool_balloon_tail)
            base_rot_press = float(getattr(balloon, "rotation_deg", 0.0))
            press_event = _screen_event_for_world(mm_to_m, event_world_mm, ring_point[0], ring_point[1])
            press_result = op._handle_left_press(context, press_event)
            _check(
                press_result == {"RUNNING_MODAL"},
                f"press: 回転リングクリックの戻り値が想定外です: {press_result!r}",
            )
            _check(
                bool(getattr(op, "_dragging", False))
                and str(getattr(op, "_drag_action", "")) == "rotate",
                "press: コマ内フキダシの回転リングクリックが回転ドラッグを開始しません "
                f"(dragging={getattr(op, '_dragging', None)!r} "
                f"action={getattr(op, '_drag_action', None)!r})",
            )
            # ホバー(6)とプレス(2)が同一座標で同一結果である事はここまでで
            # 「リング内=両方回転」「ハンドル上/リング外=両方回転でない」として確認される
            move_event = _screen_event_for_world(
                mm_to_m, event_world_mm,
                ring_point[0] + 3.0, ring_point[1] - 3.0,
            )
            # 回転ドラッグ中の MOUSEMOVE でカーソルが DEFAULT へ戻らないこと。
            # 実 modal は MOUSEMOVE ごとに _update_overlay_pointer を呼ぶため、
            # 「ホバーで _rotate_cursor_active=True → プレスで回転開始 → 最初の
            # MOUSEMOVE」の実流を再現し、カーソル変更呼び出しを記録して検証する。
            op._rotate_cursor_active = True
            cursor_calls: list[str] = []
            orig_set_cursor = coma_modal_state.set_modal_cursor
            coma_modal_state.set_modal_cursor = (
                lambda _ctx, cursor: bool(cursor_calls.append(str(cursor)) or True)
            )
            try:
                op._update_overlay_pointer(context, move_event)
            finally:
                coma_modal_state.set_modal_cursor = orig_set_cursor
            _check(
                "DEFAULT" not in cursor_calls,
                f"回転ドラッグ中の MOUSEMOVE でカーソルが DEFAULT へ戻りました: {cursor_calls!r}",
            )
            op._update_drag(context, move_event)
            _check(
                abs(float(balloon.rotation_deg) - base_rot_press) > 0.01,
                "press: ドラッグ移動しても回転角度が変化しません",
            )
            op._cancel_drag(context)
            _check(
                abs(float(balloon.rotation_deg) - base_rot_press) < 1e-6,
                f"press: キャンセル後に角度が戻りません: {balloon.rotation_deg}",
            )

            # 「表示ハンドルの中心」= リサイズハンドルの実ヒット座標では
            # 回転が始まらない (リサイズが優先) こと。
            handle_press_event = _screen_event_for_world(mm_to_m, event_world_mm, handle_point[0], handle_point[1])
            op._handle_left_press(context, handle_press_event)
            _check(
                str(getattr(op, "_drag_action", "")) != "rotate",
                f"press: ハンドル中心クリックが回転になってしまいました (action={getattr(op, '_drag_action', None)!r})",
            )
            if bool(getattr(op, "_dragging", False)):
                op._cancel_drag(context)

            # --- 5. _start_rotation_drag: captureがNoneを返す場合にドラッグ
            #     状態が一切セットされないこと (item3: 空ドラッグ/空Undo防止) ---
            _check(
                not bool(getattr(op, "_dragging", False)),
                f"前段の後片付けが不完全です (dragging={getattr(op, '_dragging', None)!r})",
            )
            orig_capture_snapshot = object_rotation.capture_rotation_snapshot
            object_rotation.capture_rotation_snapshot = lambda _ctx, _key: None
            try:
                fake_rot_hit = {"key": balloon_key, "center": (0.0, 0.0), "kind": "balloon"}
                started = op._start_rotation_drag(context, press_event, fake_rot_hit)
            finally:
                object_rotation.capture_rotation_snapshot = orig_capture_snapshot
            _check(started is False, f"captureがNoneを返したのにTrueが返りました: {started!r}")
            _check(
                not bool(getattr(op, "_dragging", False))
                and str(getattr(op, "_drag_action", "")) != "rotate",
                "captureがNoneを返したのに回転ドラッグ状態がセットされてしまいました "
                f"(dragging={getattr(op, '_dragging', None)!r} "
                f"action={getattr(op, '_drag_action', None)!r})",
            )
        else:
            # グローバルルール: silent skip 禁止。明示的に失敗として報告する。
            FAILURES.append(
                "SKIPPED: VIEW_3D が無いため press/hover の実機検証を実行できません"
            )
            print("SKIP: press/hover 実機検証には VIEW_3D が必要です", flush=True)

        if FAILURES:
            for f in FAILURES:
                print(f"FAIL: {f}", flush=True)
            raise AssertionError(f"{len(FAILURES)} 件の検証失敗/未実行があります")
        print("BMANGA_ROTATION_GATE_PRIORITY_OK", flush=True)
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
