"""Blender実機用: ラスター(ペイント)レイヤーの回転対応の回帰確認.

背景:
    「選択ハンドル四隅の少し外側をドラッグしてオブジェクトを中心軸回転する」
    機能 (operators/object_rotation.py) の回転コアは実装済みだが、raster
    レイヤー (kind="raster") の Mesh は常にページキャンバス全面の一枚板
    (コマ配下は静的マスクの Boolean でクリップ) のため、balloon/gp の
    ように Object.rotation_euler やストローク点座標の直接回転は使えない。
    専用ハンドラー (operators/object_rotation_raster.py) で「ピクセル
    バッファの逆回転写像 + バイリニア補間 (numpy)」方式により対応した。

本テストはヘッドレスで以下を確認する:
    1. raster レイヤーを選択した状態で capture_rotation_snapshot が
       有効なスナップショットを返す (レジストリへの登録確認)。
    2. apply_rotation_snapshot(90.0) で、既知の横棒パターンが縦向きに
       なる (回転中心近傍の代表ピクセルをサンプリングして独立検算)。
    3. 続けて apply_rotation_snapshot(45.0) を呼ぶと「90度からの累積」
       ではなく「元の状態から45度」の絶対角度になる。
    4. restore_rotation_snapshot (= apply 0.0) で全ピクセルがビット単位
       で完全復元されること。
    5. rotation_hit_with_priority が、raster レイヤー選択中のハンドル
       表示角の外側リング座標で kind=="raster" の rot_hit を返すこと。
    6. apply 1回の所要時間を計測してログ出力する (600dpi 相当の
       ページ全面キャンバス、2048×2048以上で計測)。
    7. 回転後に save_raster_png が dirty フラグを検知して保存できる
       こと (move と同等の dirty 管理)。

実行:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python test\\blender_raster_rotation_check.py
"""

from __future__ import annotations

import importlib.util
import math
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_raster_rotation"

FAILURES: list[str] = []

# object_rotation の回転リング (表示角からの距離 3.0mm < d <= 8.0mm) の中央値。
_RING_OFFSET_MM = 5.0

# 既知の横棒パターン (中心から右へ伸びる): 長さ・太さ (px)。
_BAR_LEN_PX = 60
_BAR_THICK_PX = 12


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
    x, y, w, h = rect_world.x, rect_world.y, rect_world.width, rect_world.height
    handle = (x + w + outset, y + h + outset)
    direction = (1.0 / math.sqrt(2.0), 1.0 / math.sqrt(2.0))
    ring_point = (handle[0] + direction[0] * ring_offset, handle[1] + direction[1] * ring_offset)
    return handle, ring_point


def _add_raster(dpi_preset: str):
    result = bpy.ops.bmanga.raster_layer_add(
        dpi_preset=dpi_preset, bit_depth="gray8", enter_paint=False,
    )
    assert "FINISHED" in result, result
    scene = bpy.context.scene
    return scene.bmanga_raster_layers[len(scene.bmanga_raster_layers) - 1]


def _read_pixels(image) -> np.ndarray:
    w, h = int(image.size[0]), int(image.size[1])
    flat = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(flat)
    return flat.reshape(h, w, 4)


def _write_pixels(image, arr: np.ndarray) -> None:
    image.pixels.foreach_set(np.ascontiguousarray(arr, dtype=np.float32).ravel())
    image.update()


def _independent_pivot_px(context, work, page_index: int, rect, img_w: int, img_h: int):
    """production の _mm_to_px を呼ばず、テスト側で同じ変換式を独立実装する."""
    from bmanga_dev_raster_rotation.operators import object_tool_selection

    ox, oy = object_tool_selection.page_offset_mm(context, work, page_index)
    paper = work.paper
    width_mm = float(paper.canvas_width_mm)
    height_mm = float(paper.canvas_height_mm)
    cx_mm, cy_mm = rect.center
    u = (cx_mm - ox) / width_mm
    v = (cy_mm - oy) / height_mm
    return u * (img_w - 1), v * (img_h - 1)


def _paint_bar(image, cx_px: float, cy_px: float) -> np.ndarray:
    """中心(cx_px, cy_px)から右へ伸びる不透明な横棒を書き込み、Imageへ実際に
    格納された (8bit量子化後の) 配列を読み直して返す (ビット単位比較の基準用。
    書き込みに使ったfloat32配列をそのまま基準にすると、Imageが内部で8bit
    量子化するため書き戻し後の値と一致せず誤検出になる)。"""
    w, h = int(image.size[0]), int(image.size[1])
    arr = np.zeros((h, w, 4), dtype=np.float32)
    cx_i, cy_i = int(round(cx_px)), int(round(cy_px))
    half_t = _BAR_THICK_PX // 2
    y0, y1 = max(0, cy_i - half_t), min(h, cy_i + half_t)
    x0, x1 = max(0, cx_i), min(w, cx_i + _BAR_LEN_PX)
    arr[y0:y1, x0:x1, :] = (0.1, 0.1, 0.1, 1.0)
    _write_pixels(image, arr)
    return _read_pixels(image)


def _forward_rotate(dx: float, dy: float, angle_deg: float) -> tuple[float, float]:
    """テスト側の独立実装 (本番コードを一切呼ばない検算用、順回転)."""
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    return (dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a)


def _alpha_at(pixels: np.ndarray, x_px: float, y_px: float) -> float:
    w, h = pixels.shape[1], pixels.shape[0]
    xi = max(0, min(w - 1, int(round(x_px))))
    yi = max(0, min(h - 1, int(round(y_px))))
    return float(pixels[yi, xi, 3])


def _run_correctness_check(context, work, page, object_rotation, object_selection, object_tool_selection) -> dict:
    entry = _add_raster("150")
    raster_key = object_selection.raster_key(entry)
    object_selection.select_key(context, raster_key, mode="single")
    _check(
        object_selection.get_keys(context) == [raster_key],
        "rasterレイヤーの選択がset_keysに反映されません",
    )

    initial_rect = object_tool_selection.selection_bounds_for_key(context, raster_key)
    _check(initial_rect is not None, "rasterレイヤーの選択矩形が取得できません")

    from bmanga_dev_raster_rotation.operators import raster_layer_op

    image = raster_layer_op.ensure_raster_image(context, entry, create_missing=False)
    _check(image is not None, "raster画像が取得できません")
    img_w, img_h = int(image.size[0]), int(image.size[1])

    cx_px, cy_px = _independent_pivot_px(context, work, 0, initial_rect, img_w, img_h)
    painted = _paint_bar(image, cx_px, cy_px)

    # --- 1. capture_rotation_snapshot ---
    snapshot = object_rotation.capture_rotation_snapshot(context, raster_key)
    _check(snapshot is not None, "rasterレイヤーの回転スナップショットが作成できません")
    if snapshot is None:
        raise AssertionError("スナップショットが None のため以降のテストを続行できません")
    _check(
        snapshot.get("kind") == "raster" and snapshot.get("key") == raster_key,
        f"スナップショットのkind/keyが不正です: {snapshot.get('kind')!r} / {snapshot.get('key')!r}",
    )
    _check(
        abs(float(snapshot.get("base_rotation_deg", -999.0))) < 1e-9,
        f"rasterのbase_rotation_degは常に0.0であるべきです: {snapshot.get('base_rotation_deg')!r}",
    )
    pivot_px = snapshot.get("pivot_px")
    _check(
        pivot_px is not None and abs(pivot_px[0] - cx_px) < 1.0 and abs(pivot_px[1] - cy_px) < 1.0,
        f"スナップショットの回転中心(px)が想定と異なります: {pivot_px!r} (期待=({cx_px}, {cy_px}))",
    )

    # --- 2. apply_rotation_snapshot(90.0): 横棒が縦向きになる ---
    object_rotation.apply_rotation_snapshot(context, snapshot, 90.0)
    rotated_90 = _read_pixels(image)
    dx90, dy90 = _forward_rotate(_BAR_LEN_PX / 2.0, 0.0, 90.0)
    _check(
        _alpha_at(rotated_90, cx_px + dx90, cy_px + dy90) > 0.5,
        "90度回転後、期待位置(縦向きの棒の内部)が不透明ではありません",
    )
    _check(
        _alpha_at(rotated_90, cx_px + _BAR_LEN_PX / 2.0, cy_px) < 0.5,
        "90度回転後、元の横棒位置がまだ不透明のままです (回転していない疑い)",
    )
    _check(
        _alpha_at(rotated_90, cx_px, cy_px) > 0.5,
        "90度回転後、回転中心付近 (棒の根本) が透明になっています",
    )

    # --- 3. apply_rotation_snapshot(45.0): 90度からの累積ではなく絶対角度 ---
    object_rotation.apply_rotation_snapshot(context, snapshot, 45.0)
    rotated_45 = _read_pixels(image)
    dx45, dy45 = _forward_rotate(_BAR_LEN_PX / 2.0, 0.0, 45.0)
    dx135, dy135 = _forward_rotate(_BAR_LEN_PX / 2.0, 0.0, 135.0)
    _check(
        _alpha_at(rotated_45, cx_px + dx45, cy_px + dy45) > 0.5,
        "45度回転(絶対角度)後、期待位置が不透明ではありません",
    )
    _check(
        _alpha_at(rotated_45, cx_px + dx135, cy_px + dy135) < 0.5,
        "回転が絶対角度ではなく累積(90+45=135相当)になっています",
    )

    # --- 4. restore_rotation_snapshot: ビット単位で完全復元 ---
    object_rotation.restore_rotation_snapshot(context, snapshot)
    restored = _read_pixels(image)
    _check(
        np.array_equal(restored, painted),
        "restore後に元のピクセル値へビット単位で完全復元されません",
    )

    # --- 7. dirty/保存経路が move と同等に機能する ---
    object_rotation.apply_rotation_snapshot(context, snapshot, 30.0)
    saved = raster_layer_op.save_raster_png(context, entry, force=False)
    _check(saved is True, "回転後にsave_raster_pngがdirtyを検知して保存できません")
    object_rotation.restore_rotation_snapshot(context, snapshot)
    raster_layer_op.save_raster_png(context, entry, force=True)

    return {"entry": entry, "key": raster_key, "rect": initial_rect}


def _run_ring_hit_check(context, object_rotation, object_tool_selection, info: dict) -> None:
    from bmanga_dev_raster_rotation.utils.object_selection import SELECTION_HANDLE_OUTSET_MM

    initial_rect = info["rect"]
    raster_key = info["key"]
    handle_point, ring_point = _diagonal_ring_point(initial_rect, SELECTION_HANDLE_OUTSET_MM, _RING_OFFSET_MM)
    rot_hit = object_rotation.rotation_hit_with_priority(
        context, SimpleNamespace(), lambda _ctx, _ev: ring_point, hit=None,
    )
    _check(
        rot_hit is not None and rot_hit.get("kind") == "raster" and rot_hit.get("key") == raster_key,
        f"rasterレイヤーの回転リングヒットが得られません: {rot_hit!r} (ring_point={ring_point!r})",
    )
    outside_point = (
        handle_point[0] + (1.0 / math.sqrt(2.0)) * 30.0,
        handle_point[1] + (1.0 / math.sqrt(2.0)) * 30.0,
    )
    rot_hit_outside = object_rotation.rotation_hit_with_priority(
        context, SimpleNamespace(), lambda _ctx, _ev: outside_point, hit=None,
    )
    _check(rot_hit_outside is None, f"リング外なのにrasterの回転ヒットが返りました: {rot_hit_outside!r}")


def _run_perf_check(context, object_rotation, object_selection) -> None:
    """600dpi相当のページ全面キャンバスでapply 1回の所要時間を計測する."""
    entry = _add_raster("600")
    raster_key = object_selection.raster_key(entry)
    object_selection.select_key(context, raster_key, mode="single")

    from bmanga_dev_raster_rotation.operators import raster_layer_op

    image = raster_layer_op.ensure_raster_image(context, entry, create_missing=False)
    w, h = int(image.size[0]), int(image.size[1])
    print(f"BMANGA_RASTER_ROTATION_PERF_SIZE {w}x{h} ({w * h} px)", flush=True)
    _check(w * h >= 2048 * 2048, f"perf計測キャンバスが2048x2048未満です: {w}x{h}")

    # 最悪ケース (ページ全面に描画がある背景トーン等) を想定し全面を不透明化する。
    full = np.ones((h, w, 4), dtype=np.float32)
    _write_pixels(image, full)

    snapshot = object_rotation.capture_rotation_snapshot(context, raster_key)
    _check(snapshot is not None, "perf計測用rasterのスナップショットが作成できません")
    if snapshot is None:
        return
    t0 = time.perf_counter()
    object_rotation.apply_rotation_snapshot(context, snapshot, 37.0)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    print(f"BMANGA_RASTER_ROTATION_PERF_MS {elapsed_ms:.1f}", flush=True)
    _check(elapsed_ms < 15000.0, f"apply所要時間が異常に長すぎます: {elapsed_ms:.1f}ms")


def _run_check() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_raster_rotation_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "RasterRotationCheck.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        from bmanga_dev_raster_rotation.operators import object_rotation, object_tool_selection
        from bmanga_dev_raster_rotation.utils import object_selection

        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]

        _check(
            "raster" in object_rotation.ROTATION_HANDLERS,
            "object_rotation_raster のインポートで raster ハンドラーが登録されません",
        )

        info = _run_correctness_check(
            context, work, page, object_rotation, object_selection, object_tool_selection,
        )
        _run_ring_hit_check(context, object_rotation, object_tool_selection, info)
        _run_perf_check(context, object_rotation, object_selection)

        if FAILURES:
            for f in FAILURES:
                print(f"FAIL: {f}", flush=True)
            raise AssertionError(f"{len(FAILURES)} 件の検証失敗があります")
        print("BMANGA_RASTER_ROTATION_OK", flush=True)
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
