"""フキダシ本体の輪郭に沿ったパス線 (スタンプ/リボン) メッシュ.

効果線のパス線 (utils/effect_line_path.py) と同じ考え方を、フキダシ本体の
閉じた輪郭ポリライン (utils/balloon_line_mesh.py が主線メッシュ用に持つ
サンプル点列) へ適用する。フキダシには「基準パス」の概念が無く、本体の
輪郭そのものが常にパスになる。

パス線が有効 (内容が「生成形状」、または画像が読み込めた) の間は、呼び出し側
(utils/balloon_curve_object.py) が主線の帯メッシュ生成を止め、代わりにここで
生成するメッシュだけを表示する。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import bpy

from . import balloon_line_mesh, effect_line_path, log, path_content
from .geom import mm_to_m

_logger = log.get_logger(__name__)

BALLOON_PATH_LINE_MESH_NAME_PREFIX = "balloon_path_line_mesh_"
BALLOON_PATH_LINE_MATERIAL_PREFIX = "BManga_BalloonPathLine_"
PROP_BALLOON_LINE_MESH_KIND = balloon_line_mesh.PROP_BALLOON_LINE_MESH_KIND
_KIND_PATH_LINE = "balloon_path_line_mesh"

_MAX_STAMP_FACES = 20000

_COLOR_FIELDS = {
    "color_field": "line_image_color",
    "start_field": "line_image_inout_start_color",
    "end_field": "line_image_inout_end_color",
    "color_enabled": "line_image_inout_color_enabled",
    "opacity_enabled": "line_image_inout_opacity_enabled",
}


def _mesh_object_name(balloon_id: str) -> str:
    return f"{BALLOON_PATH_LINE_MESH_NAME_PREFIX}{balloon_id}"


def _mesh_data_name(balloon_id: str) -> str:
    return f"{BALLOON_PATH_LINE_MESH_NAME_PREFIX}{balloon_id}_mesh"


def _image_path(entry) -> Optional[Path]:
    raw = str(getattr(entry, "line_image_path", "") or "").strip()
    if not raw:
        return None
    path = Path(bpy.path.abspath(raw))
    return path if path.is_file() else None


def line_image_active(entry) -> bool:
    """パス線 (スタンプ/リボン) を主線の帯メッシュの代わりに表示すべきかどうか."""

    if entry is None:
        return False
    # 明示トグル (2026-07-24 追加)。OFF の間はパス線を一切有効化しない。これが
    # 無かった頃は line_image_path が残っているだけでパス線が主線を置き換えたため、
    # 線種「画像」→「実線」へ戻すと主線が出ない不具合になっていた (共有プロパティ)。
    if not bool(getattr(entry, "path_line_enabled", False)):
        return False
    if str(getattr(entry, "line_image_source", "image") or "image") == "shape":
        return True
    return _image_path(entry) is not None


def _load_image(entry) -> Optional[bpy.types.Image]:
    if str(getattr(entry, "line_image_source", "image") or "image") != "image":
        return None
    path = _image_path(entry)
    if path is None:
        return None
    try:
        image = bpy.data.images.load(str(path), check_existing=True)
        image.colorspace_settings.name = "sRGB"
        return image
    except Exception:  # noqa: BLE001
        _logger.warning("balloon path line image load failed: %s", path)
        return None


def _body_loop_points_m(entry, body_object) -> list[tuple[float, float, float]]:
    """フキダシ本体カーブの閉じた輪郭サンプル点 (x, y, radius) を局所m単位で返す."""

    return balloon_line_mesh._body_samples_for_line_mesh(entry, body_object)


def _closed_lengths(points: list[tuple[float, float, float]]) -> tuple[list[float], float]:
    """閉ループの累積弧長 (最後の点から先頭点へ戻る辺を含む n+1 要素) を返す."""

    n = len(points)
    cumulative = [0.0]
    for i in range(n):
        ax, ay, _ar = points[i]
        bx, by, _br = points[(i + 1) % n]
        cumulative.append(cumulative[-1] + math.hypot(bx - ax, by - ay))
    return cumulative, cumulative[-1]


def _point_at_distance(
    points: list[tuple[float, float, float]],
    cumulative: list[float],
    total: float,
    distance: float,
) -> tuple[float, float, float]:
    """閉ループ上の弧長位置から (x, y, 進行方向の角度) を求める."""

    n = len(points)
    if n < 2 or total <= 1.0e-9:
        x, y, _r = points[0] if points else (0.0, 0.0, 0.0)
        return x, y, 0.0
    distance = distance % total
    for i in range(n):
        seg_start = cumulative[i]
        seg_end = cumulative[i + 1]
        if distance <= seg_end or i == n - 1:
            ax, ay, _ar = points[i]
            bx, by, _br = points[(i + 1) % n]
            span = max(1.0e-9, seg_end - seg_start)
            k = max(0.0, min(1.0, (distance - seg_start) / span))
            x = ax + (bx - ax) * k
            y = ay + (by - ay) * k
            angle = math.atan2(by - ay, bx - ax)
            return x, y, angle
    ax, ay, _ar = points[-1]
    bx, by, _br = points[0]
    return bx, by, math.atan2(by - ay, bx - ax)


def _stamp_angle(entry, path_angle: float) -> float:
    base = math.radians(float(getattr(entry, "line_image_angle_deg", 0.0) or 0.0))
    mode = str(getattr(entry, "line_image_stamp_angle_mode", "line") or "line")
    if mode == "line":
        return path_angle + base
    if mode == "object":
        obj = bpy.data.objects.get(str(getattr(entry, "line_image_stamp_angle_object_name", "") or ""))
        if obj is not None:
            return float(getattr(obj.rotation_euler, "z", 0.0) or 0.0) + base
    return base


def _append_stamp_mesh(verts, faces, uvs, colors, points, entry, *, z: float, max_faces: int) -> None:
    cumulative, total = _closed_lengths(points)
    if total <= 1.0e-9:
        return
    brush = mm_to_m(max(0.1, float(getattr(entry, "line_image_brush_size_mm", 3.0) or 3.0)))
    aspect = max(0.01, float(getattr(entry, "line_image_aspect_ratio", 1.0) or 1.0))
    spacing = max(
        mm_to_m(0.1),
        brush * max(1.0, float(getattr(entry, "line_image_spacing_percent", 100.0) or 100.0)) / 100.0,
    )
    source = str(getattr(entry, "line_image_source", "image") or "image")
    corners = [(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)]
    shape = path_content.unit_shape_points(
        str(getattr(entry, "line_image_shape_kind", "circle") or "circle"),
        sides=int(getattr(entry, "line_image_shape_sides", 6) or 6),
    )
    face_uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    distance = 0.0
    while distance < total:
        if len(faces) >= max_faces:
            break
        x, y, path_angle = _point_at_distance(points, cumulative, total, distance)
        profile = path_content.inout_profile_value(entry, distance, total)
        size_factor = path_content.size_factor(entry, profile, "line_image_inout_size_enabled")
        rgba = path_content.color_for_path_distance(entry, distance, total, **_COLOR_FIELDS)
        angle = _stamp_angle(entry, path_angle)
        ca, sa = math.cos(angle), math.sin(angle)
        base_index = len(verts)
        base_shape = shape if source == "shape" else corners
        for ux, uy in base_shape:
            lx = ux * brush * aspect * size_factor
            ly = uy * brush * size_factor
            verts.append((x + lx * ca - ly * sa, y + lx * sa + ly * ca, z))
            colors.append(rgba)
        faces.append(tuple(range(base_index, base_index + len(base_shape))))
        uvs.extend(face_uvs if source != "shape" else [(0.0, 0.0)] * len(base_shape))
        distance += spacing


def _ribbon_tangent(points: list[tuple[float, float, float]], index: int) -> tuple[float, float]:
    n = len(points)
    ax, ay, _ar = points[(index - 1) % n]
    bx, by, _br = points[(index + 1) % n]
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    return (1.0, 0.0) if length <= 1.0e-9 else (dx / length, dy / length)


def _append_ribbon_mesh(verts, faces, uvs, colors, points, entry, *, z: float) -> None:
    n = len(points)
    if n < 3:
        return
    cumulative, total = _closed_lengths(points)
    if total <= 1.0e-9:
        return
    brush = mm_to_m(max(0.1, float(getattr(entry, "line_image_brush_size_mm", 3.0) or 3.0)))
    aspect = max(0.01, float(getattr(entry, "line_image_aspect_ratio", 1.0) or 1.0))
    spacing = max(
        mm_to_m(0.1),
        brush * aspect * max(1.0, float(getattr(entry, "line_image_spacing_percent", 100.0) or 100.0)) / 100.0,
    )
    stretch = str(getattr(entry, "line_image_ribbon_repeat_mode", "repeat") or "repeat") == "stretch"
    angle = math.radians(float(getattr(entry, "line_image_angle_deg", 0.0) or 0.0))
    base_index = len(verts)
    for i in range(n):
        x, y, _radius = points[i]
        distance = cumulative[i]
        profile = path_content.inout_profile_value(entry, distance, total)
        profile_factor = path_content.size_factor(entry, profile, "line_image_inout_size_enabled")
        rgba = path_content.color_for_path_distance(entry, distance, total, **_COLOR_FIELDS)
        tx, ty = _ribbon_tangent(points, i)
        width = brush * profile_factor
        nx, ny = -ty, tx
        verts.append((x + nx * width * 0.5, y + ny * width * 0.5, z))
        verts.append((x - nx * width * 0.5, y - ny * width * 0.5, z))
        colors.extend([rgba, rgba])
        u = distance / total if stretch else distance / spacing
        uvs.append(effect_line_path._uv_rotated(u, 1.0, angle, repeat=not stretch))
        uvs.append(effect_line_path._uv_rotated(u, 0.0, angle, repeat=not stretch))
    # 閉ループ: 最後の頂点対から先頭の頂点対へ戻る面も作り、継ぎ目を残さない。
    for i in range(n):
        start = base_index + i * 2
        nxt = base_index + ((i + 1) % n) * 2
        faces.append((start, start + 1, nxt + 1, nxt))


def _build_mesh(mesh: bpy.types.Mesh, entry, points: list[tuple[float, float, float]]) -> bool:
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    uvs: list[tuple[float, float]] = []
    colors: list[tuple[float, float, float, float]] = []
    z = balloon_line_mesh.LINE_Z_OFFSET_M
    source = str(getattr(entry, "line_image_source", "image") or "image")
    mode = str(getattr(entry, "line_image_draw_mode", "ribbon") or "ribbon") if source == "image" else "stamp"
    if mode == "stamp":
        _append_stamp_mesh(verts, faces, uvs, colors, points, entry, z=z, max_faces=_MAX_STAMP_FACES)
    else:
        _append_ribbon_mesh(verts, faces, uvs, colors, points, entry, z=z)
    mesh.clear_geometry()
    if verts and faces:
        mesh.from_pydata(verts, [], faces)
    mesh.update()
    uv_layer = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
    if len(uvs) == len(verts):
        for poly in mesh.polygons:
            for loop_index in poly.loop_indices:
                vertex_index = mesh.loops[loop_index].vertex_index
                if 0 <= vertex_index < len(uvs):
                    uv_layer.data[loop_index].uv = uvs[vertex_index]
    path_content.write_color_attribute(mesh, colors)
    mesh.update()
    return bool(faces)


def ensure_balloon_path_line_mesh(
    *,
    scene,
    work,
    page,
    entry,
    body_object: bpy.types.Object,
    mask_info=None,
) -> Optional[bpy.types.Object]:
    """フキダシのパス線メッシュを生成・更新する。対象外なら既存メッシュを撤去する。"""

    del work, page
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id or body_object is None:
        return None
    if not line_image_active(entry):
        remove_balloon_path_line_mesh(balloon_id)
        return None
    points = _body_loop_points_m(entry, body_object)
    if len(points) < 3:
        remove_balloon_path_line_mesh(balloon_id)
        return None
    mesh_name = _mesh_data_name(balloon_id)
    mesh = bpy.data.meshes.get(mesh_name) or bpy.data.meshes.new(mesh_name)
    if not _build_mesh(mesh, entry, points):
        remove_balloon_path_line_mesh(balloon_id)
        return None
    image = _load_image(entry)
    source = str(getattr(entry, "line_image_source", "image") or "image")
    material = path_content.ensure_material(
        f"{BALLOON_PATH_LINE_MATERIAL_PREFIX}{balloon_id}",
        image,
        float(getattr(entry, "opacity", 100.0) or 100.0),
        mask_info=mask_info,
        fallback_alpha=1.0 if source == "shape" else 0.0,
    )
    return balloon_line_mesh._attach_band_mesh_object(
        obj_name=_mesh_object_name(balloon_id),
        mesh=mesh,
        material=material,
        body_object=body_object,
        scene=scene,
        kind=_KIND_PATH_LINE,
        balloon_id=balloon_id,
        visible=bool(getattr(entry, "visible", True)),
        mask_info=mask_info,
        geometry_sig=None,
    )


def remove_balloon_path_line_mesh(balloon_id: str) -> None:
    if not balloon_id:
        return
    balloon_line_mesh._remove_named_band_mesh(_mesh_object_name(balloon_id))


__all__ = [
    "ensure_balloon_path_line_mesh",
    "line_image_active",
    "remove_balloon_path_line_mesh",
]
