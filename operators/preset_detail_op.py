"""プリセット詳細設定ダイアログ."""

from __future__ import annotations

from typing import Any

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import Operator

from ..utils import log

_logger = log.get_logger(__name__)

_ICON_MAP = {
    "border": "MESH_PLANE",
    "balloon": "MESH_CIRCLE",
    "text": "FONT_DATA",
    "effect_line": "FORCE_FORCE",
    "fill": "SNAP_FACE",
    "gradient": "NODE_TEXTURE",
    "image_path": "CURVE_BEZCURVE",
    "tail": "SHARPCURVE",
}


def _load_preset_data(preset_type: str, preset_name: str) -> dict[str, Any] | None:
    try:
        if preset_type == "fill":
            from ..io import fill_presets
            p = fill_presets.load_preset_by_name(preset_name)
            return p.data if p else None
        if preset_type == "gradient":
            from ..io import gradient_presets
            p = gradient_presets.load_preset_by_name(preset_name)
            return p.data if p else None
        if preset_type == "text":
            from ..io import text_presets
            p = text_presets.load_preset_by_name(preset_name)
            return p.data if p else None
        if preset_type == "border":
            from ..io import border_presets
            p = border_presets.load_preset_by_name(preset_name, None)
            return p.data if p else None
        if preset_type == "tail":
            from ..io import tail_presets
            from ..core.work import get_work
            work = get_work(bpy.context)
            work_dir = None
            if work and getattr(work, "work_dir", ""):
                from pathlib import Path
                work_dir = Path(work.work_dir)
            p = tail_presets.load_preset_by_name(preset_name, work_dir)
            return p.data if p else None
        if preset_type == "effect_line":
            from ..io import effect_line_presets
            p = effect_line_presets.load_preset_by_name(preset_name, None)
            return p.data if p else None
        if preset_type == "image_path":
            from ..io import image_path_presets
            p = image_path_presets.load_preset_by_name(preset_name, None)
            return p.data if p else None
    except Exception:  # noqa: BLE001
        _logger.exception("failed to load preset %s/%s", preset_type, preset_name)
    return None


def _save_preset_data(preset_type: str, preset_name: str, data: dict[str, Any]) -> bool:
    try:
        if preset_type == "fill":
            from ..io import fill_presets
            fill_presets.save_local_preset(preset_name, data.get("description", ""), data)
            return True
        if preset_type == "gradient":
            from ..io import gradient_presets
            gradient_presets.save_local_preset(preset_name, data.get("description", ""), data)
            return True
        if preset_type == "text":
            from ..io import text_presets
            text_presets.save_local_preset(None, preset_name, data.get("description", ""), data)
            return True
        if preset_type == "border":
            from ..io import border_presets
            border_presets._write_local_preset_data(None, data, preset_name)
            return True
        if preset_type == "tail":
            from ..io import tail_presets
            from ..utils import json_io
            from ..io import shared_presets
            target_dir = shared_presets.preset_dir("tails")
            target_dir.mkdir(parents=True, exist_ok=True)
            filename = preset_name.replace("/", "_").replace("\\", "_") + ".json"
            json_io.write_json(target_dir / filename, data)
            return True
        if preset_type == "effect_line":
            from ..io import effect_line_presets
            from ..utils import json_io
            from ..io import shared_presets
            target_dir = shared_presets.preset_dir("effect_lines")
            target_dir.mkdir(parents=True, exist_ok=True)
            filename = preset_name.replace("/", "_").replace("\\", "_") + ".json"
            json_io.write_json(target_dir / filename, data)
            return True
    except Exception:  # noqa: BLE001
        _logger.exception("failed to save preset %s/%s", preset_type, preset_name)
    return False


def _color_from_data(data: dict, key: str, default=(0.0, 0.0, 0.0, 1.0)):
    raw = data.get(key, default)
    if isinstance(raw, str):
        raw = _hex_to_rgba(raw)
    try:
        return tuple(float(raw[i]) for i in range(4))
    except (TypeError, IndexError, ValueError):
        return default


def _hex_to_rgba(hex_str: str) -> tuple[float, float, float, float]:
    hex_str = hex_str.lstrip("#")
    if len(hex_str) == 6:
        hex_str += "FF"
    try:
        r = int(hex_str[0:2], 16) / 255.0
        g = int(hex_str[2:4], 16) / 255.0
        b = int(hex_str[4:6], 16) / 255.0
        a = int(hex_str[6:8], 16) / 255.0
        return (r, g, b, a)
    except (ValueError, IndexError):
        return (0.0, 0.0, 0.0, 1.0)


def _rgba_to_hex(rgba) -> str:
    r = int(round(max(0.0, min(1.0, rgba[0])) * 255))
    g = int(round(max(0.0, min(1.0, rgba[1])) * 255))
    b = int(round(max(0.0, min(1.0, rgba[2])) * 255))
    return f"#{r:02X}{g:02X}{b:02X}"


class BMANGA_OT_preset_detail_edit(Operator):
    bl_idname = "bmanga.preset_detail_edit"
    bl_label = "プリセット詳細設定"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    preset_type: StringProperty()  # type: ignore[valid-type]
    preset_name: StringProperty()  # type: ignore[valid-type]

    description_text: StringProperty(name="説明")  # type: ignore[valid-type]

    # Fill / Gradient
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="色", subtype="COLOR", size=4, min=0.0, max=1.0, default=(0, 0, 0, 1)
    )
    color2: FloatVectorProperty(  # type: ignore[valid-type]
        name="色2", subtype="COLOR", size=4, min=0.0, max=1.0, default=(1, 1, 1, 1)
    )
    opacity: IntProperty(name="不透明度 (%)", min=0, max=100, default=100)  # type: ignore[valid-type]
    gradient_type: EnumProperty(  # type: ignore[valid-type]
        name="グラデーション種類",
        items=[("linear", "線形", ""), ("radial", "円形", "")],
    )

    # Text
    writing_mode: EnumProperty(  # type: ignore[valid-type]
        name="書字方向",
        items=[("vertical", "縦書き", ""), ("horizontal", "横書き", "")],
    )
    font_size_value: FloatProperty(name="フォントサイズ", min=1.0, max=200.0, default=20.0)  # type: ignore[valid-type]
    font_size_unit: EnumProperty(  # type: ignore[valid-type]
        name="単位",
        items=[("Q", "Q", ""), ("pt", "pt", ""), ("mm", "mm", "")],
    )
    line_height: FloatProperty(name="行間", min=0.5, max=5.0, default=1.4)  # type: ignore[valid-type]
    letter_spacing: FloatProperty(name="字間", min=-1.0, max=2.0, default=0.0)  # type: ignore[valid-type]
    font_bold: BoolProperty(name="太字", default=False)  # type: ignore[valid-type]
    font_italic: BoolProperty(name="斜体", default=False)  # type: ignore[valid-type]
    stroke_enabled: BoolProperty(name="フチ文字", default=False)  # type: ignore[valid-type]

    # Border
    border_style: EnumProperty(  # type: ignore[valid-type]
        name="スタイル",
        items=[("solid", "実線", ""), ("none", "なし", ""), ("dashed", "破線", "")],
    )
    border_width_mm: FloatProperty(name="線幅 (mm)", min=0.0, max=10.0, default=0.5)  # type: ignore[valid-type]
    border_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="枠色", subtype="COLOR", size=4, min=0.0, max=1.0, default=(0, 0, 0, 1)
    )
    border_blur: FloatProperty(name="ぼかし", min=0.0, max=10.0, default=0.5)  # type: ignore[valid-type]
    white_margin_enabled: BoolProperty(name="白フチ", default=True)  # type: ignore[valid-type]
    white_margin_width_mm: FloatProperty(name="白フチ幅 (mm)", min=0.0, max=10.0, default=0.5)  # type: ignore[valid-type]

    # Tail
    tail_line_type: EnumProperty(  # type: ignore[valid-type]
        name="形状",
        items=[
            ("wedge", "三角 (くさび)", ""),
            ("curve", "曲線", ""),
            ("pen", "ペン線 (抜き)", ""),
            ("ellipse", "楕円", ""),
        ],
    )
    tail_root_width_mm: FloatProperty(name="根元幅 (mm)", min=0.0, max=30.0, default=3.0)  # type: ignore[valid-type]
    tail_tip_width_mm: FloatProperty(name="先端幅 (mm)", min=0.0, max=30.0, default=0.0)  # type: ignore[valid-type]
    tail_length_mm: FloatProperty(name="長さ (mm)", min=0.0, max=100.0, default=6.0)  # type: ignore[valid-type]
    tail_curve_bend: FloatProperty(name="曲がり", min=-1.0, max=1.0, default=0.0)  # type: ignore[valid-type]

    # Effect line (key fields only)
    effect_type: EnumProperty(  # type: ignore[valid-type]
        name="効果線種類",
        items=[
            ("focus", "集中線", ""),
            ("uni_flash", "ウニフラ", ""),
            ("beta_flash", "ベタフラ", ""),
            ("speed", "流線", ""),
            ("white_outline", "白抜き線", ""),
        ],
    )
    brush_size_mm: FloatProperty(name="ブラシサイズ (mm)", min=0.01, max=10.0, default=0.3)  # type: ignore[valid-type]
    max_line_count: IntProperty(name="最大本数", min=1, max=2000, default=60)  # type: ignore[valid-type]

    _preset_data: dict[str, Any] = {}

    def invoke(self, context, event):
        data = _load_preset_data(self.preset_type, self.preset_name)
        if data is None:
            self.report({"WARNING"}, f"プリセットが見つかりません: {self.preset_name}")
            return {"CANCELLED"}
        self._preset_data = data
        self.description_text = str(data.get("description", "") or "")
        self._load_type_fields(data)
        return context.window_manager.invoke_props_dialog(self, width=320)

    def _load_type_fields(self, data: dict[str, Any]) -> None:
        pt = self.preset_type
        if pt == "fill":
            self.color = _color_from_data(data, "color")
            self.opacity = int(data.get("opacity", 100))
        elif pt == "gradient":
            self.gradient_type = str(data.get("gradient_type", "linear"))
            self.color = _color_from_data(data, "color")
            self.color2 = _color_from_data(data, "color2", (1, 1, 1, 1))
            self.opacity = int(data.get("opacity", 100))
        elif pt == "text":
            self.writing_mode = str(data.get("writing_mode", "vertical"))
            self.font_size_value = float(data.get("font_size_value", 20.0))
            self.font_size_unit = str(data.get("font_size_unit", "Q"))
            self.line_height = float(data.get("line_height", 1.4))
            self.letter_spacing = float(data.get("letter_spacing", 0.0))
            self.color = _color_from_data(data, "color")
            self.font_bold = bool(data.get("font_bold", False))
            self.font_italic = bool(data.get("font_italic", False))
            self.stroke_enabled = bool(data.get("stroke_enabled", False))
        elif pt == "border":
            border = data.get("border", {})
            self.border_style = str(border.get("style", "solid"))
            self.border_width_mm = float(border.get("widthMm", 0.5))
            self.border_color = _color_from_data(border, "color", (0, 0, 0, 1))
            self.border_blur = float(border.get("blurAmount", 0.5))
            wm_data = data.get("whiteMargin", {})
            self.white_margin_enabled = bool(wm_data.get("enabled", True))
            self.white_margin_width_mm = float(wm_data.get("widthMm", 0.5))
        elif pt == "tail":
            tail = data.get("tail", {})
            self.tail_line_type = str(tail.get("lineType", "wedge"))
            self.tail_root_width_mm = float(tail.get("rootWidthMm", 3.0))
            self.tail_tip_width_mm = float(tail.get("tipWidthMm", 0.0))
            self.tail_length_mm = float(tail.get("lengthMm", 6.0))
            self.tail_curve_bend = float(tail.get("curveBend", 0.0))
        elif pt == "effect_line":
            self.effect_type = str(data.get("effect_type", "focus"))
            self.opacity = int(data.get("opacity") or 100)
            self.brush_size_mm = float(data.get("brush_size_mm") or 0.3)
            self.max_line_count = int(data.get("max_line_count") or 60)

    def draw(self, context):
        layout = self.layout
        pt = self.preset_type
        layout.prop(self, "description_text")
        layout.separator()

        if pt == "fill":
            layout.prop(self, "color")
            layout.prop(self, "opacity")
        elif pt == "gradient":
            layout.prop(self, "gradient_type")
            layout.prop(self, "color")
            layout.prop(self, "color2")
            layout.prop(self, "opacity")
        elif pt == "text":
            layout.prop(self, "writing_mode")
            row = layout.row(align=True)
            row.prop(self, "font_size_value")
            row.prop(self, "font_size_unit", text="")
            layout.prop(self, "line_height")
            layout.prop(self, "letter_spacing")
            layout.prop(self, "color")
            row = layout.row()
            row.prop(self, "font_bold")
            row.prop(self, "font_italic")
            layout.prop(self, "stroke_enabled")
        elif pt == "border":
            layout.prop(self, "border_style")
            layout.prop(self, "border_width_mm")
            layout.prop(self, "border_color")
            layout.prop(self, "border_blur")
            layout.separator()
            layout.prop(self, "white_margin_enabled")
            if self.white_margin_enabled:
                layout.prop(self, "white_margin_width_mm")
        elif pt == "tail":
            layout.prop(self, "tail_line_type")
            layout.prop(self, "tail_root_width_mm")
            layout.prop(self, "tail_tip_width_mm")
            layout.prop(self, "tail_length_mm")
            layout.prop(self, "tail_curve_bend")
        elif pt == "effect_line":
            layout.prop(self, "effect_type")
            layout.prop(self, "opacity")
            layout.prop(self, "brush_size_mm")
            layout.prop(self, "max_line_count")
        else:
            layout.label(text="このプリセットタイプは詳細編集未対応です")

    def execute(self, context):
        data = self._preset_data.copy()
        data["description"] = self.description_text
        pt = self.preset_type
        if pt == "fill":
            data["color"] = list(self.color)
            data["opacity"] = self.opacity
        elif pt == "gradient":
            data["gradient_type"] = self.gradient_type
            data["color"] = list(self.color)
            data["color2"] = list(self.color2)
            data["opacity"] = self.opacity
        elif pt == "text":
            data["writing_mode"] = self.writing_mode
            data["font_size_value"] = self.font_size_value
            data["font_size_unit"] = self.font_size_unit
            data["line_height"] = self.line_height
            data["letter_spacing"] = self.letter_spacing
            data["color"] = list(self.color)
            data["font_bold"] = self.font_bold
            data["font_italic"] = self.font_italic
            data["stroke_enabled"] = self.stroke_enabled
        elif pt == "border":
            border = data.get("border", {})
            border["style"] = self.border_style
            border["widthMm"] = self.border_width_mm
            border["color"] = _rgba_to_hex(self.border_color)
            border["blurAmount"] = self.border_blur
            data["border"] = border
            wm_data = data.get("whiteMargin", {})
            wm_data["enabled"] = self.white_margin_enabled
            wm_data["widthMm"] = self.white_margin_width_mm
            data["whiteMargin"] = wm_data
        elif pt == "tail":
            tail = data.get("tail", {})
            tail["lineType"] = self.tail_line_type
            tail["rootWidthMm"] = self.tail_root_width_mm
            tail["tipWidthMm"] = self.tail_tip_width_mm
            tail["lengthMm"] = self.tail_length_mm
            tail["curveBend"] = self.tail_curve_bend
            data["tail"] = tail
        elif pt == "effect_line":
            data["effect_type"] = self.effect_type
            data["opacity"] = self.opacity
            data["brush_size_mm"] = self.brush_size_mm
            data["max_line_count"] = self.max_line_count

        if _save_preset_data(pt, self.preset_name, data):
            self.report({"INFO"}, f"プリセット「{self.preset_name}」を保存しました")
        else:
            self.report({"WARNING"}, f"プリセット「{self.preset_name}」の保存に失敗しました")
            return {"CANCELLED"}
        return {"FINISHED"}


_CLASSES = (BMANGA_OT_preset_detail_edit,)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
