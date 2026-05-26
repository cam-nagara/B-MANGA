"""フキダシ主線をメッシュバンド方式で直接構築する.

雲・モフモフ・トゲ曲線のような滑らか形状で、本体カーブの中心線サンプル点列から
外周と内周の点ペアを算出し、四角形ストリップのメッシュを直接構築する。

鋭い谷 (本体カーブの handles が連続しない anchor) のところでオフセットが
深く突き出さないよう、サンプル点列の鋭角を半径 R の小さな円弧で事前に丸めて
からオフセットする (本体カーブ自体は変更しない)。

線幅は本体 Bezier の per-point radius を補間で反映する。
コマ枠 (coma_border) と同じ作り方で、Curve+FillCurve ではなく Mesh 直接構築。
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

import bpy

from . import balloon_shapes
from . import log
from . import object_naming as on

_logger = log.get_logger(__name__)

BALLOON_LINE_MESH_NAME_PREFIX = "balloon_line_mesh_"
PROP_BALLOON_LINE_MESH_KIND = "bname_balloon_line_mesh_kind"
PROP_BALLOON_LINE_MESH_OWNER_ID = "bname_balloon_line_mesh_owner_id"

SAMPLES_PER_SEGMENT = 24
SHARP_THRESHOLD_RAD = math.radians(30.0)
ARC_STEP_DEG = 12.0
LINE_Z_OFFSET_M = 0.00010

# 本方式 (メッシュ直接構築) を使う形状。それ以外は既存のノードグループ経路。
MESH_BAND_LINE_SHAPES = {"cloud", "fluffy", "thorn-curve"}


def is_mesh_band_shape(entry) -> bool:
    shape = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect"))
    return shape in MESH_BAND_LINE_SHAPES


def _cubic_bezier_point(p0, p1, p2, p3, t):
    u = 1.0 - t
    return (
        u * u * u * p0[0] + 3.0 * u * u * t * p1[0] + 3.0 * u * t * t * p2[0] + t * t * t * p3[0],
        u * u * u * p0[1] + 3.0 * u * u * t * p1[1] + 3.0 * u * t * t * p2[1] + t * t * t * p3[1],
    )


def _sample_body_bezier(spline, samples_per_segment: int) -> list[tuple[float, float, float]]:
    """Bezier 閉スプラインをサンプリングして、(x, y, per_point_radius) のタプル列を返す."""
    samples: list[tuple[float, float, float]] = []
    if str(getattr(spline, "type", "") or "") != "BEZIER":
        return samples
    if not bool(getattr(spline, "use_cyclic_u", False)):
        return samples
    points = list(getattr(spline, "bezier_points", []) or [])
    n = len(points)
    if n < 3:
        return samples
    steps = max(4, int(samples_per_segment))
    for i in range(n):
        a = points[i]
        b = points[(i + 1) % n]
        p0 = (float(a.co.x), float(a.co.y))
        p1 = (float(a.handle_right.x), float(a.handle_right.y))
        p2 = (float(b.handle_left.x), float(b.handle_left.y))
        p3 = (float(b.co.x), float(b.co.y))
        r0 = max(0.0, float(getattr(a, "radius", 1.0) or 0.0))
        r1 = max(0.0, float(getattr(b, "radius", 1.0) or 0.0))
        for step in range(steps):
            t = step / steps
            pos = _cubic_bezier_point(p0, p1, p2, p3, t)
            radius = r0 * (1.0 - t) + r1 * t
            samples.append((pos[0], pos[1], radius))
    return samples


def _smooth_sharp_corners(
    samples: Sequence[tuple[float, float, float]],
    *,
    smooth_radius_m: float,
    sharp_threshold_rad: float,
    arc_step_deg: float,
) -> list[tuple[float, float, float]]:
    """サンプル点列の鋭角を、半径 smooth_radius_m のフィレット円弧で置き換える.

    各円弧点の radius は元の鋭角点の radius を引き継ぐ。
    """
    n = len(samples)
    if n < 3 or smooth_radius_m <= 1.0e-9:
        return list(samples)
    arc_step = math.radians(max(1.0, arc_step_deg))
    result: list[tuple[float, float, float]] = []
    for i in range(n):
        prev_s = samples[(i - 1) % n]
        curr_s = samples[i]
        next_s = samples[(i + 1) % n]
        ax, ay = curr_s[0] - prev_s[0], curr_s[1] - prev_s[1]
        bx, by = next_s[0] - curr_s[0], next_s[1] - curr_s[1]
        la = math.hypot(ax, ay)
        lb = math.hypot(bx, by)
        if la <= 1.0e-9 or lb <= 1.0e-9:
            result.append(curr_s)
            continue
        cross = ax * by - ay * bx
        dot = ax * bx + ay * by
        delta = math.atan2(cross, dot)
        if abs(delta) <= sharp_threshold_rad:
            result.append(curr_s)
            continue
        ext = abs(delta)
        if ext >= math.pi - 0.02:
            result.append(curr_s)
            continue
        td = smooth_radius_m / math.tan(ext / 2.0)
        td = min(td, la * 0.49, lb * 0.49)
        if td <= 1.0e-9:
            result.append(curr_s)
            continue
        r_eff = td * math.tan(ext / 2.0)
        a_dx, a_dy = ax / la, ay / la
        b_dx, b_dy = bx / lb, by / lb
        tp_prev = (curr_s[0] - a_dx * td, curr_s[1] - a_dy * td)
        if delta > 0:
            n_inside = (-a_dy, a_dx)
        else:
            n_inside = (a_dy, -a_dx)
        center = (tp_prev[0] + n_inside[0] * r_eff, tp_prev[1] + n_inside[1] * r_eff)
        v0 = (tp_prev[0] - center[0], tp_prev[1] - center[1])
        steps = max(2, int(math.ceil(ext / arc_step)))
        for s in range(steps + 1):
            t = s / steps
            theta = delta * t
            cos_t = math.cos(theta)
            sin_t = math.sin(theta)
            vx = v0[0] * cos_t - v0[1] * sin_t
            vy = v0[0] * sin_t + v0[1] * cos_t
            result.append((center[0] + vx, center[1] + vy, curr_s[2]))
    return result


def _polygon_area(points: Sequence[tuple[float, float]]) -> float:
    area = 0.0
    if not points:
        return 0.0
    prev = points[-1]
    for curr in points:
        area += prev[0] * curr[1] - curr[0] * prev[1]
        prev = curr
    return area * 0.5


def _band_loops(
    samples: Sequence[tuple[float, float, float]],
    *,
    half_width_m: float,
) -> Optional[tuple[list[tuple[float, float]], list[tuple[float, float]]]]:
    """各サンプル点の per-point radius を反映した外周/内周点列を返す.

    各点で、両隣の segment 法線の bisector 方向にオフセットする (per-point
    radius でスケール)。bisector が小さい (折り返しに近い) 点はスキップする。
    """
    n = len(samples)
    if n < 3 or half_width_m <= 1.0e-9:
        return None
    pts = [(s[0], s[1]) for s in samples]
    area = _polygon_area(pts)
    if abs(area) <= 1.0e-12:
        return None
    ccw = area > 0.0
    outer: list[tuple[float, float]] = []
    inner: list[tuple[float, float]] = []
    for i in range(n):
        prev_s = samples[(i - 1) % n]
        curr_s = samples[i]
        next_s = samples[(i + 1) % n]
        ax, ay = curr_s[0] - prev_s[0], curr_s[1] - prev_s[1]
        bx, by = next_s[0] - curr_s[0], next_s[1] - curr_s[1]
        la = math.hypot(ax, ay)
        lb = math.hypot(bx, by)
        if la <= 1.0e-9 or lb <= 1.0e-9:
            continue
        a_dx, a_dy = ax / la, ay / la
        b_dx, b_dy = bx / lb, by / lb
        left_prev = (-a_dy, a_dx)
        left_next = (-b_dy, b_dx)
        if ccw:
            inner_n = (left_prev[0] + left_next[0], left_prev[1] + left_next[1])
            outer_n = (-inner_n[0], -inner_n[1])
        else:
            outer_n = (left_prev[0] + left_next[0], left_prev[1] + left_next[1])
            inner_n = (-outer_n[0], -outer_n[1])
        on_len = math.hypot(*outer_n)
        in_len = math.hypot(*inner_n)
        if on_len <= 1.0e-9 or in_len <= 1.0e-9:
            continue
        outer_n = (outer_n[0] / on_len, outer_n[1] / on_len)
        inner_n = (inner_n[0] / in_len, inner_n[1] / in_len)
        radius_scale = max(0.0, float(curr_s[2]))
        d = half_width_m * radius_scale
        outer.append((curr_s[0] + outer_n[0] * d, curr_s[1] + outer_n[1] * d))
        inner.append((curr_s[0] + inner_n[0] * d, curr_s[1] + inner_n[1] * d))
    if len(outer) < 3 or len(inner) != len(outer):
        return None
    return outer, inner


def _rebuild_band_mesh(
    mesh: bpy.types.Mesh,
    outer: Sequence[tuple[float, float]],
    inner: Sequence[tuple[float, float]],
    z_m: float,
) -> None:
    if len(outer) < 3 or len(inner) != len(outer):
        mesh.clear_geometry()
        mesh.update()
        return
    count = len(outer)
    verts: list[tuple[float, float, float]] = []
    verts.extend((float(x), float(y), float(z_m)) for x, y in outer)
    verts.extend((float(x), float(y), float(z_m)) for x, y in inner)
    faces: list[tuple[int, int, int, int]] = [
        (i, (i + 1) % count, count + (i + 1) % count, count + i)
        for i in range(count)
    ]
    mesh.clear_geometry()
    mesh.from_pydata(verts, [], faces)
    mesh.update()


def _line_mesh_object_name(balloon_id: str) -> str:
    return f"{BALLOON_LINE_MESH_NAME_PREFIX}{balloon_id}"


def _line_mesh_data_name(balloon_id: str) -> str:
    return f"{BALLOON_LINE_MESH_NAME_PREFIX}{balloon_id}_mesh"


def ensure_balloon_line_mesh(
    *,
    scene,
    work,
    page,
    entry,
    body_object: bpy.types.Object,
    line_material: bpy.types.Material,
) -> Optional[bpy.types.Object]:
    """フキダシ主線のメッシュバンドオブジェクトを生成・更新する.

    対象形状でない場合や線が無効な場合は既存のメッシュを撤去する。
    """
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return None

    if not is_mesh_band_shape(entry):
        remove_balloon_line_mesh(balloon_id)
        return None

    line_style = str(getattr(entry, "line_style", "") or "")
    line_width_mm = max(0.0, float(getattr(entry, "line_width_mm", 0.3) or 0.0))
    if line_style == "none" or line_width_mm <= 1.0e-6:
        remove_balloon_line_mesh(balloon_id)
        return None

    if body_object is None or getattr(body_object, "type", "") != "CURVE":
        remove_balloon_line_mesh(balloon_id)
        return None
    body_curve = getattr(body_object, "data", None)
    if body_curve is None:
        remove_balloon_line_mesh(balloon_id)
        return None
    splines = list(getattr(body_curve, "splines", []) or [])
    body_spline = None
    for spline in splines:
        if str(getattr(spline, "type", "") or "") == "BEZIER" and bool(getattr(spline, "use_cyclic_u", False)):
            body_spline = spline
            break
    if body_spline is None:
        remove_balloon_line_mesh(balloon_id)
        return None

    samples = _sample_body_bezier(body_spline, SAMPLES_PER_SEGMENT)
    if len(samples) < 3:
        remove_balloon_line_mesh(balloon_id)
        return None

    half_width_m = line_width_mm * 0.5 * 0.001
    smooth_radius_m = max(half_width_m, 0.0001)
    smoothed = _smooth_sharp_corners(
        samples,
        smooth_radius_m=smooth_radius_m,
        sharp_threshold_rad=SHARP_THRESHOLD_RAD,
        arc_step_deg=ARC_STEP_DEG,
    )
    band = _band_loops(smoothed, half_width_m=half_width_m)
    if band is None:
        remove_balloon_line_mesh(balloon_id)
        return None
    outer, inner = band

    mesh_name = _line_mesh_data_name(balloon_id)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    _rebuild_band_mesh(mesh, outer, inner, LINE_Z_OFFSET_M)

    obj_name = _line_mesh_object_name(balloon_id)
    obj = bpy.data.objects.get(obj_name)
    if obj is not None and getattr(obj, "type", "") != "MESH":
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
        obj = None
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        obj.data = mesh

    if not mesh.materials:
        mesh.materials.append(line_material)
    elif mesh.materials[0] is not line_material:
        mesh.materials[0] = line_material

    obj[PROP_BALLOON_LINE_MESH_KIND] = "balloon_line_mesh"
    obj[PROP_BALLOON_LINE_MESH_OWNER_ID] = balloon_id
    obj[on.PROP_MANAGED] = False
    obj.hide_select = True

    # 本体オブジェクトのコレクションに同居させる
    target_collections = list(getattr(body_object, "users_collection", []) or [])
    if not target_collections:
        target_collections = [scene.collection] if scene is not None else []
    current_collections = set(getattr(obj, "users_collection", []) or [])
    for coll in target_collections:
        if coll not in current_collections:
            try:
                coll.objects.link(obj)
            except Exception:  # noqa: BLE001
                pass
    for coll in list(current_collections):
        if coll not in target_collections:
            try:
                coll.objects.unlink(obj)
            except Exception:  # noqa: BLE001
                pass

    # 本体カーブにペアレントして transform を追従させる
    if obj.parent is not body_object:
        obj.parent = body_object
        obj.matrix_parent_inverse.identity()
    obj.location = (0.0, 0.0, 0.0)
    obj.rotation_euler = (0.0, 0.0, 0.0)
    obj.scale = (1.0, 1.0, 1.0)

    visible = bool(getattr(entry, "visible", True))
    obj.hide_viewport = not visible
    obj.hide_render = not visible

    return obj


def remove_balloon_line_mesh(balloon_id: str) -> None:
    if not balloon_id:
        return
    obj_name = _line_mesh_object_name(balloon_id)
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        return
    data = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon line mesh removal failed")
        return
    if data is not None and getattr(data, "users", 0) == 0:
        try:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
        except Exception:  # noqa: BLE001
            pass


def cleanup_orphan_line_meshes(valid_balloon_ids: set[str]) -> int:
    removed = 0
    for obj in list(bpy.data.objects):
        if obj.get(PROP_BALLOON_LINE_MESH_KIND) != "balloon_line_mesh":
            continue
        owner_id = str(obj.get(PROP_BALLOON_LINE_MESH_OWNER_ID, "") or "")
        if owner_id and owner_id not in valid_balloon_ids:
            data = getattr(obj, "data", None)
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:  # noqa: BLE001
                pass
            if data is not None and getattr(data, "users", 0) == 0:
                try:
                    if isinstance(data, bpy.types.Mesh):
                        bpy.data.meshes.remove(data)
                except Exception:  # noqa: BLE001
                    pass
            removed += 1
    return removed
