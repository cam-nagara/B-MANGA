from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_ai_visual"
OUT_PATH = Path(
    os.environ.get(
        "BMANGA_BALLOON_FREE_TRANSFORM_AI_VISUAL_OUT",
        str(ROOT / "_verify" / "balloon_free_transform_ai_visual_check.png"),
    )
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _select_stack_item(context, kind: str, key: str) -> int:
    from bmanga_dev_ai_visual.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    for index, item in enumerate(stack or []):
        if str(getattr(item, "kind", "") or "") == kind and str(getattr(item, "key", "") or "") == key:
            assert layer_stack_utils.select_stack_index(context, index), (kind, key)
            return index
    raise AssertionError(f"stack item not found: {kind} {key}")


def _find_stack_uid(context, uid: str):
    from bmanga_dev_ai_visual.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    for index, item in enumerate(stack or []):
        if layer_stack_utils.stack_item_uid(item) == uid:
            return index, item
    raise AssertionError(f"stack uid not found: {uid}")


def _font(size: int):
    from PIL import ImageFont

    for path in (
        Path("C:/Windows/Fonts/meiryo.ttc"),
        Path("C:/Windows/Fonts/msgothic.ttc"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ):
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:
                pass
    return ImageFont.load_default()


def _create_balloon(context, page, parent_key: str, name: str, x: float, y: float):
    from bmanga_dev_ai_visual.operators import balloon_op
    from bmanga_dev_ai_visual.utils import balloon_curve_object

    entry = balloon_op._create_balloon_entry(
        context,
        page,
        shape="ellipse",
        x=x,
        y=y,
        w=34.0,
        h=22.0,
        parent_kind="page",
        parent_key=parent_key,
    )
    if not str(getattr(entry, "id", "") or ""):
        entry.id = f"visual_balloon_{len(page.balloons):04d}"
    entry.title = name
    entry.line_width_mm = 1.6
    entry.fill_color = (1.0, 1.0, 1.0, 1.0)
    entry.line_color = (0.0, 0.0, 0.0, 1.0)
    entry.fill_opacity = 100.0
    balloon_curve_object.on_balloon_entry_changed(entry)
    return entry


def _render_balloon(entry):
    from PIL import Image
    from bmanga_dev_ai_visual.io import export_balloon

    layer = export_balloon.render_balloon_layer(entry, canvas_height_px=1200, dpi=144)
    if layer is None:
        raise AssertionError("balloon render failed")
    img = layer.image
    bbox = img.getbbox()
    if bbox is None:
        raise AssertionError("balloon render is blank")
    cropped = img.crop(bbox)
    frame = Image.new("RGBA", (260, 210), (255, 255, 255, 255))
    scale = min(220 / max(1, cropped.width), 150 / max(1, cropped.height), 1.0)
    resized = cropped.resize(
        (max(1, int(cropped.width * scale)), max(1, int(cropped.height * scale))),
        Image.Resampling.LANCZOS,
    )
    frame.alpha_composite(resized, ((frame.width - resized.width) // 2, 58))
    return frame


def _draw_card(draw, x, y, w, h, title, body_font, title_font):
    draw.rounded_rectangle((x, y, x + w, y + h), radius=8, fill=(255, 255, 255), outline=(150, 150, 150), width=2)
    draw.text((x + 14, y + 12), title, fill=(0, 0, 0), font=title_font)


def _draw_layer_rows(draw, x, y, title, rows, body_font, title_font):
    _draw_card(draw, x, y, 500, 240, title, body_font, title_font)
    row_y = y + 50
    for label, selected, visible in rows[:5]:
        fill = (210, 232, 255) if selected else (246, 246, 246)
        outline = (60, 130, 210) if selected else (190, 190, 190)
        draw.rectangle((x + 18, row_y, x + 482, row_y + 28), fill=fill, outline=outline)
        icon = "表示" if visible else "非表示"
        draw.text((x + 30, row_y + 5), label[:28], fill=(0, 0, 0), font=body_font)
        draw.text((x + 405, row_y + 5), icon, fill=(90, 90, 90), font=body_font)
        row_y += 31


def _stack_rows(context):
    from bmanga_dev_ai_visual.utils import layer_stack as layer_stack_utils
    from bmanga_dev_ai_visual.utils import layer_stack_visible

    names = {
        "balloon": "フキダシ",
        "text": "テキスト",
        "effect": "効果線",
        "balloon_group": "結合フォルダ",
    }
    rows = []
    for _index, item in layer_stack_visible.visible_layer_stack_entries(context):
        kind = str(getattr(item, "kind", "") or "")
        label = str(getattr(item, "label", "") or getattr(item, "name", "") or names.get(kind, kind))
        rows.append(
            (
                f"{names.get(kind, kind)}  {label}",
                layer_stack_utils.is_item_selected(context, item),
                bool(getattr(item, "visible", True)),
            )
        )
    return rows


def _stack_rows_for_keys(context, wanted_keys: list[str]):
    from bmanga_dev_ai_visual.utils import layer_stack as layer_stack_utils
    from bmanga_dev_ai_visual.utils import layer_stack_visible

    names = {
        "balloon": "フキダシ",
        "text": "テキスト",
        "effect": "効果線",
        "balloon_group": "結合フォルダ",
    }
    wanted = set(wanted_keys)
    rows = []
    for _index, item in layer_stack_visible.visible_layer_stack_entries(context):
        key = str(getattr(item, "key", "") or "")
        if key not in wanted:
            continue
        kind = str(getattr(item, "kind", "") or "")
        label = str(getattr(item, "label", "") or getattr(item, "name", "") or names.get(kind, kind))
        rows.append(
            (
                f"{names.get(kind, kind)}  {label}",
                layer_stack_utils.is_item_selected(context, item),
                bool(getattr(item, "visible", True)),
            )
        )
    return rows


def _invoke_multi_select(context, index: int, *, shift: bool = False, ctrl: bool = False):
    from bmanga_dev_ai_visual.operators import layer_stack_op

    op = SimpleNamespace(index=index, mode="SET")
    op.execute = lambda ctx: layer_stack_op.BMANGA_OT_layer_stack_multi_select.execute(op, ctx)
    return layer_stack_op.BMANGA_OT_layer_stack_multi_select.invoke(
        op,
        context,
        SimpleNamespace(value="PRESS", shift=shift, ctrl=ctrl, oskey=False),
    )


def _visual_summary(context, page):
    from bmanga_dev_ai_visual.io import export_balloon
    from bmanga_dev_ai_visual.operators import layer_link_duplicate_op
    from bmanga_dev_ai_visual.ui import context_menu
    from bmanga_dev_ai_visual.utils import balloon_line_mesh
    from bmanga_dev_ai_visual.utils import layer_links
    from bmanga_dev_ai_visual.utils import layer_stack as layer_stack_utils
    from bmanga_dev_ai_visual.utils import layer_stack_visible
    from bmanga_dev_ai_visual.utils.layer_hierarchy import page_stack_key

    page_key = page_stack_key(page)

    normal = _create_balloon(context, page, page_key, "通常", 15.0, 30.0)
    keep = _create_balloon(context, page, page_key, "線幅維持", 55.0, 30.0)
    thick = _create_balloon(context, page, page_key, "線幅も拡大", 95.0, 30.0)
    rotated = _create_balloon(context, page, page_key, "回転", 135.0, 30.0)

    _select_stack_item(context, "balloon", f"{page_key}:{keep.id}")
    assert bpy.ops.bmanga.balloon_free_transform_scale(
        "EXEC_DEFAULT",
        scale_percent=190.0,
        keep_line_width=True,
    ) == {"FINISHED"}

    _select_stack_item(context, "balloon", f"{page_key}:{thick.id}")
    assert bpy.ops.bmanga.balloon_free_transform_scale(
        "EXEC_DEFAULT",
        scale_percent=190.0,
        keep_line_width=False,
    ) == {"FINISHED"}

    _select_stack_item(context, "balloon", f"{page_key}:{rotated.id}")
    assert bpy.ops.bmanga.balloon_free_transform_rotate("EXEC_DEFAULT", angle_deg=28.0) == {"FINISHED"}

    linked_a = _create_balloon(context, page, page_key, "リンク元", 15.0, 75.0)
    linked_b = _create_balloon(context, page, page_key, "リンク先", 55.0, 75.0)
    uid_a = layer_stack_utils.target_uid("balloon", f"{page_key}:{linked_a.id}")
    uid_b = layer_stack_utils.target_uid("balloon", f"{page_key}:{linked_b.id}")
    group_id, count = layer_links.link_uids(context, [uid_a, uid_b])
    assert group_id and count == 2
    _select_stack_item(context, "balloon", f"{page_key}:{linked_a.id}")
    assert bpy.ops.bmanga.balloon_free_transform_scale(
        "EXEC_DEFAULT",
        scale_percent=160.0,
        keep_line_width=False,
    ) == {"FINISHED"}
    assert abs(float(linked_b.free_transform_line_width_scale) - float(linked_a.free_transform_line_width_scale)) < 1.0e-6
    assert layer_link_duplicate_op.propagate_linked_balloon_center_free(context, page, linked_a) >= 0

    select_a = _create_balloon(context, page, page_key, "通常選択", 95.0, 75.0)
    select_b = _create_balloon(context, page, page_key, "Ctrl追加", 135.0, 75.0)
    select_c = _create_balloon(context, page, page_key, "Shift範囲", 175.0, 75.0)
    idx_a, _ = _find_stack_uid(context, layer_stack_utils.target_uid("balloon", f"{page_key}:{select_a.id}"))
    idx_b, _ = _find_stack_uid(context, layer_stack_utils.target_uid("balloon", f"{page_key}:{select_b.id}"))
    idx_c, _ = _find_stack_uid(context, layer_stack_utils.target_uid("balloon", f"{page_key}:{select_c.id}"))
    assert "FINISHED" in _invoke_multi_select(context, idx_a)
    assert "FINISHED" in _invoke_multi_select(context, idx_b, ctrl=True)
    assert "FINISHED" in _invoke_multi_select(context, idx_c, shift=True)
    rows_multi = _stack_rows_for_keys(
        context,
        [
            f"{page_key}:{select_a.id}",
            f"{page_key}:{select_b.id}",
            f"{page_key}:{select_c.id}",
        ],
    )

    group_a = _create_balloon(context, page, page_key, "結合A", 15.0, 115.0)
    group_b = _create_balloon(context, page, page_key, "結合B", 55.0, 115.0)
    group_a.merge_group_id = "ai_visual_group"
    group_b.merge_group_id = "ai_visual_group"
    layer_stack_utils.sync_layer_stack_after_data_change(context)
    group_key = f"{page_key}:ai_visual_group"
    group_uid = layer_stack_utils.target_uid("balloon_group", group_key)
    group_index, _group_item = _find_stack_uid(context, group_uid)
    rows_group_open = _stack_rows_for_keys(
        context,
        [
            group_key,
            f"{page_key}:{group_a.id}",
            f"{page_key}:{group_b.id}",
        ],
    )
    assert "FINISHED" in bpy.ops.bmanga.layer_stack_toggle_expanded("EXEC_DEFAULT", index=group_index)
    layer_stack_visible.sync_visible_layer_stack(context)
    rows_group_collapsed = _stack_rows_for_keys(
        context,
        [
            group_key,
            f"{page_key}:{group_a.id}",
            f"{page_key}:{group_b.id}",
        ],
    )
    visible_keys = [str(getattr(item, "key", "") or "") for _idx, item in layer_stack_visible.visible_layer_stack_entries(context)]
    assert group_key in visible_keys
    assert f"{page_key}:{group_a.id}" not in visible_keys
    assert f"{page_key}:{group_b.id}" not in visible_keys
    assert "FINISHED" in bpy.ops.bmanga.layer_stack_toggle_visibility("EXEC_DEFAULT", index=group_index)
    assert not bool(group_a.visible) and not bool(group_b.visible)

    _select_stack_item(context, "balloon", f"{page_key}:{normal.id}")
    labels = [str(item.get("label", "")) for item in context_menu.selection_command_items(context)]
    assert "拡大・縮小" in labels and "回転" in labels

    base_w = balloon_line_mesh.scaled_entry_width_mm(normal, "line_width_mm", 0.3)
    keep_w = balloon_line_mesh.scaled_entry_width_mm(keep, "line_width_mm", 0.3)
    thick_w = balloon_line_mesh.scaled_entry_width_mm(thick, "line_width_mm", 0.3)
    assert abs(keep_w - base_w) < 1.0e-6
    assert thick_w > base_w * 1.5

    # Force export module import before drawing, so the visual sheet uses the same renderer path.
    assert export_balloon.render_balloon_layer(normal, canvas_height_px=1200, dpi=144) is not None

    return {
        "balloons": [
            ("通常", normal, f"線幅 {base_w:.1f}mm"),
            ("拡大・線幅維持", keep, f"線幅 {keep_w:.1f}mm"),
            ("拡大・線幅も拡大", thick, f"線幅 {thick_w:.1f}mm"),
            ("回転", rotated, "自由変形で回転"),
            ("リンク元", linked_a, f"線幅倍率 {linked_a.free_transform_line_width_scale:.2f}"),
            ("リンク先", linked_b, f"線幅倍率 {linked_b.free_transform_line_width_scale:.2f}"),
        ],
        "menu_labels": labels,
        "rows_multi": rows_multi,
        "rows_group_open": rows_group_open,
        "rows_group_collapsed": rows_group_collapsed,
    }


def _write_image(summary: dict):
    from PIL import Image, ImageDraw

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1680, 1260), (238, 240, 244))
    draw = ImageDraw.Draw(image)
    title_font = _font(28)
    subtitle_font = _font(18)
    body_font = _font(15)
    small_font = _font(13)
    draw.text((32, 28), "フキダシ自由変形・リンク・レイヤー一覧 AI目視チェック", fill=(0, 0, 0), font=title_font)
    draw.text(
        (34, 68),
        "実機で作成したフキダシをB-MANGAの書き出し処理で描画。右クリック項目・線幅維持・リンク共有・一覧操作を1枚で確認。",
        fill=(60, 60, 60),
        font=subtitle_font,
    )

    x = 34
    y = 112
    for idx, (title, entry, caption) in enumerate(summary["balloons"]):
        card_x = x + (idx % 3) * 535
        card_y = y + (idx // 3) * 295
        _draw_card(draw, card_x, card_y, 500, 265, title, body_font, subtitle_font)
        balloon_img = _render_balloon(entry).convert("RGB")
        image.paste(balloon_img, (card_x + 120, card_y + 46))
        draw.text((card_x + 18, card_y + 226), caption, fill=(30, 30, 30), font=body_font)

    menu_x = 34
    menu_y = 720
    _draw_card(draw, menu_x, menu_y, 500, 210, "右クリックメニュー", body_font, subtitle_font)
    wanted = ["自由変形をリセット", "拡大・縮小", "回転"]
    for i, label in enumerate(wanted):
        ok = label in summary["menu_labels"]
        fill = (0, 120, 60) if ok else (170, 0, 0)
        draw.text((menu_x + 28, menu_y + 58 + i * 34), f"{'OK' if ok else 'NG'}  {label}", fill=fill, font=body_font)
    draw.text((menu_x + 28, menu_y + 168), "拡大・縮小は設定ダイアログで「線幅を維持」を持つ", fill=(40, 40, 40), font=small_font)

    _draw_layer_rows(draw, 570, 720, "レイヤー一覧 Ctrl / Shift 選択", summary["rows_multi"], body_font, subtitle_font)
    _draw_layer_rows(draw, 1105, 720, "結合フォルダ 開いた状態", summary["rows_group_open"], body_font, subtitle_font)
    _draw_layer_rows(draw, 570, 990, "結合フォルダ 閉じた状態", summary["rows_group_collapsed"], body_font, subtitle_font)
    _draw_card(draw, 1105, 990, 500, 180, "AI目視判定", body_font, subtitle_font)
    checks = [
        "拡大しても線幅維持オンは通常線幅と一致",
        "線幅維持オフでは拡大に合わせて線が太い",
        "リンク元とリンク先の線幅倍率が一致",
        "結合フォルダは閉じると中身が一覧から消える",
    ]
    for i, label in enumerate(checks):
        draw.text((1128, 1044 + i * 31), f"OK  {label}", fill=(0, 120, 60), font=body_font)

    image.save(OUT_PATH)
    return OUT_PATH


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_ai_visual_free_transform_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "AIVisual.bmanga"))
        assert result == {"FINISHED"}, result
        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]
        work.active_page_index = 0
        summary = _visual_summary(context, page)
        out = _write_image(summary)
        print(f"BMANGA_BALLOON_FREE_TRANSFORM_AI_VISUAL_OK {out}")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
