"""Blender実機用: 塗りつぶしレイヤー (kind="fill") の回転対応の回帰確認.

背景:
    「選択ハンドル四隅の少し外側をドラッグしてオブジェクトを中心軸回転する」
    機能 (operators/object_rotation.py) の回転コアは実装済みで、balloon/image/
    text/gp に続き、塗りつぶしレイヤー (utils/fill_real_object.py が
    fill_id ごとに生成する MESH 実体) にも対応した
    (operators/object_rotation_fill.py で "fill" ハンドラーを登録)。

    塗りのメッシュ・UV・グラデーション材質ノードは balloon/image と同じく
    「中心基準のローカル座標」で構築され、obj.location をその中心へ置いた
    直後に obj.rotation_euler[2] を足すだけで正しい中心軸回転になる
    (角度指定グラデーション・ベタ塗りは剛体回転として完全に一貫する)。

    ただし、グラデーション端点指定 (use_gradient_endpoints=True) には
    「絶対mm座標を直接参照する独立オブジェクト (始点/終点ドラッグハンドル)」
    と「同じく絶対mm座標を参照するオーバーレイの接続線 (ui/overlay.py)」が
    あり、塗り実体だけを回転させるとこれらが追従せず視覚的に不整合になる。
    二重回転 (材質焼き込み分 + オブジェクト回転分) を避けつつ一貫性を保証する
    設計変更は本タスクの範囲を大きく超えるため、視覚不整合を出さない安全側の
    判断として、端点指定の塗りは capture_rotation_snapshot が None を返し
    (実際の回転が一切適用されない) 扱いにしている
    (詳細は operators/object_rotation_fill.py のモジュール docstring 参照)。

    なお rotation_hit_with_priority (回転リングのジオメトリ判定) は kind
    単位でレジストリを引くだけで、entry ごとの capture_fn は呼ばない
    (呼ぶのは実際にドラッグを開始する _start_rotation_drag の時点)。その
    ため端点指定の塗りでもリング自体はジオメトリ的にヒットし得るが、
    ドラッグ開始時に capture_rotation_snapshot が None を返すことで
    self._rotate_snapshots が空リストになり、実際には何も回転しない
    (object_tool_op.py 側の既存の仕組み。本テストでは capture 自体が
    None になることを直接検証する)。

本テストはヘッドレスで以下を確認する:
    1. BMangaFillLayer に rotation_deg があり既定値が 0.0 であること。
    2. 矩形塗り (use_region) を選択した状態で capture_rotation_snapshot が
       有効なスナップショットを返す (レジストリへの登録確認)。
    3. apply_rotation_snapshot(30.0) で entry.rotation_deg==30、実体
       obj.rotation_euler.z==radians(30)、obj.location (中心) は不変。
    4. restore_rotation_snapshot で 0 に戻り、実体の回転も0に戻る。
    5. 投げ縄塗り (lasso) でも 2-4 と同じ挙動になること。
    6. schema (fill_layer_to_dict/from_dict) のラウンドトリップと、
       rotationDeg キー無しの dict では 0.0 になること。
    7. rotation_hit_with_priority が、矩形塗り選択中のハンドル表示角の
       外側リング座標で kind=="fill" の rot_hit を返すこと。
    8. グラデーション端点指定の塗りは capture_rotation_snapshot が None を
       返す (回転非対応) こと。また rotation_deg に値が残っていても実体の
       rotation_euler.z は常に0のままであること (トグル順序に依存しない
       安全側の保証)。
    9. 端点指定グラデーション (use_gradient_endpoints=True) のまま fill_type
       だけを solid へ変更すると、残っていた rotation_deg が capture/
       rotation_euler の両方へ正しく反映されること (敵対的レビューで確認
       された欠陥: 判定条件が use_gradient_endpoints 単独だと fill_type
       変更後も回転が永久に無反応のままになっていた)。

実行:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python test\\blender_fill_rotation_check.py
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
PACKAGE = "bmanga_dev_fill_rotation"

FAILURES: list[str] = []

# object_rotation の回転リング (表示角からの距離 3.0mm < d <= 8.0mm) の中央値。
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


def _diagonal_ring_point(rect_world, outset: float, ring_offset: float):
    """rect_world (AABB) の右上ハンドル位置から、回転リング内の対角座標を作る.

    test/blender_gp_rotation_check.py と同じ考え方 (handle_rect_for_bounds =
    rect.inset(-outset) の右上角から、さらに ring_offset だけ外側)。
    """
    x, y, w, h = rect_world.x, rect_world.y, rect_world.width, rect_world.height
    handle = (x + w + outset, y + h + outset)
    direction = (1.0 / math.sqrt(2.0), 1.0 / math.sqrt(2.0))
    ring_point = (handle[0] + direction[0] * ring_offset, handle[1] + direction[1] * ring_offset)
    return handle, ring_point


def _check_rotation_lifecycle(
    object_rotation, object_selection, object_naming, fill_key: str, entry, obj,
) -> None:
    """capture -> apply(30) -> restore(0) の一連の挙動を検証する共通処理.

    矩形塗り・投げ縄塗りの両方から呼ばれる (要件3-4/5 に対応)。
    """
    object_selection.select_key(bpy.context, fill_key, mode="single")
    _check(
        object_selection.get_keys(bpy.context) == [fill_key],
        f"{entry.id}: 選択がset_keysに反映されません",
    )

    snapshot = object_rotation.capture_rotation_snapshot(bpy.context, fill_key)
    _check(snapshot is not None, f"{entry.id}: 回転スナップショットが作成できません (レジストリ未登録の疑い)")
    if snapshot is None:
        return
    _check(
        snapshot.get("kind") == "fill" and snapshot.get("key") == fill_key,
        f"{entry.id}: スナップショットのkind/keyが不正です: {snapshot!r}",
    )
    _check(
        abs(float(snapshot.get("base_rotation_deg", -999.0))) < 1e-9,
        f"{entry.id}: 初期状態のbase_rotation_degは0であるべきです: {snapshot.get('base_rotation_deg')!r}",
    )

    loc_before = (obj.location.x, obj.location.y, obj.location.z)

    object_rotation.apply_rotation_snapshot(bpy.context, snapshot, 30.0)
    _check(
        abs(float(entry.rotation_deg) - 30.0) < 1e-6,
        f"{entry.id}: apply後にentry.rotation_degが30になりません: {entry.rotation_deg!r}",
    )
    obj_after = object_naming.find_object_by_bmanga_id(str(entry.id), kind="fill")
    _check(obj_after is not None, f"{entry.id}: 回転後に実体オブジェクトが見つかりません")
    if obj_after is not None:
        _check(
            abs(float(obj_after.rotation_euler[2]) - math.radians(30.0)) < 1e-6,
            f"{entry.id}: obj.rotation_euler.zが期待値と異なります: {obj_after.rotation_euler[2]!r}",
        )
        loc_after = (obj_after.location.x, obj_after.location.y, obj_after.location.z)
        _check(
            all(abs(a - b) < 1e-6 for a, b in zip(loc_before, loc_after, strict=True)),
            f"{entry.id}: 回転しても中心位置(location)が変わらないはずです: before={loc_before} after={loc_after}",
        )

    object_rotation.restore_rotation_snapshot(bpy.context, snapshot)
    _check(
        abs(float(entry.rotation_deg)) < 1e-6,
        f"{entry.id}: restore後にentry.rotation_degが0に戻りません: {entry.rotation_deg!r}",
    )
    obj_restored = object_naming.find_object_by_bmanga_id(str(entry.id), kind="fill")
    _check(
        obj_restored is not None and abs(float(obj_restored.rotation_euler[2])) < 1e-6,
        f"{entry.id}: restore後にobj.rotation_euler.zが0に戻りません: "
        f"{obj_restored.rotation_euler[2] if obj_restored is not None else None!r}",
    )


def _run_check() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_fill_rotation_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "FillRotationCheck.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        # object_rotation_fill は operators/fill_tool_op.py 経由で addon
        # register 時に既に import され、"fill" ハンドラーが登録済みのはず。
        from bmanga_dev_fill_rotation.io import schema
        from bmanga_dev_fill_rotation.operators import object_rotation, object_tool_selection
        from bmanga_dev_fill_rotation.utils import fill_real_object, object_naming, object_selection
        from bmanga_dev_fill_rotation.utils.object_selection import SELECTION_HANDLE_OUTSET_MM

        context = bpy.context
        scene = context.scene
        work = scene.bmanga_work
        page = work.pages[0]

        _check(
            "fill" in object_rotation.ROTATION_HANDLERS,
            "object_rotation_fill のインポートで fill ハンドラーが登録されません",
        )

        # --- 1. BMangaFillLayer.rotation_deg の既定値確認 ---
        entry_rect = scene.bmanga_fill_layers.add()
        _check(
            hasattr(entry_rect, "rotation_deg") and abs(float(entry_rect.rotation_deg)) < 1e-9,
            f"rotation_degの既定値が0.0ではありません: {getattr(entry_rect, 'rotation_deg', 'MISSING')!r}",
        )
        entry_rect.id = "fill_rotation_rect"
        entry_rect.title = "矩形塗り"
        entry_rect.fill_type = "solid"
        entry_rect.color = (0.8, 0.1, 0.1, 1.0)
        entry_rect.use_region = True
        entry_rect.region_x_mm = 20.0
        entry_rect.region_y_mm = 30.0
        entry_rect.region_width_mm = 60.0
        entry_rect.region_height_mm = 40.0
        obj_rect = fill_real_object.ensure_fill_real_object(scene=scene, entry=entry_rect, page=page)
        assert obj_rect is not None
        _check(
            len(obj_rect.data.polygons) == 1,
            f"矩形塗りは単一の矩形面のはずです: polygons={len(obj_rect.data.polygons)}",
        )

        fill_key_rect = object_selection.fill_key(entry_rect)

        # --- 2-4. 矩形塗りの capture -> apply(30) -> restore(0) ---
        _check_rotation_lifecycle(
            object_rotation, object_selection, object_naming, fill_key_rect, entry_rect, obj_rect,
        )

        # --- 7. rotation_hit_with_priority (矩形塗り選択中) ---
        object_selection.select_key(context, fill_key_rect, mode="single")
        initial_rect = object_tool_selection.selection_bounds_for_key(context, fill_key_rect)
        _check(initial_rect is not None, "矩形塗りの選択矩形が取得できません")
        if initial_rect is not None:
            handle_point, ring_point = _diagonal_ring_point(
                initial_rect, SELECTION_HANDLE_OUTSET_MM, _RING_OFFSET_MM,
            )
            rot_hit = object_rotation.rotation_hit_with_priority(
                context, SimpleNamespace(), lambda _ctx, _ev: ring_point, hit=None,
            )
            _check(
                rot_hit is not None and rot_hit.get("kind") == "fill" and rot_hit.get("key") == fill_key_rect,
                f"矩形塗りの回転リングヒットが得られません: {rot_hit!r} (ring_point={ring_point!r})",
            )
            outside_point = (
                handle_point[0] + (1.0 / math.sqrt(2.0)) * 30.0,
                handle_point[1] + (1.0 / math.sqrt(2.0)) * 30.0,
            )
            rot_hit_outside = object_rotation.rotation_hit_with_priority(
                context, SimpleNamespace(), lambda _ctx, _ev: outside_point, hit=None,
            )
            _check(rot_hit_outside is None, f"リング外なのに矩形塗りの回転ヒットが返りました: {rot_hit_outside!r}")

        # --- 5. 投げ縄塗り (lasso) でも 2-4 と同じ挙動になること ---
        entry_lasso = scene.bmanga_fill_layers.add()
        entry_lasso.id = "fill_rotation_lasso"
        entry_lasso.title = "投げ縄塗り"
        entry_lasso.fill_type = "solid"
        entry_lasso.use_region = True
        entry_lasso.region_x_mm = 20.0
        entry_lasso.region_y_mm = 30.0
        entry_lasso.region_width_mm = 60.0
        entry_lasso.region_height_mm = 40.0
        # region (x:20-80, y:30-70) 内に収まる矩形の投げ縄頂点。
        entry_lasso.lasso_points_json = json.dumps(
            [[30.0, 40.0], [70.0, 40.0], [70.0, 60.0], [30.0, 60.0]],
        )
        obj_lasso = fill_real_object.ensure_fill_real_object(scene=scene, entry=entry_lasso, page=page)
        assert obj_lasso is not None
        # _rebuild_lasso_mesh は bmesh.ops.triangulate で面を分割するため、
        # 単一矩形面のまま (_rebuild_mesh) ではなく投げ縄経路を通ったことを
        # 三角形2面になっていることで確認する。
        _check(
            len(obj_lasso.data.polygons) == 2,
            f"投げ縄塗りは三角形2面のはずです (投げ縄経路が使われていない疑い): "
            f"polygons={len(obj_lasso.data.polygons)}",
        )
        fill_key_lasso = object_selection.fill_key(entry_lasso)
        _check_rotation_lifecycle(
            object_rotation, object_selection, object_naming, fill_key_lasso, entry_lasso, obj_lasso,
        )

        # --- 6. schema ラウンドトリップ ---
        entry_rect.rotation_deg = 45.5
        data = schema.fill_layer_to_dict(entry_rect)
        _check(
            abs(float(data.get("rotationDeg", -1.0)) - 45.5) < 1e-3,
            f"fill_layer_to_dictのrotationDegが期待値と異なります: {data.get('rotationDeg')!r}",
        )
        entry_rect.rotation_deg = 0.0

        entry_roundtrip = scene.bmanga_fill_layers.add()
        entry_roundtrip.id = "fill_rotation_roundtrip"
        schema.fill_layer_from_dict(entry_roundtrip, data)
        _check(
            abs(float(entry_roundtrip.rotation_deg) - 45.5) < 1e-3,
            f"fill_layer_from_dictでrotation_degが復元されません: {entry_roundtrip.rotation_deg!r}",
        )

        entry_no_key = scene.bmanga_fill_layers.add()
        entry_no_key.id = "fill_rotation_no_key"
        entry_no_key.rotation_deg = 99.0  # from_dict呼び出し前の値がリセットされることを確認
        schema.fill_layer_from_dict(entry_no_key, {"id": "fill_rotation_no_key", "fillType": "solid"})
        _check(
            abs(float(entry_no_key.rotation_deg)) < 1e-9,
            f"rotationDegキー無しのdictからは0.0になるはずです: {entry_no_key.rotation_deg!r}",
        )

        # --- 8. グラデーション端点指定の塗りは回転対象外 ---
        entry_grad = scene.bmanga_fill_layers.add()
        entry_grad.id = "fill_rotation_gradient_endpoint"
        entry_grad.title = "端点グラデーション"
        entry_grad.fill_type = "gradient"
        entry_grad.gradient_type = "linear"
        entry_grad.use_gradient_endpoints = True
        entry_grad.gradient_start_x_mm = 20.0
        entry_grad.gradient_start_y_mm = 20.0
        entry_grad.gradient_end_x_mm = 150.0
        entry_grad.gradient_end_y_mm = 200.0
        obj_grad = fill_real_object.ensure_fill_real_object(scene=scene, entry=entry_grad, page=page)
        assert obj_grad is not None

        fill_key_grad = object_selection.fill_key(entry_grad)
        object_selection.select_key(context, fill_key_grad, mode="single")
        snapshot_grad = object_rotation.capture_rotation_snapshot(context, fill_key_grad)
        _check(
            snapshot_grad is None,
            f"端点グラデーション塗りは回転対象外 (capture=None) のはずですが取得できました: {snapshot_grad!r}",
        )

        # rotation_deg に値が残っていても実体は常に無回転であることの確認
        # (端点指定を先にONにしてから値を入れる順序でも安全であることの保証)。
        entry_grad.rotation_deg = 45.0
        obj_grad_after = object_naming.find_object_by_bmanga_id(str(entry_grad.id), kind="fill")
        _check(
            obj_grad_after is not None and abs(float(obj_grad_after.rotation_euler[2])) < 1e-9,
            f"端点グラデーション塗りはrotation_degに値があっても無回転のはずです: "
            f"{obj_grad_after.rotation_euler[2] if obj_grad_after is not None else None!r}",
        )

        # --- 9. fill_type変更との整合 (敵対的レビューで確認された欠陥の修正) ---
        # 判定条件が「use_gradient_endpointsのみ」だと、端点グラデ→ベタ塗りへ
        # fill_typeを変更してもuse_gradient_endpoints=Trueが残留するため、
        # パネルの回転欄は編集可能なのに回転だけ永久に無反応になる不整合が
        # 起きていた。fill_type=="gradient" かつ use_gradient_endpoints の
        # 両方を見る is_gradient_endpoint_rotation_locked へ判定条件を統一した
        # ことで、fill_type変更に追従して回転が復活するはずである。
        _check(
            bool(getattr(entry_grad, "use_gradient_endpoints", False)),
            "use_gradient_endpointsがテスト前提通り残留していません",
        )
        entry_grad.fill_type = "solid"  # use_gradient_endpoints は意図的に残したまま
        _check(
            bool(getattr(entry_grad, "use_gradient_endpoints", False)),
            "fill_type変更だけでuse_gradient_endpointsがリセットされました "
            "(このテストの前提が崩れています)",
        )
        snapshot_grad_solid = object_rotation.capture_rotation_snapshot(context, fill_key_grad)
        _check(
            snapshot_grad_solid is not None,
            f"fill_typeをsolidへ変更したのに回転スナップショットが作成できません "
            f"(判定条件統一の回帰): {snapshot_grad_solid!r}",
        )
        obj_grad_solid = object_naming.find_object_by_bmanga_id(str(entry_grad.id), kind="fill")
        _check(
            obj_grad_solid is not None
            and abs(float(obj_grad_solid.rotation_euler[2]) - math.radians(45.0)) < 1e-6,
            "fill_typeをsolidへ変更したら、残っていたrotation_deg(45)が"
            f"rotation_eulerへ反映されるはずです: "
            f"{obj_grad_solid.rotation_euler[2] if obj_grad_solid is not None else None!r}",
        )

        if FAILURES:
            for f in FAILURES:
                print(f"FAIL: {f}", flush=True)
            raise AssertionError(f"{len(FAILURES)} 件の検証失敗があります")
        print("BMANGA_FILL_ROTATION_OK", flush=True)
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
