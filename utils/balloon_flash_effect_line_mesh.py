"""Flash balloon line mesh generated from effect-line radial strokes."""

from __future__ import annotations

import math
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Optional

import bpy

from ..core import balloon as balloon_core
from ..operators import effect_line_gen
from . import balloon_line_mesh
from . import balloon_mesh_signature
from . import balloon_shapes
from . import effect_line_object
from . import free_transform
from . import object_preserve
from .geom import Rect, mm_to_m

BALLOON_FLASH_EFFECT_LINE_MESH_NAME_PREFIX = "balloon_flash_effect_line_mesh_"
_KIND_FLASH_EFFECT_LINE = "balloon_flash_effect_line_mesh"
_FLASH_LINE_Z_M = balloon_line_mesh.LINE_Z_OFFSET_M
_FLASH_EFFECT_MESH_SIGNATURE_PROP = "bmanga_balloon_flash_effect_mesh_signature"
_COLOR_ONLY_PARAM_FIELDS = {"line_color", "fill_color", "white_underlay_color"}


def _flash_effect_line_mesh_object_name(balloon_id: str) -> str:
    return f"{BALLOON_FLASH_EFFECT_LINE_MESH_NAME_PREFIX}{balloon_id}"


def _flash_effect_line_mesh_data_name(balloon_id: str) -> str:
    return f"{BALLOON_FLASH_EFFECT_LINE_MESH_NAME_PREFIX}{balloon_id}_mesh"


def _effect_params_signature(entry, line_style: str) -> dict:
    if line_style == "white_outline":
        line_width_mm = balloon_line_mesh.scaled_entry_width_mm(entry, "line_width_mm", 0.3)
        black_brush_mm, black_endpoint_pct = _line_width_and_endpoint_pct(
            line_width_mm,
            float(getattr(entry, "line_peak_width_pct", 100.0) or 100.0),
            float(getattr(entry, "line_valley_width_pct", 0.0) or 0.0),
        )
        return {
            "line_width_mm": line_width_mm,
            "line_peak_width_pct": float(getattr(entry, "line_peak_width_pct", 100.0) or 100.0),
            "line_valley_width_pct": float(getattr(entry, "line_valley_width_pct", 0.0) or 0.0),
            "black_brush_mm": black_brush_mm,
            "black_endpoint_pct": black_endpoint_pct,
            "flash_white_line_width_percent": float(
                getattr(entry, "flash_white_line_width_percent", 100.0) or 100.0
            ),
            "flash_white_line_valley_width_pct": float(
                getattr(entry, "flash_white_line_valley_width_pct", 0.0) or 0.0
            ),
            "flash_white_line_peak_width_pct": float(
                getattr(entry, "flash_white_line_peak_width_pct", 100.0) or 100.0
            ),
            "flash_white_outline_count": int(getattr(entry, "flash_white_outline_count", 5) or 5),
            "flash_white_outline_width_mm": float(
                getattr(entry, "flash_white_outline_width_mm", 10.0) or 10.0
            ),
            "flash_white_outline_spacing_mm": float(
                getattr(entry, "flash_white_outline_spacing_mm", 0.25) or 0.25
            ),
            "flash_white_outline_white_line_count": int(
                getattr(entry, "flash_white_outline_white_line_count", 24) or 24
            ),
            "flash_white_outline_black_line_count": int(
                getattr(entry, "flash_white_outline_black_line_count", 3) or 3
            ),
            "flash_white_outline_black_spacing_mm": float(
                getattr(entry, "flash_white_outline_black_spacing_mm", 0.25) or 0.25
            ),
            # 白抜き線の詳細 (v0.6.290): 変更で再構築されるよう署名に含める
            "white_outline_angle_deg": float(getattr(entry, "white_outline_angle_deg", 0.0) or 0.0),
            "white_outline_width_jitter_enabled": bool(getattr(entry, "white_outline_width_jitter_enabled", False)),
            "white_outline_width_min_percent": float(getattr(entry, "white_outline_width_min_percent", 100.0) or 0.0),
            "white_outline_length_jitter_enabled": bool(getattr(entry, "white_outline_length_jitter_enabled", False)),
            "white_outline_length_min_percent": float(getattr(entry, "white_outline_length_min_percent", 100.0) or 0.0),
            "white_outline_white_line_count_auto": bool(getattr(entry, "white_outline_white_line_count_auto", False)),
            "white_outline_black_line_count_auto": bool(getattr(entry, "white_outline_black_line_count_auto", False)),
            "white_outline_white_ratio_percent": float(getattr(entry, "white_outline_white_ratio_percent", 70.0) or 0.0),
            "white_outline_white_attenuation": float(getattr(entry, "white_outline_white_attenuation", 0.0) or 0.0),
            "white_outline_black_direction": str(getattr(entry, "white_outline_black_direction", "outside") or "outside"),
            "white_outline_black_width_scale_percent": float(getattr(entry, "white_outline_black_width_scale_percent", 100.0) or 0.0),
            "white_outline_black_length_scale_near_percent": float(getattr(entry, "white_outline_black_length_scale_near_percent", 100.0) or 0.0),
            "white_outline_black_length_scale_far_percent": float(getattr(entry, "white_outline_black_length_scale_far_percent", 100.0) or 0.0),
            "white_outline_black_attenuation": float(getattr(entry, "white_outline_black_attenuation", 0.0) or 0.0),
            # 入り抜き (ウニフラと同じ機構を白抜き線にも適用)
            "inout_apply": str(getattr(entry, "inout_apply", "brush_size") or "brush_size"),
            "inout_apply_brush_size": bool(getattr(entry, "inout_apply_brush_size", True)),
            "inout_apply_opacity": bool(getattr(entry, "inout_apply_opacity", False)),
            "in_percent": float(getattr(entry, "in_percent", 0.0) or 0.0),
            "out_percent": float(getattr(entry, "out_percent", 0.0) or 0.0),
            "in_start_percent": float(getattr(entry, "in_start_percent", 50.0) or 0.0),
            "out_start_percent": float(getattr(entry, "out_start_percent", 50.0) or 0.0),
            "in_easing_curve": str(getattr(entry, "in_easing_curve", "") or ""),
            "out_easing_curve": str(getattr(entry, "out_easing_curve", "") or ""),
            "inout_range_mode": str(getattr(entry, "inout_range_mode", "percent") or "percent"),
            "in_range_percent": float(getattr(entry, "in_range_percent", 100.0) or 0.0),
            "out_range_percent": float(getattr(entry, "out_range_percent", 100.0) or 0.0),
            "in_range_mm": float(getattr(entry, "in_range_mm", 10.0) or 0.0),
            "out_range_mm": float(getattr(entry, "out_range_mm", 10.0) or 0.0),
        }
    data = balloon_core.uni_flash_params_to_dict(entry)
    for field in _COLOR_ONLY_PARAM_FIELDS:
        data.pop(field, None)
    data["brush_size_mm"] = float(data.get("brush_size_mm", 0.3) or 0.3) * balloon_line_mesh.entry_line_width_scale(entry)
    return data


def _mesh_signature(entry, line_style: str) -> str:
    payload = {
        "version": 1,
        "kind": _KIND_FLASH_EFFECT_LINE,
        "line_style": line_style,
        "shape": balloon_mesh_signature.entry_shape(entry),
        "effect": _effect_params_signature(entry, line_style),
    }
    return balloon_mesh_signature.stable_json(payload)


def _mesh_has_material_index(mesh: bpy.types.Mesh, material_index: int) -> bool:
    for poly in getattr(mesh, "polygons", []) or []:
        if int(getattr(poly, "material_index", 0) or 0) == int(material_index):
            return True
    return False


def _mesh_has_expected_layers(mesh: bpy.types.Mesh, entry, line_style: str) -> bool:
    if line_style == "white_outline":
        return _mesh_has_material_index(mesh, 1)
    if line_style == "uni_flash" and bool(getattr(entry, "white_underlay_enabled", True)):
        try:
            if abs(float(getattr(entry, "white_underlay_width_percent", 100.0) or 0.0)) <= 1.0e-6:
                return True
        except Exception:  # noqa: BLE001
            pass
        return _mesh_has_material_index(mesh, 2)
    return True


def has_expected_layers(obj: bpy.types.Object | None, entry) -> bool:
    if obj is None or getattr(obj, "type", "") != "MESH":
        return False
    mesh = getattr(obj, "data", None)
    if mesh is None or not isinstance(mesh, bpy.types.Mesh):
        return False
    line_style = balloon_shapes.normalize_line_style(str(getattr(entry, "line_style", "") or ""))
    return _mesh_has_expected_layers(mesh, entry, line_style)


def _cached_mesh_object(
    *,
    scene,
    entry,
    body_object: bpy.types.Object,
    line_material: bpy.types.Material,
    white_material: bpy.types.Material,
    underlay_material: bpy.types.Material,
    mask_info,
    balloon_id: str,
    signature: str,
) -> Optional[bpy.types.Object]:
    obj_name = _flash_effect_line_mesh_object_name(balloon_id)
    mesh_name = _flash_effect_line_mesh_data_name(balloon_id)
    obj = bpy.data.objects.get(obj_name)
    if obj is not None and object_preserve.is_preserved(obj):
        return None
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None and obj is not None and getattr(obj, "type", "") == "MESH":
        mesh = getattr(obj, "data", None)
    if mesh is None or not isinstance(mesh, bpy.types.Mesh):
        return None
    stored = ""
    if obj is not None and getattr(obj, "type", "") == "MESH":
        stored = str(obj.get(_FLASH_EFFECT_MESH_SIGNATURE_PROP, "") or "")
    if not stored:
        stored = str(mesh.get(_FLASH_EFFECT_MESH_SIGNATURE_PROP, "") or "")
    if stored != signature:
        return None
    line_style = balloon_shapes.normalize_line_style(str(getattr(entry, "line_style", "") or ""))
    if not _mesh_has_expected_layers(mesh, entry, line_style):
        return None
    _set_mesh_materials(mesh, (line_material, white_material, underlay_material))
    cached = balloon_line_mesh._attach_band_mesh_object(
        obj_name=obj_name,
        mesh=mesh,
        material=line_material,
        body_object=body_object,
        scene=scene,
        kind=_KIND_FLASH_EFFECT_LINE,
        balloon_id=balloon_id,
        visible=bool(getattr(entry, "visible", True)),
        mask_info=mask_info,
    )
    cached[_FLASH_EFFECT_MESH_SIGNATURE_PROP] = signature
    mesh[_FLASH_EFFECT_MESH_SIGNATURE_PROP] = signature
    return cached


def _discard_mesh_for_rebuild(balloon_id: str) -> None:
    obj = bpy.data.objects.get(_flash_effect_line_mesh_object_name(balloon_id))
    if obj is not None and object_preserve.is_preserved(obj):
        return
    remove_balloon_flash_effect_line_mesh(balloon_id)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _line_width_and_endpoint_pct(
    base_width_mm: float,
    middle_pct: float,
    endpoint_pct: float,
) -> tuple[float, float]:
    middle = max(0.0, float(middle_pct))
    width = max(0.0, float(base_width_mm)) * middle / 100.0
    if width <= 1.0e-9 or middle <= 1.0e-9:
        return 0.0, 0.0
    return width, _clamp(float(endpoint_pct) / middle * 100.0, 0.0, 100.0)


def _base_rect(entry) -> tuple[tuple[float, float], float, float]:
    width = max(0.001, float(getattr(entry, "width_mm", 0.0) or 0.0))
    height = max(0.001, float(getattr(entry, "height_mm", 0.0) or 0.0))
    rect = Rect(0.0, 0.0, width, height)
    points = balloon_shapes.outline_for_entry(entry, rect) or balloon_shapes.outline_for_shape("ellipse", rect)
    min_x = min(float(x) for x, _y in points)
    max_x = max(float(x) for x, _y in points)
    min_y = min(float(y) for _x, y in points)
    max_y = max(float(y) for _x, y in points)
    center = ((min_x + max_x) * 0.5, (min_y + max_y) * 0.5)
    return center, max(0.001, (max_x - min_x) * 0.5), max(0.001, (max_y - min_y) * 0.5)


def _default_easing_curve() -> str:
    return "0.0000,0.0000;1.0000,1.0000"


def _focus_params(entry) -> SimpleNamespace:
    data = balloon_core.uni_flash_params_to_dict(entry)
    data["effect_type"] = "focus"
    data["brush_size_mm"] = max(
        0.01,
        float(data.get("brush_size_mm", 0.3) or 0.3) * balloon_line_mesh.entry_line_width_scale(entry),
    )
    data["spacing_angle_deg"] = max(0.1, float(data.get("spacing_angle_deg", 5.0) or 5.0))
    data["spacing_distance_mm"] = max(0.01, float(data.get("spacing_distance_mm", 1.0) or 1.0))
    data["max_line_count"] = max(1, int(data.get("max_line_count", 1000) or 1000))
    data.setdefault("in_easing_curve", _default_easing_curve())
    data.setdefault("out_easing_curve", _default_easing_curve())
    return SimpleNamespace(**data)


def _white_outline_params(entry, *, black_brush_mm: float) -> SimpleNamespace:
    white_brush, white_endpoint = _line_width_and_endpoint_pct(
        black_brush_mm,
        float(getattr(entry, "flash_white_line_peak_width_pct", 100.0) or 100.0),
        float(getattr(entry, "flash_white_line_valley_width_pct", 0.0) or 0.0),
    )
    white_width_scale = max(0.0, float(getattr(entry, "flash_white_line_width_percent", 100.0) or 100.0)) / 100.0
    white_brush = max(0.01, white_brush * white_width_scale)
    spacing = max(0.0, float(getattr(entry, "flash_white_outline_spacing_mm", 0.25) or 0.25))
    # 詳細フィールドはフキダシ側にも同名で持ち、既定値 = 従来の固定値
    # (既存フキダシの見た目を変えない)
    return SimpleNamespace(
        effect_type="white_outline",
        rotation_deg=0.0,
        start_shape="ellipse",
        end_shape="ellipse",
        start_rounded_corner_enabled=False,
        end_rounded_corner_enabled=False,
        white_outline_count=max(1, int(getattr(entry, "flash_white_outline_count", 5) or 5)),
        white_outline_spacing_mm=spacing,
        white_outline_white_line_count_auto=bool(getattr(entry, "white_outline_white_line_count_auto", False)),
        white_outline_white_line_count=max(1, int(getattr(entry, "flash_white_outline_white_line_count", 24) or 24)),
        white_outline_width_mm=max(0.01, float(getattr(entry, "flash_white_outline_width_mm", 10.0) or 10.0)),
        white_outline_width_jitter_enabled=bool(getattr(entry, "white_outline_width_jitter_enabled", False)),
        white_outline_width_min_percent=float(getattr(entry, "white_outline_width_min_percent", 100.0) or 0.0),
        white_outline_length_jitter_enabled=bool(getattr(entry, "white_outline_length_jitter_enabled", False)),
        white_outline_length_min_percent=float(getattr(entry, "white_outline_length_min_percent", 100.0) or 0.0),
        white_outline_white_ratio_percent=float(getattr(entry, "white_outline_white_ratio_percent", 70.0) or 0.0),
        white_outline_white_brush_mm=white_brush,
        white_outline_white_attenuation=float(getattr(entry, "white_outline_white_attenuation", 0.0) or 0.0),
        white_outline_white_in_percent=white_endpoint,
        white_outline_white_out_percent=white_endpoint,
        white_outline_white_inout_range_mode="percent",
        white_outline_white_in_range_percent=50.0,
        white_outline_white_out_range_percent=50.0,
        white_outline_white_in_range_mm=10.0,
        white_outline_white_out_range_mm=10.0,
        white_outline_black_line_count_auto=bool(getattr(entry, "white_outline_black_line_count_auto", False)),
        white_outline_black_line_count=max(1, int(getattr(entry, "flash_white_outline_black_line_count", 3) or 3)),
        white_outline_black_direction=str(getattr(entry, "white_outline_black_direction", "outside") or "outside"),
        white_outline_black_brush_mm=max(0.01, black_brush_mm),
        white_outline_black_spacing_mm=max(0.0, float(getattr(entry, "flash_white_outline_black_spacing_mm", spacing) or spacing)),
        white_outline_black_width_scale_percent=float(getattr(entry, "white_outline_black_width_scale_percent", 100.0) or 0.0),
        white_outline_black_length_scale_near_percent=float(getattr(entry, "white_outline_black_length_scale_near_percent", 100.0) or 0.0),
        white_outline_black_length_scale_far_percent=float(getattr(entry, "white_outline_black_length_scale_far_percent", 100.0) or 0.0),
        white_outline_black_attenuation=float(getattr(entry, "white_outline_black_attenuation", 0.0) or 0.0),
        white_outline_angle_deg=float(getattr(entry, "white_outline_angle_deg", 0.0) or 0.0),
    )


def _stroke_distances(points_xyz: Sequence[tuple[float, float, float]]) -> list[float]:
    values = [0.0]
    for index in range(1, len(points_xyz)):
        ax, ay, az = points_xyz[index - 1]
        bx, by, bz = points_xyz[index]
        values.append(values[-1] + math.sqrt((bx - ax) ** 2 + (by - ay) ** 2 + (bz - az) ** 2))
    return values


def _middle_profile(distance: float, total: float, endpoint_factor: float) -> float:
    if total <= 1.0e-12:
        return endpoint_factor
    half = total * 0.5
    if distance <= half:
        return endpoint_factor + (1.0 - endpoint_factor) * (distance / max(half, 1.0e-12))
    return endpoint_factor + (1.0 - endpoint_factor) * ((total - distance) / max(half, 1.0e-12))


def _white_underlay_strokes(entry, line_strokes, black_brush_mm: float):
    if not bool(getattr(entry, "flash_white_line_enabled", True)):
        return []
    white_mid_width, white_endpoint_pct = _line_width_and_endpoint_pct(
        black_brush_mm,
        float(getattr(entry, "flash_white_line_peak_width_pct", 100.0) or 100.0),
        float(getattr(entry, "flash_white_line_valley_width_pct", 0.0) or 0.0),
    )
    width_scale = max(-300.0, min(300.0, float(getattr(entry, "flash_white_line_width_percent", 100.0) or 100.0))) / 100.0
    underlay_radius = max(0.0, (white_mid_width * abs(width_scale)) * 0.001)
    if underlay_radius <= 1.0e-12:
        return []
    endpoint_factor = _clamp(white_endpoint_pct / 100.0, 0.0, 1.0)
    side = 1.0 if width_scale >= 0.0 else -1.0
    out = []
    for stroke in line_strokes:
        if str(getattr(stroke, "role", "") or "line") != "line" or bool(getattr(stroke, "cyclic", False)):
            continue
        points = list(getattr(stroke, "points_xyz", None) or [])
        if len(points) < 2:
            continue
        distances = _stroke_distances(points)
        total = distances[-1]
        radii = [underlay_radius * _middle_profile(value, total, endpoint_factor) for value in distances]
        out.append(
            effect_line_gen.EffectLineStroke(
                points_xyz=points,
                radius=underlay_radius,
                cyclic=False,
                radii=radii,
                opacities=getattr(stroke, "opacities", None),
                role="underlay",
                curve_type=getattr(stroke, "curve_type", "POLY"),
                bezier_smooth=bool(getattr(stroke, "bezier_smooth", False)),
                density_end=float(getattr(stroke, "density_end", 1.0) or 1.0),
                side=side,
            )
        )
    return out


def _transform_stroke_to_local(entry, stroke) -> effect_line_gen.EffectLineStroke:
    ox_mm, oy_mm = balloon_line_mesh._entry_local_offset_mm(entry)
    points = []
    for x_m, y_m, z_m in list(getattr(stroke, "points_xyz", None) or []):
        x_mm = float(x_m) * 1000.0
        y_mm = float(y_m) * 1000.0
        x_mm, y_mm = free_transform.transform_entry_local_point(entry, x_mm, y_mm)
        points.append((mm_to_m(x_mm + ox_mm), mm_to_m(y_mm + oy_mm), _FLASH_LINE_Z_M + float(z_m)))
    return effect_line_gen.EffectLineStroke(
        points_xyz=points,
        radius=float(getattr(stroke, "radius", 0.0) or 0.0),
        cyclic=bool(getattr(stroke, "cyclic", False)),
        radii=list(getattr(stroke, "radii", []) or []) or None,
        opacities=list(getattr(stroke, "opacities", []) or []) or None,
        role=str(getattr(stroke, "role", "") or "line"),
        curve_type=str(getattr(stroke, "curve_type", "POLY") or "POLY"),
        bezier_smooth=bool(getattr(stroke, "bezier_smooth", False)),
        density_end=float(getattr(stroke, "density_end", 1.0) or 1.0),
        side=float(getattr(stroke, "side", 0.0) or 0.0),
    )


def generate_flash_strokes_rect_local(entry):
    """ウニフラ/白抜き線のストローク列を rect ローカル座標 (m 単位) で返す.

    ビューポートの Mesh 焼き込みと、ページ出力 (PIL 描画) の両方が
    同じ線群を使うための共通入口。free_transform やフキダシ原点への
    平行移動は含まない。
    """
    center, rx, ry = _base_rect(entry)
    line_style = balloon_shapes.normalize_line_style(str(getattr(entry, "line_style", "") or ""))
    if line_style == "white_outline":
        line_width_mm = balloon_line_mesh.scaled_entry_width_mm(entry, "line_width_mm", 0.3)
        black_brush_mm, _black_endpoint_pct = _line_width_and_endpoint_pct(
            line_width_mm,
            float(getattr(entry, "line_peak_width_pct", 100.0) or 100.0),
            float(getattr(entry, "line_valley_width_pct", 0.0) or 0.0),
        )
        if black_brush_mm <= 1.0e-9:
            return []
        params = _white_outline_params(entry, black_brush_mm=black_brush_mm)
        strokes = effect_line_gen.generate_white_outline_strokes(
            params,
            center,
            rx,
            ry,
            seed=int(getattr(getattr(entry, "shape_params", None), "shape_seed", 0) or 0),
        )
        # 入り抜き: ウニフラと同じ機構 (適用先/入り抜き%/始点%/カーブ/範囲) を
        # 白抜き線の各線にも適用する。既定 (入り0%・抜き0%) では何もしないため
        # 既存フキダシの見た目は変わらない
        try:
            inout = _focus_params(entry)
            if (
                float(getattr(inout, "in_percent", 0.0) or 0.0) > 1.0e-6
                or float(getattr(inout, "out_percent", 0.0) or 0.0) > 1.0e-6
            ):
                strokes = effect_line_gen._apply_inout_profile(
                    strokes,
                    inout,
                    roles=("line", "white_outline_white", "white_outline_black"),
                )
        except Exception:  # noqa: BLE001
            pass
    else:
        params = _focus_params(entry)
        if float(getattr(params, "brush_size_mm", 0.0) or 0.0) <= 1.0e-9:
            return []
        seed = int(getattr(getattr(entry, "shape_params", None), "shape_seed", 0) or 0)
        strokes = []
        if bool(getattr(params, "fill_base_shape", False)):
            fill = effect_line_gen.generate_end_shape_fill_stroke(
                params,
                center,
                rx,
                ry,
                seed=seed,
            )
            if fill is not None:
                strokes.append(fill)
        flash_strokes = effect_line_gen.generate_strokes(
            params,
            center_xy_mm=center,
            radius_xy_mm=(rx, ry),
            seed=seed,
        )
        # ウニフラの「ズラし量」: 線の終点を交互に出し入れする
        # (フキダシ側は params.effect_type を focus に固定しているため、
        #  生成後にここで適用する)
        offset_pct = float(getattr(entry, "uni_flash_offset_percent", 0.0) or 0.0)
        if line_style == "uni_flash" and offset_pct > 1.0e-6:
            flash_strokes = effect_line_gen.apply_uni_flash_offset(flash_strokes, center, offset_pct)
        strokes.extend(flash_strokes)
    return strokes


def _generated_strokes(entry):
    return [
        _transform_stroke_to_local(entry, stroke)
        for stroke in generate_flash_strokes_rect_local(entry)
    ]


def _set_mesh_materials(mesh: bpy.types.Mesh, materials: Sequence[bpy.types.Material | None]) -> None:
    for index, mat in enumerate(materials):
        if mat is not None:
            if len(mesh.materials) <= index:
                mesh.materials.append(mat)
            elif mesh.materials[index] is not mat:
                mesh.materials[index] = mat


def ensure_balloon_flash_effect_line_mesh(
    *,
    scene,
    work,
    page,
    entry,
    body_object: bpy.types.Object,
    line_material: bpy.types.Material,
    white_material: bpy.types.Material,
    underlay_material: bpy.types.Material,
    mask_info=None,
) -> Optional[bpy.types.Object]:
    del work, page
    balloon_id = str(getattr(entry, "id", "") or "")
    shape_norm = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "") or ""))
    line_style = balloon_shapes.normalize_line_style(str(getattr(entry, "line_style", "") or ""))
    if (
        not balloon_id
        or shape_norm == "none"
        or not balloon_shapes.is_flash_line_style(line_style)
        or line_style == "none"
        or (
            float(getattr(entry, "brush_size_mm" if line_style == "uni_flash" else "line_width_mm", 0.0) or 0.0)
            <= 1.0e-9
        )
    ):
        remove_balloon_flash_effect_line_mesh(balloon_id)
        return None
    signature = _mesh_signature(entry, line_style)
    cached = _cached_mesh_object(
        scene=scene,
        entry=entry,
        body_object=body_object,
        line_material=line_material,
        white_material=white_material,
        underlay_material=underlay_material,
        mask_info=mask_info,
        balloon_id=balloon_id,
        signature=signature,
    )
    if cached is not None:
        return cached
    _discard_mesh_for_rebuild(balloon_id)
    strokes = _generated_strokes(entry)
    if not strokes:
        remove_balloon_flash_effect_line_mesh(balloon_id)
        return None
    mesh_name = _flash_effect_line_mesh_data_name(balloon_id)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    _set_mesh_materials(mesh, (line_material, white_material, underlay_material))
    effect_line_object._rebuild_effect_display_mesh(mesh, strokes)
    mesh[_FLASH_EFFECT_MESH_SIGNATURE_PROP] = signature
    obj = balloon_line_mesh._attach_band_mesh_object(
        obj_name=_flash_effect_line_mesh_object_name(balloon_id),
        mesh=mesh,
        material=line_material,
        body_object=body_object,
        scene=scene,
        kind=_KIND_FLASH_EFFECT_LINE,
        balloon_id=balloon_id,
        visible=bool(getattr(entry, "visible", True)),
        mask_info=mask_info,
    )
    obj[_FLASH_EFFECT_MESH_SIGNATURE_PROP] = signature
    return obj


def remove_balloon_flash_effect_line_mesh(balloon_id: str) -> None:
    if not balloon_id:
        return
    obj_name = _flash_effect_line_mesh_object_name(balloon_id)
    obj = bpy.data.objects.get(obj_name)
    if obj is None or object_preserve.is_preserved(obj):
        return
    data = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        return
    if data is not None and getattr(data, "users", 0) == 0:
        try:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
        except Exception:  # noqa: BLE001
            pass


def cleanup_orphan_flash_effect_line_meshes(valid_balloon_ids: set[str]) -> int:
    removed = 0
    for obj in list(bpy.data.objects):
        if object_preserve.is_preserved(obj):
            continue
        kind = str(obj.get(balloon_line_mesh.PROP_BALLOON_LINE_MESH_KIND, "") or "")
        if kind != _KIND_FLASH_EFFECT_LINE:
            continue
        owner_id = str(obj.get(balloon_line_mesh.PROP_BALLOON_LINE_MESH_OWNER_ID, "") or "")
        if owner_id and owner_id in valid_balloon_ids:
            continue
        object_preserve.preserve_object(obj, "作品データにないフキダシ集中線メッシュを保持")
        removed += 1
    return removed
