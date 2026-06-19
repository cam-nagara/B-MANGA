"""Blender 実機用: 全形状・全方向・各種設定での多重線徹底チェック.

シナリオ:
  M1. 全形状 × 方向 outside × 本数 3
  M2. 全形状 × 方向 inside × 本数 3
  M3. 全形状 × 方向 both × 本数 4
  M4. 線幅変化 (width_scale_percent) 100%, 80%, 50%, 25%
  M5. 間隔変化 (spacing_scale_percent) 100%, 80%, 50%, 25%
  M6. トゲ専用: 谷/山の線幅 % 組合せ (100/100, 100/30, 30/100, 0/100)
  M7. トゲ専用: 長さ変化 (near/far) (100/100, 100/50, 50/100, 25/100)
  M8. トゲ専用: 山谷を延ばして交差 on/off
  M9. 本数 1 / 本数 12 (極端)

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_multi_line_audit.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_MULTI_AUDIT_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_multi_audit_"))

SHAPES = ["rect", "ellipse", "octagon", "cloud", "fluffy", "thorn", "thorn-curve"]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_multi_audit",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_multi_audit"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_ortho_camera(name: str, center_x_m: float, center_y_m: float, scale_m: float):
    if name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
    if name in bpy.data.cameras:
        bpy.data.cameras.remove(bpy.data.cameras[name])
    cam_data = bpy.data.cameras.new(name)
    cam = bpy.data.objects.new(name, cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = (center_x_m, center_y_m, 2.0)
    cam.rotation_euler = (0.0, 0.0, 0.0)
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = scale_m
    bpy.context.scene.camera = cam


def _render_to(path: Path, *, width_px: int = 1280, height_px: int = 720):
    scene = bpy.context.scene
    items = {item.identifier for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in items else "BLENDER_EEVEE"
    scene.render.resolution_x = width_px
    scene.render.resolution_y = height_px
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(path)
    scene.render.film_transparent = False
    bpy.ops.render.render(write_still=True)


def _reset_work():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_multi_work_"))
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "MultiAudit.bmanga"))  # type: ignore[attr-defined]
    assert "FINISHED" in result, result
    return bpy.context


def _add_multi_balloon(page, parent_key, idx, shape, *,
                       direction="outside", count=3,
                       width_scale=100.0, spacing_scale=100.0,
                       valley_pct=100.0, peak_pct=100.0,
                       length_near=100.0, length_far=100.0,
                       cross=False,
                       width_mm=0.4, spacing_mm=0.7, line_width_mm=1.0):
    cell = 50.0
    cols = 7
    col = idx % cols
    row = idx // cols
    entry = page.balloons.add()
    entry.id = f"m_{shape}_{idx}"
    entry.title = f"{shape}_{idx}"
    entry.shape = shape
    entry.x_mm = 15.0 + col * cell
    entry.y_mm = 100.0 - row * (cell + 5)
    entry.width_mm = 40.0
    entry.height_mm = 40.0
    entry.parent_kind = "page"
    entry.parent_key = parent_key
    entry.line_style = "double"
    entry.line_width_mm = line_width_mm
    # 黒背景に対して見えるよう、線色を赤くする (多重線も同じ色で重なる)
    entry.line_color = (1.0, 0.2, 0.2, 1.0)
    entry.fill_color = (1.0, 1.0, 1.0, 1.0)
    entry.fill_opacity = 100.0
    entry.opacity = 100.0
    entry.multi_line_count = count
    entry.multi_line_direction = direction
    entry.multi_line_width_mm = width_mm
    entry.multi_line_spacing_mm = spacing_mm
    entry.multi_line_width_scale_percent = width_scale
    entry.multi_line_spacing_scale_percent = spacing_scale
    entry.thorn_multi_line_valley_width_pct = valley_pct
    entry.thorn_multi_line_peak_width_pct = peak_pct
    entry.thorn_multi_line_length_scale_near_percent = length_near
    entry.thorn_multi_line_length_scale_far_percent = length_far
    entry.thorn_multi_line_cross_enabled = cross
    return entry


def _ensure_all_balloons(context, page):
    from bmanga_dev_multi_audit.utils import balloon_curve_object as bco
    scene = context.scene
    for entry in page.balloons:
        bco.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)


def _setup_camera_for_grid(page, scale_m=0.45):
    """画面に全フキダシが収まるカメラ設定."""
    from bmanga_dev_multi_audit.utils import page_grid
    work = bpy.context.scene.bmanga_work
    ox_mm, oy_mm = page_grid.page_total_offset_mm(work, bpy.context.scene, 0)
    xs = [entry.x_mm + entry.width_mm * 0.5 for entry in page.balloons]
    ys = [entry.y_mm + entry.height_mm * 0.5 for entry in page.balloons]
    if not xs:
        cx, cy = 100.0, 100.0
    else:
        cx = (min(xs) + max(xs)) * 0.5
        cy = (min(ys) + max(ys)) * 0.5
    cx_m = (cx + ox_mm) / 1000.0
    cy_m = (cy + oy_mm) / 1000.0
    _set_ortho_camera("multi_audit_cam", cx_m, cy_m, scale_m)


def _page_key():
    from bmanga_dev_multi_audit.utils.layer_hierarchy import page_stack_key
    return page_stack_key(bpy.context.scene.bmanga_work.pages[0])


# -----------------------------------------------------------------------------
# シナリオ
# -----------------------------------------------------------------------------

def scenario_directions():
    """M1-M3: 全形状 × 方向 outside / inside / both."""
    for direction, count, label in [
        ("outside", 3, "outside_count3"),
        ("inside", 3, "inside_count3"),
        ("both", 4, "both_count4"),
    ]:
        context = _reset_work()
        page = context.scene.bmanga_work.pages[0]
        pk = _page_key()
        for idx, shape in enumerate(SHAPES):
            _add_multi_balloon(page, pk, idx, shape,
                               direction=direction, count=count,
                               width_mm=0.5, spacing_mm=0.8, line_width_mm=1.2)
        _ensure_all_balloons(context, page)
        _setup_camera_for_grid(page, scale_m=0.40)
        _render_to(_OUT_PATH / f"m_dir_{label}.png", width_px=1400, height_px=300)
        print(f"  [M-dir] {label}: 出力 {_OUT_PATH / f'm_dir_{label}.png'}")


def scenario_width_scale():
    """M4: 線幅変化 (width_scale_percent) 100/80/50/25 (cloud で確認)."""
    context = _reset_work()
    page = context.scene.bmanga_work.pages[0]
    pk = _page_key()
    scales = [100.0, 80.0, 50.0, 25.0]
    for idx, scale in enumerate(scales):
        _add_multi_balloon(page, pk, idx, "cloud",
                           direction="outside", count=4,
                           width_scale=scale,
                           width_mm=0.8, spacing_mm=0.8, line_width_mm=1.2)
    _ensure_all_balloons(context, page)
    _setup_camera_for_grid(page, scale_m=0.25)
    _render_to(_OUT_PATH / "m_width_scale.png", width_px=1200, height_px=300)
    print(f"  [M4] width_scale: 出力")


def scenario_spacing_scale():
    """M5: 間隔変化 (spacing_scale_percent) 100/80/50/25 (rect で確認)."""
    context = _reset_work()
    page = context.scene.bmanga_work.pages[0]
    pk = _page_key()
    scales = [100.0, 80.0, 50.0, 25.0]
    for idx, scale in enumerate(scales):
        _add_multi_balloon(page, pk, idx, "rect",
                           direction="outside", count=4,
                           spacing_scale=scale,
                           width_mm=0.6, spacing_mm=1.0, line_width_mm=1.2)
    _ensure_all_balloons(context, page)
    _setup_camera_for_grid(page, scale_m=0.25)
    _render_to(_OUT_PATH / "m_spacing_scale.png", width_px=1200, height_px=300)
    print(f"  [M5] spacing_scale: 出力")


def scenario_thorn_valley_peak():
    """M6: トゲの 谷/山の線幅 % 組合せ (100/100, 100/30, 30/100, 0/100)."""
    context = _reset_work()
    page = context.scene.bmanga_work.pages[0]
    pk = _page_key()
    cases = [(100, 100), (100, 30), (30, 100), (0, 100)]
    for idx, (valley, peak) in enumerate(cases):
        _add_multi_balloon(page, pk, idx, "thorn",
                           direction="outside", count=3,
                           valley_pct=valley, peak_pct=peak,
                           width_mm=0.6, spacing_mm=0.8, line_width_mm=1.2)
    _ensure_all_balloons(context, page)
    _setup_camera_for_grid(page, scale_m=0.25)
    _render_to(_OUT_PATH / "m_thorn_valley_peak.png", width_px=1200, height_px=300)
    print(f"  [M6] thorn valley/peak: 出力")


def scenario_thorn_valley_peak_closeup():
    """M6b: トゲの 谷/山の線幅 を更に強調 (大型トゲ + 太い多重線 + 単独表示)."""
    context = _reset_work()
    page = context.scene.bmanga_work.pages[0]
    pk = _page_key()
    # 大きなトゲ (60mm) で太い多重線 (1.5mm) を 3 本配置し、 valley/peak の差を強調
    cases = [
        (100, 100, "100v_100p"),
        (100, 0, "100v_0p_peak_gone"),
        (0, 100, "0v_100p_valley_gone"),
    ]
    for idx, (valley, peak, label) in enumerate(cases):
        entry = page.balloons.add()
        entry.id = f"m_closeup_{idx}"
        entry.title = label
        entry.shape = "thorn"
        entry.x_mm = 15.0 + idx * 80.0
        entry.y_mm = 50.0
        entry.width_mm = 60.0
        entry.height_mm = 60.0
        entry.parent_kind = "page"
        entry.parent_key = pk
        entry.line_style = "double"
        entry.line_width_mm = 1.5
        entry.line_color = (1.0, 0.2, 0.2, 1.0)
        entry.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry.fill_opacity = 100.0
        entry.multi_line_count = 3
        entry.multi_line_direction = "outside"
        entry.multi_line_width_mm = 1.5
        entry.multi_line_spacing_mm = 1.0
        entry.thorn_multi_line_valley_width_pct = valley
        entry.thorn_multi_line_peak_width_pct = peak
    _ensure_all_balloons(context, page)
    _setup_camera_for_grid(page, scale_m=0.30)
    _render_to(_OUT_PATH / "m_thorn_valley_peak_closeup.png", width_px=1500, height_px=500)
    print(f"  [M6b] thorn valley/peak closeup: 出力")


def scenario_thorn_length_change():
    """M7: トゲの 長さ変化 (near/far) (100/100, 100/50, 50/100, 25/100)."""
    context = _reset_work()
    page = context.scene.bmanga_work.pages[0]
    pk = _page_key()
    cases = [(100, 100), (100, 50), (50, 100), (25, 100)]
    for idx, (near, far) in enumerate(cases):
        _add_multi_balloon(page, pk, idx, "thorn",
                           direction="outside", count=4,
                           length_near=near, length_far=far,
                           valley_pct=30.0, peak_pct=100.0,  # 顕著にするため
                           width_mm=0.6, spacing_mm=0.8, line_width_mm=1.2)
    _ensure_all_balloons(context, page)
    _setup_camera_for_grid(page, scale_m=0.25)
    _render_to(_OUT_PATH / "m_thorn_length.png", width_px=1200, height_px=300)
    print(f"  [M7] thorn length change: 出力")


def scenario_thorn_cross():
    """M8: トゲの 山谷を延ばして交差 (off/on)."""
    context = _reset_work()
    page = context.scene.bmanga_work.pages[0]
    pk = _page_key()
    for idx, cross in enumerate([False, True]):
        _add_multi_balloon(page, pk, idx, "thorn",
                           direction="outside", count=3,
                           valley_pct=30.0, peak_pct=100.0,
                           length_near=100.0, length_far=50.0,
                           cross=cross,
                           width_mm=0.6, spacing_mm=0.8, line_width_mm=1.2)
    _ensure_all_balloons(context, page)
    _setup_camera_for_grid(page, scale_m=0.18)
    _render_to(_OUT_PATH / "m_thorn_cross.png", width_px=900, height_px=400)
    print(f"  [M8] thorn cross: 出力")


def scenario_extreme_counts():
    """M9: 本数 1 (= 単線) / 本数 12 (極端多)."""
    context = _reset_work()
    page = context.scene.bmanga_work.pages[0]
    pk = _page_key()
    cases = [(1, "cloud"), (1, "rect"), (12, "cloud"), (12, "rect")]
    for idx, (count, shape) in enumerate(cases):
        _add_multi_balloon(page, pk, idx, shape,
                           direction="outside", count=count,
                           width_mm=0.3, spacing_mm=0.5, line_width_mm=1.0)
    _ensure_all_balloons(context, page)
    _setup_camera_for_grid(page, scale_m=0.25)
    _render_to(_OUT_PATH / "m_extreme_counts.png", width_px=1200, height_px=300)
    print(f"  [M9] extreme counts: 出力")


def main() -> int:
    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    print(f"=== 出力先: {_OUT_PATH} ===")
    scenario_directions()
    scenario_width_scale()
    scenario_spacing_scale()
    scenario_thorn_valley_peak()
    scenario_thorn_valley_peak_closeup()
    scenario_thorn_length_change()
    scenario_thorn_cross()
    scenario_extreme_counts()
    print(f"=== 全シナリオ完了 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
