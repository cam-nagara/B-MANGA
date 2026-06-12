"""線に沿ったテクスチャ貼り (リボン) の座標マッピング.

線種「マテリアル」の「線に沿う (リボン)」用。閉じた輪郭の周長をテクスチャの
整数枚分に合わせて貼ることで、始点終点の継ぎ目を構造的に出さないための
純粋幾何ヘルパー (bpy 非依存、numpy のみ)。
"""

from __future__ import annotations

from typing import Optional, Sequence

_MAX_SEGMENTS = 256


def loop_segments(loop_xy: Sequence[tuple[float, float]], max_segments: int = _MAX_SEGMENTS) -> Optional[dict]:
    """閉ループの線分配列と累積弧長を返す。

    細かすぎる輪郭は間引いてから使う (マッピングの滑らかさには十分で、
    投影計算のメモリと時間を抑える)。点列が縮退していれば None。
    """
    import numpy as np

    pts = np.asarray([(float(x), float(y)) for x, y in loop_xy], dtype=np.float64)
    if len(pts) >= 2 and np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    if len(pts) < 3:
        return None
    if len(pts) > max_segments:
        idx = np.unique(np.linspace(0, len(pts) - 1, max_segments, endpoint=False).astype(int))
        pts = pts[idx]
    a = pts
    b = np.roll(pts, -1, axis=0)
    d = b - a
    seg_len = np.hypot(d[:, 0], d[:, 1])
    keep = seg_len > 1.0e-12
    a, d, seg_len = a[keep], d[keep], seg_len[keep]
    if len(a) < 3:
        return None
    cum = np.concatenate(([0.0], np.cumsum(seg_len)))
    return {
        "a": a,
        "d": d,
        "seg_len": seg_len,
        "cum_start": cum[:-1],
        "total": float(cum[-1]),
    }


def tile_count(total_len: float, band_width: float, tex_width_px: int, tex_height_px: int) -> int:
    """帯幅にテクスチャ高さを合わせたときの整数タイル数.

    タイル 1 枚の幅 = テクスチャ幅 × (帯幅 / テクスチャ高さ)。周長 ÷ タイル幅を
    四捨五入した整数にすることで、閉ループ一周でちょうど割り切れ、始点終点に
    継ぎ目が出ない (各タイルはわずかに伸縮する)。
    """
    if band_width <= 1.0e-9 or tex_width_px <= 0 or tex_height_px <= 0:
        return 1
    tile_w = float(tex_width_px) * float(band_width) / float(tex_height_px)
    if tile_w <= 1.0e-9:
        return 1
    return max(1, int(round(float(total_len) / tile_w)))


def project_points(segs: dict, px, py, chunk: int = 4096):
    """各点を最近傍の線分へ投影し、弧長 s と距離 dist (>=0) を返す."""
    import numpy as np

    px = np.asarray(px, dtype=np.float64).ravel()
    py = np.asarray(py, dtype=np.float64).ravel()
    n = len(px)
    s_out = np.empty(n, dtype=np.float64)
    d_out = np.empty(n, dtype=np.float64)
    ax = segs["a"][:, 0][None, :]
    ay = segs["a"][:, 1][None, :]
    dx = segs["d"][:, 0][None, :]
    dy = segs["d"][:, 1][None, :]
    seg_len = segs["seg_len"]
    seg_len2 = (seg_len**2)[None, :]
    for i0 in range(0, n, chunk):
        i1 = min(n, i0 + chunk)
        qx = px[i0:i1, None] - ax
        qy = py[i0:i1, None] - ay
        t = (qx * dx + qy * dy) / seg_len2
        np.clip(t, 0.0, 1.0, out=t)
        rx = qx - t * dx
        ry = qy - t * dy
        dist2 = rx * rx + ry * ry
        idx = np.argmin(dist2, axis=1)
        rows = np.arange(i1 - i0)
        s_out[i0:i1] = segs["cum_start"][idx] + t[rows, idx] * seg_len[idx]
        d_out[i0:i1] = np.sqrt(dist2[rows, idx])
    return s_out, d_out
