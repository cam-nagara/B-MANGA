"""コマ用 blend のビューポートで、 魚眼レイアウト時に用紙範囲外を
グレー 50% で覆う POST_PIXEL 描画オーバーレイ.

魚眼モードはコマの出力解像度を正方形に揃えるため、 用紙が縦長/横長の
場合に正方形の中で余ったレターボックス領域 (用紙範囲外) が見えてしまう。
このオーバーレイは、 コマ編集画面のビューポート (カメラ視点) でだけ、
カメラフレーム内の用紙範囲外をグレー 50% で塗り潰して用紙範囲を分かり
やすくする。 レンダー出力 (PNG 等) には影響しない。
"""

from __future__ import annotations

from pathlib import Path

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ..core.work import get_work
from ..utils import log, paths

_logger = log.get_logger(__name__)

DARK_COLOR = (0.5, 0.5, 0.5, 1.0)

_handle = None


def _is_coma_blend_file() -> bool:
    """blend ファイルパスから、 コマ用 blend (pNNNN/cNN/cNN.blend) か判定."""
    blend_path = bpy.data.filepath
    if not blend_path:
        return False
    try:
        p = Path(blend_path)
        coma_dir = p.parent
        page_dir = coma_dir.parent
        if not paths.is_valid_coma_id(coma_dir.name):
            return False
        if not paths.is_valid_page_id(page_dir.name):
            return False
        if p.name != f"{coma_dir.name}.blend":
            return False
        return True
    except Exception:  # noqa: BLE001
        return False


def _draw_callback() -> None:
    try:
        _draw_callback_impl()
    except Exception:  # noqa: BLE001
        # ビューポート描画 callback で例外が外に漏れると Blender の描画ループが
        # 壊れるため、 必ず内部で握り潰してログのみ残す。
        _logger.exception("coma fisheye overlay draw callback failed")


def _draw_callback_impl() -> None:
    context = bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    if not bool(getattr(scene, "bname_coma_camera_fisheye_layout_mode", False)):
        return
    if not _is_coma_blend_file():
        return

    region = getattr(context, "region", None)
    rv3d = getattr(context, "region_data", None)
    space = getattr(context, "space_data", None)
    if region is None or rv3d is None or space is None:
        return
    # カメラを覗いている時のみ描画する。 自由視点ではレターボックス位置が
    # 安定しないし、 ユーザーが意図的に外している可能性が高いため。
    if getattr(space, "type", "") != "VIEW_3D":
        return
    if getattr(rv3d, "view_perspective", "") != "CAMERA":
        return

    camera = scene.camera
    if camera is None or getattr(camera, "type", "") != "CAMERA":
        return

    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return
    paper = getattr(work, "paper", None)
    if paper is None:
        return
    paper_w_mm = float(getattr(paper, "canvas_width_mm", 0.0) or 0.0)
    paper_h_mm = float(getattr(paper, "canvas_height_mm", 0.0) or 0.0)
    if paper_w_mm <= 0.0 or paper_h_mm <= 0.0:
        return

    # カメラフレームのスクリーン座標を、 region 寸法とレンダーアスペクトから
    # 直接計算する。 ``camera.data.view_frame()`` は PANO カメラだと内部 PERSP
    # フォーマットの矩形 (50mm 既定レンズ) を返してしまい、 魚眼モードで実際
    # に表示される正方形フレームと一致しない。 代わりにレンダーアスペクト
    # (= ``scene.render.resolution_x / resolution_y``、 魚眼レイアウトでは 1.0)
    # で region に内接する矩形をカメラフレームとして扱う。 これは
    # ``view_camera_zoom`` を考慮しないので非既定ズームでは多少ずれるが、
    # Blender 既定の camera view ではほぼ一致する。
    res_x = float(getattr(scene.render, "resolution_x", 0) or 0)
    res_y = float(getattr(scene.render, "resolution_y", 0) or 0)
    if res_x <= 0.0 or res_y <= 0.0:
        return
    render_aspect = res_x / res_y
    region_w_f = float(region.width)
    region_h_f = float(region.height)
    if region_w_f <= 1.0 or region_h_f <= 1.0:
        return
    region_aspect = region_w_f / region_h_f
    if region_aspect > render_aspect:
        # region がカメラフレームより横長 → 高さフィット
        frame_h = region_h_f
        frame_w = frame_h * render_aspect
    else:
        # region がカメラフレームより縦長 → 幅フィット
        frame_w = region_w_f
        frame_h = frame_w / render_aspect
    cam_min_x = (region_w_f - frame_w) * 0.5
    cam_max_x = cam_min_x + frame_w
    cam_min_y = (region_h_f - frame_h) * 0.5
    cam_max_y = cam_min_y + frame_h
    cam_w = cam_max_x - cam_min_x
    cam_h = cam_max_y - cam_min_y
    if cam_w <= 1.0 or cam_h <= 1.0:
        return

    # 用紙アスペクトと魚眼正方形の差からレターボックス幅を算出.
    # 魚眼レイアウトは ``edge = max(orig_x, orig_y)`` で正方形化するため、
    # カメラフレーム (正方形) 内で用紙領域はアスペクト保持で内接する。
    paper_aspect = paper_w_mm / paper_h_mm  # >1 横長 / <1 縦長
    cam_aspect = cam_w / cam_h if cam_h > 0 else 1.0
    if paper_aspect > cam_aspect:
        # 用紙が横長 → 縦に letterbox (上下)
        paper_w_px = cam_w
        paper_h_px = cam_w / paper_aspect
    else:
        # 用紙が縦長 → 横に letterbox (左右)
        paper_h_px = cam_h
        paper_w_px = cam_h * paper_aspect
    paper_cx = (cam_min_x + cam_max_x) * 0.5
    paper_cy = (cam_min_y + cam_max_y) * 0.5
    paper_min_x = paper_cx - paper_w_px * 0.5
    paper_max_x = paper_cx + paper_w_px * 0.5
    paper_min_y = paper_cy - paper_h_px * 0.5
    paper_max_y = paper_cy + paper_h_px * 0.5

    # 4 本のレターボックス帯 (用紙の上下左右).
    bands: list[tuple[float, float, float, float]] = []
    # 上
    if paper_max_y < cam_max_y - 0.5:
        bands.append((cam_min_x, paper_max_y, cam_max_x, cam_max_y))
    # 下
    if paper_min_y > cam_min_y + 0.5:
        bands.append((cam_min_x, cam_min_y, cam_max_x, paper_min_y))
    # 左
    if paper_min_x > cam_min_x + 0.5:
        bands.append((cam_min_x, paper_min_y, paper_min_x, paper_max_y))
    # 右
    if paper_max_x < cam_max_x - 0.5:
        bands.append((paper_max_x, paper_min_y, cam_max_x, paper_max_y))
    if not bands:
        return

    verts: list[tuple[float, float]] = []
    indices: list[tuple[int, int, int]] = []
    for x1, y1, x2, y2 in bands:
        base = len(verts)
        verts.extend([(x1, y1), (x2, y1), (x2, y2), (x1, y2)])
        indices.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])

    try:
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
        gpu.state.blend_set("ALPHA")
        shader.bind()
        shader.uniform_float("color", DARK_COLOR)
        batch.draw(shader)
        gpu.state.blend_set("NONE")
    except Exception:  # noqa: BLE001
        _logger.exception("coma fisheye overlay draw failed")


def register() -> None:
    global _handle
    if _handle is not None:
        return
    try:
        _handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, (), "WINDOW", "POST_PIXEL"
        )
    except Exception:  # noqa: BLE001
        _logger.exception("coma fisheye overlay register failed")


def unregister() -> None:
    global _handle
    if _handle is None:
        return
    try:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
    except Exception:  # noqa: BLE001
        _logger.exception("coma fisheye overlay unregister failed")
    _handle = None
