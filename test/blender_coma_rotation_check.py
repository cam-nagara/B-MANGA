"""Blender実機用: コマ (kind="coma", 漫画のコマ枠) の回転対応の回帰確認.

背景:
    「選択ハンドル四隅の少し外側をドラッグしてオブジェクトを中心軸回転する」
    機能 (operators/object_rotation.py) の回転コアは実装済みだが、コマは
    「枠ポリゴンの回転」= コマ辺編集と同じ意味論で実装する専用ハンドラー
    (operators/object_rotation_coma.py) が必要だった。矩形コマ
    (shape_type=="rect") は回転で多角形へ変換され、角度0で矩形へ完全復元
    する。コマ内側のレイヤー (フキダシ等) は回転の影響を受けない。

本テストはヘッドレスで以下を確認する:
    1. 矩形コマを選択し capture_rotation_snapshot が有効なスナップショット
       を返す (レジストリへの登録確認)。
    2. apply_rotation_snapshot(90.0) で shape_type が "polygon" になり、
       4頂点が「中心周りに90度回転」した期待座標 (テスト内で独立計算、
       ページオフセット考慮) に一致する。
    3. apply_rotation_snapshot(45.0) は「90度からの累積」ではなく
       「元の矩形から45度」の絶対角度になる。
    4. restore (= apply 0.0) で shape_type が "rect" に戻り、rect_*_mm が
       ビット単位で元の値に復元される。
    5. 元から多角形だったコマでも 2-4 相当が成立する (頂点列の回転・絶対
       角度・完全復元)。
    6. rotation_hit_with_priority がコマ選択中のリング座標で
       kind=="coma" の rot_hit を返し、同一キーのコマ頂点□マーカー相当の
       ヒットがあれば None になる (コマ頂点ハンドル優先)。
    7. コマ内に置いたフキダシの x_mm/y_mm が回転前後で不変であること
       (中身は回転しない仕様の確認)。
    8. 回転後にコマ枠線オブジェクト/マスクの更新経路が例外なく走ること
       (coma_plane / coma_border_object が例外なく取得できる)。

実行 (--factory-startup 必須。無いとサードパーティ拡張の読込でハングする):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python test\\blender_coma_rotation_check.py
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
PACKAGE = "bmanga_dev_coma_rotation"

FAILURES: list[str] = []

# object_rotation の回転リング (表示角からの距離 3.0mm < d <= 8.0mm) の中央値。
_RING_OFFSET_MM = 5.0

# 矩形コマ1 (page-local mm)。
COMA1_RECT_MM = (10.0, 20.0, 80.0, 60.0)  # x, y, w, h
COMA1_CENTER_LOCAL_MM = (10.0 + 80.0 / 2.0, 20.0 + 60.0 / 2.0)  # (50.0, 50.0)

# 元から多角形のコマ2 (page-local mm、非軸並行の四角形)。
COMA2_POLY_MM: list[tuple[float, float]] = [
    (120.0, 30.0),
    (190.0, 40.0),
    (185.0, 95.0),
    (110.0, 80.0),
]


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


def _expected_points(
    points_mm: list[tuple[float, float]], center_mm: tuple[float, float], angle_deg: float,
) -> list[tuple[float, float]]:
    return [_rotate_xy(x, y, center_mm[0], center_mm[1], angle_deg) for x, y in points_mm]


def _points_close(actual: list[tuple[float, float]], expected: list[tuple[float, float]], tol_mm: float) -> bool:
    if len(actual) != len(expected):
        return False
    return all(
        abs(ax - ex) <= tol_mm and abs(ay - ey) <= tol_mm
        for (ax, ay), (ex, ey) in zip(actual, expected)
    )


def _panel_vertices_mm(panel) -> list[tuple[float, float]]:
    return [(float(v.x_mm), float(v.y_mm)) for v in panel.vertices]


def _diagonal_ring_point(rect_world, outset: float, ring_offset: float):
    x, y, w, h = rect_world.x, rect_world.y, rect_world.width, rect_world.height
    handle = (x + w + outset, y + h + outset)
    direction = (1.0 / math.sqrt(2.0), 1.0 / math.sqrt(2.0))
    ring_point = (handle[0] + direction[0] * ring_offset, handle[1] + direction[1] * ring_offset)
    return handle, ring_point


def _run_check() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_rotation_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "ComaRotationCheck.bmanga"))
        assert "FINISHED" in result, result
        # コマ・フキダシの実体はページファイル側にあるため、開いてから検証する。
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        # object_rotation_coma は operators/coma_op.py 経由で addon register 時に
        # 既に import され、"coma" ハンドラーが登録済みのはず (下の assert で確認)。
        from bmanga_dev_coma_rotation.core.work import get_work
        from bmanga_dev_coma_rotation.operators import object_rotation, object_tool_selection
        from bmanga_dev_coma_rotation.utils import coma_border_object, coma_plane
        from bmanga_dev_coma_rotation.utils import object_selection
        from bmanga_dev_coma_rotation.utils.object_selection import SELECTION_HANDLE_OUTSET_MM

        _check(
            "coma" in object_rotation.ROTATION_HANDLERS,
            "object_rotation_coma のインポートで coma ハンドラーが登録されません",
        )

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        page = work.pages[0]

        # --- 準備: 矩形コマ1 (既存の page.comas[0] を流用し、既知の矩形へ上書き) ---
        if len(page.comas) > 0:
            panel1 = page.comas[0]
        else:
            panel1 = page.comas.add()
            panel1.id = "c01"
            panel1.coma_id = "c01"
            panel1.title = "c01"
        panel1.shape_type = "rect"
        x1, y1, w1, h1 = COMA1_RECT_MM
        panel1.rect_x_mm = x1
        panel1.rect_y_mm = y1
        panel1.rect_width_mm = w1
        panel1.rect_height_mm = h1
        if len(panel1.vertices) > 0:
            panel1.vertices.clear()
        coma_plane.ensure_coma_plane(scene, work, page, panel1)
        coma1_key = object_selection.coma_key(page, panel1)

        # --- 準備: 元から多角形のコマ2 (非軸並行の四角形) ---
        panel2 = page.comas.add()
        panel2.id = "c_rot2"
        panel2.coma_id = "c_rot2"
        panel2.title = "c_rot2"
        from bmanga_dev_coma_rotation.operators import coma_edge_move_op

        coma_edge_move_op._set_coma_polygon(panel2, list(COMA2_POLY_MM))
        coma_plane.ensure_coma_plane(scene, work, page, panel2)
        coma2_key = object_selection.coma_key(page, panel2)

        from bmanga_dev_coma_rotation.utils import page_grid

        ox_mm, oy_mm = page_grid.page_total_offset_mm(work, scene, 0)

        # ================================================================
        # 1. capture_rotation_snapshot (矩形コマ)
        # ================================================================
        object_selection.select_key(context, coma1_key, mode="single")
        _check(
            object_selection.get_keys(context) == [coma1_key],
            "コマ1の選択がset_keysに反映されません",
        )

        initial_rect = object_tool_selection.selection_bounds_for_key(context, coma1_key)
        _check(initial_rect is not None, "コマ1の選択矩形が取得できません")
        if initial_rect is None:
            raise AssertionError("selection_bounds_for_key が None のため以降のテストを続行できません")
        _check(
            abs(initial_rect.width - w1) < 1e-6 and abs(initial_rect.height - h1) < 1e-6,
            f"コマ1の初期AABBが期待値と異なります: width={initial_rect.width} height={initial_rect.height}",
        )

        snapshot1 = object_rotation.capture_rotation_snapshot(context, coma1_key)
        _check(snapshot1 is not None, "コマ1の回転スナップショットが作成できません")
        if snapshot1 is None:
            raise AssertionError("スナップショットが None のため以降のテストを続行できません")
        _check(
            snapshot1.get("kind") == "coma" and snapshot1.get("key") == coma1_key,
            f"スナップショットのkind/keyが不正です: {snapshot1.get('kind')!r} / {snapshot1.get('key')!r}",
        )
        _check(
            abs(float(snapshot1.get("base_rotation_deg", -999.0))) < 1e-9,
            f"コマのbase_rotation_degは常に0.0であるべきです: {snapshot1.get('base_rotation_deg')!r}",
        )
        _check(snapshot1.get("shape_type") == "rect", "コマ1のスナップショットshape_typeがrectではありません")
        rect_mm = snapshot1.get("rect_mm")
        _check(
            rect_mm == (x1, y1, w1, h1),
            f"コマ1のスナップショットrect_mmが元の値と一致しません: {rect_mm!r}",
        )
        center_mm1 = snapshot1.get("center_mm")
        _check(
            center_mm1 is not None
            and abs(center_mm1[0] - COMA1_CENTER_LOCAL_MM[0]) < 1e-6
            and abs(center_mm1[1] - COMA1_CENTER_LOCAL_MM[1]) < 1e-6,
            f"スナップショットの回転中心 (ローカルmm) が想定と異なります: {center_mm1!r} (期待={COMA1_CENTER_LOCAL_MM!r})",
        )

        # ================================================================
        # 2. apply_rotation_snapshot(90.0): 独立計算した期待座標と一致するか
        # ================================================================
        rect1_corners = [(x1, y1), (x1 + w1, y1), (x1 + w1, y1 + h1), (x1, y1 + h1)]
        object_rotation.apply_rotation_snapshot(context, snapshot1, 90.0)
        _check(panel1.shape_type == "polygon", "90度回転後にshape_typeがpolygonになりません")
        actual_90 = _panel_vertices_mm(panel1)
        expected_90 = _expected_points(rect1_corners, COMA1_CENTER_LOCAL_MM, 90.0)
        _check(
            _points_close(actual_90, expected_90, 1e-3),
            f"90度回転後の頂点座標が期待値と一致しません: actual={actual_90} expected={expected_90}",
        )

        # ================================================================
        # 3. apply_rotation_snapshot(45.0): 90度からの累積ではなく絶対角度であること
        # ================================================================
        object_rotation.apply_rotation_snapshot(context, snapshot1, 45.0)
        actual_45 = _panel_vertices_mm(panel1)
        expected_45 = _expected_points(rect1_corners, COMA1_CENTER_LOCAL_MM, 45.0)
        expected_cumulative_135 = _expected_points(rect1_corners, COMA1_CENTER_LOCAL_MM, 135.0)
        _check(
            _points_close(actual_45, expected_45, 1e-3),
            f"45度回転(絶対角度)後の頂点座標が期待値と一致しません: actual={actual_45} expected={expected_45}",
        )
        _check(
            not _points_close(actual_45, expected_cumulative_135, 1e-3),
            "回転が絶対角度ではなく累積(90+45=135相当)になっています",
        )

        # ================================================================
        # 4. restore (apply 0.0): 矩形へビット単位で完全復元
        # ================================================================
        object_rotation.restore_rotation_snapshot(context, snapshot1)
        _check(panel1.shape_type == "rect", "restore後にshape_typeがrectへ戻りません")
        _check(
            panel1.rect_x_mm == x1 and panel1.rect_y_mm == y1
            and panel1.rect_width_mm == w1 and panel1.rect_height_mm == h1,
            "restore後にrect_*_mmがビット単位で元の値に復元されません: "
            f"actual=({panel1.rect_x_mm}, {panel1.rect_y_mm}, {panel1.rect_width_mm}, {panel1.rect_height_mm}) "
            f"expected={(x1, y1, w1, h1)!r}",
        )
        _check(len(panel1.vertices) == 0, "restore後もvertices Collectionが残っています (矩形化未完了)")
        rect_restored = object_tool_selection.selection_bounds_for_key(context, coma1_key)
        _check(
            rect_restored is not None
            and abs(rect_restored.width - initial_rect.width) < 1e-6
            and abs(rect_restored.height - initial_rect.height) < 1e-6
            and abs(rect_restored.x - initial_rect.x) < 1e-6
            and abs(rect_restored.y - initial_rect.y) < 1e-6,
            f"restore後にAABBが元へ戻りません: {rect_restored!r} (期待={initial_rect!r})",
        )

        # ================================================================
        # 5. 元から多角形だったコマ2でも 2-4 相当が成立すること
        # ================================================================
        object_selection.select_key(context, coma2_key, mode="single")
        snapshot2 = object_rotation.capture_rotation_snapshot(context, coma2_key)
        _check(snapshot2 is not None, "コマ2(元々多角形)の回転スナップショットが作成できません")
        if snapshot2 is not None:
            _check(snapshot2.get("shape_type") == "polygon", "コマ2のスナップショットshape_typeがpolygonではありません")
            original_poly2 = snapshot2.get("vertices")
            _check(
                original_poly2 is not None and _points_close(original_poly2, COMA2_POLY_MM, 1e-6),
                f"コマ2のスナップショット頂点が元の多角形と一致しません: {original_poly2!r}",
            )
            center_mm2 = snapshot2.get("center_mm")
            xs2 = [p[0] for p in COMA2_POLY_MM]
            ys2 = [p[1] for p in COMA2_POLY_MM]
            expected_center2 = ((min(xs2) + max(xs2)) / 2.0, (min(ys2) + max(ys2)) / 2.0)
            _check(
                center_mm2 is not None
                and abs(center_mm2[0] - expected_center2[0]) < 1e-3
                and abs(center_mm2[1] - expected_center2[1]) < 1e-3,
                f"コマ2の回転中心が想定と異なります: {center_mm2!r} (期待={expected_center2!r})",
            )

            object_rotation.apply_rotation_snapshot(context, snapshot2, 90.0)
            actual2_90 = _panel_vertices_mm(panel2)
            expected2_90 = _expected_points(COMA2_POLY_MM, center_mm2, 90.0)
            _check(
                _points_close(actual2_90, expected2_90, 1e-3),
                f"コマ2の90度回転後の頂点座標が期待値と一致しません: actual={actual2_90} expected={expected2_90}",
            )

            object_rotation.apply_rotation_snapshot(context, snapshot2, 45.0)
            actual2_45 = _panel_vertices_mm(panel2)
            expected2_45 = _expected_points(COMA2_POLY_MM, center_mm2, 45.0)
            expected2_cumulative = _expected_points(COMA2_POLY_MM, center_mm2, 135.0)
            _check(
                _points_close(actual2_45, expected2_45, 1e-3),
                f"コマ2の45度回転(絶対角度)後の頂点座標が期待値と一致しません: actual={actual2_45} expected={expected2_45}",
            )
            _check(
                not _points_close(actual2_45, expected2_cumulative, 1e-3),
                "コマ2の回転が絶対角度ではなく累積になっています",
            )

            object_rotation.restore_rotation_snapshot(context, snapshot2)
            _check(panel2.shape_type == "polygon", "コマ2のrestore後にshape_typeがpolygonのままではありません")
            restored2 = _panel_vertices_mm(panel2)
            _check(
                _points_close(restored2, COMA2_POLY_MM, 1e-6),
                f"コマ2のrestore後に元の頂点座標へ完全復元されません: actual={restored2} expected={COMA2_POLY_MM}",
            )

        # ================================================================
        # 6. rotation_hit_with_priority: リング内でコマがヒットし、コマ頂点
        #    ハンドル相当のヒットがあれば優先されない (コマ1は矩形へ復元済み)
        # ================================================================
        object_selection.select_key(context, coma1_key, mode="single")
        rect_world = object_tool_selection.selection_bounds_for_key(context, coma1_key)
        _check(rect_world is not None, "コマ1(矩形復元後)の選択矩形が取得できません")
        if rect_world is not None:
            handle_point, ring_point = _diagonal_ring_point(rect_world, SELECTION_HANDLE_OUTSET_MM, _RING_OFFSET_MM)
            rot_hit = object_rotation.rotation_hit_with_priority(
                context, SimpleNamespace(), lambda _ctx, _ev: ring_point, hit=None,
            )
            _check(
                rot_hit is not None and rot_hit.get("kind") == "coma" and rot_hit.get("key") == coma1_key,
                f"コマの回転リングヒットが得られません: {rot_hit!r} (ring_point={ring_point!r})",
            )

            # リング外 (角からさらに外側) では回転ヒットしないこと。
            outside_point = (
                handle_point[0] + (1.0 / math.sqrt(2.0)) * 30.0,
                handle_point[1] + (1.0 / math.sqrt(2.0)) * 30.0,
            )
            rot_hit_outside = object_rotation.rotation_hit_with_priority(
                context, SimpleNamespace(), lambda _ctx, _ev: outside_point, hit=None,
            )
            _check(rot_hit_outside is None, f"リング外なのにコマの回転ヒットが返りました: {rot_hit_outside!r}")

            # 同一キーのコマ頂点□マーカー相当のヒットがあれば、回転より優先される
            # (コマ頂点編集の方が精密なハンドルのため)。
            fake_vertex_hit = {"kind": "coma_vertex", "part": "vertex", "key": coma1_key}
            rot_hit_excluded = object_rotation.rotation_hit_with_priority(
                context, SimpleNamespace(), lambda _ctx, _ev: ring_point, hit=fake_vertex_hit,
            )
            _check(
                rot_hit_excluded is None,
                f"同一キーのコマ頂点マーカーヒットがあるのに回転が優先されました: {rot_hit_excluded!r}",
            )
            # 別キーのコマ頂点マーカーヒットも、キーに関係なく回転より優先
            # されるべきである (敵対的レビューで確認された欠陥(a)の修正:
            # rotation_hit_with_priority の新ルール1。グラデーション端点
            # ハンドルのように精密ハンドルのキー形式が調査中キーと異なる
            # 場合でも、実際のクリックがどこかの精密ハンドルに当たっている
            # 以上、そのハンドル操作を優先しなければならない。旧実装は
            # 「precise_hit.key==調査中キー」の場合だけ排他していたため、
            # このケースでは誤ってリングが横取りしていた)。
            fake_other_key_vertex_hit = {"kind": "coma_vertex", "part": "vertex", "key": coma2_key}
            rot_hit_other_key_vertex = object_rotation.rotation_hit_with_priority(
                context, SimpleNamespace(), lambda _ctx, _ev: ring_point, hit=fake_other_key_vertex_hit,
            )
            _check(
                rot_hit_other_key_vertex is None,
                "新ルール1: 別キーのコマ頂点マーカーヒットでもキーに関係なく回転より"
                f"優先されるべきですが、回転が返りました (欠陥(a)の再現): {rot_hit_other_key_vertex!r}",
            )

        # ================================================================
        # 7. コマ内フキダシの x_mm/y_mm が回転前後で不変であること
        # ================================================================
        parent_key1 = f"{page.id}:{panel1.coma_id}"
        balloon = page.balloons.add()
        balloon.id = "coma_rotation_balloon"
        balloon.shape = "rect"
        balloon.parent_kind = "coma"
        balloon.parent_key = parent_key1
        balloon.x_mm = 30.0
        balloon.y_mm = 35.0
        balloon.width_mm = 20.0
        balloon.height_mm = 15.0
        balloon_x0, balloon_y0 = float(balloon.x_mm), float(balloon.y_mm)

        snapshot1_again = object_rotation.capture_rotation_snapshot(context, coma1_key)
        _check(snapshot1_again is not None, "コマ1の再スナップショットが作成できません")
        if snapshot1_again is not None:
            object_rotation.apply_rotation_snapshot(context, snapshot1_again, 60.0)
            _check(
                float(balloon.x_mm) == balloon_x0 and float(balloon.y_mm) == balloon_y0,
                f"コマ回転でフキダシのx_mm/y_mmが変化しました: ({balloon.x_mm}, {balloon.y_mm})"
                f" (期待=({balloon_x0}, {balloon_y0}))",
            )

            # ============================================================
            # 8. 回転後もコマ枠線・マスクの更新経路が例外なく走ること
            # ============================================================
            try:
                plane = coma_plane.ensure_coma_plane(scene, work, page, panel1)
                border = coma_border_object.ensure_coma_border_object(scene, work, page, panel1)
                _check(plane is not None, "回転後のコマ面(coma_plane)実体が取得できません")
                _check(border is not None, "回転後のコマ枠線(coma_border)実体が取得できません")
            except Exception as exc:  # noqa: BLE001
                _check(False, f"回転後のコマ面/枠線オブジェクト更新経路が例外を送出しました: {exc!r}")

            object_rotation.restore_rotation_snapshot(context, snapshot1_again)
            _check(
                float(balloon.x_mm) == balloon_x0 and float(balloon.y_mm) == balloon_y0,
                "コマ回転の復元後もフキダシのx_mm/y_mmが変化しています",
            )
            _check(panel1.shape_type == "rect", "8番検証後にコマ1がrectへ戻っていません")

        if FAILURES:
            for f in FAILURES:
                print(f"FAIL: {f}", flush=True)
            raise AssertionError(f"{len(FAILURES)} 件の検証失敗があります")
        print("BMANGA_COMA_ROTATION_OK", flush=True)
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
