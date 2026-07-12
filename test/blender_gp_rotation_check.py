"""Blender実機用: Gペン(グリースペンシル)線画レイヤーの回転対応の回帰確認.

背景:
    「選択ハンドル四隅の少し外側をドラッグしてオブジェクトを中心軸回転する」
    機能 (operators/object_rotation.py) の回転コアは実装済みだが、gp レイヤー
    (kind="gp") には rotation_deg 相当のプロパティが無いため、専用ハンドラー
    (operators/object_rotation_gp.py) で「ストローク点座標の直接書き換え」
    方式により対応した (utils.gpencil.scale等と同じ方式)。

本テストはヘッドレスで以下を確認する:
    1. gp レイヤーを選択した状態で capture_rotation_snapshot が有効な
       スナップショットを返す (レジストリへの登録確認)。
    2. apply_rotation_snapshot(90.0) で、既知の点が「選択矩形の中心周りに
       90度回転」した期待座標 (テスト内で独立計算) へ移動する
       (mm 換算誤差 1e-3 以内)。
    3. 続けて apply_rotation_snapshot(45.0) を呼ぶと「90度からの累積」では
       なく「元位置から45度」の絶対角度になる。
    4. restore_rotation_snapshot (= apply 0.0) で全点が元座標へ完全復元
       されること (選択矩形のAABBも元通りになること)。
    5. rotation_hit_with_priority が、gp レイヤー選択中のハンドル表示角の
       外側リング座標で kind=="gp" の rot_hit を返すこと。
    6. 選択矩形 (selection_bounds_for_key) が回転後の点位置に追従して
       変わる (AABB が更新される) こと。

実行:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python test\\blender_gp_rotation_check.py
"""

from __future__ import annotations

import importlib.util
import math
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_gp_rotation"

FAILURES: list[str] = []

# object_rotation の回転リング (表示角からの距離 3.0mm < d <= 8.0mm) の中央値。
_RING_OFFSET_MM = 5.0

# gp レイヤーへ書き込む既知のストローク点 (ローカル mm、親オフセット適用前)。
# AABB は (20,20)-(50,40) の矩形になり、中心は (35.0, 30.0)。
KNOWN_POINTS_MM: list[tuple[float, float]] = [
    (20.0, 20.0),
    (50.0, 20.0),
    (50.0, 40.0),
    (20.0, 40.0),
]
EXPECTED_CENTER_MM = (35.0, 30.0)


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


def _rotate_xy(x: float, y: float, cx: float, cy: float, angle_deg: float) -> tuple[float, float]:
    """テスト側の独立実装 (本番コードを一切呼ばない検算用)."""
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    dx, dy = x - cx, y - cy
    return (cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a)


def _expected_points(angle_deg: float) -> list[tuple[float, float]]:
    return [_rotate_xy(x, y, EXPECTED_CENTER_MM[0], EXPECTED_CENTER_MM[1], angle_deg) for x, y in KNOWN_POINTS_MM]


def _add_gp_layer(context, gp_utils, gp_parent, parent_key: str):
    obj = gp_utils.ensure_master_gpencil(context.scene)
    layer = obj.data.layers.new("gp_rotation_check")
    gp_parent.set_parent_key(layer, parent_key)
    frame = gp_utils.ensure_active_frame(layer)
    assert frame is not None and getattr(frame, "drawing", None) is not None
    from bmanga_dev_gp_rotation.utils.geom import mm_to_m

    ok = gp_utils.add_stroke_to_drawing(
        frame.drawing,
        [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in KNOWN_POINTS_MM],
    )
    assert ok
    return layer


def _read_points_mm(gp_parent, layer) -> list[tuple[float, float]]:
    points = []
    for point in gp_parent.iter_points(layer):
        pos = point.position
        points.append((float(pos[0]) * 1000.0, float(pos[1]) * 1000.0))
    return points


def _points_close(actual: list[tuple[float, float]], expected: list[tuple[float, float]], tol_mm: float) -> bool:
    if len(actual) != len(expected):
        return False
    return all(
        abs(ax - ex) <= tol_mm and abs(ay - ey) <= tol_mm
        for (ax, ay), (ex, ey) in zip(actual, expected)
    )


def _diagonal_ring_point(rect_world, outset: float, ring_offset: float):
    x, y, w, h = rect_world.x, rect_world.y, rect_world.width, rect_world.height
    handle = (x + w + outset, y + h + outset)
    direction = (1.0 / math.sqrt(2.0), 1.0 / math.sqrt(2.0))
    ring_point = (handle[0] + direction[0] * ring_offset, handle[1] + direction[1] * ring_offset)
    return handle, ring_point


def _run_check() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_gp_rotation_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "GpRotationCheck.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        # object_rotation_gp は operators/gpencil_op.py 経由で addon register 時に
        # 既に import され、"gp" ハンドラーが登録済みのはず (下の assert で確認する)。
        from bmanga_dev_gp_rotation.operators import object_rotation, object_tool_selection
        from bmanga_dev_gp_rotation.utils import gp_layer_parenting as gp_parent
        from bmanga_dev_gp_rotation.utils import gpencil as gp_utils
        from bmanga_dev_gp_rotation.utils import layer_hierarchy, object_selection
        from bmanga_dev_gp_rotation.utils.object_selection import SELECTION_HANDLE_OUTSET_MM

        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]
        page_key = layer_hierarchy.page_stack_key(page)

        # レジストリに "gp" ハンドラーが登録されていること (import時副作用の確認)。
        _check(
            "gp" in object_rotation.ROTATION_HANDLERS,
            "object_rotation_gp のインポートで gp ハンドラーが登録されません",
        )

        layer = _add_gp_layer(context, gp_utils, gp_parent, page_key)
        gp_key = object_selection.gp_key(layer)
        object_selection.select_key(context, gp_key, mode="single")
        _check(
            object_selection.get_keys(context) == [gp_key],
            "gpレイヤーの選択がset_keysに反映されません",
        )

        # --- 準備: 初期状態のワールド選択矩形 (AABB確認・リング座標計算用) ---
        initial_rect = object_tool_selection.selection_bounds_for_key(context, gp_key)
        _check(initial_rect is not None, "gpレイヤーの選択矩形が取得できません")
        if initial_rect is None:
            raise AssertionError("selection_bounds_for_key が None のため以降のテストを続行できません")
        # gpストローク点はBlender内部でfloat32格納のため、mm換算で1e-6mm程度の
        # 丸め誤差が生じ得る (float32の相対精度に起因)。判定は1e-3mm許容とする。
        _check(
            abs(initial_rect.width - 30.0) < 1e-3 and abs(initial_rect.height - 20.0) < 1e-3,
            f"初期AABBが期待値と異なります: width={initial_rect.width} height={initial_rect.height}",
        )

        # --- 1. capture_rotation_snapshot ---
        snapshot = object_rotation.capture_rotation_snapshot(context, gp_key)
        _check(snapshot is not None, "gpレイヤーの回転スナップショットが作成できません (レジストリ未登録の疑い)")
        if snapshot is None:
            raise AssertionError("スナップショットが None のため以降のテストを続行できません")
        _check(
            snapshot.get("kind") == "gp" and snapshot.get("key") == gp_key,
            f"スナップショットのkind/keyが不正です: {snapshot.get('kind')!r} / {snapshot.get('key')!r}",
        )
        _check(
            abs(float(snapshot.get("base_rotation_deg", -999.0))) < 1e-9,
            f"gpのbase_rotation_degは常に0.0であるべきです: {snapshot.get('base_rotation_deg')!r}",
        )
        center_mm = snapshot.get("center_mm")
        _check(
            center_mm is not None
            and abs(center_mm[0] - EXPECTED_CENTER_MM[0]) < 1e-3
            and abs(center_mm[1] - EXPECTED_CENTER_MM[1]) < 1e-3,
            f"スナップショットの回転中心が想定と異なります: {center_mm!r} (期待={EXPECTED_CENTER_MM!r})",
        )

        # --- 2. apply_rotation_snapshot(90.0): 独立計算した期待座標と一致するか ---
        object_rotation.apply_rotation_snapshot(context, snapshot, 90.0)
        actual_90 = _read_points_mm(gp_parent, layer)
        expected_90 = _expected_points(90.0)
        _check(
            _points_close(actual_90, expected_90, 1e-3),
            f"90度回転後の点座標が期待値と一致しません: actual={actual_90} expected={expected_90}",
        )

        # --- 6. 回転後にAABBが追従して変わること (30x20 -> 20x30) ---
        rect_90 = object_tool_selection.selection_bounds_for_key(context, gp_key)
        _check(rect_90 is not None, "90度回転後の選択矩形が取得できません")
        if rect_90 is not None:
            _check(
                abs(rect_90.width - 20.0) < 1e-3 and abs(rect_90.height - 30.0) < 1e-3,
                f"90度回転後のAABBが追従して更新されていません: width={rect_90.width} height={rect_90.height}",
            )
            _check(
                abs(rect_90.width - initial_rect.width) > 1.0 or abs(rect_90.height - initial_rect.height) > 1.0,
                "90度回転してもAABBが変化していません (点が実際には動いていない疑い)",
            )

        # --- 3. apply_rotation_snapshot(45.0): 90度からの累積ではなく絶対角度であること ---
        object_rotation.apply_rotation_snapshot(context, snapshot, 45.0)
        actual_45 = _read_points_mm(gp_parent, layer)
        expected_45 = _expected_points(45.0)
        expected_cumulative_135 = _expected_points(135.0)
        _check(
            _points_close(actual_45, expected_45, 1e-3),
            f"45度回転(絶対角度)後の点座標が期待値と一致しません: actual={actual_45} expected={expected_45}",
        )
        _check(
            not _points_close(actual_45, expected_cumulative_135, 1e-3),
            "回転が絶対角度ではなく累積(90+45=135相当)になっています",
        )

        # --- 4. restore_rotation_snapshot: 全点が元座標へ完全復元 ---
        object_rotation.restore_rotation_snapshot(context, snapshot)
        actual_restored = _read_points_mm(gp_parent, layer)
        _check(
            _points_close(actual_restored, KNOWN_POINTS_MM, 1e-3),
            f"restore後に元の点座標へ完全復元されません: actual={actual_restored} expected={KNOWN_POINTS_MM}",
        )
        rect_restored = object_tool_selection.selection_bounds_for_key(context, gp_key)
        _check(
            rect_restored is not None
            and abs(rect_restored.width - initial_rect.width) < 1e-3
            and abs(rect_restored.height - initial_rect.height) < 1e-3
            and abs(rect_restored.x - initial_rect.x) < 1e-3
            and abs(rect_restored.y - initial_rect.y) < 1e-3,
            f"restore後にAABBが元へ戻りません: {rect_restored!r} (期待={initial_rect!r})",
        )

        # --- 5. rotation_hit_with_priority: ハンドル表示角の外側リングでgpがヒットすること ---
        handle_point, ring_point = _diagonal_ring_point(initial_rect, SELECTION_HANDLE_OUTSET_MM, _RING_OFFSET_MM)
        rot_hit = object_rotation.rotation_hit_with_priority(
            context, SimpleNamespace(), lambda _ctx, _ev: ring_point, hit=None,
        )
        _check(
            rot_hit is not None and rot_hit.get("kind") == "gp" and rot_hit.get("key") == gp_key,
            f"gpレイヤーの回転リングヒットが得られません: {rot_hit!r} (ring_point={ring_point!r})",
        )
        # リング外 (角からさらに外側) では回転ヒットしないこと (安全側の確認)。
        outside_point = (
            handle_point[0] + (1.0 / math.sqrt(2.0)) * 30.0,
            handle_point[1] + (1.0 / math.sqrt(2.0)) * 30.0,
        )
        rot_hit_outside = object_rotation.rotation_hit_with_priority(
            context, SimpleNamespace(), lambda _ctx, _ev: outside_point, hit=None,
        )
        _check(rot_hit_outside is None, f"リング外なのにgpの回転ヒットが返りました: {rot_hit_outside!r}")

        if FAILURES:
            for f in FAILURES:
                print(f"FAIL: {f}", flush=True)
            raise AssertionError(f"{len(FAILURES)} 件の検証失敗があります")
        print("BMANGA_GP_ROTATION_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


def _main() -> None:
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
