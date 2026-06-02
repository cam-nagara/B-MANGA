"""Blender UI visual check for the balloon Uni Flash line style.

This script is intentionally kept under _verify. It captures a real Blender UI
screenshot and a full UI draw-record sheet for AI visual inspection.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / ".codex" / "visual" / "uni_flash_ui_visual_check"
WORK_DIR = OUT_DIR / "UniFlashUI.bname"
MODULE_NAME = "bname_dev_uni_flash_ui"


FORBIDDEN_UI_PROPS = {
    "flash_line_count",
    "flash_line_spacing_mm",
    "flash_white_line_width_percent",
    "flash_white_line_peak_width_pct",
    "flash_white_line_valley_width_pct",
    "fill_material_name",
    "fill_blur_amount",
    "fill_gradient_enabled",
    "outer_white_margin_enabled",
    "inner_white_margin_enabled",
}

REQUIRED_LABELS = {
    "向き",
    "全体回転",
    "始点形状",
    "終点形状",
    "線幅",
    "線の間隔",
    "密度補正",
    "最大本数",
    "まとまり",
    "入り抜き",
    "適用先",
    "入り (%)",
    "抜き (%)",
    "入り始点 (%)",
    "抜き始点 (%)",
    "色",
    "不透明度",
    "線色",
    "塗り色",
    "塗り不透明度",
    "終点形状を下地として塗る",
    "白抜き線",
    "白抜き線色",
}


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


def _first_window_screen():
    window = next(iter(bpy.context.window_manager.windows), None)
    screen = getattr(window, "screen", None) if window is not None else None
    return window, screen


def _view3d_override():
    try:
        from bname_dev_uni_flash_ui.ui import sidebar as bname_sidebar

        bname_sidebar.open_bname_sidebar(bpy.context, select_category=True)
    except Exception:  # noqa: BLE001
        pass
    window, screen = _first_window_screen()
    if window is None or screen is None:
        return None
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        space = area.spaces.active
        rv3d = getattr(space, "region_3d", None)
        if region is None or rv3d is None:
            continue
        try:
            space.show_region_ui = True
            space.shading.type = "MATERIAL"
        except Exception:  # noqa: BLE001
            pass
        return {
            "window": window,
            "screen": screen,
            "area": area,
            "region": region,
            "space_data": space,
            "region_data": rv3d,
        }
    return {"window": window, "screen": screen}


def _redraw(iterations: int = 6) -> None:
    override = _view3d_override()
    if override is None:
        return
    with bpy.context.temp_override(**override):
        if bpy.ops.wm.redraw_timer.poll():
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=iterations)


def _screenshot(name: str) -> str:
    path = OUT_DIR / name
    override = _view3d_override()
    if override is not None:
        with bpy.context.temp_override(**override):
            _redraw(8)
            result = bpy.ops.screen.screenshot("EXEC_DEFAULT", filepath=str(path), check_existing=False)
    else:
        result = bpy.ops.screen.screenshot("EXEC_DEFAULT", filepath=str(path), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"screenshot failed: {result}")
    return str(path)


def _call_layer_detail_dialog(context, entry, screenshot_name: str) -> str:
    from bname_dev_uni_flash_ui.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    index = -1
    if stack is not None:
        for i, item in enumerate(stack):
            if str(getattr(item, "kind", "") or "") != "balloon":
                continue
            resolved = layer_stack_utils.resolve_stack_item(context, item)
            if resolved is not None and resolved.get("target") == entry:
                index = i
                break
    if index < 0:
        raise RuntimeError("balloon layer stack item not found")
    override = _view3d_override()
    if override is None:
        result = bpy.ops.bname.layer_stack_detail("INVOKE_DEFAULT", index=index)
    else:
        with bpy.context.temp_override(**override):
            result = bpy.ops.bname.layer_stack_detail("INVOKE_DEFAULT", index=index)
    if "FINISHED" not in result and "RUNNING_MODAL" not in result:
        raise RuntimeError(f"layer detail dialog failed: {result}")
    _redraw(12)
    return _screenshot(screenshot_name)


def _prop_label(owner, attr: str, text: str | None) -> str:
    if text:
        return text
    try:
        prop = owner.bl_rna.properties[attr]
        name = str(getattr(prop, "name", "") or "")
        return name or attr
    except Exception:  # noqa: BLE001
        return attr


class _UILayoutRecorder:
    def __init__(self, records: list[dict], group: str, depth: int = 0):
        self.records = records
        self.group = group
        self.depth = depth
        self.enabled = True
        self.active = True
        self.ui_units_x = 0.0
        self.alignment = "LEFT"

    def _child(self, depth_delta: int = 1):
        return _UILayoutRecorder(self.records, self.group, self.depth + depth_delta)

    def box(self):
        self.records.append({"group": self.group, "depth": self.depth, "kind": "box", "label": "", "prop": ""})
        return self._child()

    def row(self, align: bool = False):  # noqa: ARG002
        return self._child(0)

    def column(self, align: bool = False):  # noqa: ARG002
        return self._child(0)

    def label(self, *, text: str = "", icon: str = "", **_kwargs):
        if text:
            self.records.append(
                {"group": self.group, "depth": self.depth, "kind": "label", "label": text, "prop": "", "icon": icon}
            )

    def prop(self, owner, attr: str, *, text: str | None = None, **_kwargs):
        self.records.append(
            {
                "group": self.group,
                "depth": self.depth,
                "kind": "prop",
                "label": _prop_label(owner, attr, text),
                "prop": str(attr),
                "enabled": bool(self.enabled),
            }
        )
        return None

    def prop_search(self, owner, attr: str, *_args, text: str | None = None, **_kwargs):
        self.records.append(
            {
                "group": self.group,
                "depth": self.depth,
                "kind": "prop_search",
                "label": _prop_label(owner, attr, text),
                "prop": str(attr),
                "enabled": bool(self.enabled),
            }
        )
        return None

    def operator(self, operator: str, *, text: str = "", icon: str = "", **_kwargs):
        self.records.append(
            {
                "group": self.group,
                "depth": self.depth,
                "kind": "operator",
                "label": text or operator,
                "prop": operator,
                "icon": icon,
                "enabled": bool(self.enabled),
            }
        )
        return SimpleNamespace()

    def template_list(self, *args, **_kwargs):
        name = str(args[0]) if args else "template_list"
        self.records.append({"group": self.group, "depth": self.depth, "kind": "list", "label": name, "prop": name})

    def template_curve_mapping(self, *_args, **_kwargs):
        self.records.append(
            {"group": self.group, "depth": self.depth, "kind": "curve", "label": "カーブ", "prop": "curve"}
        )

    def separator(self):
        return None


def _collect_draw(group: str, draw_fn, *args, panel_method: bool = False) -> list[dict]:
    records: list[dict] = []
    layout = _UILayoutRecorder(records, group)
    if panel_method:
        draw_fn(SimpleNamespace(layout=layout), *args)
    else:
        draw_fn(layout, *args)
    return records


def _create_uni_flash_scene(context):
    from bname_dev_uni_flash_ui.core import balloon as balloon_core
    from bname_dev_uni_flash_ui.operators import balloon_op
    from bname_dev_uni_flash_ui.utils import balloon_curve_object
    from bname_dev_uni_flash_ui.utils import layer_stack as layer_stack_utils
    from bname_dev_uni_flash_ui.utils.layer_hierarchy import page_stack_key

    result = bpy.ops.bname.work_new(filepath=str(WORK_DIR))
    assert result == {"FINISHED"}, result
    work = context.scene.bname_work
    page = work.pages[0]
    work.active_page_index = 0
    entry = balloon_op._create_balloon_entry(
        context,
        page,
        shape="ellipse",
        x=35.0,
        y=45.0,
        w=75.0,
        h=55.0,
        parent_kind="page",
        parent_key=page_stack_key(page),
    )
    entry.title = "ウニフラUI目視"
    entry.line_style = "uni_flash"
    balloon_core.apply_balloon_line_style_defaults(entry, force=True)
    entry.bundle_enabled = True
    entry.brush_jitter_enabled = True
    entry.spacing_jitter_enabled = True
    entry.fill_base_shape = True
    page.active_balloon_index = 0
    context.scene.bname_active_layer_kind = "balloon"
    layer_stack_utils.sync_layer_stack_after_data_change(context)
    balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    return work, page, entry


def _collect_records(context, page, entry) -> tuple[list[dict], dict]:
    from bname_dev_uni_flash_ui.core import effect_line as effect_line_core
    from bname_dev_uni_flash_ui.panels import balloon_panel, effect_line_panel, layer_stack_detail_ui
    from bname_dev_uni_flash_ui.operators import layer_detail_op

    records: list[dict] = []

    def _draw_focus_block(layout, params):
        effect_line_panel.draw_effect_params(
            layout,
            params,
            with_generate_button=False,
            fixed_effect_type="focus",
            show_type=False,
        )

    def _draw_uni_flash_block(layout, target_entry):
        effect_line_panel.draw_effect_params(
            layout,
            target_entry,
            with_generate_button=False,
            fixed_effect_type="uni_flash",
            show_type=False,
        )

    focus_params = context.scene.bname_effect_line_params
    focus_params.effect_type = "focus"
    focus_params.start_shape = entry.start_shape
    focus_params.end_shape = entry.end_shape
    focus_params.spacing_mode = entry.spacing_mode
    focus_params.bundle_enabled = entry.bundle_enabled
    focus_params.brush_jitter_enabled = entry.brush_jitter_enabled
    focus_params.spacing_jitter_enabled = entry.spacing_jitter_enabled
    focus_params.fill_base_shape = entry.fill_base_shape

    focus_records = _collect_draw(
        "効果線 / 集中線 設定ブロック",
        _draw_focus_block,
        focus_params,
    )
    uni_block_records = _collect_draw(
        "フキダシ線種 / ウニフラ 設定ブロック",
        _draw_uni_flash_block,
        entry,
    )
    panel_records = _collect_draw(
        "フキダシパネル / ウニフラ",
        balloon_panel.BNAME_PT_balloons.draw,
        context,
        panel_method=True,
    )
    stack_records = _collect_draw(
        "レイヤー詳細 / ウニフラ",
        layer_stack_detail_ui.draw_stack_item_detail,
        context,
        SimpleNamespace(kind="balloon", label="ウニフラ"),
        {"target": entry, "object": None},
    )
    detail_records = _collect_draw("右クリック詳細設定 / ウニフラ", layer_detail_op._draw_balloon_detail, entry, page)

    for group_records in (focus_records, uni_block_records, panel_records, stack_records, detail_records):
        records.extend(group_records)

    focus_props = [item["prop"] for item in focus_records if item.get("kind") == "prop"]
    uni_props = [item["prop"] for item in uni_block_records if item.get("kind") == "prop"]
    all_labels = {str(item.get("label", "")) for item in records}
    all_labels_text = "\n".join(sorted(all_labels))
    forbidden_hits = sorted(
        {
            str(item.get("prop", ""))
            for item in panel_records + stack_records + detail_records
            if str(item.get("prop", "")) in FORBIDDEN_UI_PROPS
        }
    )
    expected_fields = [
        field
        for field in effect_line_core.EFFECT_PARAM_FIELDS
        if field not in {"speed_angle_deg", "speed_line_count"} and not field.startswith("white_outline_")
    ]
    summary = {
        "focus_prop_count": len(focus_props),
        "uni_flash_prop_count": len(uni_props),
        "focus_vs_uni_props_equal": focus_props == uni_props,
        "missing_required_labels": sorted(label for label in REQUIRED_LABELS if label not in all_labels_text),
        "forbidden_ui_props": forbidden_hits,
        "expected_field_count": len(expected_fields),
        "uni_flash_field_count": len(getattr(__import__(f"{MODULE_NAME}.core.balloon", fromlist=["UNI_FLASH_PARAM_FIELDS"]), "UNI_FLASH_PARAM_FIELDS")),
        "defaults": {
            "line_style": str(entry.line_style),
            "start_shape": str(entry.start_shape),
            "end_shape": str(entry.end_shape),
            "in_percent": float(entry.in_percent),
            "out_percent": float(entry.out_percent),
            "in_start_percent": float(entry.in_start_percent),
            "out_start_percent": float(entry.out_start_percent),
            "brush_size_mm": float(entry.brush_size_mm),
            "max_line_count": int(entry.max_line_count),
            "white_underlay_enabled": bool(entry.white_underlay_enabled),
            "white_underlay_width_percent": float(entry.white_underlay_width_percent),
        },
    }
    return records, summary


def _write_records_image(records: list[dict], summary: dict) -> str:
    from PIL import Image, ImageDraw, ImageFont

    def font(size: int):
        candidates = [
            Path("C:/Windows/Fonts/meiryo.ttc"),
            Path("C:/Windows/Fonts/msgothic.ttc"),
            Path("C:/Windows/Fonts/arial.ttf"),
        ]
        for path in candidates:
            if path.exists():
                try:
                    return ImageFont.truetype(str(path), size)
                except Exception:  # noqa: BLE001
                    pass
        return ImageFont.load_default()

    width = 1900
    row_h = 25
    header_h = 160
    group_h = 34
    groups = []
    for item in records:
        group = item["group"]
        if group not in groups:
            groups.append(group)
    height = header_h + group_h * len(groups) + row_h * len(records) + 40
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = font(22)
    body_font = font(13)
    small_font = font(12)
    draw.text((24, 18), "ウニフラ線種 UI実描画 AI目視シート", fill=(0, 0, 0), font=title_font)
    status = (
        f"集中線ブロック一致: {summary['focus_vs_uni_props_equal']} / "
        f"不足ラベル: {len(summary['missing_required_labels'])} / "
        f"余分な旧項目: {len(summary['forbidden_ui_props'])}"
    )
    status_color = (0, 120, 0) if (
        summary["focus_vs_uni_props_equal"]
        and not summary["missing_required_labels"]
        and not summary["forbidden_ui_props"]
    ) else (170, 0, 0)
    draw.text((24, 54), status, fill=status_color, font=body_font)
    defaults = summary["defaults"]
    draw.text(
        (24, 82),
        (
            f"初期値: 始点={defaults['start_shape']} 終点={defaults['end_shape']} "
            f"入り={defaults['in_percent']}% 抜き={defaults['out_percent']}% "
            f"線幅={defaults['brush_size_mm']}mm 最大本数={defaults['max_line_count']} "
            f"白抜き線={defaults['white_underlay_width_percent']}%"
        ),
        fill=(0, 0, 0),
        font=body_font,
    )
    draw.text(
        (24, 112),
        "左から: 種類 / ラベル / 実プロパティ。赤字は無効状態、薄い背景は行の区切り。",
        fill=(70, 70, 70),
        font=small_font,
    )
    y = header_h
    current_group = None
    for index, item in enumerate(records):
        group = item["group"]
        if group != current_group:
            current_group = group
            draw.rectangle((18, y, width - 18, y + group_h), fill=(230, 238, 248), outline=(170, 190, 220))
            draw.text((28, y + 8), group, fill=(0, 40, 90), font=body_font)
            y += group_h
        if index % 2:
            draw.rectangle((18, y, width - 18, y + row_h), fill=(247, 247, 247))
        enabled = bool(item.get("enabled", True))
        color = (0, 0, 0) if enabled else (170, 0, 0)
        indent = int(item.get("depth", 0)) * 18
        draw.text((36 + indent, y + 6), str(item.get("kind", "")), fill=(80, 80, 80), font=small_font)
        draw.text((180 + indent, y + 6), str(item.get("label", ""))[:70], fill=color, font=small_font)
        draw.text((840, y + 6), str(item.get("prop", ""))[:100], fill=(70, 70, 70), font=small_font)
        y += row_h
    path = OUT_DIR / "uni_flash_ui_draw_records.png"
    image.save(path)
    return str(path)


def _run() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    mod = _load_addon()
    try:
        work, page, entry = _create_uni_flash_scene(bpy.context)
        records, summary = _collect_records(bpy.context, page, entry)
        records_image = _write_records_image(records, summary)
        summary["records_image"] = records_image
        summary["records_json"] = str(OUT_DIR / "uni_flash_ui_draw_records.json")
        summary["screenshot_layer_detail"] = _call_layer_detail_dialog(
            bpy.context,
            entry,
            "uni_flash_layer_detail_dialog.png",
        )
        (OUT_DIR / "uni_flash_ui_draw_records.json").write_text(
            json.dumps({"summary": summary, "records": records}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ok = (
            summary["focus_vs_uni_props_equal"]
            and not summary["missing_required_labels"]
            and not summary["forbidden_ui_props"]
        )
        print("BNAME_UNI_FLASH_UI_VISUAL_CHECK", json.dumps(summary, ensure_ascii=False), flush=True)
        os._exit(0 if ok else 2)
    finally:
        try:
            mod.unregister()
        except Exception:  # noqa: BLE001
            pass


def _timer():
    try:
        _run()
    except Exception as exc:  # noqa: BLE001
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "uni_flash_ui_visual_error.txt").write_text(str(exc), encoding="utf-8")
        raise
    return None


bpy.app.timers.register(_timer, first_interval=0.5)
