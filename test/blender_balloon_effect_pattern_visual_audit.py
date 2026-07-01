"""Blender実機用: フキダシ/効果線の全主要パターンをAI目視用に一覧化する。

実行例:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "D:/Develop/Blender/B-MANGA/test/blender_balloon_effect_pattern_visual_audit.py"
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))
OUT_DIR = Path(
    os.environ.get("BMANGA_BALLOON_EFFECT_PATTERN_OUT", "")
    or (ROOT / ".codex" / "visual" / "balloon_effect_pattern_audit")
)
ADDON_NAME = "bmanga_dev_pattern_visual_audit"

from blender_balloon_effect_pattern_visual_support import (
    apply_audit_display_materials as _apply_audit_display_materials,
    clear_audit_clones as _clear_audit_clones,
    clone_owner_visuals_for_sheet as _clone_owner_visuals_for_sheet,
    force_owner_objects_visible as _force_owner_objects_visible,
    force_render_visible as _force_render_visible,
    hide_existing_scene_objects as _hide_existing_scene_objects,
    label_sheet as _label_sheet,
    make_line_image as _make_line_image,
    material as _material,
    merge_bbox as _merge_bbox,
    mesh_polygon_count as _mesh_polygon_count,
    object_world_bbox_mm as _object_world_bbox_mm,
    owner_world_bbox_mm as _owner_world_bbox_mm,
    render_to as _render_to,
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        ADDON_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ADDON_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _enum_items(items) -> list[dict]:
    return [{"id": str(item[0]), "label": str(item[1])} for item in items]


def _owner_mesh_counts(owner_id: str, balloon_line_mesh) -> dict[str, int]:
    counts: dict[str, int] = {}
    owner_prop = balloon_line_mesh.PROP_BALLOON_LINE_MESH_OWNER_ID
    kind_prop = balloon_line_mesh.PROP_BALLOON_LINE_MESH_KIND
    for obj in bpy.data.objects:
        is_line_owner = str(obj.get(owner_prop, "") or "") == str(owner_id)
        is_fill_owner = str(obj.get("bmanga_balloon_fill_mesh_owner_id", "") or "") == str(owner_id)
        if not is_line_owner and not is_fill_owner:
            continue
        kind = str(obj.get(kind_prop, "") or obj.get("bmanga_balloon_fill_mesh_kind", "") or obj.name)
        counts[kind] = counts.get(kind, 0) + _mesh_polygon_count(obj)
    for prefix, kind in (
        ("balloon_line_shape_", "balloon_line_shape_mesh"),
        ("balloon_line_image_", "balloon_line_image_mesh"),
        ("balloon_tail_ellipse_line_", "balloon_tail_ellipse_line"),
        ("balloon_tail_stroke_", "balloon_tail_stroke"),
    ):
        obj = bpy.data.objects.get(f"{prefix}{owner_id}")
        if obj is not None:
            counts[kind] = counts.get(kind, 0) + _mesh_polygon_count(obj)
    return counts


def _page_world_offset_mm(context, page) -> tuple[float, float]:
    try:
        from bmanga_dev_pattern_visual_audit.utils import page_grid

        work = getattr(context.scene, "bmanga_work", None)
        page_id = str(getattr(page, "id", "") or "")
        for index, page_entry in enumerate(getattr(work, "pages", []) or []):
            if str(getattr(page_entry, "id", "") or "") == page_id:
                return page_grid.page_total_offset_mm(work, context.scene, index)
    except Exception:
        pass
    return (0.0, 0.0)


def _make_balloon_matrix(context, page, parent_key: str, line_image_path: Path) -> tuple[Path, list[dict], list[dict]]:
    from bmanga_dev_pattern_visual_audit.core import balloon as balloon_core
    from bmanga_dev_pattern_visual_audit.operators import balloon_op
    from bmanga_dev_pattern_visual_audit.utils import balloon_curve_object, balloon_line_mesh

    shape_items = _enum_items(balloon_core._SHAPE_ITEMS)
    line_items = _enum_items(balloon_core._LINE_STYLE_ITEMS)
    material_name = "監査用線マテリアル"
    if material_name not in bpy.data.materials:
        _material(material_name, (0.1, 0.22, 0.85, 1.0))

    cell_mm = 44.0
    cell_px = 170
    w_mm = 25.0
    h_mm = 17.0
    results: list[dict] = []
    for r, shape in enumerate(shape_items):
        for c, line in enumerate(line_items):
            x = c * cell_mm + cell_mm * 0.5
            y = (len(shape_items) - 1 - r) * cell_mm + cell_mm * 0.5
            entry = balloon_op._create_balloon_entry(
                context,
                page,
                shape=shape["id"],
                x=x,
                y=y,
                w=w_mm,
                h=h_mm,
                parent_kind="page",
                parent_key=parent_key,
            )
            entry.title = f"{shape['label']} / {line['label']}"
            entry.line_style = line["id"]
            entry.line_width_mm = 0.8
            entry.fill_color = (1.0, 1.0, 1.0, 1.0)
            entry.fill_opacity = 100.0
            entry.line_color = (0.0, 0.0, 0.0, 1.0)
            entry.outer_white_margin_enabled = line["id"] in {"solid", "dashed", "dotted", "double"}
            entry.outer_white_margin_width_mm = 0.45
            entry.outer_white_margin_color = (1.0, 0.65, 0.25, 1.0)
            entry.inner_white_margin_enabled = line["id"] in {"solid", "double"}
            entry.inner_white_margin_width_mm = 0.35
            entry.inner_white_margin_color = (0.20, 0.55, 1.0, 1.0)
            entry.dashed_segment_length_mm = 2.0
            entry.dashed_gap_mm = 1.2
            entry.dotted_gap_mm = 1.0
            entry.multi_line_count = 3
            entry.multi_line_width_mm = 0.28
            entry.multi_line_spacing_mm = 0.55
            entry.line_shape_kind = "star"
            entry.line_shape_spacing_mm = 2.5
            entry.line_shape_orient = "center"
            entry.line_image_path = str(line_image_path)
            entry.line_image_interval_mm = 7.0
            entry.line_material_name = material_name
            entry.line_material_mapping = "ribbon"
            entry.max_line_count = 80
            entry.spacing_mode = "angle"
            entry.spacing_angle_deg = 12.0
            entry.brush_size_mm = 0.28
            entry.uni_flash_offset_percent = 60.0
            entry.white_underlay_enabled = True
            entry.white_underlay_width_percent = 180.0
            entry.flash_white_outline_count = 3
            entry.flash_white_outline_white_line_count = 8
            entry.flash_white_outline_black_line_count = 2
            if shape["id"] == "custom":
                entry.custom_outline_json = json.dumps(
                    [[0, 15], [30, 0], [60, 18], [46, 42], [18, 48]],
                    separators=(",", ":"),
                )
            sp = entry.shape_params
            sp.cloud_bump_width_mm = 5.0
            sp.cloud_bump_height_mm = 5.0
            sp.cloud_offset_percent = 18.0
            sp.cloud_sub_width_ratio = 45.0
            sp.cloud_sub_height_ratio = 40.0
            obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
            _force_render_visible(obj)
            _force_owner_objects_visible(str(entry.id), balloon_line_mesh)
            _apply_audit_display_materials(str(entry.id), obj, balloon_line_mesh)
            counts = _owner_mesh_counts(str(entry.id), balloon_line_mesh)
            bbox = _merge_bbox(
                _object_world_bbox_mm(obj),
                _owner_world_bbox_mm(str(entry.id), balloon_line_mesh),
            )
            _clone_owner_visuals_for_sheet(
                str(entry.id),
                obj,
                balloon_line_mesh,
                target_center_mm=(x, y),
                source_bbox_mm=bbox,
                rank=len(results),
            )
            expected_empty = shape["id"] == "none"
            mesh_total = sum(counts.values()) + (0 if obj is None else _mesh_polygon_count(obj))
            ok = (mesh_total == 0) if expected_empty else (mesh_total > 0)
            results.append(
                {
                    "row": r,
                    "col": c,
                    "shape": shape,
                    "line_style": line,
                    "ok": ok,
                    "mesh_counts": counts,
                    "object_polygons": 0 if obj is None else _mesh_polygon_count(obj),
                    "expected_empty": expected_empty,
                    "bbox_mm": bbox,
                }
            )

    raw = OUT_DIR / "balloon_shape_line_matrix_raw.png"
    _render_to(
        raw,
        width_px=len(line_items) * cell_px,
        height_px=len(shape_items) * cell_px,
        center_mm=(len(line_items) * cell_mm * 0.5, len(shape_items) * cell_mm * 0.5),
        scale_mm=max(len(line_items) * cell_mm, len(shape_items) * cell_mm),
    )
    labeled = _label_sheet(
        raw,
        OUT_DIR / "balloon_shape_line_matrix.png",
        title="フキダシ 形状 × 線種 全パターン AI目視シート",
        cols=[item["label"] for item in line_items],
        rows=[item["label"] for item in shape_items],
        results=results,
        cell_px=cell_px,
    )
    return labeled, results, [{"shapes": shape_items, "line_styles": line_items}]


def _make_balloon_extra_sheet(context, page, parent_key: str) -> tuple[Path, list[dict], list[dict]]:
    from bmanga_dev_pattern_visual_audit.core import balloon as balloon_core
    from bmanga_dev_pattern_visual_audit.operators import balloon_op
    from bmanga_dev_pattern_visual_audit.utils import balloon_curve_object, balloon_line_mesh

    _clear_audit_clones()
    cases = [
        ("角 直角", {"shape": "rect", "corner_type": "square"}),
        ("角 丸角", {"shape": "rect", "corner_type": "rounded", "rounded_corner_radius_unit": "percent"}),
        ("角 面取り", {"shape": "rect", "corner_type": "bevel"}),
        ("多重線 外側", {"shape": "thorn", "line_style": "double", "multi_line_direction": "outside"}),
        ("多重線 内側", {"shape": "thorn", "line_style": "double", "multi_line_direction": "inside"}),
        ("多重線 両方向", {"shape": "thorn", "line_style": "double", "multi_line_direction": "both"}),
        ("線図形 丸", {"shape": "ellipse", "line_style": "shape", "line_shape_kind": "circle"}),
        ("線図形 星", {"shape": "ellipse", "line_style": "shape", "line_shape_kind": "star"}),
        ("線図形 ハート", {"shape": "ellipse", "line_style": "shape", "line_shape_kind": "heart"}),
        ("しっぽ 三角", {"shape": "ellipse", "tail": {"line_type": "wedge"}}),
        ("しっぽ 楕円", {"shape": "ellipse", "tail": {"line_type": "ellipse_chain", "ellipse_orient": "line"}}),
        ("しっぽ 線", {"shape": "ellipse", "tail": {"line_type": "line", "taper_in_percent": 60.0, "taper_out_percent": 80.0}}),
        ("塗り 内側ぼかし", {"shape": "cloud", "fill_blur_axis": "inside"}),
        ("塗り 輪郭ぼかし", {"shape": "cloud", "fill_blur_axis": "center"}),
        ("塗り 外側ぼかし", {"shape": "cloud", "fill_blur_axis": "outside"}),
    ]
    cell_mm = 48.0
    cell_px = 190
    cols = 5
    rows = math.ceil(len(cases) / cols)
    results: list[dict] = []
    for index, (label, opts) in enumerate(cases):
        c = index % cols
        r = index // cols
        x = c * cell_mm + cell_mm * 0.5
        y = (rows - 1 - r) * cell_mm + cell_mm * 0.5
        entry = balloon_op._create_balloon_entry(
            context,
            page,
            shape=str(opts.get("shape", "ellipse")),
            x=x,
            y=y,
            w=28.0,
            h=18.0,
            parent_kind="page",
            parent_key=parent_key,
        )
        entry.title = label
        entry.line_style = str(opts.get("line_style", "solid"))
        entry.line_width_mm = 0.8
        entry.line_color = (0.0, 0.0, 0.0, 1.0)
        entry.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry.fill_opacity = 100.0
        entry.multi_line_count = 4
        entry.multi_line_width_mm = 0.25
        entry.multi_line_spacing_mm = 0.55
        entry.thorn_multi_line_valley_width_pct = 55.0
        entry.thorn_multi_line_peak_width_pct = 100.0
        entry.thorn_multi_line_length_scale_near_percent = 100.0
        entry.thorn_multi_line_length_scale_far_percent = 65.0
        entry.fill_blur_amount = 0.65 if "fill_blur_axis" in opts else 0.0
        for attr, value in opts.items():
            if attr in {"shape", "tail"}:
                continue
            setattr(entry, attr, value)
        if str(getattr(entry, "corner_type", "")) == "rounded":
            entry.rounded_corner_enabled = True
            entry.rounded_corner_radius_percent = 55.0
        if "tail" in opts:
            tail = entry.tails.add()
            tail.type = "straight"
            tail.line_type = str(opts["tail"].get("line_type", "wedge"))
            tail.root_width_mm = 5.5
            tail.tip_width_mm = 0.0
            tail.length_mm = 18.0
            tail.direction_deg = 270.0
            tail.curve_mode = "curve"
            for attr, value in opts["tail"].items():
                setattr(tail, attr, value)
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        _force_render_visible(obj)
        _force_owner_objects_visible(str(entry.id), balloon_line_mesh)
        _apply_audit_display_materials(str(entry.id), obj, balloon_line_mesh)
        counts = _owner_mesh_counts(str(entry.id), balloon_line_mesh)
        bbox = _merge_bbox(
            _object_world_bbox_mm(obj),
            _owner_world_bbox_mm(str(entry.id), balloon_line_mesh),
        )
        _clone_owner_visuals_for_sheet(
            str(entry.id),
            obj,
            balloon_line_mesh,
            target_center_mm=(x, y),
            source_bbox_mm=bbox,
            rank=len(results),
        )
        mesh_total = sum(counts.values()) + (0 if obj is None else _mesh_polygon_count(obj))
        results.append({"row": r, "col": c, "label": label, "ok": mesh_total > 0, "mesh_counts": counts, "bbox_mm": bbox})

    raw = OUT_DIR / "balloon_extra_patterns_raw.png"
    _render_to(
        raw,
        width_px=cols * cell_px,
        height_px=rows * cell_px,
        center_mm=(cols * cell_mm * 0.5, rows * cell_mm * 0.5),
        scale_mm=max(cols * cell_mm, rows * cell_mm),
    )
    labeled = _label_sheet(
        raw,
        OUT_DIR / "balloon_extra_patterns.png",
        title="フキダシ 角・しっぽ・塗り・多重線 補助パターン AI目視シート",
        cols=[f"{i + 1}" for i in range(cols)],
        rows=[f"{i + 1}" for i in range(rows)],
        results=results,
        cell_px=cell_px,
    )
    inventory = [
        {
            "corner_types": _enum_items(balloon_core._CORNER_TYPE_ITEMS),
            "multi_line_directions": _enum_items(balloon_core._MULTI_LINE_DIRECTION_ITEMS),
            "tail_types": _enum_items(balloon_core._TAIL_TYPE_ITEMS),
            "tail_line_types": _enum_items(balloon_core._TAIL_LINE_TYPE_ITEMS),
            "tail_curve_modes": _enum_items(balloon_core._TAIL_CURVE_MODE_ITEMS),
            "fill_blur_axes": _enum_items(balloon_core._FILL_BLUR_AXIS_ITEMS),
            "line_shape_kinds": _enum_items(balloon_core._LINE_SHAPE_KIND_ITEMS),
        }
    ]
    return labeled, results, inventory


def _effect_base_params(effect_type: str, start_shape: str, end_shape: str) -> SimpleNamespace:
    return SimpleNamespace(
        effect_type=effect_type,
        rotation_deg=0.0,
        start_shape=start_shape,
        start_to_coma_frame=False,
        start_rounded_corner_enabled=start_shape == "rect",
        start_rounded_corner_radius_mm=4.0,
        start_rounded_corner_radius_unit="mm",
        start_rounded_corner_radius_percent=35.0,
        start_cloud_bump_width_mm=8.0,
        start_cloud_bump_width_jitter=0.0,
        start_cloud_bump_height_mm=4.0,
        start_cloud_bump_height_jitter=0.0,
        start_cloud_offset_percent=22.0,
        start_cloud_sub_width_ratio=35.0,
        start_cloud_sub_width_jitter=0.0,
        start_cloud_sub_height_ratio=35.0,
        start_cloud_sub_height_jitter=0.0,
        end_shape=end_shape,
        end_rounded_corner_enabled=end_shape == "rect",
        end_rounded_corner_radius_mm=3.0,
        end_rounded_corner_radius_unit="mm",
        end_rounded_corner_radius_percent=35.0,
        end_cloud_bump_width_mm=7.0,
        end_cloud_bump_width_jitter=0.0,
        end_cloud_bump_height_mm=3.5,
        end_cloud_bump_height_jitter=0.0,
        end_cloud_offset_percent=30.0,
        end_cloud_sub_width_ratio=35.0,
        end_cloud_sub_width_jitter=0.0,
        end_cloud_sub_height_ratio=35.0,
        end_cloud_sub_height_jitter=0.0,
        brush_size_mm=0.45 if effect_type != "white_outline" else 0.25,
        brush_jitter_enabled=False,
        brush_jitter_amount=0.15,
        length_jitter_enabled=False,
        length_jitter_amount=10.0,
        end_length_jitter_enabled=effect_type in {"focus", "uni_flash"},
        end_length_jitter_amount=18.0,
        spacing_mode="angle",
        spacing_angle_deg=20.0,
        spacing_distance_mm=7.0,
        spacing_density_compensation=True,
        spacing_jitter_enabled=False,
        spacing_jitter_amount=0.2,
        max_line_count=40,
        bundle_enabled=False,
        bundle_line_count=4,
        bundle_line_count_jitter=0.0,
        bundle_gap_mm=4.0,
        bundle_gap_jitter_amount=0.0,
        bundle_jagged_enabled=False,
        bundle_jagged_height_percent=80.0,
        inout_apply="brush_size",
        inout_apply_brush_size=True,
        inout_apply_opacity=False,
        in_percent=100.0,
        out_percent=0.0,
        in_start_percent=30.0,
        out_start_percent=60.0,
        in_easing_curve="0.0000,0.0000;1.0000,1.0000",
        out_easing_curve="0.0000,0.0000;1.0000,1.0000",
        inout_range_mode="percent",
        in_range_percent=100.0,
        out_range_percent=100.0,
        in_range_mm=10.0,
        out_range_mm=10.0,
        opacity=100.0,
        fill_color=(1.0, 1.0, 1.0, 1.0),
        fill_opacity=100.0,
        fill_base_shape=effect_type in {"beta_flash"},
        white_underlay_enabled=effect_type in {"focus", "uni_flash"},
        white_underlay_width_percent=170.0,
        white_underlay_color=(1.0, 1.0, 1.0, 1.0),
        uni_flash_offset_percent=65.0,
        speed_angle_deg=0.0,
        speed_line_count=18,
        white_outline_count=2,
        white_outline_spacing_mm=0.35,
        white_outline_white_line_count_auto=False,
        white_outline_white_line_count=8,
        white_outline_width_mm=9.0,
        white_outline_width_jitter_enabled=False,
        white_outline_width_min_percent=75.0,
        white_outline_length_jitter_enabled=True,
        white_outline_length_min_percent=65.0,
        white_outline_white_ratio_percent=68.0,
        white_outline_white_brush_mm=0.28,
        white_outline_white_attenuation=0.0,
        white_outline_white_in_percent=90.0,
        white_outline_white_out_percent=0.0,
        white_outline_white_inout_range_mode="percent",
        white_outline_white_in_range_percent=35.0,
        white_outline_white_out_range_percent=45.0,
        white_outline_white_in_range_mm=8.0,
        white_outline_white_out_range_mm=8.0,
        white_outline_black_line_count_auto=False,
        white_outline_black_line_count=2,
        white_outline_black_direction="both",
        white_outline_black_brush_mm=0.25,
        white_outline_black_spacing_mm=0.35,
        white_outline_black_width_scale_percent=90.0,
        white_outline_black_length_scale_near_percent=100.0,
        white_outline_black_length_scale_far_percent=75.0,
        white_outline_black_attenuation=0.0,
        white_outline_angle_deg=0.0,
    )


def _offset_stroke(stroke, dx_mm: float, dy_mm: float):
    from bmanga_dev_pattern_visual_audit.operators.effect_line_gen import EffectLineStroke

    dx = dx_mm * 0.001
    dy = dy_mm * 0.001
    return EffectLineStroke(
        points_xyz=[(float(x) + dx, float(y) + dy, float(z)) for x, y, z in stroke.points_xyz],
        radius=stroke.radius,
        cyclic=stroke.cyclic,
        radii=stroke.radii,
        opacities=stroke.opacities,
        role=stroke.role,
        curve_type=stroke.curve_type,
        bezier_smooth=stroke.bezier_smooth,
        density_end=stroke.density_end,
        side=stroke.side,
    )


def _build_effect_mesh(name: str, strokes, materials: tuple[bpy.types.Material, ...]) -> bpy.types.Object:
    from bmanga_dev_pattern_visual_audit.utils import effect_line_object

    mesh = bpy.data.meshes.new(f"{name}_mesh")
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    for mat in materials:
        mesh.materials.append(mat)
    effect_line_object._rebuild_effect_display_mesh(mesh, strokes)
    return obj


def _make_effect_sheets() -> tuple[list[Path], list[dict], list[dict]]:
    from bmanga_dev_pattern_visual_audit.core import effect_line as effect_core
    from bmanga_dev_pattern_visual_audit.operators import effect_line_gen

    effect_items = _enum_items(effect_core._EFFECT_TYPE_ITEMS)
    shape_items = _enum_items(effect_core._EFFECT_SHAPE_ITEMS)
    line_mat = _material("効果線_黒", (0.0, 0.0, 0.0, 1.0))
    white_mat = _material("効果線_白", (1.0, 1.0, 1.0, 1.0))
    underlay_mat = _material("効果線_下地", (1.0, 0.96, 0.70, 1.0))
    guide_mat = _material("効果線_形状ガイド", (0.38, 0.45, 0.62, 1.0))

    paths: list[Path] = []
    results: list[dict] = []
    cell_mm = 42.0
    cell_px = 150
    for effect_index, effect in enumerate(effect_items):
        for r, start in enumerate(shape_items):
            for c, end in enumerate(shape_items):
                x = c * cell_mm + cell_mm * 0.5
                y = (len(shape_items) - 1 - r) * cell_mm + cell_mm * 0.5
                params = _effect_base_params(effect["id"], start["id"], end["id"])
                strokes = effect_line_gen.generate_strokes(
                    params,
                    center_xy_mm=(x, y),
                    radius_xy_mm=(11.0, 14.0),
                    seed=100 + effect_index * 1000 + r * 20 + c,
                )
                guides = effect_line_gen.generate_shape_guide_strokes(
                    params,
                    center_xy_mm=(x, y),
                    radius_xy_mm=(11.0, 14.0),
                    seed=100 + effect_index * 1000 + r * 20 + c,
                )
                mesh = _build_effect_mesh(
                    f"効果線_{effect['id']}_{start['id']}_{end['id']}",
                    strokes,
                    (line_mat, white_mat, underlay_mat),
                )
                if guides:
                    _build_effect_mesh(
                        f"効果線ガイド_{effect['id']}_{start['id']}_{end['id']}",
                        guides,
                        (guide_mat,),
                    )
                poly_count = _mesh_polygon_count(mesh)
                results.append(
                    {
                        "effect_type": effect,
                        "start_shape": start,
                        "end_shape": end,
                        "row": r,
                        "col": c,
                        "sheet": effect["id"],
                        "ok": poly_count > 0 and len(strokes) > 0,
                        "stroke_count": len(strokes),
                        "polygons": poly_count,
                    }
                )
        raw = OUT_DIR / f"effect_line_{effect['id']}_matrix_raw.png"
        _render_to(
            raw,
            width_px=len(shape_items) * cell_px,
            height_px=len(shape_items) * cell_px,
            center_mm=(len(shape_items) * cell_mm * 0.5, len(shape_items) * cell_mm * 0.5),
            scale_mm=len(shape_items) * cell_mm,
        )
        sheet_results = [item for item in results if item["sheet"] == effect["id"]]
        labeled = _label_sheet(
            raw,
            OUT_DIR / f"effect_line_{effect['id']}_matrix.png",
            title=f"効果線 {effect['label']} 始点形状 × 終点形状 全パターン AI目視シート",
            cols=[item["label"] for item in shape_items],
            rows=[item["label"] for item in shape_items],
            results=sheet_results,
            cell_px=cell_px,
        )
        paths.append(labeled)
        for obj in list(bpy.data.objects):
            if obj.name.startswith("効果線_") or obj.name.startswith("効果線ガイド_"):
                bpy.data.objects.remove(obj, do_unlink=True)

    inventory = [
        {
            "effect_types": effect_items,
            "start_end_shapes": shape_items,
            "spacing_modes": _enum_items(effect_core._SPACING_MODE_ITEMS),
            "inout_apply": _enum_items(effect_core._INOUT_APPLY_ITEMS),
            "inout_range_modes": _enum_items(effect_core._INOUT_RANGE_MODE_ITEMS),
            "white_outline_black_directions": _enum_items(effect_core._WHITE_OUTLINE_BLACK_DIRECTION_ITEMS),
            "path_image_sources": _enum_items(effect_core._LINE_IMAGE_SOURCE_ITEMS),
            "path_image_draw_modes": _enum_items(effect_core._LINE_IMAGE_DRAW_MODE_ITEMS),
            "path_generated_shapes": _enum_items(effect_core._LINE_IMAGE_SHAPE_ITEMS),
        }
    ]
    return paths, results, inventory


def _write_json(name: str, payload: dict) -> Path:
    path = OUT_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_pattern_visual_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PatternVisualAudit.bmanga"))
        assert result == {"FINISHED"}, result

        from bmanga_dev_pattern_visual_audit.core.work import get_work
        from bmanga_dev_pattern_visual_audit.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        parent_key = page_stack_key(page)
        _hide_existing_scene_objects()
        line_image_path = _make_line_image(OUT_DIR / "balloon_line_image_sample.png")

        balloon_matrix, balloon_results, balloon_inventory = _make_balloon_matrix(
            context,
            page,
            parent_key,
            line_image_path,
        )
        balloon_extra, balloon_extra_results, balloon_extra_inventory = _make_balloon_extra_sheet(context, page, parent_key)
        balloon_json = _write_json(
            "balloon_patterns.json",
            {
                "images": [str(balloon_matrix), str(balloon_extra)],
                "inventory": balloon_inventory + balloon_extra_inventory,
                "results": balloon_results + balloon_extra_results,
            },
        )
        balloon_failures = [item for item in balloon_results + balloon_extra_results if not item.get("ok")]

        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod.unregister()
        mod = _load_addon()
        effect_images, effect_results, effect_inventory = _make_effect_sheets()
        effect_json = _write_json(
            "effect_line_patterns.json",
            {
                "images": [str(path) for path in effect_images],
                "inventory": effect_inventory,
                "results": effect_results,
            },
        )
        effect_failures = [item for item in effect_results if not item.get("ok")]

        summary = {
            "balloon_images": [str(balloon_matrix), str(balloon_extra)],
            "effect_line_images": [str(path) for path in effect_images],
            "balloon_json": str(balloon_json),
            "effect_line_json": str(effect_json),
            "balloon_result_count": len(balloon_results) + len(balloon_extra_results),
            "effect_line_result_count": len(effect_results),
            "balloon_failures": balloon_failures,
            "effect_line_failures": effect_failures,
        }
        summary_path = _write_json("summary.json", summary)
        print(f"BMANGA_BALLOON_EFFECT_PATTERN_VISUAL_OK summary={summary_path}")
        print(f"  balloon_images={summary['balloon_images']}")
        print(f"  effect_line_images={summary['effect_line_images']}")
        assert not balloon_failures, balloon_failures
        assert not effect_failures, effect_failures
        os._exit(0)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
    try:
        bpy.ops.wm.quit_blender()
    except Exception:
        pass
