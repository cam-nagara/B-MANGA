"""Blender 実機用: Phase A〜E 完了後の総合 AI 目視監査.

複数シナリオでフキダシをレンダーし、 ユーザー (= AI 目視) が画像で確認する。

シナリオ:
  S1. コマ内マスク (全形状でコマ右上にはみ出すフキダシを置く)
  S2. 不透明度・ぼかし・グラデーション・多重線・フチ
  S3. しっぽ (3 type × 各方向) + しっぽがコマ外へ突き出す
  S4. ページ直下フキダシ (マスクなし) と コマ内フキダシ (マスクあり) の混在
  S5. 細い線/太い線/極小フキダシ/極大フキダシ

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_node_minimization_visual_audit.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_VISUAL_AUDIT_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_visual_audit_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_visual_audit",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_visual_audit"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_ortho_camera(name: str, center_x_m: float, center_y_m: float, scale_m: float):
    obj_name = name
    if obj_name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[obj_name], do_unlink=True)
    if obj_name in bpy.data.cameras:
        bpy.data.cameras.remove(bpy.data.cameras[obj_name])
    cam_data = bpy.data.cameras.new(obj_name)
    cam = bpy.data.objects.new(obj_name, cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = (center_x_m, center_y_m, 2.0)
    cam.rotation_euler = (0.0, 0.0, 0.0)
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = scale_m
    bpy.context.scene.camera = cam


def _render_to(path: Path, *, width_px: int = 900, height_px: int = 900):
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
    """新しい作品を作って初期化する."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_visual_audit_work_"))
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "VisualAudit.bmanga"))  # type: ignore[attr-defined]
    assert "FINISHED" in result, result
    return bpy.context, temp_root


def _bco():
    from bmanga_dev_visual_audit.utils import balloon_curve_object as bco
    return bco


def _coma_plane():
    from bmanga_dev_visual_audit.utils import coma_plane
    return coma_plane


def _coma_stack_key(page, coma):
    from bmanga_dev_visual_audit.utils.layer_hierarchy import coma_stack_key
    return coma_stack_key(page, coma)


def _page_stack_key(page):
    from bmanga_dev_visual_audit.utils.layer_hierarchy import page_stack_key
    return page_stack_key(page)


def _page_offset_m(work, scene):
    from bmanga_dev_visual_audit.utils import page_grid
    ox_mm, oy_mm = page_grid.page_total_offset_mm(work, scene, 0)
    return ox_mm / 1000.0, oy_mm / 1000.0


def _setup_coma(page, idx, x_mm, y_mm, w_mm, h_mm, bg=(0.85, 0.85, 0.85, 1.0)):
    """既存のコマを矩形に設定する (idx=0 なら work_new で作られた初期コマを再利用)."""
    if idx >= len(page.comas):
        return None
    coma = page.comas[idx]
    coma.shape_type = "rect"
    coma.rect_x_mm = x_mm
    coma.rect_y_mm = y_mm
    coma.rect_width_mm = w_mm
    coma.rect_height_mm = h_mm
    coma.background_color = bg
    return coma


def _add_balloon(page, *, balloon_id, shape, x, y, w, h, parent_kind="page", parent_key="",
                 line_style="solid", line_width_mm=1.5, line_color=(0, 0, 0, 1),
                 fill_color=(1, 1, 1, 1), fill_opacity=100.0, opacity=100.0,
                 outer_enabled=False, outer_width_mm=1.0, outer_color=(1, 0.3, 0.3, 1),
                 inner_enabled=False, inner_width_mm=1.0, inner_color=(0.3, 0.5, 1, 1),
                 multi_count=3, multi_dir="outside", multi_width_mm=0.4, multi_spacing_mm=0.7,
                 fill_blur_amount=0.0, fill_blur_dither=False,
                 gradient_enabled=False, gradient_start=(1, 1, 1, 1), gradient_end=(0.7, 0.7, 1, 1),
                 gradient_angle_deg=0.0,
                 rotation_deg=0.0):
    entry = page.balloons.add()
    entry.id = balloon_id
    entry.title = balloon_id
    entry.shape = shape
    entry.x_mm = x
    entry.y_mm = y
    entry.width_mm = w
    entry.height_mm = h
    entry.parent_kind = parent_kind
    entry.parent_key = parent_key
    entry.line_style = line_style
    entry.line_width_mm = line_width_mm
    entry.line_color = line_color
    entry.fill_color = fill_color
    entry.fill_opacity = fill_opacity
    entry.opacity = opacity
    entry.outer_white_margin_enabled = outer_enabled
    entry.outer_white_margin_width_mm = outer_width_mm
    entry.outer_white_margin_color = outer_color
    entry.inner_white_margin_enabled = inner_enabled
    entry.inner_white_margin_width_mm = inner_width_mm
    entry.inner_white_margin_color = inner_color
    entry.multi_line_count = multi_count
    entry.multi_line_direction = multi_dir
    entry.multi_line_width_mm = multi_width_mm
    entry.multi_line_spacing_mm = multi_spacing_mm
    entry.fill_blur_amount = fill_blur_amount
    entry.fill_blur_dither = fill_blur_dither
    entry.fill_gradient_enabled = gradient_enabled
    entry.fill_gradient_start_color = gradient_start
    entry.fill_gradient_end_color = gradient_end
    entry.fill_gradient_angle_deg = gradient_angle_deg
    entry.rotation_deg = rotation_deg
    return entry


# -----------------------------------------------------------------------------
# シナリオ実装
# -----------------------------------------------------------------------------

def scenario_1_coma_mask_all_shapes():
    """S1: コマ内マスク - 各形状でコマ右上にはみ出すフキダシ."""
    context, _ = _reset_work()
    scene = context.scene
    work = scene.bmanga_work
    page = work.pages[0]
    coma = _setup_coma(page, 0, 20.0, 30.0, 100.0, 100.0)
    parent_key = _coma_stack_key(page, coma)
    cp = _coma_plane()
    cp.ensure_coma_plane(scene, work, page, coma)
    cp.ensure_coma_mask(scene, work, page, coma)

    shapes = ["rect", "ellipse", "octagon", "cloud", "fluffy", "thorn", "thorn-curve"]
    cols = 4
    cell_w = 23.0
    cell_h = 23.0
    for idx, shape in enumerate(shapes):
        col = idx % cols
        row = idx // cols
        # コマ (20..120, 30..130) 内に均等配置、はみ出すように +大きめ
        x = 30.0 + col * (cell_w + 1.0)
        y = 110.0 - row * (cell_h + 6.0)
        if idx == 0:
            # 右上に大きくはみ出すケース
            x = 95.0
            y = 105.0
        _add_balloon(
            page, balloon_id=f"s1_{shape}", shape=shape,
            x=x, y=y, w=cell_w, h=cell_h,
            parent_kind="coma", parent_key=parent_key,
            line_width_mm=1.0,
            fill_color=(1.0, 1.0, 0.85, 1.0),
        )

    for entry in page.balloons:
        _bco().ensure_balloon_curve_object(scene=scene, entry=entry, page=page)

    ox_m, oy_m = _page_offset_m(work, scene)
    cx_m = (coma.rect_x_mm + coma.rect_width_mm * 0.5) / 1000.0 + ox_m
    cy_m = (coma.rect_y_mm + coma.rect_height_mm * 0.5) / 1000.0 + oy_m
    _set_ortho_camera("audit_cam", cx_m, cy_m, 0.18)
    _render_to(_OUT_PATH / "s1_coma_mask_all_shapes.png")
    print(f"  [S1] レンダー: {_OUT_PATH / 's1_coma_mask_all_shapes.png'}")


def scenario_2_fill_features():
    """S2: 不透明度・ぼかし・グラデーション・多重線・フチ - 全コマ内に配置."""
    context, _ = _reset_work()
    scene = context.scene
    work = scene.bmanga_work
    page = work.pages[0]
    coma = _setup_coma(page, 0, 10.0, 10.0, 180.0, 180.0)
    parent_key = _coma_stack_key(page, coma)
    cp = _coma_plane()
    cp.ensure_coma_plane(scene, work, page, coma)
    cp.ensure_coma_mask(scene, work, page, coma)

    rows = [
        # (label, shape, fill_color, opacity, fill_blur, blur_dither, gradient, outer, inner, multi)
        ("solid_100", "ellipse", (1.0, 1.0, 1.0, 1.0), 100, 0.0, False, False, False, False, False),
        ("solid_50",  "ellipse", (1.0, 1.0, 1.0, 1.0), 50,  0.0, False, False, False, False, False),
        ("blur_30",   "rect",    (1.0, 1.0, 1.0, 1.0), 100, 0.3, False, False, False, False, False),
        ("blur_dith", "rect",    (1.0, 1.0, 1.0, 1.0), 100, 0.6, True,  False, False, False, False),
        ("gradient",  "rect",    (1.0, 1.0, 1.0, 1.0), 100, 0.0, False, True,  False, False, False),
        ("outer_only","cloud",   (1.0, 1.0, 1.0, 1.0), 100, 0.0, False, False, True,  False, False),
        ("inner_only","cloud",   (1.0, 1.0, 1.0, 1.0), 100, 0.0, False, False, False, True,  False),
        ("multi_out", "thorn",   (1.0, 1.0, 1.0, 1.0), 100, 0.0, False, False, False, False, True),
    ]
    cols = 4
    cell_w = 38.0
    cell_h = 38.0
    for idx, (label, shape, fc, op, blur, dither, grad, outer, inner, multi) in enumerate(rows):
        col = idx % cols
        row = idx // cols
        x = 20.0 + col * (cell_w + 8.0)
        y = 130.0 - row * (cell_h + 18.0)
        _add_balloon(
            page, balloon_id=f"s2_{label}", shape=shape,
            x=x, y=y, w=cell_w, h=cell_h,
            parent_kind="coma", parent_key=parent_key,
            line_style="double" if multi else "solid",
            line_width_mm=1.2,
            fill_color=fc, fill_opacity=op, opacity=100.0,
            fill_blur_amount=blur, fill_blur_dither=dither,
            gradient_enabled=grad,
            gradient_start=(1.0, 1.0, 1.0, 1.0),
            gradient_end=(0.2, 0.2, 0.8, 1.0),
            gradient_angle_deg=45.0,
            outer_enabled=outer, outer_width_mm=1.5, outer_color=(1.0, 0.3, 0.3, 1.0),
            inner_enabled=inner, inner_width_mm=1.5, inner_color=(0.3, 0.5, 1.0, 1.0),
            multi_count=4, multi_dir="outside",
            multi_width_mm=0.5, multi_spacing_mm=0.8,
        )

    for entry in page.balloons:
        _bco().ensure_balloon_curve_object(scene=scene, entry=entry, page=page)

    ox_m, oy_m = _page_offset_m(work, scene)
    cx_m = (coma.rect_x_mm + coma.rect_width_mm * 0.5) / 1000.0 + ox_m
    cy_m = (coma.rect_y_mm + coma.rect_height_mm * 0.5) / 1000.0 + oy_m
    _set_ortho_camera("audit_cam", cx_m, cy_m, 0.20)
    _render_to(_OUT_PATH / "s2_fill_features.png", width_px=1024, height_px=1024)
    print(f"  [S2] レンダー: {_OUT_PATH / 's2_fill_features.png'}")


def scenario_3_tails():
    """S3: しっぽ (3 type) + しっぽがコマ外へ突き出す."""
    context, _ = _reset_work()
    scene = context.scene
    work = scene.bmanga_work
    page = work.pages[0]
    coma = _setup_coma(page, 0, 20.0, 30.0, 160.0, 160.0)
    parent_key = _coma_stack_key(page, coma)
    cp = _coma_plane()
    cp.ensure_coma_plane(scene, work, page, coma)
    cp.ensure_coma_mask(scene, work, page, coma)

    # 各 type で 4 方向のしっぽを持つフキダシを 3 つ並べる
    types = ["straight", "curve", "sticky"]
    for idx, ttype in enumerate(types):
        entry = _add_balloon(
            page, balloon_id=f"s3_{ttype}_inside", shape="ellipse",
            x=30.0 + idx * 50.0, y=120.0,
            w=40.0, h=40.0,
            parent_kind="coma", parent_key=parent_key,
            line_width_mm=1.2,
            fill_color=(1.0, 0.9, 0.6, 1.0),
        )
        for direction in (0.0, 90.0, 180.0, 270.0):
            t = entry.tails.add()
            t.type = ttype
            t.direction_deg = direction
            t.length_mm = 18.0
            t.root_width_mm = 8.0
            t.tip_width_mm = 0.5

    # しっぽがコマ外まで突き出すフキダシ (1 番下に配置)
    overflow_entry = _add_balloon(
        page, balloon_id="s3_tail_overflow", shape="cloud",
        x=80.0, y=45.0, w=40.0, h=40.0,
        parent_kind="coma", parent_key=parent_key,
        line_width_mm=1.2,
        fill_color=(0.8, 1.0, 0.9, 1.0),
    )
    t = overflow_entry.tails.add()
    t.type = "straight"
    t.direction_deg = 270.0  # 下方向
    t.length_mm = 50.0  # コマ下端 (30mm) を突き抜ける
    t.root_width_mm = 10.0
    t.tip_width_mm = 0.5

    for entry in page.balloons:
        _bco().ensure_balloon_curve_object(scene=scene, entry=entry, page=page)

    ox_m, oy_m = _page_offset_m(work, scene)
    cx_m = (coma.rect_x_mm + coma.rect_width_mm * 0.5) / 1000.0 + ox_m
    cy_m = (coma.rect_y_mm + coma.rect_height_mm * 0.5) / 1000.0 + oy_m
    _set_ortho_camera("audit_cam", cx_m, cy_m, 0.20)
    _render_to(_OUT_PATH / "s3_tails.png", width_px=1024, height_px=1024)
    print(f"  [S3] レンダー: {_OUT_PATH / 's3_tails.png'}")


def scenario_4_page_vs_coma_mask():
    """S4: ページ直下フキダシ (マスクなし) と コマ内フキダシ (マスクあり) の比較."""
    context, _ = _reset_work()
    scene = context.scene
    work = scene.bmanga_work
    page = work.pages[0]
    page_pk = _page_stack_key(page)
    coma = _setup_coma(page, 0, 20.0, 30.0, 100.0, 100.0)
    coma_pk = _coma_stack_key(page, coma)
    cp = _coma_plane()
    cp.ensure_coma_plane(scene, work, page, coma)
    cp.ensure_coma_mask(scene, work, page, coma)

    # ページ直下: コマ右にはみ出すフキダシ (マスクなし → 全部見える)
    _add_balloon(
        page, balloon_id="s4_page_balloon", shape="ellipse",
        x=95.0, y=70.0, w=50.0, h=50.0,
        parent_kind="page", parent_key=page_pk,
        line_width_mm=1.2,
        fill_color=(1.0, 0.7, 0.7, 1.0),
        outer_enabled=True, outer_width_mm=1.0, outer_color=(1.0, 0.0, 0.0, 1.0),
    )

    # コマ内: 同じ位置に同じサイズのフキダシ (マスクあり → コマ外は透過)
    _add_balloon(
        page, balloon_id="s4_coma_balloon", shape="ellipse",
        x=95.0, y=30.0, w=50.0, h=50.0,
        parent_kind="coma", parent_key=coma_pk,
        line_width_mm=1.2,
        fill_color=(0.7, 0.7, 1.0, 1.0),
        outer_enabled=True, outer_width_mm=1.0, outer_color=(0.0, 0.0, 1.0, 1.0),
    )

    for entry in page.balloons:
        _bco().ensure_balloon_curve_object(scene=scene, entry=entry, page=page)

    ox_m, oy_m = _page_offset_m(work, scene)
    cx_m = (coma.rect_x_mm + coma.rect_width_mm * 0.5) / 1000.0 + ox_m + 0.03
    cy_m = (coma.rect_y_mm + coma.rect_height_mm * 0.5) / 1000.0 + oy_m
    _set_ortho_camera("audit_cam", cx_m, cy_m, 0.22)
    _render_to(_OUT_PATH / "s4_page_vs_coma_mask.png", width_px=1024, height_px=1024)
    print(f"  [S4] レンダー: {_OUT_PATH / 's4_page_vs_coma_mask.png'}")


def scenario_5_extreme_sizes_and_widths():
    """S5: 細い線/太い線/極小フキダシ/極大フキダシ - 全コマ内."""
    context, _ = _reset_work()
    scene = context.scene
    work = scene.bmanga_work
    page = work.pages[0]
    coma = _setup_coma(page, 0, 10.0, 10.0, 180.0, 180.0)
    parent_key = _coma_stack_key(page, coma)
    cp = _coma_plane()
    cp.ensure_coma_plane(scene, work, page, coma)
    cp.ensure_coma_mask(scene, work, page, coma)

    cases = [
        ("tiny_thin",  "ellipse", 30.0, 130.0,  8.0,  8.0, 0.2),
        ("tiny_thick", "ellipse", 60.0, 130.0,  8.0,  8.0, 1.5),
        ("normal",     "cloud",  100.0, 130.0, 30.0, 30.0, 1.0),
        ("big",        "rect",    20.0,  20.0, 80.0, 80.0, 2.5),
        ("very_big",   "octagon",110.0,  20.0, 80.0, 80.0, 3.5),
    ]
    for label, shape, x, y, w, h, lw in cases:
        _add_balloon(
            page, balloon_id=f"s5_{label}", shape=shape,
            x=x, y=y, w=w, h=h,
            parent_kind="coma", parent_key=parent_key,
            line_width_mm=lw,
            fill_color=(1.0, 1.0, 0.85, 1.0),
        )

    for entry in page.balloons:
        _bco().ensure_balloon_curve_object(scene=scene, entry=entry, page=page)

    ox_m, oy_m = _page_offset_m(work, scene)
    cx_m = (coma.rect_x_mm + coma.rect_width_mm * 0.5) / 1000.0 + ox_m
    cy_m = (coma.rect_y_mm + coma.rect_height_mm * 0.5) / 1000.0 + oy_m
    _set_ortho_camera("audit_cam", cx_m, cy_m, 0.22)
    _render_to(_OUT_PATH / "s5_extreme_sizes.png", width_px=1024, height_px=1024)
    print(f"  [S5] レンダー: {_OUT_PATH / 's5_extreme_sizes.png'}")


def main() -> int:
    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    print(f"=== 出力先: {_OUT_PATH} ===")
    scenario_1_coma_mask_all_shapes()
    scenario_2_fill_features()
    scenario_3_tails()
    scenario_4_page_vs_coma_mask()
    scenario_5_extreme_sizes_and_widths()
    print(f"=== 全シナリオ完了: {_OUT_PATH} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
