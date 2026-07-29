"""現行preset codecが所有するRNA fieldの抽出。"""

from __future__ import annotations

import ast
import runpy
from pathlib import Path
from typing import Any


_SMALL_FAMILIES = {
    ("BMangaFillLayer", "color"): ("fill", "gradient"),
    ("BMangaFillLayer", "opacity"): ("fill", "gradient"),
    ("BMangaFillLayer", "color2"): ("gradient",),
    ("BMangaFillLayer", "gradient_type"): ("gradient",),
}

_BORDER_FIELDS = {
    "BMangaComaBorder": (
        "style",
        "width_mm",
        "color",
        "corner_type",
        "corner_radius_mm",
        "blur_amount",
        "blur_curve_points",
        "blur_dither",
        "visible",
        "preset_name",
    ),
    "BMangaComaWhiteMargin": (
        "enabled",
        "placement",
        "width_mm",
        "color",
        "outer_color",
        "inner_color",
    ),
    "BMangaComaEntry": (
        "paper_visible",
        "background_color",
    ),
}

_IMAGE_PATH_FIELDS = (
    "content_source",
    "filepath",
    "shape_kind",
    "shape_sides",
    "color",
    "draw_mode",
    "brush_size_mm",
    "aspect_ratio",
    "image_angle_deg",
    "spacing_percent",
    "stamp_angle_mode",
    "stamp_angle_object_name",
    "ribbon_repeat_mode",
    "opacity",
    "inout_size_enabled",
    "inout_opacity_enabled",
    "inout_color_enabled",
    "in_percent",
    "out_percent",
    "in_start_percent",
    "out_start_percent",
    "in_easing_curve",
    "out_easing_curve",
    "inout_start_color",
    "inout_end_color",
)


def _literal_assignment(path: Path, name: str) -> Any:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            try:
                return ast.literal_eval(node.value)
            except (ValueError, TypeError):
                return None
    raise ValueError(f"preset field declaration is missing: {path}:{name}")


def _function_fields(path: Path, function_names: tuple[str, ...]) -> tuple[str, ...]:
    """実serializer/deserializerが直接読むRNA fieldをASTから抽出する。"""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    result: set[str] = set()
    for function_name in function_names:
        function = functions.get(function_name)
        if function is None or not function.args.args:
            raise ValueError(f"preset codec function is missing: {path}:{function_name}")
        owner = function.args.args[0].arg
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == owner
            ):
                result.add(node.attr)
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            if not (
                isinstance(node.args[0], ast.Name)
                and node.args[0].id == owner
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                continue
            call_name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
            if call_name in {
                "getattr",
                "hasattr",
                "setattr",
                "_set_attr",
                "_set_color",
            }:
                result.add(str(node.args[1].value))
    return tuple(sorted(result - {"bl_rna", "rna_type"}))


def _class_property_names(path: Path, class_name: str) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    owner = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        ),
        None,
    )
    if owner is None:
        raise ValueError(f"preset owner class is missing: {path}:{class_name}")
    result = []
    for node in owner.body:
        target = None
        call = None
        if isinstance(node, ast.AnnAssign):
            target = node.target
            call = node.value if node.value is not None else node.annotation
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, call = node.targets[0], node.value
        if isinstance(target, ast.Name) and isinstance(call, ast.Call):
            result.append(target.id)
    return tuple(sorted(result))


def _add(
    result: dict[tuple[str, str], set[str]],
    owner: str,
    fields,
    family: str,
) -> None:
    for field in fields:
        result.setdefault((owner, str(field)), set()).add(family)


def preset_field_families(root: Path) -> dict[tuple[str, str], tuple[str, ...]]:
    io_dir = root / "io"
    result: dict[tuple[str, str], set[str]] = {}
    text_fields = _literal_assignment(io_dir / "text_presets.py", "_TEXT_KEYS")
    _add(result, "BMangaTextEntry", text_fields, "text")
    balloon_fields = _literal_assignment(
        io_dir / "balloon_presets.py",
        "BALLOON_STYLE_KEYS",
    )
    shape_fields = _literal_assignment(
        io_dir / "balloon_presets.py",
        "BALLOON_SHAPE_PARAM_KEYS",
    )
    _add(result, "BMangaBalloonEntry", balloon_fields, "balloon")
    _add(result, "BMangaBalloonShapeParams", shape_fields, "balloon")
    line_effect_schema = runpy.run_path(
        str(root / "utils" / "line_effect_schema.py")
    )
    _add(
        result,
        "BMangaBalloonEntry",
        line_effect_schema["BALLOON_UNI_FLASH_PARAM_FIELDS"],
        "balloon",
    )
    linked = _literal_assignment(
        io_dir / "balloon_presets.py",
        "LINKED_TEXT_SETTING_KEYS",
    )
    _add(
        result,
        "_BMangaPresetScratchBalloon",
        (value[0] for value in linked.values()),
        "balloon",
    )
    tail_fields = _literal_assignment(io_dir / "tail_presets.py", "_TAIL_FIELDS")
    _add(result, "BMangaBalloonTail", (row[0] for row in tail_fields), "tail")
    gp_fields = _literal_assignment(io_dir / "gp_tool_presets.py", "_FIELDS")
    _add(result, "BMangaGpToolSettings", (row[1] for row in gp_fields), "gp_tool")
    for owner, fields in _BORDER_FIELDS.items():
        _add(result, owner, fields, "border")
    schema_path = io_dir / "schema.py"
    paper_functions = {
        "BMangaPaperSettings": ("paper_to_dict", "paper_from_dict"),
        "BMangaDisplayItem": ("display_item_to_dict", "display_item_from_dict"),
        "BMangaWorkInfo": ("work_info_to_dict", "work_info_from_dict"),
        "BMangaComaGap": ("coma_gap_to_dict", "coma_gap_from_dict"),
    }
    for owner, functions in paper_functions.items():
        _add(
            result,
            owner,
            _function_fields(schema_path, functions),
            "paper",
        )
    _add(result, "BMangaImagePathLayer", _IMAGE_PATH_FIELDS, "image_path")
    _add(
        result,
        "BMangaEffectLineParams",
        line_effect_schema["EFFECT_PARAM_FIELDS"],
        "effect_line",
    )
    line_fields = _literal_assignment(
        root / "addons" / "b_manga_line" / "presets.py",
        "_SETTING_FIELDS",
    )
    _add(result, "BMangaLinePreset", line_fields, "line")
    render_core = root / "addons" / "b_manga_render" / "core.py"
    _add(
        result,
        "BMangaRenderCommand",
        _class_property_names(render_core, "BMangaRenderCommand"),
        "render",
    )
    for key, families in _SMALL_FAMILIES.items():
        result.setdefault(key, set()).update(families)
    return {
        key: tuple(sorted(families))
        for key, families in sorted(result.items())
    }
