"""JSON スキーマの定義・バージョン・シリアライザ.

work.json / pages.json / page.json / cNN.json の構造を 1 箇所に
集約する。将来のフォーマット変更に備えて ``schemaVersion`` フィールドを
各 JSON のトップレベルに付与する。

PropertyGroup ↔ dict の変換は to_dict / from_dict で行い、dict は
utils.json_io で書き出す。
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from typing import Any

from ..core import balloon as balloon_core
from ..utils import (
    balloon_shapes,
    color_space,
    coma_blur_curve,
    free_transform,
    percentage,
    view_settings,
)

# ファイルフォーマットのバージョン (破壊的変更があったら繰り上げる)
WORK_SCHEMA_VERSION = 5
PAGES_SCHEMA_VERSION = 1
PAGE_SCHEMA_VERSION = 2
COMA_SCHEMA_VERSION = 2

# ---------- 共通変換 ----------


@contextmanager
def _suspend_load_property_side_effects():
    """JSON 読み込み中の即時表示再生成を抑止する."""
    patches: list[tuple[object, str, object]] = []
    with ExitStack() as stack:
        try:
            from ..utils import balloon_curve_object

            stack.enter_context(balloon_curve_object.defer_auto_sync())
        except Exception:  # noqa: BLE001
            pass
        try:
            from ..utils import text_real_object

            stack.enter_context(text_real_object.suspend_auto_sync())
        except Exception:  # noqa: BLE001
            pass

        def _patch(module, name: str) -> None:
            try:
                original = getattr(module, name)
            except Exception:  # noqa: BLE001
                return
            patches.append((module, name, original))
            try:
                setattr(module, name, lambda *args, **kwargs: None)
            except Exception:  # noqa: BLE001
                pass

        try:
            from ..utils import coma_border_object, coma_plane

            _patch(coma_plane, "on_coma_geometry_changed")
            _patch(coma_plane, "on_coma_background_color_changed")
            _patch(coma_plane, "on_coma_paper_visible_changed")
            _patch(coma_border_object, "on_coma_border_changed")
        except Exception:  # noqa: BLE001
            pass
        try:
            from ..utils import layer_stack

            _patch(layer_stack, "sync_layer_stack_after_data_change")
            _patch(layer_stack, "tag_view3d_redraw")
        except Exception:  # noqa: BLE001
            pass
        try:
            yield
        finally:
            for module, name, original in reversed(patches):
                try:
                    setattr(module, name, original)
                except Exception:  # noqa: BLE001
                    pass


def _normalize_generated_page_title(title: object, page_id: object) -> str:
    text = str(title or "").strip()
    pid = str(page_id or "").strip()
    if not text:
        return ""
    if pid and text == pid:
        return ""
    return text


def _normalize_generated_coma_title(
    title: object,
    entry_id: object,
    coma_id: object,
) -> str:
    text = str(title or "").strip()
    ids = {str(entry_id or "").strip(), str(coma_id or "").strip()}
    ids.discard("")
    if not text:
        return ""
    if text in ids:
        return ""
    if text.startswith("基本枠"):
        return ""
    for suffix in ("(複製)", "（複製）", "(分割)", "（分割）"):
        if text.endswith(suffix):
            base = text[: -len(suffix)].strip()
            if not base or base in ids:
                return ""
    parts = text.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].count("-") == 1:
        left, right = parts[1].split("-", 1)
        if left.isdigit() and right.isdigit() and (not parts[0].strip() or parts[0].strip() in ids):
            return ""
    return text


def color_to_hex(rgba: tuple[float, float, float, float]) -> str:
    """(r,g,b,a) 浮動小数 → "#RRGGBB" (alpha は別管理)."""
    r, g, b = rgba[0], rgba[1], rgba[2]
    return "#{:02X}{:02X}{:02X}".format(
        max(0, min(255, round(r * 255))),
        max(0, min(255, round(g * 255))),
        max(0, min(255, round(b * 255))),
    )


def hex_to_rgba(code: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    """"#RRGGBB" または "#RRGGBBAA" → (r,g,b,a) 浮動小数."""
    code = code.strip()
    if code.startswith("#"):
        code = code[1:]
    if len(code) == 6:
        r = int(code[0:2], 16) / 255.0
        g = int(code[2:4], 16) / 255.0
        b = int(code[4:6], 16) / 255.0
        return (r, g, b, alpha)
    if len(code) == 8:
        r = int(code[0:2], 16) / 255.0
        g = int(code[2:4], 16) / 255.0
        b = int(code[4:6], 16) / 255.0
        a = int(code[6:8], 16) / 255.0
        return (r, g, b, a)
    raise ValueError(f"invalid color hex: {code}")


def _data_schema_version(data: dict[str, Any], default: int = 1) -> int:
    try:
        return int(data.get("schemaVersion", default) or default)
    except Exception:  # noqa: BLE001
        return default


def _opacity_unit_is_percent(data: dict[str, Any]) -> bool:
    return str(data.get("opacityUnit", "") or "").lower() == "percent"


def _opacity_to_data(value: object, default: float = 100.0) -> float:
    return round(percentage.clamp_percent(value, default), 4)


def _opacity_from_data(
    data: dict[str, Any],
    key: str,
    default: float = 100.0,
    *,
    percent_schema: bool = False,
) -> float:
    if key not in data:
        return percentage.clamp_percent(default)
    if percent_schema or _opacity_unit_is_percent(data):
        return percentage.clamp_percent(data.get(key), default)
    return percentage.legacy_factor_to_percent(data.get(key), default)


# ---------- PaperSettings ----------


def paper_to_dict(paper) -> dict[str, Any]:
    return {
        "canvasWidthMm": round(paper.canvas_width_mm, 3),
        "canvasHeightMm": round(paper.canvas_height_mm, 3),
        "dpi": paper.dpi,
        "unit": paper.unit,
        "finishWidthMm": round(paper.finish_width_mm, 3),
        "finishHeightMm": round(paper.finish_height_mm, 3),
        "bleedMm": round(paper.bleed_mm, 3),
        "innerFrameWidthMm": round(paper.inner_frame_width_mm, 3),
        "innerFrameHeightMm": round(paper.inner_frame_height_mm, 3),
        "innerFrameOffsetXMm": round(paper.inner_frame_offset_x_mm, 3),
        "innerFrameOffsetYMm": round(paper.inner_frame_offset_y_mm, 3),
        "comaBorderWidthMm": round(float(getattr(paper, "coma_border_width_mm", 0.5)), 3),
        "safeTopMm": round(paper.safe_top_mm, 3),
        "safeBottomMm": round(paper.safe_bottom_mm, 3),
        "safeGutterMm": round(paper.safe_gutter_mm, 3),
        "safeForeEdgeMm": round(paper.safe_fore_edge_mm, 3),
        "colorMode": paper.color_mode,
        "defaultLineCount": round(paper.default_line_count, 2),
        "paperColor": color_to_hex(paper.paper_color),
        "paperColorAlpha": round(paper.paper_color[3], 3),
        "showGuides": bool(getattr(paper, "show_guides", True)),
        "showCanvasFrame": bool(getattr(paper, "show_canvas_frame", True)),
        "showBleedFrame": bool(getattr(paper, "show_bleed_frame", True)),
        "showFinishFrame": bool(getattr(paper, "show_finish_frame", True)),
        "showInnerFrame": bool(getattr(paper, "show_inner_frame", True)),
        "showSafeLine": bool(getattr(paper, "show_safe_line", True)),
        "showTrimMarks": bool(getattr(paper, "show_trim_marks", True)),
        "colorProfile": paper.color_profile,
        "startSide": paper.start_side,
        "readDirection": paper.read_direction,
        "presetName": paper.preset_name,
    }


def paper_from_dict(paper, data: dict[str, Any]) -> None:
    data = data or {}
    paper.canvas_width_mm = float(data.get("canvasWidthMm", 257.00))
    paper.canvas_height_mm = float(data.get("canvasHeightMm", 364.00))
    paper.dpi = int(data.get("dpi", 600))
    paper.unit = data.get("unit", "mm")
    paper.finish_width_mm = float(data.get("finishWidthMm", 221.81))
    paper.finish_height_mm = float(data.get("finishHeightMm", 328.78))
    paper.bleed_mm = float(data.get("bleedMm", 7.00))
    paper.inner_frame_width_mm = float(data.get("innerFrameWidthMm", 180.00))
    paper.inner_frame_height_mm = float(data.get("innerFrameHeightMm", 270.00))
    paper.inner_frame_offset_x_mm = float(data.get("innerFrameOffsetXMm", 0.0))
    paper.inner_frame_offset_y_mm = float(data.get("innerFrameOffsetYMm", 0.0))
    paper.coma_border_width_mm = float(data.get("comaBorderWidthMm", 0.5))
    paper.safe_top_mm = float(data.get("safeTopMm", 17.49))
    paper.safe_bottom_mm = float(data.get("safeBottomMm", 17.49))
    paper.safe_gutter_mm = float(data.get("safeGutterMm", 20.90))
    paper.safe_fore_edge_mm = float(data.get("safeForeEdgeMm", 17.23))
    paper.color_mode = data.get("colorMode", "monochrome")
    paper.default_line_count = float(data.get("defaultLineCount", 60.0))
    hex_code = data.get("paperColor", "#FFFFFF")
    alpha = float(data.get("paperColorAlpha", 1.0))
    paper.paper_color = hex_to_rgba(hex_code, alpha)
    paper.show_guides = bool(data.get("showGuides", True))
    paper.show_canvas_frame = bool(data.get("showCanvasFrame", True))
    paper.show_bleed_frame = bool(data.get("showBleedFrame", True))
    paper.show_finish_frame = bool(data.get("showFinishFrame", True))
    paper.show_inner_frame = bool(data.get("showInnerFrame", True))
    paper.show_safe_line = bool(data.get("showSafeLine", True))
    paper.show_trim_marks = bool(data.get("showTrimMarks", True))
    paper.color_profile = data.get("colorProfile", "sRGB IEC61966-2.1")
    paper.start_side = data.get("startSide", "left")
    paper.read_direction = data.get("readDirection", "left")
    paper.preset_name = data.get("presetName", "集英社マンガ誌汎用")


# ---------- WorkInfo / DisplayItem / Nombre ----------


def display_item_to_dict(item) -> dict[str, Any]:
    return {
        "enabled": bool(item.enabled),
        "position": item.position,
        "fontSizeQ": round(item.font_size_q, 2),
        "fontSizePt": round(float(getattr(item, "font_size_pt", 0.0) or 0.0), 2),
        "fontSizeUnit": str(getattr(item, "font_size_unit", "q") or "q"),
        "color": color_to_hex(item.color),
    }


_DISPLAY_POSITION_MIGRATE = {
    # middle 段は廃止 (仕上がり枠外への配置でアンカーが不自然なため)
    "middle-left": "bottom-left",
    "middle-center": "bottom-center",
    "middle-right": "bottom-right",
}


def display_item_from_dict(item, data: dict[str, Any]) -> None:
    data = data or {}
    item.enabled = bool(data.get("enabled", False))
    pos = data.get("position", "bottom-left")
    item.position = _DISPLAY_POSITION_MIGRATE.get(pos, pos)
    unit = str(data.get("fontSizeUnit", "q") or "q")
    # フォントサイズ: Q 数優先 (新)、旧 fontSizePt があれば pt → Q に変換
    if unit == "pt" and "fontSizePt" in data and hasattr(item, "font_size_pt"):
        item.font_size_pt = float(data["fontSizePt"])
    elif "fontSizeQ" in data:
        item.font_size_q = float(data["fontSizeQ"])
        if hasattr(item, "font_size_pt"):
            from ..utils.geom import q_to_pt
            item.font_size_pt = float(q_to_pt(float(item.font_size_q)))
    elif "fontSizePt" in data:
        from ..utils.geom import pt_to_q
        if hasattr(item, "font_size_pt"):
            item.font_size_pt = float(data["fontSizePt"])
        else:
            item.font_size_q = float(pt_to_q(float(data["fontSizePt"])))
    else:
        item.font_size_q = 20.0
        if hasattr(item, "font_size_pt"):
            from ..utils.geom import q_to_pt
            item.font_size_pt = float(q_to_pt(float(item.font_size_q)))
    if hasattr(item, "font_size_unit"):
        item.font_size_unit = unit
    item.color = hex_to_rgba(data.get("color", "#000000"))


def work_info_to_dict(info) -> dict[str, Any]:
    return {
        "workName": info.work_name,
        "episodeNumber": int(info.episode_number),
        "subtitle": info.subtitle,
        "author": info.author,
        "displayOnCanvas": {
            "workName": display_item_to_dict(info.display_work_name),
            "episode": display_item_to_dict(info.display_episode),
            "subtitle": display_item_to_dict(info.display_subtitle),
            "author": display_item_to_dict(info.display_author),
            "pageNumber": display_item_to_dict(info.display_page_number),
        },
        "pageNumberStart": int(info.page_number_start),
        "pageNumberEnd": int(getattr(info, "page_number_end", info.page_number_start)),
    }


def work_info_from_dict(info, data: dict[str, Any]) -> None:
    data = data or {}
    info.work_name = data.get("workName", "")
    info.episode_number = int(data.get("episodeNumber", 1))
    info.subtitle = data.get("subtitle", "")
    info.author = data.get("author", "")
    disp = data.get("displayOnCanvas", {})
    display_item_from_dict(info.display_work_name, disp.get("workName", {}))
    display_item_from_dict(info.display_episode, disp.get("episode", {}))
    display_item_from_dict(info.display_subtitle, disp.get("subtitle", {}))
    display_item_from_dict(info.display_author, disp.get("author", {}))
    display_item_from_dict(info.display_page_number, disp.get("pageNumber", {}))
    start = int(data.get("pageNumberStart", 1))
    info.page_number_start = start
    if hasattr(info, "page_number_end"):
        info.page_number_end = int(data.get("pageNumberEnd", start))


def nombre_to_dict(n) -> dict[str, Any]:
    return {
        "enabled": bool(n.enabled),
        "format": n.format,
        "font": n.font,
        "fontSizePt": round(n.font_size_pt, 2),
        "position": n.position,
        "gapVerticalMm": round(n.gap_vertical_mm, 3),
        "gapHorizontalMm": round(n.gap_horizontal_mm, 3),
        "color": color_to_hex(n.color),
        "border": {
            "enabled": bool(n.border_enabled),
            "widthMm": round(n.border_width_mm, 3),
            "color": color_to_hex(n.border_color),
        },
        "startNumber": int(n.start_number),
        "hiddenNombre": bool(n.hidden_nombre),
    }


def nombre_from_dict(n, data: dict[str, Any]) -> None:
    data = data or {}
    n.enabled = bool(data.get("enabled", True))
    n.format = data.get("format", "{page}")
    n.font = data.get("font", "I-OTFアンチックStd B")
    n.font_size_pt = float(data.get("fontSizePt", 9.0))
    n.position = data.get("position", "bottom-center")
    n.gap_vertical_mm = float(data.get("gapVerticalMm", 5.0))
    n.gap_horizontal_mm = float(data.get("gapHorizontalMm", 0.0))
    n.color = hex_to_rgba(data.get("color", "#000000"))
    border = data.get("border", {})
    n.border_enabled = bool(border.get("enabled", False))
    n.border_width_mm = float(border.get("widthMm", 0.3))
    n.border_color = hex_to_rgba(border.get("color", "#FFFFFF"))
    n.start_number = int(data.get("startNumber", 1))
    n.hidden_nombre = bool(data.get("hiddenNombre", False))


# ---------- SafeAreaOverlay ----------


def safe_area_to_dict(sa) -> dict[str, Any]:
    raw_color = tuple(float(c) for c in sa.color[:3])
    color = color_space.linear_to_srgb_rgb(raw_color)
    if all(abs(c - 0.7) < 1e-4 for c in raw_color):
        # 旧実装は COLOR プロパティに 0.7 を直接入れていたため、
        # UI上では約 0.854 に見える。未変更の旧既定は現行既定として保存する。
        color_hex = "#B3B3B3"
    elif all(abs(c - 0.7) < 1e-4 for c in color):
        color_hex = "#B3B3B3"
    else:
        color_hex = color_to_hex(color)
    return {
        "enabled": bool(sa.enabled),
        "color": color_hex,
        "opacity": _opacity_to_data(getattr(sa, "opacity", 30.0), 30.0),
        "opacityUnit": "percent",
    }


def safe_area_from_dict(sa, data: dict[str, Any]) -> None:
    data = data or {}
    sa.enabled = bool(data.get("enabled", True))
    # color は size=3 の RGB のみ (旧データの alpha は無視)。
    # 未保存時の既定値は明度 0.7 のグレーに揃える。
    if "color" in data:
        color_code = str(data["color"]).strip().upper()
        # 旧版の既定値は #808080 だった。保存済み作品の「旧既定」が
        # 新規既定に見えてしまうため、読み込み時に現行既定へ移行する。
        if color_code in {
            "#808080", "808080",
            "#7F7F7F", "7F7F7F",
            "#B2B2B2", "B2B2B2",
            "#B3B3B3", "B3B3B3",
            "#D9D9D9", "D9D9D9",
            "#DADADA", "DADADA",
        }:
            sa.color = color_space.srgb_to_linear_rgb((0.7, 0.7, 0.7))
        else:
            rgba = hex_to_rgba(color_code)
            sa.color = color_space.srgb_to_linear_rgb(rgba[:3])
    else:
        sa.color = color_space.srgb_to_linear_rgb((0.7, 0.7, 0.7))
    if hasattr(sa, "opacity"):
        sa.opacity = _opacity_from_data(data, "opacity", 30.0)
    # 旧 blendMode フィールドが残っていても無視 (互換読込)


# ---------- ComaGap ----------


def coma_gap_to_dict(pg) -> dict[str, Any]:
    return {
        "verticalMm": round(pg.vertical_mm, 3),
        "horizontalMm": round(pg.horizontal_mm, 3),
    }


def coma_gap_from_dict(pg, data: dict[str, Any]) -> None:
    data = data or {}
    pg.vertical_mm = float(data.get("verticalMm", 7.3))
    pg.horizontal_mm = float(data.get("horizontalMm", 2.1))


# ---------- RasterLayer ----------


def _scene_from_work(work):
    scene = getattr(work, "id_data", None)
    return scene if scene is not None and hasattr(scene, "bname_raster_layers") else None


def raster_layer_to_dict(entry) -> dict[str, Any]:
    rgb = color_space.linear_to_srgb_rgb(tuple(float(c) for c in entry.line_color[:3]))
    return {
        "id": entry.id,
        "title": entry.title,
        "image_name": entry.image_name,
        "filepath_rel": entry.filepath_rel,
        "dpi": int(entry.dpi),
        "bit_depth": entry.bit_depth,
        "line_color": color_to_hex((*rgb, 1.0)),
        "line_color_alpha": round(float(entry.line_color[3]), 3),
        "opacity": _opacity_to_data(entry.opacity),
        "opacityUnit": "percent",
        "visible": bool(entry.visible),
        "locked": bool(entry.locked),
        "scope": entry.scope,
        "parent_kind": entry.parent_kind,
        "parent_key": entry.parent_key,
        "folderKey": getattr(entry, "folder_key", ""),
    }


def raster_layer_from_dict(entry, data: dict[str, Any], *, opacity_percent: bool = False) -> None:
    data = data or {}
    raster_id = str(data.get("id", "") or "")
    entry.id = raster_id
    entry.title = str(data.get("title", "") or "")
    entry.image_name = str(data.get("image_name", "") or f"raster_{raster_id}")
    entry.filepath_rel = str(data.get("filepath_rel", "") or f"raster/{raster_id}.png")
    entry.dpi = int(data.get("dpi", 300))
    entry.bit_depth = data.get("bit_depth", "gray8")
    alpha = float(data.get("line_color_alpha", 1.0))
    rgba = hex_to_rgba(str(data.get("line_color", "#000000")), alpha)
    entry.line_color = (*color_space.srgb_to_linear_rgb(rgba[:3]), rgba[3])
    entry.opacity = _opacity_from_data(data, "opacity", 100.0, percent_schema=opacity_percent)
    entry.visible = bool(data.get("visible", True))
    entry.locked = bool(data.get("locked", False))
    entry.scope = data.get("scope", "page")
    entry.parent_kind = data.get("parent_kind", "page")
    entry.parent_key = str(data.get("parent_key", "") or "")
    if hasattr(entry, "folder_key"):
        entry.folder_key = str(data.get("folderKey", data.get("folder_key", "")) or "")


# ---------- ImageLayer ----------


def image_layer_to_dict(entry) -> dict[str, Any]:
    tint = color_space.linear_to_srgb_rgb(tuple(float(c) for c in entry.tint_color[:3]))
    return {
        "id": entry.id,
        "title": entry.title,
        "filepath": entry.filepath,
        "xMm": round(float(entry.x_mm), 3),
        "yMm": round(float(entry.y_mm), 3),
        "widthMm": round(float(entry.width_mm), 3),
        "heightMm": round(float(entry.height_mm), 3),
        "rotationDeg": round(float(entry.rotation_deg), 3),
        "flipX": bool(entry.flip_x),
        "flipY": bool(entry.flip_y),
        "visible": bool(entry.visible),
        "locked": bool(entry.locked),
        "opacity": _opacity_to_data(entry.opacity),
        "opacityUnit": "percent",
        "blendMode": entry.blend_mode,
        "brightness": round(float(entry.brightness), 4),
        "contrast": round(float(entry.contrast), 4),
        "binarizeEnabled": bool(entry.binarize_enabled),
        "binarizeThreshold": round(float(entry.binarize_threshold), 4),
        "tintColor": color_to_hex((*tint, 1.0)),
        "tintColorAlpha": round(float(entry.tint_color[3]), 4),
        "parentKind": getattr(entry, "parent_kind", "none"),
        "parentKey": getattr(entry, "parent_key", ""),
        "folderKey": getattr(entry, "folder_key", ""),
    }


def image_layer_from_dict(entry, data: dict[str, Any], *, opacity_percent: bool = False) -> None:
    data = data or {}
    entry.id = str(data.get("id", "") or "")
    entry.title = str(data.get("title", "") or "")
    entry.filepath = str(data.get("filepath", "") or "")
    entry.x_mm = float(data.get("xMm", data.get("x_mm", 0.0)))
    entry.y_mm = float(data.get("yMm", data.get("y_mm", 0.0)))
    entry.width_mm = float(data.get("widthMm", data.get("width_mm", 100.0)))
    entry.height_mm = float(data.get("heightMm", data.get("height_mm", 100.0)))
    entry.rotation_deg = float(data.get("rotationDeg", data.get("rotation_deg", 0.0)))
    entry.flip_x = bool(data.get("flipX", data.get("flip_x", False)))
    entry.flip_y = bool(data.get("flipY", data.get("flip_y", False)))
    entry.visible = bool(data.get("visible", True))
    entry.locked = bool(data.get("locked", False))
    entry.opacity = _opacity_from_data(data, "opacity", 100.0, percent_schema=opacity_percent)
    entry.blend_mode = str(data.get("blendMode", data.get("blend_mode", "normal")) or "normal")
    entry.brightness = float(data.get("brightness", 0.0))
    entry.contrast = float(data.get("contrast", 0.0))
    entry.binarize_enabled = bool(data.get("binarizeEnabled", data.get("binarize_enabled", False)))
    entry.binarize_threshold = float(data.get("binarizeThreshold", data.get("binarize_threshold", 0.5)))
    alpha = float(data.get("tintColorAlpha", data.get("tint_color_alpha", 1.0)))
    tint = hex_to_rgba(str(data.get("tintColor", data.get("tint_color", "#FFFFFF"))), alpha)
    entry.tint_color = (*color_space.srgb_to_linear_rgb(tint[:3]), tint[3])
    entry.parent_kind = str(data.get("parentKind", data.get("parent_kind", "none")) or "none")
    entry.parent_key = str(data.get("parentKey", data.get("parent_key", "")) or "")
    if hasattr(entry, "folder_key"):
        entry.folder_key = str(data.get("folderKey", data.get("folder_key", "")) or "")


# ---------- LayerFolder ----------


def layer_folder_to_dict(entry) -> dict[str, Any]:
    return {
        "id": str(getattr(entry, "id", "") or ""),
        "title": str(getattr(entry, "title", "") or ""),
        "parentKey": str(getattr(entry, "parent_key", "") or ""),
        "expanded": bool(getattr(entry, "expanded", True)),
    }


def layer_folder_from_dict(entry, data: dict[str, Any]) -> None:
    data = data or {}
    entry.id = str(data.get("id", "") or "")
    entry.title = str(data.get("title", "") or "フォルダ")
    entry.parent_key = str(data.get("parentKey", data.get("parent_key", "")) or "")
    entry.expanded = bool(data.get("expanded", True))


# ---------- WorkData (root) ----------


def _page_preview_scale_percentage_from_data(value: object) -> float:
    try:
        percentage = float(value or 12.5)
    except (TypeError, ValueError):
        percentage = 12.5
    return max(1.0, min(100.0, percentage))


def _page_preview_resolution_percentage_default() -> float:
    try:
        return view_settings.default_page_preview_resolution_percentage()
    except Exception:  # noqa: BLE001
        return view_settings.DEFAULT_PAGE_PREVIEW_RESOLUTION_PERCENTAGE


def _page_preview_resolution_percentage_from_data(value: object, default: float | None = None) -> float:
    fallback = (
        float(default)
        if default is not None
        else _page_preview_resolution_percentage_default()
    )
    try:
        resolution = float(value if value is not None else fallback)
    except (TypeError, ValueError):
        resolution = fallback
    return max(5.0, min(200.0, resolution))


def _view_settings_to_dict(work) -> dict[str, Any]:
    default_resolution = _page_preview_resolution_percentage_default()
    return {
        "overlayEnabled": bool(getattr(work, "view_overlay_enabled", True)),
        "overviewCols": int(getattr(work, "view_overview_cols", 4) or 4),
        "overviewGapMm": round(float(getattr(work, "view_overview_gap_mm", 30.0)), 3),
        "pagePreviewEnabled": bool(getattr(work, "view_page_preview_enabled", True)),
        "pagePreviewPageRadius": int(getattr(work, "view_page_preview_page_radius", 3)),
        "pagePreviewResolutionPercentage": round(
            _page_preview_resolution_percentage_from_data(
                getattr(
                    work,
                    "view_page_preview_resolution_percentage",
                    default_resolution,
                ),
                default_resolution,
            ),
            3,
        ),
        "pageBrowserPosition": str(getattr(work, "view_page_browser_position", "LEFT") or "LEFT"),
        "pageBrowserSize": round(float(getattr(work, "view_page_browser_size", 0.28) or 0.28), 4),
        "pageBrowserFit": bool(getattr(work, "view_page_browser_fit", True)),
    }


def _view_settings_from_dict(work, data: dict[str, Any]) -> None:
    settings = data.get("viewSettings", {}) or {}
    if hasattr(work, "view_overlay_enabled"):
        work.view_overlay_enabled = bool(settings.get("overlayEnabled", True))
    if hasattr(work, "view_overview_cols"):
        try:
            cols = int(settings.get("overviewCols", 4) or 4)
        except (TypeError, ValueError):
            cols = 4
        work.view_overview_cols = max(2, cols)
    if hasattr(work, "view_overview_gap_mm"):
        try:
            gap = float(settings["overviewGapMm"]) if "overviewGapMm" in settings else 30.0
        except (TypeError, ValueError):
            gap = 30.0
        work.view_overview_gap_mm = max(0.0, gap)
    if hasattr(work, "view_page_preview_enabled"):
        work.view_page_preview_enabled = bool(settings.get("pagePreviewEnabled", True))
    if hasattr(work, "view_page_preview_page_radius"):
        try:
            radius = (
                int(settings["pagePreviewPageRadius"])
                if "pagePreviewPageRadius" in settings
                else 3
            )
        except (TypeError, ValueError):
            radius = 3
        work.view_page_preview_page_radius = max(0, radius)
    if hasattr(work, "view_page_preview_resolution_percentage"):
        work.view_page_preview_resolution_percentage = (
            _page_preview_resolution_percentage_from_data(
                settings.get("pagePreviewResolutionPercentage")
                if "pagePreviewResolutionPercentage" in settings
                else None
            )
        )
    if hasattr(work, "view_page_browser_position"):
        position = str(settings.get("pageBrowserPosition", "LEFT") or "LEFT").upper()
        work.view_page_browser_position = (
            position if position in {"LEFT", "RIGHT", "TOP", "BOTTOM"} else "LEFT"
        )
    if hasattr(work, "view_page_browser_size"):
        try:
            size = float(settings.get("pageBrowserSize", 0.28) or 0.28)
        except (TypeError, ValueError):
            size = 0.28
        work.view_page_browser_size = max(0.12, min(0.5, size))
    if hasattr(work, "view_page_browser_fit"):
        work.view_page_browser_fit = bool(settings.get("pageBrowserFit", True))


def work_to_dict(work) -> dict[str, Any]:
    """BNameWorkData → work.json dict."""
    scene = _scene_from_work(work)
    raster_layers = getattr(scene, "bname_raster_layers", None) if scene is not None else None
    image_layers = getattr(scene, "bname_image_layers", None) if scene is not None else None
    return {
        "schemaVersion": WORK_SCHEMA_VERSION,
        "workInfo": work_info_to_dict(work.work_info),
        "nombre": nombre_to_dict(work.nombre),
        "paper": paper_to_dict(work.paper),
        "comaGap": coma_gap_to_dict(work.coma_gap),
        "comaBlendTemplatePath": str(getattr(work, "coma_blend_template_path", "") or ""),
        "pagePreviewScalePercentage": round(
            _page_preview_scale_percentage_from_data(
                getattr(work, "page_preview_scale_percentage", 12.5)
            ),
            3,
        ),
        "autoRenderComaThumbOnReturn": bool(
            getattr(work, "auto_render_coma_thumb_on_return", True)
        ),
        "viewSettings": _view_settings_to_dict(work),
        "safeAreaOverlay": safe_area_to_dict(work.safe_area_overlay),
        "raster_layers": [
            raster_layer_to_dict(entry)
            for entry in (raster_layers or [])
        ],
        "image_layers": [
            image_layer_to_dict(entry)
            for entry in (image_layers or [])
        ],
        "shared_balloons": [
            balloon_entry_to_dict(entry)
            for entry in getattr(work, "shared_balloons", [])
        ],
        "shared_texts": [
            text_entry_to_dict(entry)
            for entry in getattr(work, "shared_texts", [])
        ],
        "shared_comas": [
            coma_entry_to_dict(entry)
            for entry in getattr(work, "shared_comas", [])
        ],
        "layer_folders": [
            layer_folder_to_dict(entry)
            for entry in getattr(work, "layer_folders", [])
        ],
    }


def work_from_dict(work, data: dict[str, Any]) -> None:
    """work.json dict → BNameWorkData.

    schemaVersion が将来上がった場合はここでマイグレーションを挟む。
    """
    data = data or {}
    work_schema_version = _data_schema_version(data, 1)
    opacity_percent_schema = work_schema_version >= 5
    # 現状は v1 のみ対応。未知バージョンは読み込もうとするが警告は呼出側で。
    work_info_from_dict(work.work_info, data.get("workInfo", {}))
    nombre_from_dict(work.nombre, data.get("nombre", {}))
    paper_from_dict(work.paper, data.get("paper", {}))
    coma_gap_from_dict(work.coma_gap, data.get("comaGap", {}))
    if hasattr(work, "coma_blend_template_path"):
        work.coma_blend_template_path = str(data.get("comaBlendTemplatePath", "") or "")
    if hasattr(work, "page_preview_scale_percentage"):
        work.page_preview_scale_percentage = _page_preview_scale_percentage_from_data(
            data.get("pagePreviewScalePercentage", 12.5)
        )
    if hasattr(work, "auto_render_coma_thumb_on_return"):
        work.auto_render_coma_thumb_on_return = bool(
            data.get("autoRenderComaThumbOnReturn", True)
        )
    _view_settings_from_dict(work, data)
    safe_area_from_dict(work.safe_area_overlay, data.get("safeAreaOverlay", {}))
    scene = _scene_from_work(work)
    raster_layers = getattr(scene, "bname_raster_layers", None) if scene is not None else None
    if raster_layers is not None:
        raster_layers.clear()
        with _suspend_load_property_side_effects():
            for item in data.get("raster_layers", []) or []:
                entry = raster_layers.add()
                raster_layer_from_dict(entry, item, opacity_percent=opacity_percent_schema)
        if hasattr(scene, "bname_active_raster_layer_index"):
            scene.bname_active_raster_layer_index = 0 if len(raster_layers) else -1
    image_layers = getattr(scene, "bname_image_layers", None) if scene is not None else None
    if image_layers is not None:
        image_layers.clear()
        with _suspend_load_property_side_effects():
            for item in data.get("image_layers", []) or []:
                entry = image_layers.add()
                image_layer_from_dict(entry, item, opacity_percent=opacity_percent_schema)
        if hasattr(scene, "bname_active_image_layer_index"):
            scene.bname_active_image_layer_index = 0 if len(image_layers) else -1
    if hasattr(work, "shared_balloons"):
        work.shared_balloons.clear()
        with _suspend_load_property_side_effects():
            for item in data.get("shared_balloons", data.get("sharedBalloons", [])) or []:
                entry = work.shared_balloons.add()
                balloon_entry_from_dict(entry, item, opacity_percent=opacity_percent_schema)
                entry.parent_kind = "none"
                entry.parent_key = ""
    if hasattr(work, "shared_texts"):
        work.shared_texts.clear()
        with _suspend_load_property_side_effects():
            for item in data.get("shared_texts", data.get("sharedTexts", [])) or []:
                entry = work.shared_texts.add()
                text_entry_from_dict(entry, item)
                entry.parent_kind = "none"
                entry.parent_key = ""
    if hasattr(work, "shared_comas"):
        work.shared_comas.clear()
        for item in data.get("shared_comas", data.get("sharedComas", [])) or []:
            entry = work.shared_comas.add()
            coma_entry_from_dict(entry, item)
    if hasattr(work, "layer_folders"):
        work.layer_folders.clear()
        for item in data.get("layer_folders", data.get("layerFolders", [])) or []:
            entry = work.layer_folders.add()
            layer_folder_from_dict(entry, item)


# ---------- PageEntry / pages.json ----------


def page_entry_to_dict(entry) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": entry.id,
        "title": _normalize_generated_page_title(entry.title, entry.id),
        "dir": entry.dir_rel,
        "spread": bool(entry.spread),
        "visible": bool(getattr(entry, "visible", True)),
        "inPageRange": bool(getattr(entry, "in_page_range", True)),
        "offsetXMm": round(float(getattr(entry, "offset_x_mm", 0.0)), 3),
        "offsetYMm": round(float(getattr(entry, "offset_y_mm", 0.0)), 3),
    }
    if entry.spread:
        d["originalPages"] = [ref.page_id for ref in entry.original_pages]
        d["tombo"] = {
            "aligned": bool(entry.tombo_aligned),
            "gapMm": round(entry.tombo_gap_mm, 3),
        }
    if entry.thumbnail_rel:
        d["thumbnail"] = entry.thumbnail_rel
    if entry.coma_count:
        d["comaCount"] = int(entry.coma_count)
    return d


def page_entry_from_dict(entry, data: dict[str, Any]) -> None:
    data = data or {}
    entry.id = data.get("id", "")
    entry.title = _normalize_generated_page_title(data.get("title", ""), entry.id)
    entry.dir_rel = data.get("dir", "")
    entry.spread = bool(data.get("spread", False))
    if hasattr(entry, "visible"):
        entry.visible = bool(data.get("visible", True))
    if hasattr(entry, "in_page_range"):
        entry.in_page_range = bool(data.get("inPageRange", True))
    entry.offset_x_mm = float(data.get("offsetXMm", 0.0))
    entry.offset_y_mm = float(data.get("offsetYMm", 0.0))
    entry.original_pages.clear()
    for ref_id in data.get("originalPages", []):
        ref = entry.original_pages.add()
        ref.page_id = ref_id
    tombo = data.get("tombo", {})
    entry.tombo_aligned = bool(tombo.get("aligned", True))
    entry.tombo_gap_mm = float(tombo.get("gapMm", -9.6))
    entry.thumbnail_rel = data.get("thumbnail", "")
    entry.coma_count = int(data.get("comaCount", 0))


def pages_to_dict(work, *, last_modified: str = "") -> dict[str, Any]:
    return {
        "schemaVersion": PAGES_SCHEMA_VERSION,
        "pages": [page_entry_to_dict(p) for p in work.pages],
        "totalPages": len(work.pages),
        "activePageIndex": int(work.active_page_index),
        "lastModified": last_modified,
    }


# ---------- Coma (border/white_margin/entry) ----------


def coma_border_to_dict(border) -> dict[str, Any]:
    return {
        "style": border.style,
        "widthMm": round(border.width_mm, 3),
        "color": color_to_hex(border.color),
        "corner": {
            "type": border.corner_type,
            "radiusMm": round(border.corner_radius_mm, 3),
        },
        "blurAmount": round(float(getattr(border, "blur_amount", 0.5)), 3),
        "blurCurve": coma_blur_curve.points_to_json(
            coma_blur_curve.parse_points(getattr(border, "blur_curve_points", ""))
        ),
        "blurDither": bool(getattr(border, "blur_dither", False)),
        "visible": bool(border.visible),
        "presetName": str(getattr(border, "preset_name", "") or ""),
    }


def coma_border_from_dict(border, data: dict[str, Any]) -> None:
    data = data or {}
    border.style = data.get("style", "solid")
    border.width_mm = float(data.get("widthMm", 0.8))
    border.color = hex_to_rgba(data.get("color", "#000000"))
    corner = data.get("corner", {})
    border.corner_type = corner.get("type", "square")
    border.corner_radius_mm = float(corner.get("radiusMm", 0.0))
    if "blurAmount" in data:
        border.blur_amount = float(data["blurAmount"])
    if "blurCurve" in data:
        border.blur_curve_points = coma_blur_curve.points_to_text(
            coma_blur_curve.parse_points(data["blurCurve"])
        )
    if "blurDither" in data:
        border.blur_dither = bool(data["blurDither"])
    border.visible = bool(data.get("visible", True))
    if hasattr(border, "preset_name"):
        border.preset_name = str(data.get("presetName", "") or "")


def coma_white_margin_to_dict(wm) -> dict[str, Any]:
    return {
        "enabled": bool(wm.enabled),
        "placement": str(getattr(wm, "placement", "outside") or "outside"),
        "widthMm": round(wm.width_mm, 3),
        "color": color_to_hex(wm.color),
        "outerColor": color_to_hex(getattr(wm, "outer_color", wm.color)),
        "outerColorAlpha": round(float(getattr(wm, "outer_color", wm.color)[3]), 3),
        "innerColor": color_to_hex(getattr(wm, "inner_color", wm.color)),
        "innerColorAlpha": round(float(getattr(wm, "inner_color", wm.color)[3]), 3),
    }


def coma_white_margin_from_dict(wm, data: dict[str, Any]) -> None:
    data = data or {}
    wm.enabled = bool(data.get("enabled", False))
    if hasattr(wm, "placement"):
        placement = str(data.get("placement", "outside") or "outside")
        wm.placement = placement if placement in {"outside", "inside", "both"} else "outside"
    wm.width_mm = float(data.get("widthMm", 0.37))
    base_color = hex_to_rgba(data.get("color", "#FFFFFF"))
    wm.color = base_color
    if hasattr(wm, "outer_color"):
        alpha = data.get("outerColorAlpha", base_color[3])
        wm.outer_color = hex_to_rgba(data.get("outerColor", data.get("color", "#FFFFFF")), alpha)
    if hasattr(wm, "inner_color"):
        alpha = data.get("innerColorAlpha", base_color[3])
        wm.inner_color = hex_to_rgba(data.get("innerColor", data.get("color", "#FFFFFF")), alpha)


def coma_entry_to_dict(entry) -> dict[str, Any]:
    title = _normalize_generated_coma_title(
        entry.title,
        entry.id,
        entry.coma_id,
    )
    d: dict[str, Any] = {
        "schemaVersion": COMA_SCHEMA_VERSION,
        "id": entry.id,
        "title": title,
        "comaId": entry.coma_id,
        "displayNumber": int(getattr(entry, "display_number", 0) or 0),
        "comaBlendTemplatePath": str(getattr(entry, "coma_blend_template_path", "") or ""),
        "comaBlendTemplateNeedsApply": bool(getattr(entry, "coma_blend_template_needs_apply", False)),
        "shape": {
            "type": entry.shape_type,
            "rect": {
                "x": round(entry.rect_x_mm, 3),
                "y": round(entry.rect_y_mm, 3),
                "widthMm": round(entry.rect_width_mm, 3),
                "heightMm": round(entry.rect_height_mm, 3),
            },
            "vertices": [[round(v.x_mm, 3), round(v.y_mm, 3)] for v in entry.vertices],
        },
        "zOrder": int(entry.z_order),
        "overlapClipping": bool(entry.overlap_clipping),
        "visible": bool(getattr(entry, "visible", True)),
        "paperVisible": bool(getattr(entry, "paper_visible", True)),
        "backgroundColor": color_to_hex(entry.background_color),
        "backgroundColorAlpha": round(entry.background_color[3], 3),
        "border": coma_border_to_dict(entry.border),
        "whiteMargin": coma_white_margin_to_dict(entry.white_margin),
        "layerRefs": [r.layer_id for r in entry.layer_refs],
        "comaGap": {
            "verticalMm": round(entry.coma_gap_vertical_mm, 3),
            "horizontalMm": round(entry.coma_gap_horizontal_mm, 3),
        },
    }
    return d


def coma_entry_from_dict(entry, data: dict[str, Any]) -> None:
    data = data or {}
    entry.id = data.get("id", "")
    entry.coma_id = data.get("comaId", "")
    entry.title = _normalize_generated_coma_title(
        data.get("title", ""),
        entry.id,
        entry.coma_id,
    )
    if hasattr(entry, "display_number"):
        entry.display_number = max(0, int(data.get("displayNumber", 0) or 0))
    if hasattr(entry, "coma_blend_template_path"):
        entry.coma_blend_template_path = str(data.get("comaBlendTemplatePath", "") or "")
    if hasattr(entry, "coma_blend_template_needs_apply"):
        entry.coma_blend_template_needs_apply = bool(data.get("comaBlendTemplateNeedsApply", False))
    shape = data.get("shape", {})
    entry.shape_type = shape.get("type", "rect")
    rect = shape.get("rect", {})
    entry.rect_x_mm = float(rect.get("x", 0.0))
    entry.rect_y_mm = float(rect.get("y", 0.0))
    entry.rect_width_mm = float(rect.get("widthMm", 50.0))
    entry.rect_height_mm = float(rect.get("heightMm", 50.0))
    entry.vertices.clear()
    for pair in shape.get("vertices", []):
        v = entry.vertices.add()
        v.x_mm = float(pair[0]) if len(pair) > 0 else 0.0
        v.y_mm = float(pair[1]) if len(pair) > 1 else 0.0
    entry.z_order = int(data.get("zOrder", 0))
    entry.overlap_clipping = bool(data.get("overlapClipping", True))
    if hasattr(entry, "visible"):
        entry.visible = bool(data.get("visible", True))
    if hasattr(entry, "paper_visible"):
        entry.paper_visible = bool(data.get("paperVisible", True))
    # 既定値を opaque (1.0) に変更 (2026-05-02 リアーキ: コマ平面 Mesh が
    # 背景色 + マスク Boolean reference を兼ねるため、 alpha=0 だと意味が無い)
    bg_alpha = float(data.get("backgroundColorAlpha", 1.0))
    entry.background_color = hex_to_rgba(data.get("backgroundColor", "#FFFFFF"), bg_alpha)
    coma_border_from_dict(entry.border, data.get("border", {}))
    coma_white_margin_from_dict(entry.white_margin, data.get("whiteMargin", {}))
    entry.layer_refs.clear()
    for lid in data.get("layerRefs", []):
        ref = entry.layer_refs.add()
        ref.layer_id = str(lid)
    gap = data.get("comaGap", {})
    entry.coma_gap_vertical_mm = float(gap.get("verticalMm", -1.0))
    entry.coma_gap_horizontal_mm = float(gap.get("horizontalMm", -1.0))


# ---------- Balloon / Text (Phase 3) ----------


def _free_transform_to_dict(entry) -> dict[str, Any]:
    offsets = free_transform.entry_offsets(entry)
    return {
        "enabled": bool(getattr(entry, "free_transform_enabled", False)) and not free_transform.offsets_are_zero(offsets),
        "offsets": {
            corner: [round(float(value[0]), 3), round(float(value[1]), 3)]
            for corner, value in offsets.items()
        },
    }


def _free_transform_from_dict(entry, data: dict[str, Any] | None) -> None:
    payload = data if isinstance(data, dict) else {}
    raw_offsets = payload.get("offsets") if isinstance(payload, dict) else {}
    offsets = free_transform.zero_offsets()
    if isinstance(raw_offsets, dict):
        for corner in free_transform.CORNERS:
            raw_pair = raw_offsets.get(corner) or (0.0, 0.0)
            try:
                x_value = float(raw_pair[0])
                y_value = float(raw_pair[1])
            except Exception:  # noqa: BLE001
                x_value = 0.0
                y_value = 0.0
            offsets[corner] = (
                x_value,
                y_value,
            )
    free_transform.set_entry_offsets(
        entry,
        offsets,
        enabled=bool(payload.get("enabled", False)),
    )


def balloon_entry_to_dict(entry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "visible": bool(getattr(entry, "visible", True)),
        "shape": balloon_shapes.normalize_shape(entry.shape),
        "customPresetName": entry.custom_preset_name,
        "xMm": round(entry.x_mm, 3),
        "yMm": round(entry.y_mm, 3),
        "widthMm": round(entry.width_mm, 3),
        "heightMm": round(entry.height_mm, 3),
        "rotationDeg": round(entry.rotation_deg, 3),
        "centerOffsetXMm": round(float(getattr(entry, "center_offset_x_mm", 0.0)), 3),
        "centerOffsetYMm": round(float(getattr(entry, "center_offset_y_mm", 0.0)), 3),
        "freeTransform": _free_transform_to_dict(entry),
        "opacity": _opacity_to_data(getattr(entry, "opacity", 100.0)),
        "opacityUnit": "percent",
        "cornerType": balloon_shapes.corner_type_for_entry(entry),
        "roundedCornerEnabled": bool(entry.rounded_corner_enabled),
        "roundedCornerRadiusMm": round(entry.rounded_corner_radius_mm, 3),
        "roundedCornerRadiusUnit": str(getattr(entry, "rounded_corner_radius_unit", "mm") or "mm"),
        "roundedCornerRadiusPercent": round(float(getattr(entry, "rounded_corner_radius_percent", 30.0)), 3),
        "lineStyle": entry.line_style,
        "lineWidthMm": round(entry.line_width_mm, 3),
        "dashedSegmentLengthMm": round(float(getattr(entry, "dashed_segment_length_mm", 3.6)), 3),
        "dashedGapMm": round(float(getattr(entry, "dashed_gap_mm", 2.4)), 3),
        "dottedGapMm": round(float(getattr(entry, "dotted_gap_mm", 0.45)), 3),
        "multiLineCount": int(getattr(entry, "multi_line_count", 3) or 3),
        "multiLineWidthMm": round(float(getattr(entry, "multi_line_width_mm", 0.3)), 3),
        "multiLineSpacingMm": round(float(getattr(entry, "multi_line_spacing_mm", 0.4)), 3),
        "multiLineWidthScalePercent": round(float(getattr(entry, "multi_line_width_scale_percent", 100.0)), 3),
        "multiLineSpacingScalePercent": round(float(getattr(entry, "multi_line_spacing_scale_percent", 100.0)), 3),
        "lineValleyWidthPct": round(float(getattr(entry, "line_valley_width_pct", 100.0)), 3),
        "linePeakWidthPct": round(float(getattr(entry, "line_peak_width_pct", 100.0)), 3),
        "flashLineCount": int(getattr(entry, "flash_line_count", 120) or 120),
        "flashLineSpacingMm": round(float(getattr(entry, "flash_line_spacing_mm", 1.0)), 3),
        "flashWhiteLineEnabled": (
            bool(getattr(entry, "flash_white_line_enabled", True))
            if balloon_shapes.is_flash_line_style(getattr(entry, "line_style", ""))
            else False
        ),
        "flashWhiteLineWidthPercent": round(float(getattr(entry, "flash_white_line_width_percent", 100.0)), 3),
        "flashWhiteLineValleyWidthPct": round(float(getattr(entry, "flash_white_line_valley_width_pct", 0.0)), 3),
        "flashWhiteLinePeakWidthPct": round(float(getattr(entry, "flash_white_line_peak_width_pct", 100.0)), 3),
        "flashWhiteOutlineCount": int(getattr(entry, "flash_white_outline_count", 5) or 5),
        "flashWhiteOutlineWidthMm": round(float(getattr(entry, "flash_white_outline_width_mm", 10.0)), 3),
        "flashWhiteOutlineSpacingMm": round(float(getattr(entry, "flash_white_outline_spacing_mm", 0.25)), 3),
        "flashWhiteOutlineWhiteLineCount": int(getattr(entry, "flash_white_outline_white_line_count", 24) or 24),
        "flashWhiteOutlineBlackLineCount": int(getattr(entry, "flash_white_outline_black_line_count", 3) or 3),
        "flashWhiteOutlineBlackSpacingMm": round(float(getattr(entry, "flash_white_outline_black_spacing_mm", 0.25)), 3),
        "uniFlashParams": (
            balloon_core.uni_flash_params_to_dict(entry)
            if str(getattr(entry, "line_style", "") or "") == "uni_flash"
            else {}
        ),
        "multiLineDirection": str(getattr(entry, "multi_line_direction", "outside") or "outside"),
        "thornMultiLineValleyWidthPct": round(float(getattr(entry, "thorn_multi_line_valley_width_pct", 100.0)), 3),
        "thornMultiLinePeakWidthPct": round(float(getattr(entry, "thorn_multi_line_peak_width_pct", 100.0)), 3),
        "thornMultiLineLengthScalePercent": round(float(getattr(entry, "thorn_multi_line_length_scale_percent", 100.0)), 3),
        "thornMultiLineLengthScaleNearPercent": round(float(getattr(entry, "thorn_multi_line_length_scale_near_percent", 100.0)), 3),
        "thornMultiLineLengthScaleFarPercent": round(float(getattr(entry, "thorn_multi_line_length_scale_far_percent", 100.0)), 3),
        "thornMultiLineCrossEnabled": bool(getattr(entry, "thorn_multi_line_cross_enabled", False)),
        "lineColor": color_to_hex(entry.line_color),
        "lineColorAlpha": round(entry.line_color[3], 3),
        "fillColor": color_to_hex(entry.fill_color),
        "fillColorAlpha": round(entry.fill_color[3], 3),
        "fillOpacity": _opacity_to_data(getattr(entry, "fill_opacity", 100.0)),
        "fillMaterialName": str(getattr(entry, "fill_material_name", "") or ""),
        "fillBlurAmount": round(float(getattr(entry, "fill_blur_amount", 0.0)), 3),
        "fillBlurDither": bool(getattr(entry, "fill_blur_dither", False)),
        "fillGradientEnabled": bool(getattr(entry, "fill_gradient_enabled", False)),
        "fillGradientStartColor": color_to_hex(getattr(entry, "fill_gradient_start_color", entry.fill_color)),
        "fillGradientStartColorAlpha": round(float(getattr(entry, "fill_gradient_start_color", entry.fill_color)[3]), 3),
        "fillGradientEndColor": color_to_hex(getattr(entry, "fill_gradient_end_color", entry.fill_color)),
        "fillGradientEndColorAlpha": round(float(getattr(entry, "fill_gradient_end_color", entry.fill_color)[3]), 3),
        "fillGradientAngleDeg": round(float(getattr(entry, "fill_gradient_angle_deg", 90.0)), 3),
        "outerWhiteMarginEnabled": bool(getattr(entry, "outer_white_margin_enabled", False)),
        "outerWhiteMarginWidthMm": round(float(getattr(entry, "outer_white_margin_width_mm", 1.0)), 3),
        "outerWhiteMarginColor": color_to_hex(getattr(entry, "outer_white_margin_color", (1.0, 1.0, 1.0, 1.0))),
        "outerWhiteMarginColorAlpha": round(float(getattr(entry, "outer_white_margin_color", (1.0, 1.0, 1.0, 1.0))[3]), 3),
        "innerWhiteMarginEnabled": bool(getattr(entry, "inner_white_margin_enabled", False)),
        "innerWhiteMarginWidthMm": round(float(getattr(entry, "inner_white_margin_width_mm", 1.0)), 3),
        "innerWhiteMarginColor": color_to_hex(getattr(entry, "inner_white_margin_color", (1.0, 1.0, 1.0, 1.0))),
        "innerWhiteMarginColorAlpha": round(float(getattr(entry, "inner_white_margin_color", (1.0, 1.0, 1.0, 1.0))[3]), 3),
        "mergeGroupId": getattr(entry, "merge_group_id", ""),
        "parentKind": getattr(entry, "parent_kind", "page"),
        "parentKey": getattr(entry, "parent_key", ""),
        "folderKey": getattr(entry, "folder_key", ""),
        "tails": [
            {
                "type": t.type,
                "directionDeg": round(t.direction_deg, 3),
                "lengthMm": round(t.length_mm, 3),
                "rootWidthMm": round(t.root_width_mm, 3),
                "tipWidthMm": round(t.tip_width_mm, 3),
                "curveBend": round(t.curve_bend, 3),
                "customPointsEnabled": bool(getattr(t, "custom_points_enabled", False)),
                "startXMm": round(float(getattr(t, "start_x_mm", 0.0)), 3),
                "startYMm": round(float(getattr(t, "start_y_mm", 0.0)), 3),
                "endXMm": round(float(getattr(t, "end_x_mm", 0.0)), 3),
                "endYMm": round(float(getattr(t, "end_y_mm", 0.0)), 3),
                "points": [
                    {
                        "xMm": round(float(getattr(point, "x_mm", 0.0)), 3),
                        "yMm": round(float(getattr(point, "y_mm", 0.0)), 3),
                        "cornerType": str(getattr(point, "corner_type", "line") or "line"),
                    }
                    for point in getattr(t, "points", [])
                ],
            }
            for t in entry.tails
        ],
        "shapeParams": {
            "cloudBumpWidthMm": round(entry.shape_params.cloud_bump_width_mm, 3),
            "cloudBumpHeightMm": round(entry.shape_params.cloud_bump_height_mm, 3),
            "cloudBumpWidthJitter": round(entry.shape_params.cloud_bump_width_jitter, 3),
            "cloudBumpHeightJitter": round(entry.shape_params.cloud_bump_height_jitter, 3),
            "cloudOffset": round(entry.shape_params.cloud_offset_percent / 100.0, 3),
            "shapeSeed": int(getattr(entry.shape_params, "shape_seed", 0) or 0),
            "cloudSubWidthRatio": round(entry.shape_params.cloud_sub_width_ratio, 3),
            "cloudSubHeightRatio": round(entry.shape_params.cloud_sub_height_ratio, 3),
            "cloudSubWidthJitter": round(entry.shape_params.cloud_sub_width_jitter, 3),
            "cloudSubHeightJitter": round(entry.shape_params.cloud_sub_height_jitter, 3),
            "cloudValleySharp": bool(entry.shape_params.cloud_valley_sharp),
            "dynamicShapeBaseKind": str(getattr(entry.shape_params, "dynamic_shape_base_kind", "ellipse") or "ellipse"),
            "cloudWaveCount": int(entry.shape_params.cloud_wave_count),
            "cloudWaveAmplitudeMm": round(entry.shape_params.cloud_wave_amplitude_mm, 3),
            "spikeCount": int(entry.shape_params.spike_count),
            "spikeDepthMm": round(entry.shape_params.spike_depth_mm, 3),
            "spikeJitter": round(entry.shape_params.spike_jitter, 3),
        },
        "textId": entry.text_id,
    }


def balloon_entry_from_dict(entry, data: dict[str, Any], *, opacity_percent: bool = False) -> None:
    data = data or {}
    entry.id = data.get("id", entry.id)
    entry.visible = bool(data.get("visible", True))
    raw_shape = data.get("shape", entry.shape)
    legacy_flash_line_style = balloon_shapes.legacy_flash_shape_to_line_style(raw_shape)
    entry.shape = balloon_shapes.normalize_shape(raw_shape)
    entry.custom_preset_name = data.get("customPresetName", "")
    entry.x_mm = float(data.get("xMm", 0.0))
    entry.y_mm = float(data.get("yMm", 0.0))
    entry.width_mm = float(data.get("widthMm", 40.0))
    entry.height_mm = float(data.get("heightMm", 20.0))
    entry.rotation_deg = float(data.get("rotationDeg", 0.0))
    entry.center_offset_x_mm = float(data.get("centerOffsetXMm", 0.0))
    entry.center_offset_y_mm = float(data.get("centerOffsetYMm", 0.0))
    _free_transform_from_dict(entry, data.get("freeTransform"))
    entry.opacity = _opacity_from_data(data, "opacity", 100.0, percent_schema=opacity_percent)
    corner_type = str(data.get("cornerType", "") or "")
    if corner_type not in balloon_shapes.CORNER_TYPES:
        corner_type = "rounded" if bool(data.get("roundedCornerEnabled", False)) else "square"
    entry.corner_type = corner_type
    entry.corner_type_initialized = True
    entry.rounded_corner_enabled = corner_type != "square"
    entry.rounded_corner_radius_mm = float(data.get("roundedCornerRadiusMm", 3.0))
    if hasattr(entry, "rounded_corner_radius_unit"):
        unit = str(data.get("roundedCornerRadiusUnit", "mm") or "mm")
        entry.rounded_corner_radius_unit = unit if unit in {"mm", "percent"} else "mm"
    if hasattr(entry, "rounded_corner_radius_percent"):
        entry.rounded_corner_radius_percent = float(data.get("roundedCornerRadiusPercent", 30.0))
    raw_line_style = data.get("lineStyle", "")
    if legacy_flash_line_style and str(raw_line_style or "") not in {"none", "uni_flash", "white_outline"}:
        line_style = legacy_flash_line_style
    else:
        line_style = raw_line_style or legacy_flash_line_style or "solid"
    entry.line_style = balloon_shapes.normalize_line_style(line_style)
    is_flash_line_style = balloon_shapes.is_flash_line_style(entry.line_style)
    default_flash_endpoint_width = 0.0 if is_flash_line_style else 100.0
    entry.line_width_mm = float(data.get("lineWidthMm", 0.3))
    entry.dashed_segment_length_mm = float(data.get("dashedSegmentLengthMm", 3.6))
    entry.dashed_gap_mm = float(data.get("dashedGapMm", 2.4))
    entry.dotted_gap_mm = float(data.get("dottedGapMm", 0.45))
    entry.multi_line_count = int(data.get("multiLineCount", 3))
    entry.multi_line_width_mm = float(data.get("multiLineWidthMm", 0.3))
    entry.multi_line_spacing_mm = float(data.get("multiLineSpacingMm", 0.4))
    entry.multi_line_width_scale_percent = float(data.get("multiLineWidthScalePercent", 100.0))
    entry.multi_line_spacing_scale_percent = float(data.get("multiLineSpacingScalePercent", 100.0))
    entry.line_valley_width_pct = float(data.get("lineValleyWidthPct", default_flash_endpoint_width))
    entry.line_peak_width_pct = float(data.get("linePeakWidthPct", 100.0))
    if hasattr(entry, "flash_line_count"):
        entry.flash_line_count = int(data.get("flashLineCount", 120))
    if hasattr(entry, "flash_line_spacing_mm"):
        entry.flash_line_spacing_mm = float(data.get("flashLineSpacingMm", 1.0))
    entry.flash_white_line_enabled = bool(data.get("flashWhiteLineEnabled", is_flash_line_style))
    entry.flash_white_line_width_percent = float(data.get("flashWhiteLineWidthPercent", 100.0))
    entry.flash_white_line_valley_width_pct = float(data.get("flashWhiteLineValleyWidthPct", default_flash_endpoint_width))
    entry.flash_white_line_peak_width_pct = float(data.get("flashWhiteLinePeakWidthPct", 100.0))
    if hasattr(entry, "flash_white_outline_count"):
        entry.flash_white_outline_count = int(data.get("flashWhiteOutlineCount", 5))
    if hasattr(entry, "flash_white_outline_width_mm"):
        entry.flash_white_outline_width_mm = float(data.get("flashWhiteOutlineWidthMm", 10.0))
    if hasattr(entry, "flash_white_outline_spacing_mm"):
        entry.flash_white_outline_spacing_mm = float(data.get("flashWhiteOutlineSpacingMm", 0.25))
    if hasattr(entry, "flash_white_outline_white_line_count"):
        entry.flash_white_outline_white_line_count = int(data.get("flashWhiteOutlineWhiteLineCount", 24))
    if hasattr(entry, "flash_white_outline_black_line_count"):
        entry.flash_white_outline_black_line_count = int(data.get("flashWhiteOutlineBlackLineCount", 3))
    if hasattr(entry, "flash_white_outline_black_spacing_mm"):
        entry.flash_white_outline_black_spacing_mm = float(data.get("flashWhiteOutlineBlackSpacingMm", 0.25))
    entry.multi_line_direction = data.get("multiLineDirection", "outside")
    entry.thorn_multi_line_valley_width_pct = float(
        data.get("thornMultiLineValleyWidthPct", default_flash_endpoint_width)
    )
    entry.thorn_multi_line_peak_width_pct = float(data.get("thornMultiLinePeakWidthPct", 100.0))
    entry.thorn_multi_line_length_scale_percent = float(data.get("thornMultiLineLengthScalePercent", 100.0))
    # 旧 `thornMultiLineLengthScalePercent` が non-default のときは far の初期値に流用。
    legacy_length_far = float(data.get("thornMultiLineLengthScalePercent", 100.0))
    entry.thorn_multi_line_length_scale_near_percent = float(data.get("thornMultiLineLengthScaleNearPercent", 100.0))
    entry.thorn_multi_line_length_scale_far_percent = float(data.get("thornMultiLineLengthScaleFarPercent", legacy_length_far))
    entry.thorn_multi_line_cross_enabled = bool(data.get("thornMultiLineCrossEnabled", False))
    alpha = float(data.get("lineColorAlpha", 1.0))
    entry.line_color = hex_to_rgba(data.get("lineColor", "#000000"), alpha)
    alpha = float(data.get("fillColorAlpha", 1.0))
    entry.fill_color = hex_to_rgba(data.get("fillColor", "#FFFFFF"), alpha)
    entry.fill_opacity = _opacity_from_data(data, "fillOpacity", 100.0, percent_schema=opacity_percent)
    uni_flash_data = data.get("uniFlashParams")
    if isinstance(uni_flash_data, dict):
        balloon_core.uni_flash_params_from_dict(entry, uni_flash_data)
    elif entry.line_style == "uni_flash":
        peak_pct = max(0.0, float(getattr(entry, "line_peak_width_pct", 100.0) or 100.0))
        valley_pct = max(0.0, float(getattr(entry, "line_valley_width_pct", 0.0) or 0.0))
        line_width_mm = max(0.0, float(getattr(entry, "line_width_mm", 0.3) or 0.3))
        entry.brush_size_mm = max(0.01, line_width_mm * peak_pct / 100.0)
        endpoint_pct = 0.0 if peak_pct <= 1.0e-6 else max(0.0, min(100.0, valley_pct / peak_pct * 100.0))
        entry.in_percent = endpoint_pct
        entry.out_percent = endpoint_pct
        entry.in_start_percent = 50.0
        entry.out_start_percent = 50.0
        entry.spacing_mode = "distance"
        entry.spacing_distance_mm = max(0.01, float(getattr(entry, "flash_line_spacing_mm", 1.0) or 1.0))
        entry.max_line_count = max(1, int(getattr(entry, "flash_line_count", 120) or 120))
        entry.white_underlay_enabled = bool(getattr(entry, "flash_white_line_enabled", True))
        entry.white_underlay_width_percent = float(getattr(entry, "flash_white_line_width_percent", 100.0) or 100.0)
    entry.fill_material_name = str(data.get("fillMaterialName", "") or "")
    entry.fill_blur_amount = float(data.get("fillBlurAmount", 0.0))
    entry.fill_blur_dither = bool(data.get("fillBlurDither", False))
    entry.fill_gradient_enabled = bool(data.get("fillGradientEnabled", False))
    alpha = float(data.get("fillGradientStartColorAlpha", 1.0))
    entry.fill_gradient_start_color = hex_to_rgba(data.get("fillGradientStartColor", data.get("fillColor", "#FFFFFF")), alpha)
    alpha = float(data.get("fillGradientEndColorAlpha", 1.0))
    entry.fill_gradient_end_color = hex_to_rgba(data.get("fillGradientEndColor", data.get("fillColor", "#FFFFFF")), alpha)
    entry.fill_gradient_angle_deg = float(data.get("fillGradientAngleDeg", 90.0))
    entry.outer_white_margin_enabled = bool(data.get("outerWhiteMarginEnabled", False))
    entry.outer_white_margin_width_mm = float(data.get("outerWhiteMarginWidthMm", 1.0))
    alpha = float(data.get("outerWhiteMarginColorAlpha", 1.0))
    entry.outer_white_margin_color = hex_to_rgba(data.get("outerWhiteMarginColor", "#FFFFFF"), alpha)
    entry.inner_white_margin_enabled = bool(data.get("innerWhiteMarginEnabled", False))
    entry.inner_white_margin_width_mm = float(data.get("innerWhiteMarginWidthMm", 1.0))
    alpha = float(data.get("innerWhiteMarginColorAlpha", 1.0))
    entry.inner_white_margin_color = hex_to_rgba(data.get("innerWhiteMarginColor", "#FFFFFF"), alpha)
    if hasattr(entry, "blend_mode"):
        entry.blend_mode = "normal"
    entry.merge_group_id = data.get("mergeGroupId", "")
    entry.parent_kind = data.get("parentKind", data.get("parent_kind", "page"))
    entry.parent_key = str(data.get("parentKey", data.get("parent_key", "")) or "")
    if hasattr(entry, "folder_key"):
        entry.folder_key = str(data.get("folderKey", data.get("folder_key", "")) or "")
    entry.tails.clear()
    for td in data.get("tails", []):
        tail = entry.tails.add()
        tail.type = td.get("type", "straight")
        tail.direction_deg = float(td.get("directionDeg", 270.0))
        tail.length_mm = float(td.get("lengthMm", 6.0))
        tail.root_width_mm = float(td.get("rootWidthMm", 3.0))
        tail.tip_width_mm = float(td.get("tipWidthMm", 0.0))
        tail.curve_bend = float(td.get("curveBend", 0.0))
        tail.custom_points_enabled = bool(td.get("customPointsEnabled", False))
        tail.start_x_mm = float(td.get("startXMm", 0.0))
        tail.start_y_mm = float(td.get("startYMm", 0.0))
        tail.end_x_mm = float(td.get("endXMm", 0.0))
        tail.end_y_mm = float(td.get("endYMm", 0.0))
        tail.points.clear()
        for pd in td.get("points", []):
            point = tail.points.add()
            point.x_mm = float(pd.get("xMm", 0.0))
            point.y_mm = float(pd.get("yMm", 0.0))
            point.corner_type = str(pd.get("cornerType", "line") or "line")
        if len(tail.points) < 2 and bool(getattr(tail, "custom_points_enabled", False)):
            point = tail.points.add()
            point.x_mm = float(getattr(tail, "start_x_mm", 0.0))
            point.y_mm = float(getattr(tail, "start_y_mm", 0.0))
            point.corner_type = "line"
            point = tail.points.add()
            point.x_mm = float(getattr(tail, "end_x_mm", 0.0))
            point.y_mm = float(getattr(tail, "end_y_mm", 0.0))
            point.corner_type = "line"
    sp = data.get("shapeParams", {})
    entry.shape_params.cloud_bump_width_mm = float(sp.get("cloudBumpWidthMm", 10.0))
    entry.shape_params.cloud_bump_height_mm = float(sp.get("cloudBumpHeightMm", 4.0))
    entry.shape_params.cloud_bump_width_jitter = float(sp.get("cloudBumpWidthJitter", 0.0))
    entry.shape_params.cloud_bump_height_jitter = float(sp.get("cloudBumpHeightJitter", 0.0))
    entry.shape_params.cloud_sub_width_jitter = float(sp.get("cloudSubWidthJitter", 0.0))
    entry.shape_params.cloud_sub_height_jitter = float(sp.get("cloudSubHeightJitter", 0.0))
    entry.shape_params.cloud_valley_sharp = bool(sp.get("cloudValleySharp", False))
    base_kind = str(sp.get("dynamicShapeBaseKind", "ellipse") or "ellipse")
    if base_kind not in {"ellipse", "rect"}:
        base_kind = "ellipse"
    entry.shape_params.dynamic_shape_base_kind = base_kind
    entry.shape_params.shape_seed = int(sp.get("shapeSeed", sp.get("seed", 0)) or 0)
    if "cloudOffsetPercent" in sp:
        entry.shape_params.cloud_offset_percent = float(sp.get("cloudOffsetPercent", 50.0))
    else:
        offset_value = float(sp.get("cloudOffset", 0.5))
        entry.shape_params.cloud_offset_percent = offset_value * 100.0 if offset_value <= 1.0 else offset_value
    entry.shape_params.cloud_sub_width_ratio = float(
        sp.get("cloudSubWidthRatio", sp.get("cloudSubBumpRatio", 0.0))
    )
    entry.shape_params.cloud_sub_height_ratio = float(
        sp.get("cloudSubHeightRatio", sp.get("cloudSubBumpRatio", 0.0))
    )
    entry.shape_params.cloud_wave_count = int(sp.get("cloudWaveCount", 12))
    entry.shape_params.cloud_wave_amplitude_mm = float(sp.get("cloudWaveAmplitudeMm", 3.0))
    entry.shape_params.spike_count = int(sp.get("spikeCount", 24))
    entry.shape_params.spike_depth_mm = float(sp.get("spikeDepthMm", 6.0))
    entry.shape_params.spike_jitter = float(sp.get("spikeJitter", 0.2))
    entry.text_id = data.get("textId", "")


def text_entry_to_dict(entry) -> dict[str, Any]:
    from ..utils.geom import pt_to_q
    from ..utils import text_style

    font_size_q = float(
        getattr(entry, "font_size_q", pt_to_q(float(getattr(entry, "font_size_pt", 9.0))))
    )
    return {
        "id": entry.id,
        "visible": bool(getattr(entry, "visible", True)),
        "body": entry.body,
        "speakerType": entry.speaker_type,
        "speakerName": entry.speaker_name,
        "font": entry.font,
        "fontSizeQ": round(font_size_q, 3),
        "fontSizePt": round(float(getattr(entry, "font_size_pt", 0.0) or 0.0), 3),
        "fontSizeUnit": str(getattr(entry, "font_size_unit", "q") or "q"),
        "color": color_to_hex(entry.color),
        "colorAlpha": round(entry.color[3], 3),
        "writingMode": entry.writing_mode,
        "lineHeight": round(entry.line_height, 3),
        "letterSpacing": round(entry.letter_spacing, 3),
        "strokeEnabled": bool(entry.stroke_enabled),
        "strokeWidthMm": round(entry.stroke_width_mm, 3),
        "strokeColor": color_to_hex(entry.stroke_color),
        "strokeColorAlpha": round(entry.stroke_color[3], 3),
        "xMm": round(entry.x_mm, 3),
        "yMm": round(entry.y_mm, 3),
        "widthMm": round(entry.width_mm, 3),
        "heightMm": round(entry.height_mm, 3),
        "freeTransform": _free_transform_to_dict(entry),
        "parentBalloonId": entry.parent_balloon_id,
        "parentKind": getattr(entry, "parent_kind", "page"),
        "parentKey": getattr(entry, "parent_key", ""),
        "folderKey": getattr(entry, "folder_key", ""),
        "fontSpans": [
            {
                "start": int(start),
                "length": int(end - start),
                "font": font,
            }
            for start, end, font in text_style.font_spans_snapshot(entry)
        ],
        "styleSpans": [
            {
                "start": int(start),
                "length": int(end - start),
                "font": style[0],
                "fontSizeQ": round(float(style[1]), 3),
                "color": color_to_hex(style[2]),
                "colorAlpha": round(float(style[2][3]), 3),
                "bold": bool(style[3]),
                "italic": bool(style[4]),
            }
            for start, end, style in text_style.style_spans_snapshot(entry)
        ],
    }


def text_entry_from_dict(entry, data: dict[str, Any]) -> None:
    from ..utils import text_style
    from ..utils.geom import pt_to_q, q_to_pt

    data = data or {}
    entry.id = data.get("id", entry.id)
    entry.visible = bool(data.get("visible", True))
    entry.body = data.get("body", "")
    entry.speaker_type = data.get("speakerType", "normal")
    entry.speaker_name = data.get("speakerName", "")
    entry.font = data.get("font", "")
    unit = str(data.get("fontSizeUnit", "q") or "q")
    if unit == "pt" and "fontSizePt" in data and hasattr(entry, "font_size_pt"):
        entry.font_size_pt = float(data["fontSizePt"])
    elif "fontSizeQ" in data:
        entry.font_size_q = float(data["fontSizeQ"])
    elif "fontSizePt" in data:
        entry.font_size_q = float(pt_to_q(float(data["fontSizePt"])))
    else:
        entry.font_size_q = 20.0
    if unit != "pt" or not hasattr(entry, "font_size_pt"):
        entry.font_size_pt = float(q_to_pt(float(entry.font_size_q)))
    if hasattr(entry, "font_size_unit"):
        entry.font_size_unit = unit
    alpha = float(data.get("colorAlpha", 1.0))
    entry.color = hex_to_rgba(data.get("color", "#000000"), alpha)
    entry.writing_mode = data.get("writingMode", "vertical")
    entry.line_height = float(data.get("lineHeight", 1.4))
    entry.letter_spacing = float(data.get("letterSpacing", 0.0))
    entry.stroke_enabled = bool(data.get("strokeEnabled", False))
    entry.stroke_width_mm = float(data.get("strokeWidthMm", 0.2))
    alpha = float(data.get("strokeColorAlpha", 1.0))
    entry.stroke_color = hex_to_rgba(data.get("strokeColor", "#FFFFFF"), alpha)
    entry.x_mm = float(data.get("xMm", 0.0))
    entry.y_mm = float(data.get("yMm", 0.0))
    entry.width_mm = float(data.get("widthMm", 30.0))
    entry.height_mm = float(data.get("heightMm", 15.0))
    _free_transform_from_dict(entry, data.get("freeTransform"))
    entry.parent_balloon_id = data.get("parentBalloonId", "")
    entry.parent_kind = data.get("parentKind", data.get("parent_kind", "page"))
    entry.parent_key = str(data.get("parentKey", data.get("parent_key", "")) or "")
    if hasattr(entry, "folder_key"):
        entry.folder_key = str(data.get("folderKey", data.get("folder_key", "")) or "")
    entry.font_spans.clear()
    for item in data.get("fontSpans", []):
        span = entry.font_spans.add()
        span.start = int(item.get("start", 0))
        span.length = max(1, int(item.get("length", 1)))
        span.font = str(item.get("font", "") or "")
    text_style.normalize_font_spans(entry)
    entry.style_spans.clear()
    for item in data.get("styleSpans", []):
        span = entry.style_spans.add()
        span.start = int(item.get("start", 0))
        span.length = max(1, int(item.get("length", 1)))
        span.font = str(item.get("font", "") or "")
        span.font_size_q = float(item.get("fontSizeQ", entry.font_size_q))
        alpha = float(item.get("colorAlpha", 1.0))
        span.color = hex_to_rgba(item.get("color", "#000000"), alpha)
        span.font_bold = bool(item.get("bold", False))
        span.font_italic = bool(item.get("italic", False))
    text_style.normalize_style_spans(entry)


# ---------- page.json ----------


def page_to_dict(page_entry) -> dict[str, Any]:
    """page.json (個別ページメタ) を書き出す.

    page_entry は BNamePageEntry。comas / balloons / texts をシリアライズする。
    """
    return {
        "schemaVersion": PAGE_SCHEMA_VERSION,
        "id": page_entry.id,
        "title": _normalize_generated_page_title(page_entry.title, page_entry.id),
        "spread": bool(page_entry.spread),
        "offsetXMm": round(float(getattr(page_entry, "offset_x_mm", 0.0)), 3),
        "offsetYMm": round(float(getattr(page_entry, "offset_y_mm", 0.0)), 3),
        "activeComaIndex": int(page_entry.active_coma_index),
        "activeBalloonIndex": int(page_entry.active_balloon_index),
        "activeTextIndex": int(page_entry.active_text_index),
        "comas": [coma_entry_to_dict(p) for p in page_entry.comas],
        "balloons": [balloon_entry_to_dict(b) for b in page_entry.balloons],
        "texts": [text_entry_to_dict(t) for t in page_entry.texts],
    }


def page_from_dict(page_entry, data: dict[str, Any]) -> None:
    with _suspend_load_property_side_effects():
        data = data or {}
        page_schema_version = _data_schema_version(data, 1)
        opacity_percent_schema = page_schema_version >= 2
        page_entry.id = data.get("id", page_entry.id)
        if "title" in data:
            page_entry.title = _normalize_generated_page_title(data["title"], page_entry.id)
        page_entry.offset_x_mm = float(data.get("offsetXMm", getattr(page_entry, "offset_x_mm", 0.0)))
        page_entry.offset_y_mm = float(data.get("offsetYMm", getattr(page_entry, "offset_y_mm", 0.0)))
        page_entry.comas.clear()
        for coma_data in data.get("comas", []):
            entry = page_entry.comas.add()
            coma_entry_from_dict(entry, coma_data)
        page_entry.balloons.clear()
        for b_data in data.get("balloons", []):
            entry = page_entry.balloons.add()
            balloon_entry_from_dict(entry, b_data, opacity_percent=opacity_percent_schema)
        page_entry.texts.clear()
        for t_data in data.get("texts", []):
            entry = page_entry.texts.add()
            text_entry_from_dict(entry, t_data)
        idx = int(data.get("activeComaIndex", -1))
        if idx < -1 or idx >= len(page_entry.comas):
            idx = 0 if len(page_entry.comas) > 0 else -1
        page_entry.active_coma_index = idx
        idx = int(data.get("activeBalloonIndex", -1))
        if idx < -1 or idx >= len(page_entry.balloons):
            idx = 0 if len(page_entry.balloons) > 0 else -1
        page_entry.active_balloon_index = idx
        idx = int(data.get("activeTextIndex", -1))
        if idx < -1 or idx >= len(page_entry.texts):
            idx = 0 if len(page_entry.texts) > 0 else -1
        page_entry.active_text_index = idx


def pages_from_dict(work, data: dict[str, Any]) -> None:
    data = data or {}
    work.pages.clear()
    for entry_data in data.get("pages", []):
        entry = work.pages.add()
        page_entry_from_dict(entry, entry_data)
    idx = int(data.get("activePageIndex", -1))
    if idx < -1 or idx >= len(work.pages):
        idx = 0 if len(work.pages) > 0 else -1
    work.active_page_index = idx
