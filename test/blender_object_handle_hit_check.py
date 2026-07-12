"""Blender実機用: フキダシ/テキスト/効果線の選択ハンドル(境界+3mm)ヒット判定の回帰確認.

2026-07-02 (c653c629) でハンドルの「描画」位置が本体境界の外側
SELECTION_HANDLE_OUTSET_MM (3mm) へ変わったが、フキダシ (_balloon_hit_part) と
テキスト (_text_hit_part) の「ヒット判定」が境界±2.5mmのまま追随しておらず、
選択中のハンドルをクリック/ドラッグしても反応せず下のオブジェクトが選択
されてしまう症状があった (効果線 _effect_hit_part は追随済みで対象外)。

この回帰テストは以下を確認する:
  1. 未選択のフキダシ/テキスト/効果線は、ハンドル中心(境界+3mm)ではヒット
     しない (従来どおり±2.5mm帯の外は不当たり)。
  2. 選択中のフキダシ/テキスト/効果線は、8個のハンドル中心すべてで
     ヒットする (直接関数 + 中間ラッパー関数の両方)。
  3. 選択中フキダシ/テキスト/効果線のハンドル中心をクリックすると、
     hit_object_at_event が全面に重なる下のコマではなく、選択中の
     オブジェクト自身を返す (フォールスルーしない)。
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Quaternion, Vector


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_object_handle_hit"

FAILURES: list[str] = []


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
    """真上からの正投影ビューへ直接設定する (center_world_mm はワールドmm中心).

    bmanga.view_fit_page はスムーズ遷移(タイマー駆動)のアニメーション付き
    オペレーターで、Python側で直後に ORTHO / view_rotation を上書きしても
    アニメーションが後から巻き戻してしまい、is_perspective が True のまま
    残ることを実機で確認した (screen<->world 変換が不安定になり座標が
    収束しない原因)。 test/blender_effect_line_handle_visual_check.py と
    同じ「view_location/view_distance を直接代入する」決定的な方法を使う。

    さらに、プロパティ代入直後は rv3d の内部行列 (view_matrix 等) が
    再計算されず古い (パースペクティブ) 状態のまま残ることも実機で確認した。
    redraw_timer で明示的に再描画させ、行列を確定させてから返す。
    """
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
        type="LEFTMOUSE",
        value="PRESS",
        mouse_x=int(round(region.x + px)),
        mouse_y=int(round(region.y + py)),
        ctrl=False,
        shift=False,
        alt=False,
    )


def _screen_event_for_world(mm_to_m, event_world_mm, x_mm: float, y_mm: float):
    """world mm 座標 -> スクリーン座標イベント (反復補正付き).

    test/blender_tool_behavior_visual_audit.py の _screen_event_for_world と
    同じ考え方 (Newton法によるscreen<->world変換の補正)。
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


def _handle_points(rect: tuple[float, float, float, float], outset: float) -> dict[str, tuple[float, float]]:
    x, y, w, h = rect
    left, bottom, right, top = x, y, x + w, y + h
    hl, hb, hr, ht = left - outset, bottom - outset, right + outset, top + outset
    return {
        "top_left": (hl, ht),
        "top_right": (hr, ht),
        "bottom_left": (hl, hb),
        "bottom_right": (hr, hb),
        "left": (hl, (hb + ht) * 0.5),
        "right": (hr, (hb + ht) * 0.5),
        "top": ((hl + hr) * 0.5, ht),
        "bottom": ((hl + hr) * 0.5, hb),
    }


def _run_check() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_object_handle_hit_"))
    mod = None
    try:
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "ObjectHandleHit.bmanga"))
        assert "FINISHED" in result, result

        from bmanga_dev_object_handle_hit.operators import balloon_op, coma_picker, effect_line_op, object_tool_op, text_op
        from bmanga_dev_object_handle_hit.utils import layer_hierarchy, object_selection
        from bmanga_dev_object_handle_hit.utils.geom import mm_to_m

        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]
        outset = object_selection.SELECTION_HANDLE_OUTSET_MM

        # ページ全域を覆う「下敷き」のコマ。フォールスルー時にここへ着地する。
        # 効果線の親には (test/blender_coma_mask_hit_visibility_check.py と同じ)
        # コマキーを使う。 "page" 直下の parent_key は effect line の
        # link_object_to_parent が対応しておらず (collection 未検出でオブジェクト
        # が再構築され Python 参照が無効化される、既知の別件バグ) 本テストの
        # 対象外なので避ける。
        panel = page.comas[0]
        panel.shape_type = "rect"
        panel.rect_x_mm = 0.0
        panel.rect_y_mm = 0.0
        panel.rect_width_mm = 300.0
        panel.rect_height_mm = 300.0
        coma_key = layer_hierarchy.coma_stack_key(page, panel)

        BALLOON_RECT = (20.0, 20.0, 40.0, 30.0)
        TEXT_RECT = (100.0, 20.0, 30.0, 15.0)
        EFFECT_RECT = (20.0, 100.0, 40.0, 30.0)

        balloon = balloon_op._create_balloon_entry(
            context, page,
            shape="rect", x=BALLOON_RECT[0], y=BALLOON_RECT[1], w=BALLOON_RECT[2], h=BALLOON_RECT[3],
            parent_kind="coma", parent_key=coma_key,
        )
        text, missing = text_op._create_text_entry(
            context, page,
            body="hit",
            x_mm=TEXT_RECT[0], y_mm=TEXT_RECT[1], width_mm=TEXT_RECT[2], height_mm=TEXT_RECT[3],
            parent_kind="coma", parent_key=coma_key,
        )
        assert not missing
        effect_obj, effect_layer = effect_line_op._create_effect_layer(
            context, EFFECT_RECT, parent_key=coma_key,
        )
        assert effect_obj is not None and effect_layer is not None
        # 効果線オブジェクトの Python 参照は、直後の他エントリ作成や同期処理で
        # 無効化されうる (既知の別件バグ: 更新コールバックによる再構築)。
        # ここでキー文字列だけを取り出し、以降は _hit_effect_layer 等の
        # 「毎回 bpy.data から引き直す」経路だけを使う (参照を保持しない)。
        effect_key = object_selection.effect_key(effect_layer)
        effect_obj = None
        effect_layer = None

        # プリセット適用等で作成時に値が動く可能性があるため、実際に保存された
        # 値からハンドル位置を計算する (ハードコードした定数を信用しない)。
        BALLOON_RECT = (float(balloon.x_mm), float(balloon.y_mm), float(balloon.width_mm), float(balloon.height_mm))
        TEXT_RECT = (float(text.x_mm), float(text.y_mm), float(text.width_mm), float(text.height_mm))

        balloon_key = object_selection.balloon_key(page, balloon)
        text_key = object_selection.text_key(page, text)

        # --- 1. 未選択状態: ハンドル中心(境界+3mm)ではヒットしない (従来どおり) ---
        object_selection.clear(context)
        for name, (hx, hy) in _handle_points(BALLOON_RECT, outset).items():
            direct = balloon_op._balloon_hit_part(balloon, hx, hy, handle_outset_mm=0.0)
            _check(direct == "", f"未選択フキダシの直接判定がハンドル位置({name})で誤ヒット: {direct!r}")
            _idx, entry, part = balloon_op._hit_balloon_entry(page, hx, hy)
            _check(entry is None, f"未選択フキダシがハンドル位置({name})で拾われました: part={part!r}")
        for name, (hx, hy) in _handle_points(TEXT_RECT, outset).items():
            direct = text_op._text_hit_part(text, hx, hy, handle_outset_mm=0.0)
            _check(direct == "", f"未選択テキストの直接判定がハンドル位置({name})で誤ヒット: {direct!r}")
            _idx, entry, part = text_op._hit_text_entry(page, hx, hy)
            _check(entry is None, f"未選択テキストがハンドル位置({name})で拾われました: part={part!r}")
        for name, (hx, hy) in _handle_points(EFFECT_RECT, outset).items():
            direct = effect_line_op._effect_hit_part(EFFECT_RECT, hx, hy, handle_outset_mm=0.0)
            _check(direct == "", f"未選択効果線の直接判定がハンドル位置({name})で誤ヒット: {direct!r}")

        # --- 2a. 選択中フキダシ: 8ハンドル全てでヒットする ---
        # 注: PropertyGroup コレクション要素は [] アクセスのたびに新しい
        # Python ラッパーが返ることがあり `is` 比較は当てにできないため、
        # id 文字列で同一性を確認する (StructRNA の再構築問題とは無関係)。
        balloon_id = str(getattr(balloon, "id", "") or "")
        text_id = str(getattr(text, "id", "") or "")
        object_selection.select_key(context, balloon_key, mode="single")
        _check(bool(getattr(balloon, "selected", False)), "select_key後もballoon.selectedがTrueになりません")
        for name, (hx, hy) in _handle_points(BALLOON_RECT, outset).items():
            direct = balloon_op._balloon_hit_part(balloon, hx, hy, handle_outset_mm=outset)
            _check(direct != "", f"選択中フキダシの直接判定がハンドル位置({name})でヒットしません")
            idx, entry, part = balloon_op._hit_balloon_entry(page, hx, hy)
            hit_id = "" if entry is None else str(getattr(entry, "id", "") or "")
            _check(hit_id == balloon_id and part != "", f"選択中フキダシがハンドル位置({name})で拾えません: idx={idx} hit_id={hit_id!r} part={part!r}")

        # --- 2b. 選択中テキスト: 8ハンドル全てでヒットする (常に body) ---
        object_selection.select_key(context, text_key, mode="single")
        _check(not bool(getattr(balloon, "selected", False)), "テキスト選択後もフキダシがselected=Trueのままです")
        _check(bool(getattr(text, "selected", False)), "select_key後もtext.selectedがTrueになりません")
        for name, (hx, hy) in _handle_points(TEXT_RECT, outset).items():
            direct = text_op._text_hit_part(text, hx, hy, handle_outset_mm=outset)
            _check(direct == "body", f"選択中テキストの直接判定がハンドル位置({name})でヒットしません: {direct!r}")
            idx, entry, part = text_op._hit_text_entry(page, hx, hy)
            hit_id = "" if entry is None else str(getattr(entry, "id", "") or "")
            _check(hit_id == text_id and part == "body", f"選択中テキストがハンドル位置({name})で拾えません: idx={idx} hit_id={hit_id!r} part={part!r}")

        # --- 2c. 選択中効果線: 8ハンドル全てでヒットする (直接関数レベル) ---
        # 注: effect_line_op._hit_effect_layer / hit_object_at_event 経由の
        # 効果線ラウンドトリップは、本テストとは無関係な既存の別件バグ
        # (effect_layer_bounds が作成直後の再スキャンで None を返すことがある。
        # test/blender_coma_mask_hit_visibility_check.py が変更前コードでも
        # 同種の理由で失敗することを stash 比較で確認済み) の影響を受けるため、
        # ここでは対象外の _effect_hit_part (純粋関数、フキダシ/テキストと
        # 同じ handle_outset_mm パターンを検証済み) のみを確認する。
        object_selection.select_key(context, effect_key, mode="single")
        _check(not bool(getattr(text, "selected", False)), "効果線選択後もテキストがselected=Trueのままです")
        for name, (hx, hy) in _handle_points(EFFECT_RECT, outset).items():
            direct = effect_line_op._effect_hit_part(EFFECT_RECT, hx, hy, handle_outset_mm=outset)
            _check(direct != "", f"選択中効果線の直接判定がハンドル位置({name})でヒットしません")

        # --- 3. hit_object_at_event: 選択中オブジェクトのハンドルは、全面に重なる
        #        下のコマへフォールスルーせず自分自身を返す ---
        if _has_view3d_context():
            from bmanga_dev_object_handle_hit.utils import page_grid

            # entry.x_mm/y_mm はページローカルmm。 hit_object_at_event は実際の
            # 3Dビュー座標(ワールドmm = ページローカル + ページのグリッドオフセット)
            # を要求するため、オフセットを加算してから screen 変換する
            # (test/blender_coma_mask_hit_visibility_check.py と同じ考え方)。
            ox_mm, oy_mm = page_grid.page_total_offset_mm(work, context.scene, 0)

            def _world_handle_points(rect):
                local = _handle_points(rect, outset)
                return {name: (x + ox_mm, y + oy_mm) for name, (x, y) in local.items()}

            content_center_world = (ox_mm + 75.0, oy_mm + 75.0)
            _set_top_view(mm_to_m, content_center_world)
            event_world_mm = coma_picker._event_world_mm

            object_selection.select_key(context, balloon_key, mode="single")
            for name, (hx, hy) in _world_handle_points(BALLOON_RECT).items():
                event = _screen_event_for_world(mm_to_m, event_world_mm, hx, hy)
                hit = object_tool_op.hit_object_at_event(context, event)
                kind = None if hit is None else str(hit.get("kind", ""))
                _check(kind == "balloon", f"選択中フキダシのハンドル({name})クリックがフキダシへ届きません: hit={hit}")

            object_selection.select_key(context, text_key, mode="single")
            for name, (hx, hy) in _world_handle_points(TEXT_RECT).items():
                event = _screen_event_for_world(mm_to_m, event_world_mm, hx, hy)
                hit = object_tool_op.hit_object_at_event(context, event)
                kind = None if hit is None else str(hit.get("kind", ""))
                _check(kind == "text", f"選択中テキストのハンドル({name})クリックがテキストへ届きません: hit={hit}")

            # 効果線の hit_object_at_event 経由チェックは、2c と同じ理由
            # (既存の別件バグ) で対象外とする。
        else:
            # --background 実行時は VIEW_3D が無く hit_object_at_event を検証できない。
            # 黙ってスキップとして扱わず、明示的に報告する (グローバルルール: silent skip 禁止)。
            FAILURES.append(
                "SKIPPED: VIEW_3D が無いため hit_object_at_event のフォールスルー検証を実行できません"
                " (--factory-startup かつ --background 無しで再実行してください)"
            )
            print(
                "SKIP: hit_object_at_event フォールスルー検証は VIEW_3D が必要なため --background 無しで再実行してください",
                flush=True,
            )

        if FAILURES:
            for f in FAILURES:
                print(f"FAIL: {f}", flush=True)
            raise AssertionError(f"{len(FAILURES)} 件の検証失敗/未実行があります")
        print("BMANGA_OBJECT_HANDLE_HIT_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


_TICK_WAITED = 0.0
_TICK_MAX_WAIT = 20.0


def _tick():
    global _TICK_WAITED
    if not _has_view3d_context() and _TICK_WAITED < _TICK_MAX_WAIT:
        _TICK_WAITED += 0.25
        return 0.25
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
    return None


if __name__ == "__main__":
    bpy.app.timers.register(_tick, first_interval=0.25)
