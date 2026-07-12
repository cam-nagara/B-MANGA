"""Blender実機用: 画像パスレイヤー (kind="image_path"、パターンカーブ) の回転対応の回帰確認.

背景:
    「選択ハンドル四隅の少し外側をドラッグしてオブジェクトを中心軸回転する」
    機能 (operators/object_rotation.py) の回転コアは実装済みだが、画像パス
    レイヤー (kind="image_path") には rotation_deg 相当のプロパティが無い
    ため、専用ハンドラー (operators/object_rotation_image_path.py) で
    「パス頂点座標 (entry.path_points_json) の直接書き換え」方式により
    対応した (gp レイヤーの object_rotation_gp.py と同じ方式)。

    entry.path_points_json は「ページローカル mm」(ページのグリッド上の
    ワールドオフセットを含まない座標) で保存されている一方、選択矩形
    (selection_bounds_for_key) は実体メッシュの「ワールド」境界矩形を返す。
    そのため回転中心は、ワールド中心からページのオフセット
    (utils.image_real_object.entry_page_offset_mm) を差し引いてページ
    ローカル mm へ変換してから使う必要がある。本テストは既定の作品設定
    (start_side="left", read_direction="left") では 1 ページ目であっても
    グリッド上の「見開き逆側の空白スロット」補正でオフセットが非ゼロになる
    ことを利用し、このオフセット変換が正しく行われているかを検証する
    (もしオフセットが 0 のままだと `_check` でその前提条件自体が失敗として
    報告される)。

本テストはヘッドレスで以下を確認する:
    1. 画像パスレイヤーを選択した状態で capture_rotation_snapshot が
       有効なスナップショットを返す (レジストリへの登録確認)。
    2. apply_rotation_snapshot(90.0) で、既知のパス頂点が「選択矩形中心
       周りに90度回転」した期待座標 (テスト内で独立計算) へ移動する。
       編集用ベジェカーブのハンドル座標 (co からの相対ベクトル) も、
       同じ角度で回転した値になっていること。
    3. 続けて apply_rotation_snapshot(45.0) を呼ぶと「90度からの累積」
       ではなく「元位置から45度」の絶対角度になること。
    4. restore_rotation_snapshot (= apply 0.0) で path_points_json が
       元の文字列と完全一致すること (float誤差を残さない検証)。
    5. rotation_hit_with_priority が、画像パスレイヤー選択中のハンドル
       表示角の外側リング座標で kind=="image_path" の rot_hit を返すこと。
    6. 選択矩形 (selection_bounds_for_key) が回転後の頂点位置に追従して
       変わる (AABB の幅/高さが入れ替わる) こと。
    7. 表示メッシュ実体の rotation_euler が回転後も (0,0,0) のまま
       (プロパティ回転ではなく頂点焼き込み方式であることの確認)。

実行:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python test\\blender_image_path_rotation_check.py
"""

from __future__ import annotations

import importlib.util
import json
import math
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_image_path_rotation"

FAILURES: list[str] = []

# object_rotation の回転リング (表示角からの距離 3.0mm < d <= 8.0mm) の中央値。
_RING_OFFSET_MM = 5.0

# 画像パスへ書き込む既知のパス頂点 (ページローカル mm、ページオフセット
# 適用前)。utils/image_path_object.ensure_image_path_object はスタンプ配置
# 前に _smooth_path_points (Catmull-Rom平滑化、3点以上でのみ作用) を通す
# ため、頂点3点以上の折れ線だと角が丸まって/膨らんでAABBを解析的に予測
# できなくなる。ちょうど2点 (直線) にすると平滑化が完全にスキップされる
# (_smooth_path_points は len(points)<3 ならそのまま返す) ので、実体メッシュ
# のワールド境界矩形をテスト側で厳密に予測できる。
KNOWN_POINTS_MM: list[tuple[float, float]] = [
    (20.0, 30.0),
    (50.0, 30.0),
]
LOCAL_CENTER_MM = (35.0, 30.0)
BRUSH_SIZE_MM = 10.0
_MARGIN_MM = BRUSH_SIZE_MM * 0.5  # aspect=1.0 の正方形スタンプの半幅・半高

# 経路(直線、水平)の初期AABB: 幅30 x 高さ0。正方形スタンプは水平線に
# 沿ってどこでも angle=0 (atan2(0,30)) のまま (2点しかないため一区間だけ)
# で、マージンは常に軸並行 (±margin) に均等展開されるため、実体メッシュの
# ワールドAABBは (30+2*margin) x (0+2*margin) になる。
_EXPECTED_W_INITIAL = 30.0 + _MARGIN_MM * 2.0
_EXPECTED_H_INITIAL = 0.0 + _MARGIN_MM * 2.0
# 90度回転後は経路が垂直線になり、AABBの幅/高さが入れ替わる。
_EXPECTED_W_AFTER_90 = 0.0 + _MARGIN_MM * 2.0
_EXPECTED_H_AFTER_90 = 30.0 + _MARGIN_MM * 2.0


# 回転そのものはPython double演算のみで完結するが、回転中心(center_mm)は
# 実体メッシュのワールド境界矩形 (bound_box) から求めるため、Blenderの
# メッシュ頂点がfloat32格納であることに由来する 1e-6mm 程度の丸め誤差を
# 含む。この誤差は回転後の座標にもそのまま伝播するため、JSON側の許容も
# メッシュ側と同程度に緩める。
_TOL_JSON_MM = 1e-3
_TOL_MESH_MM = 2e-2  # selection_bounds_for_key はメッシュ(bound_box)経由


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
    return [_rotate_xy(x, y, LOCAL_CENTER_MM[0], LOCAL_CENTER_MM[1], angle_deg) for x, y in KNOWN_POINTS_MM]


def _points_close(actual: list[tuple[float, float]], expected: list[tuple[float, float]], tol_mm: float) -> bool:
    if len(actual) != len(expected):
        return False
    return all(
        abs(ax - ex) <= tol_mm and abs(ay - ey) <= tol_mm
        for (ax, ay), (ex, ey) in zip(actual, expected)
    )


def _expected_handle_offsets_mm(points: list[tuple[float, float]]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """テスト側の独立実装: utils/image_path_object._edit_curve_handle_positions
    と同じ「近傍点からの接線推定」の式を、co からの相対ベクトル (mm) として
    再現する (co を経由しない差分なので、どんな平行移動・回転にも
    R(a)-R(b) = R(a-b) の線形性がそのまま使える)。

    戻り値は [(left_offset, right_offset), ...] (各要素は (dx, dy) mm)。
    """
    n = len(points)
    out: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for i in range(n):
        if n < 2:
            out.append(((0.0, 0.0), (0.0, 0.0)))
            continue
        if n == 2:
            other = points[1 - i]
            delta = ((other[0] - points[i][0]) / 3.0, (other[1] - points[i][1]) / 3.0)
            out.append(((0.0, 0.0), delta) if i == 0 else (delta, (0.0, 0.0)))
            continue
        if i == 0:
            nxt = points[1]
            out.append(((0.0, 0.0), ((nxt[0] - points[0][0]) / 3.0, (nxt[1] - points[0][1]) / 3.0)))
            continue
        if i == n - 1:
            prv = points[n - 2]
            out.append((((points[n - 1][0] - prv[0]) / -3.0, (points[n - 1][1] - prv[1]) / -3.0), (0.0, 0.0)))
            continue
        prv, nxt = points[i - 1], points[i + 1]
        tangent = ((nxt[0] - prv[0]) / 6.0, (nxt[1] - prv[1]) / 6.0)
        out.append(((-tangent[0], -tangent[1]), (tangent[0], tangent[1])))
    return out


def _curve_handle_offsets_mm(curve_obj) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    from bmanga_dev_image_path_rotation.utils.geom import m_to_mm

    spline = curve_obj.data.splines[0]
    out = []
    for bp in spline.bezier_points:
        co = bp.co
        left = bp.handle_left
        right = bp.handle_right
        out.append((
            (m_to_mm(left.x - co.x), m_to_mm(left.y - co.y)),
            (m_to_mm(right.x - co.x), m_to_mm(right.y - co.y)),
        ))
    return out


def _offsets_close(
    actual: list[tuple[tuple[float, float], tuple[float, float]]],
    expected: list[tuple[tuple[float, float], tuple[float, float]]],
    tol_mm: float,
) -> bool:
    if len(actual) != len(expected):
        return False
    for (a_left, a_right), (e_left, e_right) in zip(actual, expected):
        if abs(a_left[0] - e_left[0]) > tol_mm or abs(a_left[1] - e_left[1]) > tol_mm:
            return False
        if abs(a_right[0] - e_right[0]) > tol_mm or abs(a_right[1] - e_right[1]) > tol_mm:
            return False
    return True


def _diagonal_ring_point(rect_world, outset: float, ring_offset: float):
    x, y, w, h = rect_world.x, rect_world.y, rect_world.width, rect_world.height
    handle = (x + w + outset, y + h + outset)
    direction = (1.0 / math.sqrt(2.0), 1.0 / math.sqrt(2.0))
    ring_point = (handle[0] + direction[0] * ring_offset, handle[1] + direction[1] * ring_offset)
    return handle, ring_point


def _run_check() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_image_path_rotation_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "ImagePathRotationCheck.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        # object_rotation_image_path は operators/image_path_tool_op.py 経由で
        # addon register 時に既に import され、"image_path" ハンドラーが
        # 登録済みのはず (下の assert で確認する)。
        from bmanga_dev_image_path_rotation.operators import object_rotation, object_tool_selection
        from bmanga_dev_image_path_rotation.utils import image_path_object, image_real_object
        from bmanga_dev_image_path_rotation.utils import object_selection
        from bmanga_dev_image_path_rotation.utils.object_selection import SELECTION_HANDLE_OUTSET_MM

        context = bpy.context
        scene = context.scene
        work = scene.bmanga_work
        page = work.pages[0]

        # レジストリに "image_path" ハンドラーが登録されていること (import時副作用の確認)。
        _check(
            "image_path" in object_rotation.ROTATION_HANDLERS,
            "object_rotation_image_path のインポートで image_path ハンドラーが登録されません",
        )

        entry = scene.bmanga_image_path_layers.add()
        entry.id = "image_path_rotation_check"
        entry.title = "回転確認用パターンカーブ"
        entry.parent_kind = "page"
        entry.parent_key = str(getattr(page, "id", "") or "")
        entry.path_points_json = json.dumps([list(p) for p in KNOWN_POINTS_MM])
        entry.draw_mode = "stamp"
        entry.brush_size_mm = BRUSH_SIZE_MM
        entry.aspect_ratio = 1.0
        entry.spacing_percent = 100.0

        obj = image_path_object.ensure_image_path_object(scene=scene, entry=entry, page=page)
        _check(obj is not None, "画像パス実体が作成されません")
        if obj is None:
            raise AssertionError("画像パス実体が None のため以降のテストを続行できません")

        # --- テスト前提: ページオフセットが非ゼロであること (回転中心の空間
        # 変換 = entry_page_offset_mm 差し引きが本当に効いているかを確認する
        # ための前提。既定の作品設定 (start_side="left", read_direction="left")
        # では1ページ目でも見開き逆側の空白スロット補正で非ゼロになる)。
        ox_mm, oy_mm = image_real_object.entry_page_offset_mm(scene, work, entry, page)
        _check(
            abs(ox_mm) > 1.0 or abs(oy_mm) > 1.0,
            f"テスト前提条件: ページのグリッドオフセットが0近辺です (ox={ox_mm}, oy={oy_mm})。"
            "page_grid のレイアウト仕様が変わった可能性があり、このテストの座標空間検証が"
            "弱まっています (要調整)。",
        )

        key = object_selection.image_path_key(entry)
        object_selection.select_key(context, key, mode="single")
        _check(
            object_selection.get_keys(context) == [key],
            "画像パスの選択がset_keysに反映されません",
        )

        # --- 準備: 初期状態のワールド選択矩形 (AABB確認・リング座標計算用) ---
        initial_rect = object_tool_selection.selection_bounds_for_key(context, key)
        _check(initial_rect is not None, "画像パスの選択矩形が取得できません")
        if initial_rect is None:
            raise AssertionError("selection_bounds_for_key が None のため以降のテストを続行できません")
        _check(
            abs(initial_rect.width - _EXPECTED_W_INITIAL) < _TOL_MESH_MM
            and abs(initial_rect.height - _EXPECTED_H_INITIAL) < _TOL_MESH_MM,
            f"初期AABBが期待値と異なります: width={initial_rect.width} height={initial_rect.height} "
            f"(期待 width={_EXPECTED_W_INITIAL} height={_EXPECTED_H_INITIAL})",
        )

        # --- 1. capture_rotation_snapshot ---
        snapshot = object_rotation.capture_rotation_snapshot(context, key)
        _check(snapshot is not None, "画像パスの回転スナップショットが作成できません (レジストリ未登録の疑い)")
        if snapshot is None:
            raise AssertionError("スナップショットが None のため以降のテストを続行できません")
        _check(
            snapshot.get("kind") == "image_path" and snapshot.get("key") == key,
            f"スナップショットのkind/keyが不正です: {snapshot.get('kind')!r} / {snapshot.get('key')!r}",
        )
        _check(
            abs(float(snapshot.get("base_rotation_deg", -999.0))) < 1e-9,
            f"image_pathのbase_rotation_degは常に0.0であるべきです: {snapshot.get('base_rotation_deg')!r}",
        )
        original_json = str(entry.path_points_json)
        _check(
            str(snapshot.get("original_json", "")) == original_json,
            "スナップショットの元JSON文字列が現在値と一致しません",
        )
        center_mm = snapshot.get("center_mm")
        _check(
            center_mm is not None
            and abs(center_mm[0] - LOCAL_CENTER_MM[0]) < _TOL_MESH_MM
            and abs(center_mm[1] - LOCAL_CENTER_MM[1]) < _TOL_MESH_MM,
            f"スナップショットの回転中心が想定と異なります: {center_mm!r} (期待={LOCAL_CENTER_MM!r})。"
            "ワールド中心からのページオフセット差し引きが正しく行われていない疑いがあります。",
        )

        # --- 2. apply_rotation_snapshot(90.0): 独立計算した期待座標と一致するか ---
        object_rotation.apply_rotation_snapshot(context, snapshot, 90.0)
        actual_90 = [(float(p[0]), float(p[1])) for p in json.loads(entry.path_points_json)]
        expected_90 = _expected_points(90.0)
        _check(
            _points_close(actual_90, expected_90, _TOL_JSON_MM),
            f"90度回転後のパス頂点が期待値と一致しません: actual={actual_90} expected={expected_90}",
        )

        curve_obj = bpy.data.objects.get(f"image_path_curve_{entry.id}")
        _check(curve_obj is not None, "編集用カーブ実体が見つかりません")
        if curve_obj is not None:
            actual_offsets = _curve_handle_offsets_mm(curve_obj)
            expected_offsets_base = _expected_handle_offsets_mm(KNOWN_POINTS_MM)
            expected_offsets_90 = [
                (_rotate_xy(l[0], l[1], 0.0, 0.0, 90.0), _rotate_xy(r[0], r[1], 0.0, 0.0, 90.0))
                for l, r in expected_offsets_base
            ]
            _check(
                _offsets_close(actual_offsets, expected_offsets_90, _TOL_MESH_MM),
                "90度回転後の編集用カーブのベジェハンドルが期待値 (制御点と同じ角度で回転した"
                f"ベクトル) と一致しません: actual={actual_offsets} expected={expected_offsets_90}",
            )

        # --- 6. 回転後にAABBが追従して変わること (幅/高さが入れ替わる) ---
        rect_90 = object_tool_selection.selection_bounds_for_key(context, key)
        _check(rect_90 is not None, "90度回転後の選択矩形が取得できません")
        if rect_90 is not None:
            _check(
                abs(rect_90.width - _EXPECTED_W_AFTER_90) < _TOL_MESH_MM
                and abs(rect_90.height - _EXPECTED_H_AFTER_90) < _TOL_MESH_MM,
                f"90度回転後のAABBが追従して更新されていません: width={rect_90.width} height={rect_90.height} "
                f"(期待 width={_EXPECTED_W_AFTER_90} height={_EXPECTED_H_AFTER_90})",
            )

        # --- 7. 表示メッシュ実体のrotation_eulerが(0,0,0)のまま (焼き込み方式の確認) ---
        _check(
            all(abs(float(v)) < 1e-9 for v in obj.rotation_euler),
            f"表示メッシュのrotation_eulerが変化しています (プロパティ回転になっている疑い): {tuple(obj.rotation_euler)!r}",
        )

        # --- 3. apply_rotation_snapshot(45.0): 90度からの累積ではなく絶対角度であること ---
        object_rotation.apply_rotation_snapshot(context, snapshot, 45.0)
        actual_45 = [(float(p[0]), float(p[1])) for p in json.loads(entry.path_points_json)]
        expected_45 = _expected_points(45.0)
        expected_cumulative_135 = _expected_points(135.0)
        _check(
            _points_close(actual_45, expected_45, _TOL_JSON_MM),
            f"45度回転(絶対角度)後のパス頂点が期待値と一致しません: actual={actual_45} expected={expected_45}",
        )
        _check(
            not _points_close(actual_45, expected_cumulative_135, 1.0),
            "回転が絶対角度ではなく累積(90+45=135相当)になっています",
        )

        # --- 4. restore_rotation_snapshot: path_points_jsonが元の文字列と完全一致 ---
        object_rotation.restore_rotation_snapshot(context, snapshot)
        _check(
            str(entry.path_points_json) == original_json,
            f"restore後にpath_points_jsonが元の文字列と完全一致しません: "
            f"actual={entry.path_points_json!r} expected={original_json!r}",
        )
        rect_restored = object_tool_selection.selection_bounds_for_key(context, key)
        _check(
            rect_restored is not None
            and abs(rect_restored.width - initial_rect.width) < _TOL_MESH_MM
            and abs(rect_restored.height - initial_rect.height) < _TOL_MESH_MM
            and abs(rect_restored.x - initial_rect.x) < _TOL_MESH_MM
            and abs(rect_restored.y - initial_rect.y) < _TOL_MESH_MM,
            f"restore後にAABBが元へ戻りません: {rect_restored!r} (期待={initial_rect!r})",
        )

        # --- 5. rotation_hit_with_priority: ハンドル表示角の外側リングでimage_pathがヒットすること ---
        handle_point, ring_point = _diagonal_ring_point(initial_rect, SELECTION_HANDLE_OUTSET_MM, _RING_OFFSET_MM)
        rot_hit = object_rotation.rotation_hit_with_priority(
            context, SimpleNamespace(), lambda _ctx, _ev: ring_point, hit=None,
        )
        _check(
            rot_hit is not None and rot_hit.get("kind") == "image_path" and rot_hit.get("key") == key,
            f"画像パスの回転リングヒットが得られません: {rot_hit!r} (ring_point={ring_point!r})",
        )
        # リング外 (角からさらに外側) では回転ヒットしないこと (安全側の確認)。
        outside_point = (
            handle_point[0] + (1.0 / math.sqrt(2.0)) * 30.0,
            handle_point[1] + (1.0 / math.sqrt(2.0)) * 30.0,
        )
        rot_hit_outside = object_rotation.rotation_hit_with_priority(
            context, SimpleNamespace(), lambda _ctx, _ev: outside_point, hit=None,
        )
        _check(rot_hit_outside is None, f"リング外なのに画像パスの回転ヒットが返りました: {rot_hit_outside!r}")

        if FAILURES:
            for f in FAILURES:
                print(f"FAIL: {f}", flush=True)
            raise AssertionError(f"{len(FAILURES)} 件の検証失敗があります")
        print("BMANGA_IMAGE_PATH_ROTATION_OK", flush=True)
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
