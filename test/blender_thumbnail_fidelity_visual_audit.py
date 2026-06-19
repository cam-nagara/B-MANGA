"""Blender実機用: サムネイル(出力エンジン)とビューポート(実機レンダー)の一致監査.

`side_by_side.png` を見比べて差異を探す。

多パターンの要素を 1 ページに配置し、
  A) Blender 実機レンダー (EEVEE, ビューポート相当 = メッシュの見た目)
  B) 出力エンジン (export_pipeline.render_page = ページ一覧サムネイル/ページ出力と同じ)
を同じ画角で描き、横並び画像にして AI 目視レビューする。

出力: D:/Develop/Blender/B-MANGA/_verify/thumb_audit/*.png
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "_verify" / "thumb_audit"
MOD = "bmanga_thumb_fidelity_audit"
DPI = 150
FONT_PATH = "C:/Windows/Fonts/YuGothM.ttc"


def _load_addon():
    spec = importlib.util.spec_from_file_location(MOD, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD] = mod
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _sub(path: str):
    return importlib.import_module(f"{MOD}.{path}")


def _add_balloon(page, *, shape="ellipse", x=10.0, y=10.0, w=40.0, h=32.0, **props):
    balloon_op = _sub("operators.balloon_op")
    entry = balloon_op._create_balloon_entry(
        bpy.context, page, shape=shape, x=x, y=y, w=w, h=h,
        parent_kind="page", parent_key=str(page.id),
    )
    for key, value in props.items():
        if hasattr(entry, key):
            setattr(entry, key, value)
    return entry


def _add_tail_points(entry, points_page_mm, **tail_props):
    balloon_op = _sub("operators.balloon_op")
    idx = balloon_op._add_tail_polyline(entry, points_page_mm[:2])
    tail = entry.tails[idx]
    geom_mod = _sub("utils.balloon_tail_geom")
    for p in points_page_mm[2:]:
        geom_mod.add_polyline_point(tail, (p[0] - entry.x_mm, p[1] - entry.y_mm))
    for key, value in tail_props.items():
        if hasattr(tail, key):
            setattr(tail, key, value)
    return tail


def _build_patterns(page) -> list[tuple[float, float, str]]:
    """要素パターンを配置し、キャプション [(x_mm, y_mm, text), ...] を返す."""
    from PIL import Image, ImageDraw

    captions: list[tuple[float, float, str]] = []
    sp_on = {"cloud_valley_sharp": True}

    # 1段目 (y 250-300): 角を尖らせる
    b = _add_balloon(page, shape="thorn", x=8, y=250, w=46, h=40, line_width_mm=2.0)
    for k, v in sp_on.items():
        setattr(b.shape_params, k, v)
    captions.append((31, 244, "トゲ+尖らせON+太線"))

    b = _add_balloon(page, shape="thorn", x=62, y=250, w=46, h=40, line_width_mm=2.0,
                     outer_white_margin_enabled=True, outer_white_margin_width_mm=1.5,
                     outer_white_margin_color=(0.75, 0.75, 0.9, 1.0))
    for k, v in sp_on.items():
        setattr(b.shape_params, k, v)
    captions.append((85, 244, "トゲ+尖らせ+外フチ"))

    b = _add_balloon(page, shape="thorn-curve", x=116, y=250, w=46, h=40, line_width_mm=1.6,
                     line_style="double", multi_line_count=2)
    for k, v in sp_on.items():
        setattr(b.shape_params, k, v)
    captions.append((139, 244, "トゲ曲線+尖らせ+多重線"))

    b = _add_balloon(page, shape="ellipse", x=170, y=252, w=44, h=36, line_width_mm=2.4)
    _add_tail_points(b, [(192, 256), (180, 220)], root_width_mm=8.0, sharp_corners=True)
    captions.append((192, 214, "しっぽ尖らせON"))

    # 2段目 (y 175-225): 新しっぽ群
    b = _add_balloon(page, shape="cloud", x=8, y=178, w=46, h=38)
    _add_tail_points(b, [(31, 192), (20, 150), (38, 138)], line_type="ellipse_chain",
                     root_width_mm=6.0, tip_width_mm=1.2, ellipse_gap_mm=1.2, curve_mode="curve")
    captions.append((31, 132, "雲+楕円しっぽ結合+曲線"))

    b = _add_balloon(page, shape="ellipse", x=62, y=178, w=44, h=36)
    _add_tail_points(b, [(84, 182), (70, 148), (92, 136)], line_type="line",
                     root_width_mm=2.0, tip_width_mm=2.0, taper_out_percent=70.0, curve_mode="curve")
    captions.append((84, 130, "線しっぽ+抜き70%"))

    b = _add_balloon(page, shape="ellipse", x=116, y=178, w=44, h=36)
    _add_tail_points(b, [(138, 182), (126, 144)], line_type="ellipse_chain",
                     root_width_mm=6.0, tip_width_mm=1.5, ellipse_gap_mm=1.5,
                     ellipse_orient="fixed", ellipse_angle_deg=45.0)
    captions.append((138, 138, "楕円 固定45°"))

    b = _add_balloon(page, shape="ellipse", x=170, y=178, w=44, h=36, line_width_mm=0.8)
    tail = b.tails.add()
    tail.type = "curve"
    tail.direction_deg = 250.0
    tail.length_mm = 22.0
    tail.root_width_mm = 5.0
    tail.curve_bend = 0.8
    captions.append((192, 134, "曲げしっぽ(なめらか)"))

    # 3段目 (y 100-150): 線種・塗り
    b = _add_balloon(page, shape="ellipse", x=8, y=104, w=44, h=36, line_style="shape", line_width_mm=1.8)
    b.line_shape_kind = "triangle"
    b.line_shape_spacing_mm = 1.6
    b.line_shape_orient = "center"
    captions.append((30, 98, "図形▲中心点"))

    ribbon = Image.new("RGBA", (96, 20), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ribbon)
    rd.rectangle((0, 2, 95, 17), fill=(255, 170, 60, 255), outline=(120, 60, 0, 255))
    for sx in range(-20, 96, 14):
        rd.line((sx, 20, sx + 20, 0), fill=(200, 90, 20, 255), width=3)
    ribbon_path = Path(tempfile.gettempdir()) / "bmanga_audit_ribbon.png"
    ribbon.save(ribbon_path)
    b = _add_balloon(page, shape="rect", x=62, y=104, w=44, h=36, corner_type="rounded",
                     line_style="image", line_width_mm=2.5)
    b.line_image_path = str(ribbon_path)
    b.line_image_interval_mm = 14.0
    captions.append((84, 98, "画像線"))

    b = _add_balloon(page, shape="rect", x=116, y=104, w=44, h=36, corner_type="rounded",
                     line_style="dashed", fill_gradient_enabled=True,
                     fill_gradient_start_color=(1.0, 0.85, 0.85, 1.0),
                     fill_gradient_end_color=(0.85, 0.9, 1.0, 1.0),
                     fill_gradient_angle_deg=45.0)
    captions.append((138, 98, "破線+グラデ塗り"))

    b = _add_balloon(page, shape="ellipse", x=170, y=104, w=44, h=36, line_style="dotted",
                     rotation_deg=25.0, fill_color=(0.95, 1.0, 0.9, 1.0))
    _add_tail_points(b, [(192, 108), (182, 78)], root_width_mm=4.0)
    captions.append((192, 72, "点線+回転25°"))

    # 4段目 (y 24-75): フラッシュ / NURBS / 細線曲線
    b = _add_balloon(page, shape="ellipse", x=8, y=28, w=46, h=38, line_style="uni_flash")
    captions.append((31, 20, "ウニフラ"))

    b = _add_balloon(page, shape="ellipse", x=62, y=28, w=46, h=38, line_style="white_outline")
    captions.append((85, 20, "白抜き線"))

    # NURBS フキダシ (ツールと同じ補間)
    nurbs_mod = _sub("operators.balloon_nurbs_tool_op")
    geom = _sub("utils.geom")
    page_grid = _sub("utils.page_grid")
    work = bpy.context.scene.bmanga_work
    ox, oy = page_grid.page_total_offset_mm(work, bpy.context.scene, 0)
    pts = [(120, 64), (142, 70), (158, 52), (148, 30), (124, 32)]
    controls = nurbs_mod._interpolating_controls([(ox + x, oy + y) for x, y in pts])
    curve = bpy.data.curves.new("AuditNURBS", "CURVE")
    curve.dimensions = "2D"
    spline = curve.splines.new("NURBS")
    spline.points.add(len(controls) - 1)
    for sp_pt, (x_mm, y_mm) in zip(spline.points, controls):
        sp_pt.co = (geom.mm_to_m(x_mm), geom.mm_to_m(y_mm), 0.0, 1.0)
    spline.use_cyclic_u = True
    spline.order_u = 4
    obj = bpy.data.objects.new("AuditNURBS", curve)
    bpy.context.scene.collection.objects.link(obj)
    for sel in list(bpy.context.selected_objects):
        sel.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    result = bpy.ops.bmanga.balloon_register_selected_curve()
    assert "FINISHED" in result, result
    captions.append((139, 16, "NURBSフキダシ"))

    b = _add_balloon(page, shape="fluffy", x=170, y=28, w=46, h=38, line_width_mm=0.4)
    captions.append((193, 20, "もやもや細線"))

    return captions


def _ensure_all(page) -> None:
    bco = _sub("utils.balloon_curve_object")
    for entry in page.balloons:
        bco.ensure_balloon_curve_object(scene=bpy.context.scene, entry=entry, page=page)


def _render_scene(out_path: Path, page_index: int, page_w_mm: float, page_h_mm: float) -> None:
    """実機 (EEVEE) レンダー: ビューポートのメッシュの見た目をそのまま写す."""
    page_grid = _sub("utils.page_grid")
    work = bpy.context.scene.bmanga_work
    ox, oy = page_grid.page_total_offset_mm(work, bpy.context.scene, page_index)
    cx_m = (ox + page_w_mm * 0.5) * 0.001
    cy_m = (oy + page_h_mm * 0.5) * 0.001
    for o in list(bpy.data.objects):
        if o.type == "CAMERA":
            bpy.data.objects.remove(o, do_unlink=True)
    cam_data = bpy.data.cameras.new("auditcam")
    cam = bpy.data.objects.new("auditcam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = (cx_m, cy_m, 1.0)
    cam.rotation_euler = (0.0, 0.0, 0.0)
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = max(page_w_mm, page_h_mm) * 0.001
    sc = bpy.context.scene
    sc.camera = cam
    world = sc.world or bpy.data.worlds.new("AuditWorld")
    sc.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
        bg.inputs[1].default_value = 1.0
    items = {i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    sc.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in items else "BLENDER_EEVEE"
    # フィルム調の色変換 (AgX) だと色が薄く写り、出力エンジンとの比較にならないため
    # Standard で素の色を写す
    try:
        sc.view_settings.view_transform = "Standard"
        sc.view_settings.look = "None"
    except Exception:
        pass
    px_w = int(round(page_w_mm / 25.4 * DPI))
    px_h = int(round(page_h_mm / 25.4 * DPI))
    sc.render.resolution_x = px_w
    sc.render.resolution_y = px_h
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.filepath = str(out_path)
    sc.render.film_transparent = False
    bpy.ops.render.render(write_still=True)
    print(f"SCENE_RENDER: {out_path}", flush=True)


def _render_export(out_path: Path, page) -> None:
    export_pipeline = _sub("io.export_pipeline")
    work = bpy.context.scene.bmanga_work
    options = export_pipeline.ExportOptions(
        area="canvas",
        dpi_override=DPI,
        include_border=False,
        include_white_margin=False,
        include_nombre=False,
        include_work_info=False,
        include_tombo=False,
        include_paper_color=True,
        include_coma_previews=False,
    )
    image = export_pipeline.render_page(work, page, options).convert("RGB")
    image.save(out_path)
    print(f"EXPORT_RENDER: {out_path}", flush=True)


def _side_by_side(scene_png: Path, export_png: Path, captions, page_h_mm: float, out_path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    a = Image.open(scene_png).convert("RGB")
    b = Image.open(export_png).convert("RGB")
    if b.size != a.size:
        b = b.resize(a.size, Image.LANCZOS)
    font = ImageFont.truetype(FONT_PATH, 26)
    title_font = ImageFont.truetype(FONT_PATH, 40)

    def _annotate(img, title):
        draw = ImageDraw.Draw(img)
        draw.text((20, 12), title, fill=(180, 30, 30), font=title_font)
        for x_mm, y_mm, text in captions:
            px = int(round(x_mm / 25.4 * DPI))
            py = img.height - int(round(y_mm / 25.4 * DPI))
            draw.text((px, py), text, fill=(90, 90, 90), font=font, anchor="ma")
        return img

    a = _annotate(a, "A: 実機レンダー (画面の見た目)")
    b = _annotate(b, "B: 出力エンジン (サムネイル/ページ出力)")
    gap = 24
    combo = Image.new("RGB", (a.width + b.width + gap, a.height), (200, 200, 200))
    combo.paste(a, (0, 0))
    combo.paste(b, (a.width + gap, 0))
    if combo.width > 2000:
        combo = combo.resize((2000, int(combo.height * 2000 / combo.width)), Image.LANCZOS)
    combo.save(out_path)
    print(f"SIDE_BY_SIDE: {out_path}", flush=True)


def main() -> None:
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_thumb_audit_"))
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "ThumbAudit.bmanga"))
    assert result == {"FINISHED"}, result
    work = bpy.context.scene.bmanga_work
    work.paper.canvas_width_mm = 220.0
    work.paper.canvas_height_mm = 310.0
    work.paper.dpi = 600
    result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
    assert result == {"FINISHED"}, result
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    page.comas.clear()
    # コマの実体オブジェクトも除去 (実アプリではコマ削除オペレーターが行う)
    for obj in list(bpy.data.objects):
        if obj.name.startswith(("coma_", "page_coma_")):
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass

    captions = _build_patterns(page)
    _ensure_all(page)



    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scene_png = OUT_DIR / "A_scene_render.png"
    export_png = OUT_DIR / "B_export_render.png"
    _render_export(export_png, page)
    _render_scene(scene_png, 0, 220.0, 310.0)
    _side_by_side(scene_png, export_png, captions, 310.0, OUT_DIR / "side_by_side.png")
    print("THUMB_AUDIT_DONE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)
    sys.stdout.flush()
    os._exit(0)
