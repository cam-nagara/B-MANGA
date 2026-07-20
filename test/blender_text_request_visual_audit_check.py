"""Blender実機用: テキスト要望の目視監査画像を生成する."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(os.environ.get("BMANGA_TEXT_REQUEST_VISUAL_AUDIT_OUT", "") or ROOT / "_verify" / "text_request_visual_audit")


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _entry(page, text_id: str, body: str, mode: str, x: float, y: float, w: float, h: float):
    entry = page.texts.add()
    entry.id = text_id
    entry.body = body
    entry.writing_mode = mode
    entry.x_mm = x
    entry.y_mm = y
    entry.width_mm = w
    entry.height_mm = h
    entry.font_size_q = 32.0
    entry.line_height = 1.4
    return entry


def _placements(entry):
    from bmanga_dev.operators import text_edit_runtime
    from bmanga_dev.typography import layout as text_layout

    rect = text_edit_runtime.text_rect(entry)
    inner = text_edit_runtime.text_inner_rect(rect)
    result = text_layout.typeset(entry, inner.x, inner.y, inner.width, inner.height)
    return [
        (
            int(getattr(item, "index", -1)),
            str(getattr(item, "ch", "")),
            round(float(getattr(item, "x_mm", 0.0)), 5),
            round(float(getattr(item, "y_mm", 0.0)), 5),
        )
        for item in result.placements
    ]


def _assert_same_placements(label: str, before, after) -> None:
    if before != after:
        raise AssertionError(f"{label}: 入力中と確定後の文字位置が一致しません: {before} != {after}")


def _render_entry(entry):
    from bmanga_dev.utils import text_real_object

    rendered = text_real_object._render_entry_to_pillow(entry)
    if rendered is None:
        raise AssertionError("テキスト画像を生成できません")
    image, pad_mm, width_mm, height_mm = rendered
    return image.convert("RGBA"), float(pad_mm), float(width_mm), float(height_mm)


def _tint(image, rgb: tuple[int, int, int], alpha_scale: float):
    from PIL import Image

    source = image.convert("RGBA")
    alpha = source.getchannel("A").point(lambda value: int(value * alpha_scale))
    tinted = Image.new("RGBA", source.size, (*rgb, 0))
    tinted.putalpha(alpha)
    return tinted


def _draw_rect_mm(draw, rect, scale: float, canvas_h: int, color, width: int = 3, origin=(0, 0)):
    ox, oy = origin
    x, y, w, h = rect
    left = ox + x * scale
    top = oy + canvas_h - (y + h) * scale
    right = ox + (x + w) * scale
    bottom = oy + canvas_h - y * scale
    draw.rectangle((left, top, right, bottom), outline=color, width=width)


def _paste_render(canvas, entry, rendered, scale: float, canvas_h: int, tint_rgb=None, alpha_scale=1.0, origin=(0, 0)):
    ox, oy = origin
    image, pad_mm, width_mm, height_mm = rendered
    if tint_rgb is not None:
        image = _tint(image, tint_rgb, alpha_scale)
    target_w = max(1, int(round((width_mm + pad_mm * 2.0) * scale)))
    target_h = max(1, int(round((height_mm + pad_mm * 2.0) * scale)))
    image = image.resize((target_w, target_h))
    x = ox + int(round((float(entry.x_mm) - pad_mm) * scale))
    y_top = oy + int(round(canvas_h - (float(entry.y_mm) + float(entry.height_mm) + pad_mm) * scale))
    canvas.alpha_composite(image, (x, y_top))


def _entry_snapshot(rect):
    return SimpleNamespace(x_mm=rect[0], y_mm=rect[1], width_mm=rect[2], height_mm=rect[3])


def _make_visual(state: dict) -> Path:
    from PIL import Image, ImageDraw

    scale = 5.0
    panel_w = 560
    panel_h = 520
    montage = Image.new("RGBA", (panel_w * 2, panel_h * 2), (255, 255, 255, 255))
    draw = ImageDraw.Draw(montage)

    def panel_origin(index: int) -> tuple[int, int]:
        return (panel_w * (index % 2), panel_h * (index // 2))

    for i, title in enumerate(
        (
            "初期入力欄: 9文字×3行 (縦書きは縦長 / 横書きは横長)",
            "横書き: 入力中(青)と確定後(赤)の文字位置",
            "縦書き: 入力中(青)と確定後(赤)の文字位置",
            "確定後: 改行に合わせて本文ぴったり",
        )
    ):
        x0, y0 = panel_origin(i)
        draw.rectangle((x0, y0, x0 + panel_w - 1, y0 + panel_h - 1), outline=(60, 60, 60), width=2)
        draw.text((x0 + 14, y0 + 12), title, fill=(0, 0, 0))

    # Panel 0: click-origin rectangles.
    ox, oy = panel_origin(0)
    click_px = (ox + int(50.0 * scale), oy + panel_h - int(60.0 * scale))
    draw.line((click_px[0] - 8, click_px[1], click_px[0] + 8, click_px[1]), fill=(0, 0, 0), width=2)
    draw.line((click_px[0], click_px[1] - 8, click_px[0], click_px[1] + 8), fill=(0, 0, 0), width=2)
    for rect, color, label in (
        (state["initial_vertical"], (0, 100, 230), "縦書き"),
        (state["initial_horizontal"], (230, 90, 0), "横書き"),
    ):
        x, y, w, h = rect
        shifted = (x, y, w, h)
        _draw_rect_mm(draw, shifted, scale, panel_h, color, width=3, origin=(ox, oy))
        draw.text((ox + int((x + w + 2.0) * scale), oy + panel_h - int((y + h) * scale)), label, fill=color)

    # Panels 1-3: rendered overlays.
    for panel_index, key, title_color in (
        (1, "horizontal", (20, 70, 200)),
        (2, "vertical", (20, 130, 70)),
    ):
        x0, y0 = panel_origin(panel_index)
        before = state[key]["before_entry"]
        after = state[key]["after_entry"]
        before_render = state[key]["before_render"]
        after_render = state[key]["after_render"]
        _draw_rect_mm(draw, before, scale, panel_h, (0, 120, 255), width=3, origin=(x0, y0))
        _draw_rect_mm(draw, after, scale, panel_h, (255, 0, 80), width=3, origin=(x0, y0))
        temp = Image.new("RGBA", (panel_w, panel_h), (255, 255, 255, 0))
        _paste_render(temp, state[key]["before_obj"], before_render, scale, panel_h, (0, 90, 255), 0.55)
        _paste_render(temp, state[key]["after_obj"], after_render, scale, panel_h, (255, 0, 80), 0.55)
        montage.alpha_composite(temp, (x0, y0))
        draw.text((x0 + 16, y0 + 44), "青=入力中 / 赤=Ctrl+Enter確定後 / 重なれば位置ずれなし", fill=title_color)

    # Panel 3: final tight boxes only.
    x0, y0 = panel_origin(3)
    for key, color in (("horizontal", (255, 0, 80)), ("vertical", (255, 0, 80))):
        after = state[key]["after_entry"]
        after_render = state[key]["after_render"]
        _draw_rect_mm(draw, after, scale, panel_h, color, width=3, origin=(x0, y0))
        temp = Image.new("RGBA", (panel_w, panel_h), (255, 255, 255, 0))
        _paste_render(temp, state[key]["after_obj"], after_render, scale, panel_h)
        montage.alpha_composite(temp, (x0, y0))
    draw.text((x0 + 16, y0 + 44), "赤枠が確定後の入力欄。本文と改行分だけに縮む", fill=(160, 0, 60))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "text_request_visual_audit_montage.png"
    montage.convert("RGB").save(path)
    return path


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_text_request_visual_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "TextRequestVisual.bmanga"))
        assert "FINISHED" in result, result
        page = bpy.context.scene.bmanga_work.pages[0]
        page.texts.clear()

        from bmanga_dev.operators import text_edit_runtime, text_op
        from bmanga_dev.utils import text_real_object

        # 初期テキスト枠は 9 文字 × 3 行ぶん (既定 20Q=5mm / 行間1.4 / 字間0)。
        # 書字方向 9 文字 = 45mm、行送り 3 行 = 19mm、+ 内側余白 2.5mm × 2。
        initial_vertical = text_op._default_text_rect_for_metrics(
            "vertical", 50.0, 60.0, em_mm=5.0, line_height=1.4, letter_spacing=0.0
        )
        initial_horizontal = text_op._default_text_rect_for_metrics(
            "horizontal", 50.0, 60.0, em_mm=5.0, line_height=1.4, letter_spacing=0.0
        )
        assert initial_vertical == (38.0, 35.0, 24.0, 50.0), initial_vertical
        assert initial_horizontal == (25.0, 48.0, 50.0, 24.0), initial_horizontal
        # プリセット経由の入口でも縦書きは縦長・横書きは横長になる。
        cx, cy, cw, ch = text_op._default_text_rect_for_click(bpy.context, "vertical", 50.0, 60.0)
        assert ch > cw, (cw, ch)
        cx, cy, cw, ch = text_op._default_text_rect_for_click(bpy.context, "horizontal", 50.0, 60.0)
        assert cw > ch, (cw, ch)

        horizontal = _entry(page, "horizontal_fit", "ABCDEF\nGH", "horizontal", 10.0, 10.0, 100.0, 80.0)
        vertical = _entry(page, "vertical_fit", "日本\n語", "vertical", 10.0, 10.0, 100.0, 80.0)

        state: dict = {
            "initial_vertical": tuple(float(v) for v in initial_vertical),
            "initial_horizontal": tuple(float(v) for v in initial_horizontal),
        }

        for key, entry in (("horizontal", horizontal), ("vertical", vertical)):
            with text_real_object.suspend_auto_sync():
                before_entry = (
                    float(entry.x_mm),
                    float(entry.y_mm),
                    float(entry.width_mm),
                    float(entry.height_mm),
                )
                before_placements = _placements(entry)
                before_render = _render_entry(entry)
                text_edit_runtime.fit_text_rect_to_body(
                    entry,
                    min_width=2.0,
                    min_height=2.0,
                    allow_shrink=True,
                )
                after_entry = (
                    float(entry.x_mm),
                    float(entry.y_mm),
                    float(entry.width_mm),
                    float(entry.height_mm),
                )
                after_placements = _placements(entry)
                after_render = _render_entry(entry)
            _assert_same_placements(key, before_placements, after_placements)
            if key == "horizontal":
                assert abs(before_entry[0] - after_entry[0]) < 1.0e-5
                assert abs(before_entry[1] + before_entry[3] - (after_entry[1] + after_entry[3])) < 1.0e-5
            else:
                assert abs(before_entry[0] + before_entry[2] - (after_entry[0] + after_entry[2])) < 1.0e-5
                assert abs(before_entry[1] + before_entry[3] - (after_entry[1] + after_entry[3])) < 1.0e-5
            assert after_entry[2] < before_entry[2], (key, before_entry, after_entry)
            assert after_entry[3] < before_entry[3], (key, before_entry, after_entry)
            state[key] = {
                "before_entry": before_entry,
                "after_entry": after_entry,
                "before_placements": before_placements,
                "after_placements": after_placements,
                "before_render": before_render,
                "after_render": after_render,
                "before_obj": _entry_snapshot(before_entry),
                "after_obj": _entry_snapshot(after_entry),
            }

        visual_state = state.copy()
        path = _make_visual(visual_state)
        json_state = {
            key: value
            for key, value in state.items()
            if key not in {"horizontal", "vertical"}
        }
        for key in ("horizontal", "vertical"):
            item = state[key]
            json_state[key] = {
                "before_entry": item["before_entry"],
                "after_entry": item["after_entry"],
                "before_placements": item["before_placements"],
                "after_placements": item["after_placements"],
            }
        (OUT_DIR / "state.json").write_text(json.dumps(json_state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"BMANGA_TEXT_REQUEST_VISUAL_AUDIT_OK visual={path}")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
