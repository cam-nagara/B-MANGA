"""フキダシ線種「図形」「画像」の Mesh 焼き込み.

- 図形: ●・★ などの図形を主線の輪郭に沿って連続配置する (線素材で塗る)
- 画像: 指定画像を輪郭に沿って帯状に引き延ばして貼る
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import bpy

from . import line_decor_geom, log
from .balloon_line_mesh import (
    LINE_Z_OFFSET_M,
    SAMPLES_PER_SEGMENT,
    _attach_band_mesh_object,
    _body_samples_for_line_mesh,
    _build_band_mesh_from_polygons,
    _outline_samples_with_tails,
    band_geometry_cache_hit,
    scaled_entry_width_mm,
)

_logger = log.get_logger(__name__)

KIND_LINE_SHAPE = "balloon_line_shape_mesh"
KIND_LINE_IMAGE = "balloon_line_image_mesh"
_SHAPE_OBJ_PREFIX = "balloon_line_shape_"
_IMAGE_OBJ_PREFIX = "balloon_line_image_"
_IMAGE_MATERIAL_PREFIX = "BManga_BalloonLineImage_"


def _decor_outline_m(entry, body_object) -> list[tuple[float, float]]:
    samples = _body_samples_for_line_mesh(entry, body_object)
    if len(samples) < 3:
        return []
    samples, _merged = _outline_samples_with_tails(entry, samples)
    return [(float(x), float(y)) for x, y, *_rest in samples]


def ensure_balloon_line_shape_mesh(
    *,
    scene,
    work,
    page,
    entry,
    body_object: bpy.types.Object,
    line_material: bpy.types.Material,
    mask_info=None,
    geometry_sig=None,
) -> Optional[bpy.types.Object]:
    """線種「図形」の図形列メッシュを生成・更新する."""
    del work, page
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return None
    cached = band_geometry_cache_hit(f"{_SHAPE_OBJ_PREFIX}{balloon_id}", geometry_sig)
    if cached is not None:
        return _attach_band_mesh_object(
            obj_name=f"{_SHAPE_OBJ_PREFIX}{balloon_id}",
            mesh=cached.data,
            material=line_material,
            body_object=body_object,
            scene=scene,
            kind=KIND_LINE_SHAPE,
            balloon_id=balloon_id,
            visible=bool(getattr(entry, "visible", True)),
            mask_info=mask_info,
            geometry_sig=geometry_sig,
        )
    loop = _decor_outline_m(entry, body_object)
    line_width_mm = scaled_entry_width_mm(entry, "line_width_mm", 0.3)
    if len(loop) < 3 or line_width_mm <= 1.0e-6:
        remove_balloon_line_shape_mesh(balloon_id)
        return None
    # 「中心点」向きの基準: フキダシ本体 (しっぽ結合前) の中心
    body_samples = _body_samples_for_line_mesh(entry, body_object)
    if body_samples:
        center = (
            sum(float(s[0]) for s in body_samples) / len(body_samples),
            sum(float(s[1]) for s in body_samples) / len(body_samples),
        )
    else:
        center = None
    polygons = line_decor_geom.decorations_along_loop(
        loop,
        kind=str(getattr(entry, "line_shape_kind", "circle") or "circle"),
        size=line_width_mm * 0.001,
        spacing=max(0.0, float(getattr(entry, "line_shape_spacing_mm", 1.5) or 0.0)) * 0.001,
        angle_rad=math.radians(float(getattr(entry, "line_shape_angle_deg", 0.0) or 0.0)),
        jitter=float(getattr(entry, "line_shape_jitter", 0.0) or 0.0),
        seed=int(getattr(entry, "line_shape_seed", 0) or 0),
        orient=str(getattr(entry, "line_shape_orient", "line") or "line"),
        center=center,
    )
    if not polygons:
        remove_balloon_line_shape_mesh(balloon_id)
        return None
    mesh_name = f"{_SHAPE_OBJ_PREFIX}{balloon_id}_mesh"
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    _build_band_mesh_from_polygons(mesh, [(poly, []) for poly in polygons], LINE_Z_OFFSET_M)
    return _attach_band_mesh_object(
        obj_name=f"{_SHAPE_OBJ_PREFIX}{balloon_id}",
        mesh=mesh,
        material=line_material,
        body_object=body_object,
        scene=scene,
        kind=KIND_LINE_SHAPE,
        balloon_id=balloon_id,
        visible=bool(getattr(entry, "visible", True)),
        mask_info=mask_info,
        geometry_sig=geometry_sig,
    )


def _load_line_image(entry) -> bpy.types.Image | None:
    raw = str(getattr(entry, "line_image_path", "") or "").strip()
    if not raw:
        return None
    try:
        abspath = bpy.path.abspath(raw)
        if not Path(abspath).is_file():
            return None
        return bpy.data.images.load(abspath, check_existing=True)
    except Exception:  # noqa: BLE001
        _logger.warning("balloon line image load failed: %s", raw)
        return None


def _ensure_image_material(name: str, image: bpy.types.Image) -> bpy.types.Material:
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    try:
        mat.blend_method = "BLEND"
        mat.show_transparent_back = False
    except Exception:  # noqa: BLE001
        pass
    nt = mat.node_tree
    for node in list(nt.nodes):
        nt.nodes.remove(node)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (420, 0)
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (240, 0)
    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (60, -120)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (60, 120)
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.location = (-220, 0)
    tex.image = image
    try:
        tex.extension = "REPEAT"
        tex.interpolation = "Linear"
    except Exception:  # noqa: BLE001
        pass
    try:
        nt.links.new(tex.outputs["Color"], emission.inputs["Color"])
        nt.links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
        nt.links.new(transparent.outputs["BSDF"], mix.inputs[1])
        nt.links.new(emission.outputs["Emission"], mix.inputs[2])
        nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    except Exception:  # noqa: BLE001
        _logger.exception("balloon line image material link failed")
    try:
        mat.update_tag()
    except Exception:  # noqa: BLE001
        pass
    return mat


def _strip_mesh(
    mesh: bpy.types.Mesh,
    loop: list[tuple[float, float]],
    half_width_m: float,
    interval_m: float,
    angle_rad: float,
    jitter: float,
) -> None:
    """輪郭に沿った帯メッシュを作り、弧長に応じた UV を貼る."""
    n = len(loop)
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int, int]] = []
    arc = [0.0]
    for i in range(n):
        p0 = loop[i]
        p1 = loop[(i + 1) % n]
        arc.append(arc[-1] + math.hypot(p1[0] - p0[0], p1[1] - p0[1]))
    for i in range(n):
        prev_p = loop[(i - 1) % n]
        next_p = loop[(i + 1) % n]
        tx = next_p[0] - prev_p[0]
        ty = next_p[1] - prev_p[1]
        length = math.hypot(tx, ty)
        if length <= 1.0e-9:
            tx, ty = 1.0, 0.0
            length = 1.0
        nx = -ty / length
        ny = tx / length
        x, y = loop[i]
        wobble = 0.0
        if jitter > 0.0:
            wobble = math.sin(arc[i] / max(interval_m, 1.0e-6) * math.tau) * half_width_m * jitter
        verts.append((x + nx * (half_width_m + wobble), y + ny * (half_width_m + wobble), LINE_Z_OFFSET_M))
        verts.append((x - nx * (half_width_m - wobble), y - ny * (half_width_m - wobble), LINE_Z_OFFSET_M))
    for i in range(n):
        a = i * 2
        b = ((i + 1) % n) * 2
        faces.append((a, a + 1, b + 1, b))
    mesh.clear_geometry()
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    uv_layer = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    def _uv(u: float, v: float) -> tuple[float, float]:
        # 画像の角度: UV を中心 v=0.5 まわりに回転して貼る
        du = u
        dv = v - 0.5
        return (du * cos_a - dv * sin_a, dv * cos_a + du * sin_a + 0.5)

    for face_index, poly in enumerate(mesh.polygons):
        i = face_index
        u0 = arc[i] / max(interval_m, 1.0e-6)
        u1 = arc[i + 1] / max(interval_m, 1.0e-6)
        uvs = (_uv(u0, 1.0), _uv(u0, 0.0), _uv(u1, 0.0), _uv(u1, 1.0))
        for loop_index, uv in zip(poly.loop_indices, uvs, strict=False):
            uv_layer.data[loop_index].uv = uv


def ensure_balloon_line_image_mesh(
    *,
    scene,
    work,
    page,
    entry,
    body_object: bpy.types.Object,
    mask_info=None,
    geometry_sig=None,
) -> Optional[bpy.types.Object]:
    """線種「画像」の帯メッシュを生成・更新する.

    画像は線に沿って引き延ばされ、「画像の間隔」ごとに 1 枚分を繰り返す。
    (コマ内容マスクには現状未対応: 画像線はコマ外でも表示される)
    """
    del work, page, mask_info
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return None
    cached = band_geometry_cache_hit(f"{_IMAGE_OBJ_PREFIX}{balloon_id}", geometry_sig)
    if cached is not None:
        image = _load_line_image(entry)
        if image is not None:
            material = _ensure_image_material(f"{_IMAGE_MATERIAL_PREFIX}{balloon_id}", image)
            return _attach_band_mesh_object(
                obj_name=f"{_IMAGE_OBJ_PREFIX}{balloon_id}",
                mesh=cached.data,
                material=material,
                body_object=body_object,
                scene=scene,
                kind=KIND_LINE_IMAGE,
                balloon_id=balloon_id,
                visible=bool(getattr(entry, "visible", True)),
                mask_info=None,
                geometry_sig=geometry_sig,
            )
    image = _load_line_image(entry)
    loop = _decor_outline_m(entry, body_object)
    line_width_mm = scaled_entry_width_mm(entry, "line_width_mm", 0.3)
    if image is None or len(loop) < 3 or line_width_mm <= 1.0e-6:
        remove_balloon_line_image_mesh(balloon_id)
        return None
    interval_m = max(0.5, float(getattr(entry, "line_image_interval_mm", 20.0) or 20.0)) * 0.001
    mesh_name = f"{_IMAGE_OBJ_PREFIX}{balloon_id}_mesh"
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    _strip_mesh(
        mesh,
        loop,
        line_width_mm * 0.001 * 0.5,
        interval_m,
        math.radians(float(getattr(entry, "line_image_angle_deg", 0.0) or 0.0)),
        float(getattr(entry, "line_image_jitter", 0.0) or 0.0),
    )
    material = _ensure_image_material(f"{_IMAGE_MATERIAL_PREFIX}{balloon_id}", image)
    return _attach_band_mesh_object(
        obj_name=f"{_IMAGE_OBJ_PREFIX}{balloon_id}",
        mesh=mesh,
        material=material,
        body_object=body_object,
        scene=scene,
        kind=KIND_LINE_IMAGE,
        balloon_id=balloon_id,
        visible=bool(getattr(entry, "visible", True)),
        mask_info=None,
        geometry_sig=geometry_sig,
    )


def _remove_named(obj_name: str) -> None:
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        return
    data = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        return
    if data is not None and getattr(data, "users", 0) == 0:
        try:
            bpy.data.meshes.remove(data)
        except Exception:  # noqa: BLE001
            pass


def remove_balloon_line_shape_mesh(balloon_id: str) -> None:
    if balloon_id:
        _remove_named(f"{_SHAPE_OBJ_PREFIX}{balloon_id}")


def remove_balloon_line_image_mesh(balloon_id: str) -> None:
    if balloon_id:
        _remove_named(f"{_IMAGE_OBJ_PREFIX}{balloon_id}")


def remove_balloon_line_decor_meshes(balloon_id: str) -> None:
    remove_balloon_line_shape_mesh(balloon_id)
    remove_balloon_line_image_mesh(balloon_id)
