"""Blender実機用: ページ/コママスクの組み合わせ目視監査."""

from __future__ import annotations

import base64
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(
    os.environ.get("BNAME_MASK_VISUAL_OUT", "")
    or (ROOT / ".codex" / "visual" / "bname_mask_matrix")
)
PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYPgPAAEDAQCW"
    "A0r4AAAAAElFTkSuQmCC"
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_mask_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_mask_visual"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _write_png(path: Path) -> None:
    path.write_bytes(base64.b64decode(PNG_1PX))


def _mesh_mask_state(obj, expected: str, mask_apply) -> tuple[bool, str]:
    coma = obj.modifiers.get(mask_apply.MOD_NAME_COMA_MASK)
    page = obj.modifiers.get(mask_apply.MOD_NAME_PAGE_MASK)
    if expected == "coma":
        ok = coma is not None and getattr(coma, "object", None) is not None and page is None
        return ok, "コマ" if ok else f"NG c={bool(coma)} p={bool(page)}"
    if expected == "page":
        ok = page is not None and getattr(page, "object", None) is not None and coma is None
        return ok, "ページ" if ok else f"NG c={bool(coma)} p={bool(page)}"
    ok = coma is None and page is None
    return ok, "なし" if ok else f"NG c={bool(coma)} p={bool(page)}"


def _gp_mask_bounds_mm(obj) -> tuple[float, float, float, float] | None:
    from bname_dev_mask_visual.utils.geom import m_to_mm

    layers = getattr(getattr(obj, "data", None), "layers", None)
    if layers is None:
        return None
    mask_layer = layers.get("__bname_mask")
    if mask_layer is None:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for frame in getattr(mask_layer, "frames", []) or []:
        drawing = getattr(frame, "drawing", None)
        for stroke in getattr(drawing, "strokes", []) or []:
            for point in getattr(stroke, "points", []) or []:
                pos = getattr(point, "position", None)
                if pos is None:
                    continue
                xs.append(m_to_mm(float(pos[0])))
                ys.append(m_to_mm(float(pos[1])))
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _close_bounds(actual, expected, tol: float = 0.35) -> bool:
    if actual is None:
        return False
    return all(abs(float(a) - float(e)) <= tol for a, e in zip(actual, expected, strict=True))


def _gp_mask_state(obj, expected: str, page_bounds, coma_bounds) -> tuple[bool, str]:
    bounds = _gp_mask_bounds_mm(obj)
    if expected == "coma":
        ok = _close_bounds(bounds, coma_bounds)
        return ok, "コマ" if ok else f"NG {bounds}"
    if expected == "page":
        ok = _close_bounds(bounds, page_bounds)
        return ok, "ページ" if ok else f"NG {bounds}"
    layers = getattr(getattr(obj, "data", None), "layers", None)
    mask_layer = layers.get("__bname_mask") if layers is not None else None
    return mask_layer is None, "なし" if mask_layer is None else "NG maskあり"


def _effect_mask_state(obj) -> tuple[bool, str]:
    layers = getattr(getattr(obj, "data", None), "layers", None)
    mask_layer = layers.get("__bname_mask") if layers is not None else None
    return mask_layer is None, "なし" if mask_layer is None else "NG maskあり"


def _assert_page_mask_volumes_are_hidden(mask_apply) -> None:
    from bname_dev_mask_visual.utils import paper_bg_object

    for obj in bpy.data.objects:
        if obj.name.startswith(paper_bg_object.PAPER_BG_NAME_PREFIX):
            assert obj.modifiers.get(mask_apply.MOD_NAME_PAGE_MASK_VOLUME) is None, (
                f"表示用紙にマスク用の厚みがあります: {obj.name}"
            )
        if obj.name.startswith(mask_apply.PAGE_MASK_VOLUME_NAME_PREFIX):
            assert obj.hide_viewport and obj.hide_render and obj.hide_select, (
                f"ページマスク用オブジェクトが表示対象です: {obj.name}"
            )


def _create_image(context, page, parent_kind: str, parent_key: str, index: int, image_path: Path):
    from bname_dev_mask_visual.utils import image_real_object

    entry = context.scene.bname_image_layers.add()
    entry.id = f"mask_image_{index}"
    entry.title = "画像"
    entry.filepath = str(image_path)
    entry.x_mm = -20.0
    entry.y_mm = 20.0
    entry.width_mm = 190.0
    entry.height_mm = 110.0
    entry.parent_kind = parent_kind
    entry.parent_key = parent_key
    return image_real_object.ensure_image_real_object(scene=context.scene, entry=entry, page=page)


def _create_raster(context, parent_kind: str, parent_key: str, index: int):
    from bname_dev_mask_visual.operators import raster_layer_op

    entry = context.scene.bname_raster_layers.add()
    entry.id = f"mask_raster_{index}"
    entry.title = "ラスター"
    entry.scope = "master" if parent_kind in {"none", "outside"} else "page"
    entry.parent_kind = "none" if parent_kind in {"none", "outside"} else parent_kind
    entry.parent_key = parent_key
    entry.width_mm = 190.0
    entry.height_mm = 110.0
    return raster_layer_op.ensure_raster_plane(context, entry)


def _create_text(context, page, parent_kind: str, parent_key: str, index: int):
    from bname_dev_mask_visual.operators import text_op
    from bname_dev_mask_visual.utils import text_real_object

    entry, _missing = text_op._create_text_entry(
        context,
        page,
        body=f"文字{index}",
        speaker_type="normal",
        x_mm=-20.0,
        y_mm=20.0,
        width_mm=190.0,
        height_mm=60.0,
        parent_kind=parent_kind,
        parent_key=parent_key,
    )
    return text_real_object.find_text_object(page.id, entry.id)


def _create_balloon(context, page, parent_kind: str, parent_key: str, index: int):
    from bname_dev_mask_visual.utils import balloon_curve_object

    entry = page.balloons.add()
    entry.id = f"mask_balloon_{index}"
    entry.title = "フキダシ"
    entry.shape = "ellipse"
    entry.x_mm = -20.0
    entry.y_mm = 20.0
    entry.width_mm = 190.0
    entry.height_mm = 90.0
    entry.parent_kind = parent_kind
    entry.parent_key = parent_key
    obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    fill = bpy.data.objects.get(f"{balloon_curve_object.BALLOON_FILL_NAME_PREFIX}{entry.id}")
    return obj, fill


def _create_gp(context, parent_kind: str, parent_key: str, index: int):
    from bname_dev_mask_visual.utils import gp_object_layer

    return gp_object_layer.create_layer_gp_object(
        scene=context.scene,
        bname_id=f"mask_gp_{index}",
        title="GP",
        z_index=100 + index,
        parent_kind=parent_kind,
        parent_key=parent_key,
    )


def _create_effect(context, parent_kind: str, parent_key: str, index: int):
    from bname_dev_mask_visual.utils import effect_line_object

    return effect_line_object.create_effect_line_object(
        scene=context.scene,
        bname_id=f"mask_effect_{index}",
        title="効果線",
        z_index=200 + index,
        parent_kind=parent_kind,
        parent_key=parent_key,
    )


def _draw_report(rows: list[dict], output: Path) -> None:
    from bname_dev_mask_visual.utils import python_deps

    python_deps.ensure_bundled_wheels_on_path()
    from PIL import Image, ImageDraw, ImageFont  # type: ignore

    cell_w = 210
    cell_h = 82
    left_w = 96
    header_h = 82
    cols = ["ページ", "コマ", "ページ外"]
    layer_names = ["画像", "ラスター", "テキスト", "フキダシ", "GP", "効果線"]
    width = left_w + cell_w * len(cols)
    height = header_h + cell_h * len(layer_names) + 42
    image = Image.new("RGB", (width, height), "#f6f6f6")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("meiryo.ttc", 15)
        font_bold = ImageFont.truetype("meiryob.ttc", 16)
        small = ImageFont.truetype("meiryo.ttc", 12)
    except Exception:
        font = font_bold = small = ImageFont.load_default()
    draw.text((18, 16), "B-Name マスク組み合わせ目視監査", fill="#111", font=font_bold)
    draw.text((18, 44), "緑=期待マスク適用 / 赤=不整合", fill="#333", font=small)
    for c, name in enumerate(cols):
        x = left_w + c * cell_w
        draw.rectangle((x, header_h - 34, x + cell_w - 8, header_h - 8), fill="#e7e7e7")
        draw.text((x + 12, header_h - 30), name, fill="#111", font=font_bold)
    by_key = {(r["layer"], r["scope"]): r for r in rows}
    for r, layer in enumerate(layer_names):
        y = header_h + r * cell_h
        draw.text((18, y + 25), layer, fill="#111", font=font_bold)
        for c, scope in enumerate(cols):
            x = left_w + c * cell_w
            data = by_key[(layer, scope)]
            ok = bool(data["ok"])
            fill = "#dff2df" if ok else "#ffd9d9"
            outline = "#3c8f3c" if ok else "#b93636"
            draw.rectangle((x, y, x + cell_w - 8, y + cell_h - 8), fill=fill, outline=outline, width=2)
            draw.text((x + 12, y + 10), "OK" if ok else "NG", fill=outline, font=font_bold)
            draw.text((x + 12, y + 36), str(data["state"])[:22], fill="#222", font=font)
            draw.text((x + 12, y + 58), str(data["note"])[:25], fill="#555", font=small)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_mask_visual_"))
    mod = None
    rows: list[dict] = []
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "MaskVisual.bname"))
        assert "FINISHED" in result, result

        from bname_dev_mask_visual.utils import active_target
        from bname_dev_mask_visual.utils import coma_plane
        from bname_dev_mask_visual.utils import mask_apply
        from bname_dev_mask_visual.utils.layer_hierarchy import coma_stack_key, page_stack_key

        context = bpy.context
        scene = context.scene
        work = scene.bname_work
        page = work.pages[0]
        coma = page.comas[0]
        coma.shape_type = "rect"
        coma.rect_x_mm = 20.0
        coma.rect_y_mm = 40.0
        coma.rect_width_mm = 120.0
        coma.rect_height_mm = 160.0
        page_key = page_stack_key(page)
        coma_key = coma_stack_key(page, coma)
        coma_plane.ensure_coma_mask(scene, work, page, coma)
        active_target.focus_active_coma(scene, work, 0, 0)

        image_path = temp_root / "source.png"
        _write_png(image_path)
        page_bounds = (0.0, 0.0, float(work.paper.canvas_width_mm), float(work.paper.canvas_height_mm))
        coma_bounds = (20.0, 40.0, 140.0, 200.0)
        scopes = [
            ("ページ", "page", page_key, "page"),
            ("コマ", "coma", coma_key, "coma"),
            ("ページ外", "none", "", "none"),
        ]
        creators = [
            ("画像", lambda pk, key, i: _create_image(context, page, pk, key, i, image_path), "mesh"),
            ("ラスター", lambda pk, key, i: _create_raster(context, pk, key, i), "mesh"),
            ("テキスト", lambda pk, key, i: _create_text(context, page, pk, key, i), "mesh"),
            ("フキダシ", lambda pk, key, i: _create_balloon(context, page, pk, key, i), "balloon"),
            ("GP", lambda pk, key, i: _create_gp(context, pk, key, i), "gp"),
            ("効果線", lambda pk, key, i: _create_effect(context, pk, key, i), "effect"),
        ]
        index = 0
        for layer_name, creator, check_kind in creators:
            for scope_name, parent_kind, parent_key, expected in scopes:
                index += 1
                created = creator(parent_kind, parent_key, index)
                ok = False
                state = "未確認"
                if check_kind == "mesh":
                    ok, state = _mesh_mask_state(created, expected, mask_apply)
                elif check_kind == "gp":
                    ok, state = _gp_mask_state(created, expected, page_bounds, coma_bounds)
                elif check_kind == "effect":
                    ok, state = _effect_mask_state(created)
                else:
                    outline, fill = created
                    ok1, state1 = _mesh_mask_state(outline, expected, mask_apply)
                    ok2, state2 = _mesh_mask_state(fill, expected, mask_apply)
                    ok = ok1 and ok2
                    state = f"線:{state1} 塗:{state2}"
                rows.append({
                    "layer": layer_name,
                    "scope": scope_name,
                    "ok": ok,
                    "state": state,
                    "note": "期待=" + ("マスクなし" if expected == "none" else expected),
                })
        output = OUT_DIR / "bname_mask_matrix.png"
        _draw_report(rows, output)
        _assert_page_mask_volumes_are_hidden(mask_apply)
        if not all(bool(row["ok"]) for row in rows):
            failed = [f"{row['layer']}/{row['scope']}:{row['state']}" for row in rows if not row["ok"]]
            raise AssertionError("; ".join(failed))
        print(f"BNAME_MASK_VISUAL_MATRIX_OK visual={output}")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
