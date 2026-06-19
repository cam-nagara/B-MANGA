"""Blender実機用: B-MANGA本体の詳細設定チェックボックスをAI目視用に一覧化する."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(
    os.environ.get("BMANGA_FULL_VISUAL_OUT", "")
    or tempfile.mkdtemp(prefix="bmanga_full_visual_")
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_full_audit",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_full_audit"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _font(ImageFont, *, size: int):
    for path in (
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
    ):
        try:
            if Path(path).is_file():
                return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _bool_value(value) -> bool:
    return bool(value)


def _toggle_check(results: list[dict], label: str, target, attr: str, *, verifier=None) -> None:
    before = _bool_value(getattr(target, attr))
    setattr(target, attr, not before)
    after = _bool_value(getattr(target, attr))
    ok = after is (not before)
    extra = ""
    if verifier is not None:
        verified, extra = verifier(before, after)
        ok = ok and bool(verified)
    setattr(target, attr, before)
    results.append(
        {
            "group": label.split(" / ", 1)[0],
            "label": label,
            "before": before,
            "after": after,
            "ok": ok,
            "extra": extra,
        }
    )


def _set_active_object(obj) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _raster_obj_for(entry):
    from bmanga_dev_full_audit.operators import raster_layer_op
    from bmanga_dev_full_audit.utils import object_naming as on

    return on.find_object_by_bmanga_id(entry.id, kind="raster") or bpy.data.objects.get(
        raster_layer_op.raster_plane_name(entry.id)
    )


def _value_node_default(material) -> float:
    for node in material.node_tree.nodes:
        if node.bl_idname == "ShaderNodeValue":
            return float(node.outputs[0].default_value)
    raise AssertionError("raster alpha value node not found")


def _build_scene(context):
    from bmanga_dev_full_audit.core.work import get_work
    from bmanga_dev_full_audit.operators import balloon_op
    from bmanga_dev_full_audit.operators import effect_line_op
    from bmanga_dev_full_audit.operators import text_op
    from bmanga_dev_full_audit.utils import gp_layer_parenting
    from bmanga_dev_full_audit.utils import gpencil as gp_utils
    from bmanga_dev_full_audit.utils import layer_hierarchy
    from bmanga_dev_full_audit.utils import object_naming as on
    from bmanga_dev_full_audit.utils.geom import mm_to_m

    work = get_work(context)
    assert work is not None
    page = work.pages[0]
    coma = page.comas[0]
    page_key = layer_hierarchy.page_stack_key(page)
    coma_key = layer_hierarchy.coma_stack_key(page, coma)
    x = float(coma.rect_x_mm) + float(coma.rect_width_mm) * 0.5
    y = float(coma.rect_y_mm) + float(coma.rect_height_mm) * 0.5

    result = bpy.ops.bmanga.raster_layer_add(
        "EXEC_DEFAULT",
        dpi_preset="custom",
        dpi=72,
        bit_depth="gray8",
        enter_paint=False,
    )
    assert result == {"FINISHED"}, result
    raster_index = int(context.scene.bmanga_active_raster_layer_index)
    assert raster_index >= 0
    raster = context.scene.bmanga_raster_layers[raster_index]
    raster_obj = _raster_obj_for(raster)
    assert raster_obj is not None

    image = context.scene.bmanga_image_layers.add()
    image.id = "image_detail_audit"
    image.title = "画像詳細チェック"
    image.parent_kind = "page"
    image.parent_key = page_key

    balloon = balloon_op._create_balloon_entry(
        context,
        page,
        shape="rect",
        x=x,
        y=y,
        w=45.0,
        h=28.0,
        parent_kind="coma",
        parent_key=coma_key,
    )
    text, missing = text_op._create_text_entry(
        context,
        page,
        body="詳細設定チェック",
        speaker_type="normal",
        x_mm=x,
        y_mm=y,
        width_mm=42.0,
        height_mm=18.0,
        parent_kind="coma",
        parent_key=coma_key,
    )
    assert not missing

    gp_obj = gp_utils.ensure_master_gpencil(context.scene)
    gp_layer = gp_obj.data.layers.new("詳細GP")
    gp_layer_parenting.set_parent_key(gp_layer, page_key)
    frame = gp_utils.ensure_active_frame(gp_layer)
    assert frame is not None and frame.drawing is not None
    gp_utils.add_stroke_to_drawing(
        frame.drawing,
        [(mm_to_m(8), mm_to_m(8), 0.0), (mm_to_m(24), mm_to_m(16), 0.0)],
    )
    gp_mat = gp_utils.ensure_layer_material(gp_obj, gp_layer, activate=True, assign_existing=True)

    effect_obj, effect_layer = effect_line_op._create_effect_layer(
        context,
        (x, y, 38.0, 30.0),
        parent_key=coma_key,
    )
    _set_active_object(effect_obj)
    context.scene.bmanga_active_layer_kind = "effect"
    effect_layer.select = True
    effect_obj.data.layers.active = effect_layer
    effect_obj[on.PROP_KIND] = "effect"

    return {
        "work": work,
        "page": page,
        "coma": coma,
        "image": image,
        "raster": raster,
        "raster_obj": raster_obj,
        "balloon": balloon,
        "text": text,
        "gp_obj": gp_obj,
        "gp_layer": gp_layer,
        "gp_mat": gp_mat,
        "effect_obj": effect_obj,
        "effect_layer": effect_layer,
    }


def _check_detail_toggles(context, targets) -> list[dict]:
    from bmanga_dev_full_audit.core import effect_line
    from bmanga_dev_full_audit.operators import raster_layer_op

    results: list[dict] = []
    image = targets["image"]
    raster = targets["raster"]
    raster_obj = targets["raster_obj"]
    balloon = targets["balloon"]
    text = targets["text"]
    page = targets["page"]
    coma = targets["coma"]
    gp_obj = targets["gp_obj"]
    gp_layer = targets["gp_layer"]
    gp_style = getattr(targets["gp_mat"], "grease_pencil", None)
    effect_params = context.scene.bmanga_effect_line_params

    _toggle_check(results, "画像 / 左右反転", image, "flip_x")
    _toggle_check(results, "画像 / 上下反転", image, "flip_y")
    _toggle_check(results, "画像 / 表示", image, "visible")
    _toggle_check(results, "画像 / ロック", image, "locked")
    _toggle_check(results, "画像 / 2値化", image, "binarize_enabled")

    def raster_visible(_before, after):
        return raster_obj.hide_viewport is (not after) and raster_obj.hide_render is (not after), (
            f"viewport={not raster_obj.hide_viewport}, render={not raster_obj.hide_render}"
        )

    _toggle_check(results, "ラスター / 表示", raster, "visible", verifier=raster_visible)
    _toggle_check(results, "ラスター / ロック", raster, "locked")
    raster.opacity = 31.0
    raster.line_color = (0.8, 0.1, 0.1, 0.5)
    mat = bpy.data.materials.get(raster_layer_op.raster_material_name(raster.id))
    assert mat is not None
    results.append(
        {
            "group": "ラスター",
            "label": "ラスター / 不透明度と線色",
            "before": True,
            "after": True,
            "ok": abs(_value_node_default(mat) - 0.155) < 1e-5,
            "extra": f"alpha={_value_node_default(mat):.3f}",
        }
    )

    _toggle_check(results, "フキダシ / 角丸", balloon, "rounded_corner_enabled")
    _toggle_check(results, "フキダシ / 水平反転", balloon, "flip_h")
    _toggle_check(results, "フキダシ / 垂直反転", balloon, "flip_v")
    _toggle_check(results, "フキダシ / 表示", balloon, "visible")

    _toggle_check(results, "テキスト / 太字", text, "font_bold")
    _toggle_check(results, "テキスト / 斜体", text, "font_italic")
    _toggle_check(results, "テキスト / 白フチ", text, "stroke_enabled")
    _toggle_check(results, "テキスト / 表示", text, "visible")

    _toggle_check(results, "ページ / 表示", page, "visible")
    _toggle_check(results, "コマ枠 / 枠線を表示", coma.border, "visible")
    _toggle_check(results, "フチ / フチ", coma.white_margin, "enabled")

    _toggle_check(results, "GP / 表示 viewport", gp_obj, "hide_viewport")
    _toggle_check(results, "GP / 表示 render", gp_obj, "hide_render")
    if hasattr(gp_layer, "hide"):
        _toggle_check(results, "GP / 非表示", gp_layer, "hide")
    if hasattr(gp_layer, "lock"):
        _toggle_check(results, "GP / ロック", gp_layer, "lock")
    if gp_style is not None:
        _toggle_check(results, "GP / 線を描く", gp_style, "show_stroke")
        if hasattr(gp_style, "show_fill"):
            _toggle_check(results, "GP / 塗りを描く", gp_style, "show_fill")

    def effect_verifier(attr):
        def _verify(_before, after):
            data = effect_line.effect_params_to_dict(effect_params)
            return bool(data.get(attr)) is after, f"保存={data.get(attr)}"

        return _verify

    for attr, label in (
        ("start_to_coma_frame", "始点をコマ枠に設定"),
        ("start_rounded_corner_enabled", "始点 角丸"),
        ("end_rounded_corner_enabled", "終点 角丸"),
        ("brush_jitter_enabled", "線 乱れ"),
        ("spacing_jitter_enabled", "描画間隔 乱れ"),
        ("bundle_enabled", "まとまり"),
        ("fill_base_shape", "終点形状を下地として塗る"),
        ("white_outline_width_jitter_enabled", "太さ乱れ"),
        ("white_outline_length_jitter_enabled", "長さ乱れ"),
    ):
        _toggle_check(results, f"効果線 / {label}", effect_params, attr, verifier=effect_verifier(attr))

    return results


def _write_report(results: list[dict]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / "bmanga_full_visual_audit.json"
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return json_path

    width = 1520
    row_h = 26
    header_h = 92
    height = header_h + row_h * (len(results) + 3)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = _font(ImageFont, size=18)
    font = _font(ImageFont, size=12)
    draw.text((24, 18), "B-MANGA 詳細設定チェックボックス AI目視シート", fill=(0, 0, 0), font=title_font)
    ok_count = sum(1 for item in results if item["ok"])
    draw.text((24, 50), f"対象: {len(results)}項目 / OK: {ok_count} / NG: {len(results) - ok_count}", fill=(0, 0, 0), font=font)
    y = header_h
    current_group = ""
    for item in results:
        if item["group"] != current_group:
            current_group = item["group"]
            draw.rectangle((20, y, width - 20, y + row_h), fill=(232, 236, 244), outline=(160, 160, 160))
            draw.text((30, y + 7), current_group, fill=(0, 0, 0), font=font)
            y += row_h
        ok = bool(item["ok"])
        fill = (236, 249, 236) if ok else (255, 234, 234)
        draw.rectangle((20, y, width - 20, y + row_h), fill=fill, outline=(190, 210, 190) if ok else (220, 150, 150))
        draw.text((30, y + 7), "OK" if ok else "NG", fill=(0, 120, 0) if ok else (180, 0, 0), font=font)
        draw.text((90, y + 7), item["label"], fill=(0, 0, 0), font=font)
        draw.text((560, y + 7), f"{item['before']} → {item['after']}", fill=(0, 0, 0), font=font)
        draw.text((720, y + 7), str(item.get("extra", "")), fill=(45, 45, 45), font=font)
        y += row_h
    image_path = OUT_DIR / "bmanga_full_visual_audit.png"
    image.save(image_path)
    return image_path


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_full_visual_audit_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "FullVisualAudit.bmanga"))
        assert result == {"FINISHED"}, result
        context = bpy.context
        targets = _build_scene(context)
        results = _check_detail_toggles(context, targets)
        failures = [item for item in results if not item["ok"]]
        report = _write_report(results)
        print(f"BMANGA_FULL_VISUAL_AUDIT_OK visual={report} items={len(results)}")
        assert not failures, failures
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
