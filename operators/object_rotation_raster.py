"""オブジェクトツール回転: ラスター(ペイント)レイヤー (kind="raster") 対応.

背景:
    raster レイヤーの Mesh は常にページキャンバス全面の一枚板であり
    (raster_layer_op._ensure_raster_mesh)、コマ配下では静的マスクの
    Boolean でクリップ表示されているだけである。そのため balloon/gp の
    ように Object.rotation_euler やストローク点座標を直接回転する方式は
    使えない (ページ全面が回転してマスクと食い違う上、書き出し
    io/export_raster.py は保存済み PNG をそのまま合成するため Object
    変換は反映されない)。

    移動ドラッグは既に「ピクセルバッファの書き換え」方式
    (translate_raster_layer_pixels, raster_layer_op.py) で実装済みだが
    整数シフトのみで無劣化である。回転は補間を伴う破壊的編集になる
    (一般的なペイントソフトのラスター回転と同じ性質。仕様として許容)。

方式:
    capture 時に Image の全ピクセルを numpy 配列としてスナップショット
    保存する (object_tool_op.py の _make_snapshots raster 分岐と同じ
    「全ピクセル保持」方針。ただし tuple ではなく numpy float32 で保持
    するため実メモリ効率は同等以上)。回転中心は他 kind と同様に
    selection_bounds_for_key の矩形中心 (mm) をピクセル座標へ変換した
    ものを使う。apply は**毎回スナップショットから**逆回転写像
    (インバースマッピング + バイリニア補間) を numpy でベクトル化して
    再計算し、Image.pixels へ書き戻す (累積回転にしない。
    rotation_deg==0.0 は完全な無劣化復元)。

    600dpi 級ページ全面 (数千×数千px) を毎回丸ごと補間すると重いため、
    「回転中心から不透明ピクセルまでの最大距離」を capture 時に一度だけ
    求め、その半径を覆う正方形の範囲だけをバイリインア補間する
    (それ以外の領域は回転で座標が変わっても定義上ずっと透明のままな
    ので再計算不要)。Image.pixels への書き戻しだけはページ全面分を
    まとめて行う (bpy の API がピクセルの部分書き込みに未対応なため)。

known issue:
    ページ全面に描画があるレイヤー (背景トーン等) では上記の範囲限定が
    ほぼ効かず、ページ全面分の補間コストがかかる。実測値は
    test/blender_raster_rotation_check.py のログ出力を参照。
"""

from __future__ import annotations

import math

import numpy as np

from ..core.work import get_work
from ..utils import layer_stack as layer_stack_utils
from ..utils import log, object_selection
from . import object_rotation, object_tool_selection

_logger = log.get_logger(__name__)

# バイリニア補間の近傍参照が範囲外に出ないための安全マージン (px)。
_CROP_MARGIN_PX = 4

# crop範囲がこのピクセル数を超える場合はバイリニア補間ではなく最近傍補間へ
# 切り替える。バイリニアは4近傍のgather+ブレンドが必要で大きなcropでは
# 実測 (test/blender_raster_rotation_check.py のログ) で 1px あたり約200ns
# かかるのに対し、最近傍は1近傍のgatherのみで約1/4のコストで済む。
# 600dpi級のページ全面 (数千万px) をそのまま補間すると数秒〜十数秒かかる
# ため、内容が小さい (典型的な1コマ程度) 場合は高品質なバイリニアを保ち、
# ページ全面に近い巨大な選択だけ画質を落として速度を優先する。
_BILINEAR_MAX_PX = 400_000


def _resolve_page_index(work, entry) -> int:
    """raster_world_rect (object_tool_selection.py) と同じページ解決.

    raster の Image は scope/parent_kind に関わらず常にページ全面
    サイズなので、ピクセル<->mm 変換は必ず「ページ」のオフセットを
    使う (コマ配下でも coma の矩形ではなくページの矩形を使う)。
    """
    parent_key = str(getattr(entry, "parent_key", "") or "")
    page_key = parent_key.split(":", 1)[0] if parent_key else ""
    page_index, page = object_tool_selection.page_index_for_key(work, page_key)
    if page is None:
        page_index = int(getattr(work, "active_page_index", -1) or -1)
        pages = getattr(work, "pages", []) or []
        if not (0 <= page_index < len(pages)):
            page_index = -1
    return page_index


def _mm_to_px(
    context, work, page_index: int, x_mm: float, y_mm: float, img_w: int, img_h: int,
) -> tuple[float, float]:
    """object_tool_selection._raster_alpha_at_world と同じ変換式 (連続値版)."""
    ox, oy = object_tool_selection.page_offset_mm(context, work, page_index)
    paper = getattr(work, "paper", None)
    width_mm = max(1.0e-6, float(getattr(paper, "canvas_width_mm", 0.0) or 0.0)) if paper else 1.0
    height_mm = max(1.0e-6, float(getattr(paper, "canvas_height_mm", 0.0) or 0.0)) if paper else 1.0
    u = (float(x_mm) - ox) / width_mm
    v = (float(y_mm) - oy) / height_mm
    return u * (img_w - 1), v * (img_h - 1)


def _read_image_pixels(image) -> np.ndarray | None:
    try:
        w, h = int(image.size[0]), int(image.size[1])
    except Exception:  # noqa: BLE001
        return None
    if w <= 0 or h <= 0:
        return None
    flat = np.empty(w * h * 4, dtype=np.float32)
    try:
        image.pixels.foreach_get(flat)
    except Exception:  # noqa: BLE001
        return None
    return flat.reshape(h, w, 4)


def _content_radius_px(pixels: np.ndarray, cx: float, cy: float) -> float:
    """不透明ピクセルの (cx, cy) からの最大距離 (px)。何も無ければ0."""
    alpha = pixels[:, :, 3]
    rows = np.any(alpha > 0.0, axis=1)
    cols = np.any(alpha > 0.0, axis=0)
    if not rows.any() or not cols.any():
        return 0.0
    y_idx = np.nonzero(rows)[0]
    x_idx = np.nonzero(cols)[0]
    y_min, y_max = float(y_idx[0]), float(y_idx[-1])
    x_min, x_max = float(x_idx[0]), float(x_idx[-1])
    corners = ((x_min, y_min), (x_max, y_min), (x_min, y_max), (x_max, y_max))
    return max(math.hypot(x - cx, y - cy) for x, y in corners)


def _crop_box_for_radius(cx: float, cy: float, radius_px: float, img_w: int, img_h: int) -> tuple[int, int, int, int]:
    """回転中心からradius_pxを覆う正方形の範囲 (x0, y0, x1, y1) を返す (x1/y1は排他)."""
    half = int(math.ceil(radius_px)) + _CROP_MARGIN_PX
    x0 = max(0, int(math.floor(cx)) - half)
    x1 = min(img_w, int(math.ceil(cx)) + half + 1)
    y0 = max(0, int(math.floor(cy)) - half)
    y1 = min(img_h, int(math.ceil(cy)) + half + 1)
    if x1 <= x0 or y1 <= y0:
        return (0, 0, 0, 0)
    return (x0, y0, x1, y1)


def _bilinear_sample(
    src: np.ndarray, src_x: np.ndarray, src_y: np.ndarray, img_w: int, img_h: int,
) -> np.ndarray:
    """src から (src_x, src_y) (連続px座標、src_xと同shape) をバイリニア
    補間でサンプリングする (完全ベクトル化)。範囲外は透明を返す。"""
    x0f = np.floor(src_x)
    y0f = np.floor(src_y)
    fx = (src_x - x0f).astype(np.float32)[..., None]
    fy = (src_y - y0f).astype(np.float32)[..., None]
    x0i = x0f.astype(np.int32)
    y0i = y0f.astype(np.int32)
    x1i = x0i + 1
    y1i = y0i + 1
    valid = (x0i >= 0) & (x1i <= img_w - 1) & (y0i >= 0) & (y1i <= img_h - 1)
    x0c = np.clip(x0i, 0, img_w - 1)
    x1c = np.clip(x1i, 0, img_w - 1)
    y0c = np.clip(y0i, 0, img_h - 1)
    y1c = np.clip(y1i, 0, img_h - 1)
    p00 = src[y0c, x0c]
    p10 = src[y0c, x1c]
    p01 = src[y1c, x0c]
    p11 = src[y1c, x1c]
    top = p00 * (1.0 - fx) + p10 * fx
    bottom = p01 * (1.0 - fx) + p11 * fx
    result = top * (1.0 - fy) + bottom * fy
    result[~valid] = 0.0
    return result.astype(np.float32)


def _nearest_sample(
    src: np.ndarray, src_x: np.ndarray, src_y: np.ndarray, img_w: int, img_h: int,
) -> np.ndarray:
    """_bilinear_sample の軽量版 (最近傍)。巨大cropの速度優先フォールバック用."""
    xi = np.round(src_x).astype(np.int32)
    yi = np.round(src_y).astype(np.int32)
    valid = (xi >= 0) & (xi <= img_w - 1) & (yi >= 0) & (yi <= img_h - 1)
    xc = np.clip(xi, 0, img_w - 1)
    yc = np.clip(yi, 0, img_h - 1)
    result = src[yc, xc].copy()
    result[~valid] = 0.0
    return result


def _capture_raster_rotation(context, key: str) -> dict | None:
    """raster レイヤーの回転スナップショットを作る (未対応/対象無しは None)."""
    _kind, _page_id, item_id = object_selection.parse_key(key)
    work = get_work(context)
    if work is None:
        return None
    _idx, entry = object_tool_selection.find_raster_by_key(context, item_id)
    if entry is None:
        return None
    from . import raster_layer_op

    image = raster_layer_op.ensure_raster_image(context, entry, create_missing=False)
    if image is None:
        return None
    pixels = _read_image_pixels(image)
    if pixels is None:
        return None
    img_h, img_w = int(pixels.shape[0]), int(pixels.shape[1])
    rect = object_tool_selection.selection_bounds_for_key(context, key)
    if rect is None:
        return None
    cx_mm, cy_mm = rect.center
    page_index = _resolve_page_index(work, entry)
    cx_px, cy_px = _mm_to_px(context, work, page_index, cx_mm, cy_mm, img_w, img_h)
    radius_px = _content_radius_px(pixels, cx_px, cy_px)
    crop_box = _crop_box_for_radius(cx_px, cy_px, radius_px, img_w, img_h)
    return {
        "entry": entry,
        "image": image,
        "pixels": pixels,  # 元画像の完全スナップショット (以後mutateしない)
        "work_buffer": pixels.copy(),  # 書き戻し用の可変バッファ (crop部分だけ都度上書き)
        "width": img_w,
        "height": img_h,
        "pivot_px": (cx_px, cy_px),
        "crop_box": crop_box,
        "base_rotation_deg": 0.0,
    }


def _rotated_crop(src: np.ndarray, pivot_px, crop_box, rotation_deg: float, img_w: int, img_h: int) -> np.ndarray:
    """crop_box範囲について、rotation_deg度回転した結果をsrc(元画像)から再計算する."""
    x0, y0, x1, y1 = crop_box
    if abs(rotation_deg) < 1e-9:
        # 完全な絶対角度0: 元スナップショットをそのまま複製する (ビット単位で復元)。
        return src[y0:y1, x0:x1].copy()
    cx, cy = np.float32(pivot_px[0]), np.float32(pivot_px[1])
    rad = math.radians(float(rotation_deg))
    cos_a, sin_a = np.float32(math.cos(rad)), np.float32(math.sin(rad))
    out_x = (np.arange(x0, x1, dtype=np.float32) - cx).reshape(1, -1)
    out_y = (np.arange(y0, y1, dtype=np.float32) - cy).reshape(-1, 1)
    # 逆回転写像 (出力座標 -> 元座標)。rotate_point_around_center (gp) と
    # 同じ順回転規約 new = center + R(angle)@(orig-center) の逆行列 R(-angle)。
    src_x = cx + out_x * cos_a + out_y * sin_a
    src_y = cy - out_x * sin_a + out_y * cos_a
    if (x1 - x0) * (y1 - y0) > _BILINEAR_MAX_PX:
        return _nearest_sample(src, src_x, src_y, img_w, img_h)
    return _bilinear_sample(src, src_x, src_y, img_w, img_h)


def _apply_raster_rotation(context, snapshot: dict, rotation_deg: float) -> None:
    """スナップショットから絶対角度rotation_degの状態を再計算しImageへ書き戻す."""
    image = snapshot.get("image")
    src = snapshot.get("pixels")
    work_buffer = snapshot.get("work_buffer")
    img_w, img_h = int(snapshot.get("width", 0)), int(snapshot.get("height", 0))
    if image is None or src is None or work_buffer is None or img_w <= 0 or img_h <= 0:
        return
    x0, y0, x1, y1 = snapshot.get("crop_box", (0, 0, 0, 0))
    if x1 > x0 and y1 > y0:
        crop_result = _rotated_crop(src, snapshot.get("pivot_px", (0.0, 0.0)), (x0, y0, x1, y1), rotation_deg, img_w, img_h)
        work_buffer[y0:y1, x0:x1] = crop_result
    try:
        image.pixels.foreach_set(np.ascontiguousarray(work_buffer, dtype=np.float32).ravel())
        image.update()
    except Exception:  # noqa: BLE001
        _logger.exception("raster rotation apply failed")
        return
    entry = snapshot.get("entry")
    if entry is not None:
        from . import raster_layer_op

        raster_layer_op.mark_raster_dirty(entry)
    layer_stack_utils.tag_view3d_redraw(context)


def _can_rotate_raster(context, key: str) -> bool:
    """回転リングのホバー用軽量プローブ (capture 冒頭と同じ Image 解決).

    Image が取得できない (未生成) 対象は回転非対応なのでリングを無効化する。
    ensure_raster_image(create_missing=False) は capture でも使われている
    既存の非生成解決経路であり、新規Image作成やピクセル走査などの重い
    副作用は行わない。
    """
    _kind, _page_id, item_id = object_selection.parse_key(key)
    work = get_work(context)
    if work is None:
        return True
    _idx, entry = object_tool_selection.find_raster_by_key(context, item_id)
    if entry is None:
        return True
    from . import raster_layer_op

    image = raster_layer_op.ensure_raster_image(context, entry, create_missing=False)
    return image is not None


object_rotation.register_rotation_handler(
    "raster", _capture_raster_rotation, _apply_raster_rotation, _can_rotate_raster,
)
