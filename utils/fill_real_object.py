"""フィルレイヤーの実体平面同期.

ベタ塗り・グラデーションをマテリアル付き Mesh 平面として表示する。
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from typing import Optional

import bpy

from . import layer_object_sync as los
from . import log
from . import object_naming as on
from . import object_preserve
from .geom import mm_to_m

_logger = log.get_logger(__name__)

FILL_OBJECT_NAME_PREFIX = "fill_"
FILL_MESH_NAME_PREFIX = "fill_mesh_"
FILL_MATERIAL_NAME_PREFIX = "BManga_Fill_"
GRADIENT_HANDLE_KIND = "gradient_handle"
_HANDLE_DISPLAY_SIZE = 0.008
_HANDLE_MESH_ARM_M = 0.0025
_HANDLE_Z_M = 0.30
FILL_Z_BASE = 250
_AUTO_SYNC_SUSPEND_DEPTH = 0
_HANDLE_WRITEBACK_GUARD = False


@contextmanager
def suspend_auto_sync():
    global _AUTO_SYNC_SUSPEND_DEPTH
    _AUTO_SYNC_SUSPEND_DEPTH += 1
    try:
        yield
    finally:
        _AUTO_SYNC_SUSPEND_DEPTH = max(0, _AUTO_SYNC_SUSPEND_DEPTH - 1)


def auto_sync_suspended() -> bool:
    return _AUTO_SYNC_SUSPEND_DEPTH > 0


def _safe_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(value or ""))


def _object_name(fill_id: str) -> str:
    return f"{FILL_OBJECT_NAME_PREFIX}{_safe_token(fill_id)}"


def _mesh_name(fill_id: str) -> str:
    return f"{FILL_MESH_NAME_PREFIX}{_safe_token(fill_id)}"


def _material_name(fill_id: str) -> str:
    return f"{FILL_MATERIAL_NAME_PREFIX}{_safe_token(fill_id)}"


def is_gradient_endpoint_rotation_locked(entry) -> bool:
    """端点指定グラデーション塗りか (回転抑制条件の単一情報源).

    ``fill_type=="gradient"`` かつ ``use_gradient_endpoints=True`` の場合の
    みTrue。ベタ塗りへ fill_type を変更しても use_gradient_endpoints が
    True のまま残留する仕様のため、fill_type 側も必ず併せて見る
    (use_gradient_endpoints 単独で判定すると、端点グラデ→ベタ塗りへ変更した
    直後にパネルは編集可能なのに回転だけ永久に無反応になる不整合が起きる)。
    panels/layer_stack_detail_ui.py の回転欄の活性判定と同じ条件式であり、
    operators/object_rotation_fill.py の capture_fn/can_rotate_fn と
    ここ (obj.rotation_euler 抑制) の3箇所から共通で呼ばれる。
    """
    fill_type = str(getattr(entry, "fill_type", "solid") or "solid")
    return fill_type == "gradient" and bool(getattr(entry, "use_gradient_endpoints", False))


def _ensure_parent_collection(
    scene: bpy.types.Scene, parent_kind: str, parent_key: str,
) -> None:
    """stamp_layer_object が link 先を見つけられるよう親 Collection を確保."""
    from . import outliner_model as _om

    if parent_kind == "coma" and ":" in parent_key:
        page_id, coma_id = parent_key.split(":", 1)
        _om.ensure_coma_collection(scene, page_id, coma_id)
    elif parent_kind == "page" and parent_key:
        _om.ensure_page_collection(scene, parent_key)


def _resolve_parent_for_entry(entry, page, folder_id: str) -> tuple[str, str, str]:
    parent_kind = str(getattr(entry, "parent_kind", "") or "page")
    parent_key = str(getattr(entry, "parent_key", "") or "")
    entry_folder = folder_id or str(getattr(entry, "folder_key", "") or "")
    if parent_kind in {"none", "outside"}:
        return "outside", "", ""
    if parent_kind == "coma" and parent_key:
        return "coma", parent_key, entry_folder
    if parent_kind == "folder":
        folder_key = entry_folder or parent_key
        if folder_key:
            return "folder", folder_key, folder_key
    return "page", parent_key or str(getattr(page, "id", "") or ""), entry_folder


def _page_by_id(work, page_id: str):
    if work is None or not page_id:
        return None
    for candidate in getattr(work, "pages", []) or []:
        if str(getattr(candidate, "id", "") or "") == page_id:
            return candidate
    return None


def _semantic_parent_key_for_entry(work, entry, fallback_page=None) -> str:
    parent_kind = str(getattr(entry, "parent_kind", "") or "page")
    parent_key = str(getattr(entry, "parent_key", "") or "")
    folder_key = str(getattr(entry, "folder_key", "") or "")
    if parent_kind in {"none", "outside"}:
        return ""
    if parent_kind == "folder":
        folder_key = folder_key or parent_key
        if folder_key:
            try:
                from . import layer_folder
                from .layer_hierarchy import OUTSIDE_STACK_KEY

                semantic = layer_folder.semantic_parent_key_for_folder(work, folder_key)
                return "" if semantic == OUTSIDE_STACK_KEY else semantic
            except Exception:  # noqa: BLE001
                return ""
    if parent_key:
        return parent_key
    if folder_key:
        try:
            from . import layer_folder
            from .layer_hierarchy import OUTSIDE_STACK_KEY

            semantic = layer_folder.semantic_parent_key_for_folder(work, folder_key)
            if semantic != OUTSIDE_STACK_KEY:
                return semantic
        except Exception:  # noqa: BLE001
            pass
    return str(getattr(fallback_page, "id", "") or "")


def page_for_entry(scene, work, entry, fallback_page=None):
    key = _semantic_parent_key_for_entry(work, entry, fallback_page)
    page_id = key.split(":", 1)[0] if key else ""
    page = _page_by_id(work, page_id)
    if (
        page is None
        and str(getattr(entry, "parent_kind", "") or "page") == "page"
        and not page_id
    ):
        pages = getattr(work, "pages", None)
        if pages and len(pages):
            page = pages[0]
    return page


def _sync_coma_mask_position(scene, work, parent_key: str) -> None:
    """コママスク Object の位置をページグリッドに合わせて補正する."""
    try:
        from . import coma_plane as _cp
        from . import page_grid as _pg
    except ImportError:
        return
    parts = parent_key.split(":", 1)
    if len(parts) != 2:
        return
    page_id, coma_id = parts[0], parts[1]
    mask = _cp.find_coma_mask_object(page_id, coma_id)
    if mask is None:
        return
    page = _page_by_id(work, page_id)
    if page is None:
        return
    ox_mm, oy_mm = entry_page_offset_mm(scene, work, None, page)
    coma = None
    for c in getattr(page, "comas", []) or []:
        if str(getattr(c, "id", "") or "") == coma_id:
            coma = c
            break
    if coma is None:
        return
    shape_type = str(getattr(coma, "shape_type", "rect") or "rect")
    local_x = float(getattr(coma, "rect_x_mm", 0.0) or 0.0) if shape_type == "rect" else 0.0
    local_y = float(getattr(coma, "rect_y_mm", 0.0) or 0.0) if shape_type == "rect" else 0.0
    mask.location.x = mm_to_m(ox_mm + local_x)
    mask.location.y = mm_to_m(oy_mm + local_y)


def entry_page_offset_mm(scene, work, entry, page):
    try:
        from . import page_grid
    except ImportError:
        return 0.0, 0.0
    if page is None or work is None:
        return 0.0, 0.0
    page_id = str(getattr(page, "id", "") or "")
    for i, p in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(p, "id", "") or "") == page_id:
            return page_grid.page_total_offset_mm(work, scene, i)
    return 0.0, 0.0


def _fill_z_index(scene, fill_id: str) -> int:
    coll = getattr(scene, "bmanga_fill_layers", None) if scene is not None else None
    if coll is None:
        return FILL_Z_BASE
    for i, entry in enumerate(coll):
        if str(getattr(entry, "id", "") or "") == fill_id:
            return FILL_Z_BASE + (i + 1) * 10
    return FILL_Z_BASE


def _rebuild_mesh(mesh: bpy.types.Mesh, width_m: float, height_m: float) -> None:
    half_w = width_m * 0.5
    half_h = height_m * 0.5
    verts = [
        (-half_w, -half_h, 0.0),
        (half_w, -half_h, 0.0),
        (half_w, half_h, 0.0),
        (-half_w, half_h, 0.0),
    ]
    mesh.clear_geometry()
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    mesh.update()
    uv_layer = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
    uvs = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    for loop_index, uv in zip(mesh.polygons[0].loop_indices, uvs, strict=False):
        uv_layer.data[loop_index].uv = uv


def _rebuild_lasso_mesh(
    mesh: bpy.types.Mesh,
    points_mm: list,
    center_x_mm: float,
    center_y_mm: float,
    canvas_w_mm: float,
    canvas_h_mm: float,
) -> bool:
    import bmesh

    if len(points_mm) < 3:
        return False
    bm = bmesh.new()
    try:
        for x, y in points_mm:
            bm.verts.new((mm_to_m(x - center_x_mm), mm_to_m(y - center_y_mm), 0.0))
        bm.verts.ensure_lookup_table()
        try:
            face = bm.faces.new(bm.verts)
        except ValueError:
            return False
        bmesh.ops.triangulate(bm, faces=[face])
        mesh.clear_geometry()
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()
    uv_layer = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
    cw = canvas_w_mm if canvas_w_mm > 1e-6 else 1.0
    ch = canvas_h_mm if canvas_h_mm > 1e-6 else 1.0
    for poly in mesh.polygons:
        for li in poly.loop_indices:
            v = mesh.vertices[mesh.loops[li].vertex_index]
            px = v.co.x * 1000.0 + center_x_mm
            py = v.co.y * 1000.0 + center_y_mm
            uv_layer.data[li].uv = (px / cw, py / ch)
    return True


def _ensure_solid_material(
    name: str, color: tuple, opacity: float, *, mask_info=None,
) -> bpy.types.Material:
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    try:
        mat.blend_method = "BLEND"
        mat.show_transparent_back = True
        # blend_method="BLEND" は surface_render_method を副作用で
        # "BLENDED" (順序依存の疑似合成、他の半透明レイヤーと重なると
        # 深度を無視して不正な前後関係になる) に変えてしまう。コマ内の
        # 他レイヤー(効果線・ラスター等)と正しい重なり順で描画するため、
        # 深度を尊重する "DITHERED" へ明示的に上書きする。
        # 実機確認: この代入は逆方向にも副作用があり、最終的に
        # mat.blend_method は "HASHED" になる (直前の "BLEND" 代入は
        # surface_render_method 側の上書きで打ち消される)。blend_method
        # と surface_render_method のペアとしては HASHED/DITHERED が
        # 最終状態であり、これが深度を尊重する正しい組み合わせ。
        mat.surface_render_method = "DITHERED"
    except Exception:  # noqa: BLE001
        pass
    nt = mat.node_tree
    # gradient→solid 切替時に詳細ダイアログの template_curve_mapping が
    # Float Curve ノードへの C レベル参照を保持している場合がある。
    # 破棄するとクラッシュするため、名前で保全して残す。
    fc_name = None
    for node in nt.nodes:
        if node.type == "CURVE_FLOAT":
            fc_name = node.name
            break
    for node in list(nt.nodes):
        if node.name != fc_name:
            nt.nodes.remove(node)

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (360, 0)
    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (-60, -140)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (-60, 60)
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (140, 0)

    r, g, b = float(color[0]), float(color[1]), float(color[2])
    a = float(color[3]) if len(color) > 3 else 1.0
    fac = a * (opacity / 100.0)

    emission.inputs["Color"].default_value = (r, g, b, 1.0)
    emission.inputs["Strength"].default_value = 1.0
    nt.links.new(transparent.outputs["BSDF"], mix.inputs[1])
    nt.links.new(emission.outputs["Emission"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])

    if mask_info is not None:
        from . import material_opacity_mask
        val = nt.nodes.new("ShaderNodeValue")
        val.location = (-260, -260)
        val.outputs[0].default_value = fac
        alpha_out = material_opacity_mask.multiply_alpha_by_mask(
            nt, val.outputs[0],
            mask_object=getattr(mask_info, "space_object", None),
            mask_image=getattr(mask_info, "image", None),
        )
        if alpha_out is not None:
            nt.links.new(alpha_out, mix.inputs["Fac"])
        else:
            mix.inputs["Fac"].default_value = fac
    else:
        mix.inputs["Fac"].default_value = fac

    try:
        mat.diffuse_color = (r, g, b, fac)
    except Exception:  # noqa: BLE001
        pass
    try:
        mat.update_tag()
    except Exception:  # noqa: BLE001
        pass
    return mat


def _extract_curve_points(nt) -> list[tuple[float, float, str]] | None:
    """ノードツリーから FloatCurve のポイントデータを退避."""
    for node in nt.nodes:
        if node.type == "CURVE_FLOAT":
            curve = node.mapping.curves[0]
            return [(p.location[0], p.location[1], p.handle_type) for p in curve.points]
    return None


def _apply_curve_points(float_curve_node, points: list[tuple[float, float, str]] | None) -> None:
    """FloatCurve ノードにポイントデータを復元."""
    if points is None or len(points) < 2:
        return
    curve = float_curve_node.mapping.curves[0]
    while len(curve.points) > 2:
        curve.points.remove(curve.points[1])
    curve.points[0].location = (points[0][0], points[0][1])
    curve.points[0].handle_type = points[0][2]
    curve.points[-1].location = (points[-1][0], points[-1][1])
    curve.points[-1].handle_type = points[-1][2]
    for px, py, ht in points[1:-1]:
        p = curve.points.new(px, py)
        p.handle_type = ht
    float_curve_node.mapping.update()


def _ensure_gradient_material(
    name: str,
    color1: tuple,
    color2: tuple,
    gradient_type: str,
    angle_rad: float,
    opacity: float,
    *,
    endpoint_uv: tuple | None = None,
    curve_points: list[tuple[float, float, str]] | None = None,
    mask_info=None,
) -> bpy.types.Material:
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    try:
        mat.blend_method = "BLEND"
        mat.show_transparent_back = True
        # _ensure_solid_material と同じ理由 (surface_render_method の
        # 副作用による誤った前後関係を防ぐため DITHERED を明示する)。
        mat.surface_render_method = "DITHERED"
    except Exception:  # noqa: BLE001
        pass
    nt = mat.node_tree
    # 詳細設定ダイアログの template_curve_mapping が FloatCurve ノードへの
    # C レベル参照を保持しているため、ノードを破棄するとクラッシュする。
    # 既存の FloatCurve ノードを保存し、他ノードだけ再作成する。
    # 詳細設定ダイアログの template_curve_mapping が FloatCurve ノードへの
    # C レベル参照を保持しているため、ノードを破棄するとクラッシュする。
    # Blender RNA は Python `is` で同一性比較できない (イテレーションの
    # たびに新しいラッパーが生成される) ため、名前で識別する。
    fc_name = None
    for node in nt.nodes:
        if node.type == "CURVE_FLOAT":
            fc_name = node.name
            break
    if fc_name is None:
        saved_curve = curve_points or _extract_curve_points(nt)
    else:
        saved_curve = curve_points
    for node in list(nt.nodes):
        if node.name != fc_name:
            nt.nodes.remove(node)

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (700, 0)
    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (260, -140)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (260, 60)
    mix_shader = nt.nodes.new("ShaderNodeMixShader")
    mix_shader.location = (480, 0)

    tex_coord = nt.nodes.new("ShaderNodeTexCoord")
    tex_coord.location = (-600, 0)
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.location = (-400, 0)
    mapping.vector_type = "TEXTURE"
    gradient = nt.nodes.new("ShaderNodeTexGradient")
    gradient.location = (-200, 0)
    if fc_name is not None:
        float_curve = nt.nodes[fc_name]
        float_curve.location = (-20, 0)
        float_curve.label = "濃度カーブ"
    else:
        float_curve = nt.nodes.new("ShaderNodeFloatCurve")
        float_curve.location = (-20, 0)
        float_curve.label = "濃度カーブ"
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.location = (60, 0)

    if saved_curve is not None:
        _apply_curve_points(float_curve, saved_curve)

    if gradient_type == "radial":
        gradient.gradient_type = "SPHERICAL"
        if endpoint_uv is not None:
            su, sv, eu, ev = endpoint_uv
            dx, dy = eu - su, ev - sv
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < 1e-6:
                dist = 1.0
            mapping.inputs["Location"].default_value = (su, sv, 0.0)
            mapping.inputs["Scale"].default_value = (dist, dist, 1.0)
        else:
            mapping.inputs["Location"].default_value = (0.5, 0.5, 0.0)
    else:
        gradient.gradient_type = "LINEAR"
        if endpoint_uv is not None:
            su, sv, eu, ev = endpoint_uv
            dx, dy = eu - su, ev - sv
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < 1e-6:
                dist = 1.0
            ep_angle = math.atan2(dy, dx)
            mapping.inputs["Location"].default_value = (su, sv, 0.0)
            mapping.inputs["Rotation"].default_value = (0.0, 0.0, ep_angle)
            mapping.inputs["Scale"].default_value = (dist, 1.0, 1.0)
        else:
            mapping.inputs["Rotation"].default_value = (0.0, 0.0, angle_rad)
            mapping.inputs["Location"].default_value = (0.0, 0.0, 0.0)

    cr = ramp.color_ramp
    cr.elements[0].color = (float(color1[0]), float(color1[1]), float(color1[2]), 1.0)
    cr.elements[1].color = (float(color2[0]), float(color2[1]), float(color2[2]), 1.0)

    alpha = opacity / 100.0
    emission.inputs["Strength"].default_value = 1.0

    nt.links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], gradient.inputs["Vector"])
    nt.links.new(gradient.outputs["Fac"], float_curve.inputs["Value"])
    nt.links.new(float_curve.outputs["Value"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], emission.inputs["Color"])
    nt.links.new(transparent.outputs["BSDF"], mix_shader.inputs[1])
    nt.links.new(emission.outputs["Emission"], mix_shader.inputs[2])
    nt.links.new(mix_shader.outputs["Shader"], out.inputs["Surface"])

    if mask_info is not None:
        from . import material_opacity_mask
        val = nt.nodes.new("ShaderNodeValue")
        val.location = (-600, -260)
        val.outputs[0].default_value = alpha
        alpha_out = material_opacity_mask.multiply_alpha_by_mask(
            nt, val.outputs[0],
            mask_object=getattr(mask_info, "space_object", None),
            mask_image=getattr(mask_info, "image", None),
        )
        if alpha_out is not None:
            nt.links.new(alpha_out, mix_shader.inputs["Fac"])
        else:
            mix_shader.inputs["Fac"].default_value = alpha
    else:
        mix_shader.inputs["Fac"].default_value = alpha

    r1, g1, b1 = float(color1[0]), float(color1[1]), float(color1[2])
    try:
        mat.diffuse_color = (r1, g1, b1, alpha)
    except Exception:  # noqa: BLE001
        pass
    try:
        mat.update_tag()
    except Exception:  # noqa: BLE001
        pass
    return mat


def _endpoint_uv_for_entry(entry, width_mm: float, height_mm: float):
    if not getattr(entry, "use_gradient_endpoints", False):
        return None
    if width_mm < 1e-6 or height_mm < 1e-6:
        return None
    su = float(getattr(entry, "gradient_start_x_mm", 0.0) or 0.0) / width_mm
    sv = float(getattr(entry, "gradient_start_y_mm", 0.0) or 0.0) / height_mm
    eu = float(getattr(entry, "gradient_end_x_mm", 0.0) or 0.0) / width_mm
    ev = float(getattr(entry, "gradient_end_y_mm", 0.0) or 0.0) / height_mm
    return (su, sv, eu, ev)


def _ensure_material(
    entry, width_mm: float = 182.0, height_mm: float = 257.0, *, mask_info=None,
) -> bpy.types.Material:
    fill_id = str(getattr(entry, "id", "") or "")
    name = _material_name(fill_id)
    fill_type = str(getattr(entry, "fill_type", "solid") or "solid")
    opacity = float(getattr(entry, "opacity", 100.0) or 100.0)
    color = tuple(entry.color)

    if fill_type == "gradient":
        color2 = tuple(entry.color2)
        grad_type = str(getattr(entry, "gradient_type", "linear") or "linear")
        angle = float(getattr(entry, "gradient_angle", 0.0) or 0.0)
        ep_uv = _endpoint_uv_for_entry(entry, width_mm, height_mm)
        pending = None
        try:
            raw = entry.get("_pending_curve_points")
            if raw is not None:
                del entry["_pending_curve_points"]
                import json as _json
                parsed = _json.loads(raw) if isinstance(raw, str) else raw
                pending = [(float(p[0]), float(p[1]), str(p[2])) for p in parsed if len(p) >= 3]
        except Exception:  # noqa: BLE001
            pass
        return _ensure_gradient_material(
            name, color, color2, grad_type, angle, opacity,
            endpoint_uv=ep_uv, curve_points=pending, mask_info=mask_info,
        )
    return _ensure_solid_material(name, color, opacity, mask_info=mask_info)


def ensure_fill_real_object(
    *,
    scene: bpy.types.Scene,
    entry,
    page,
    folder_id: str = "",
) -> Optional[bpy.types.Object]:
    if scene is None or entry is None:
        return None
    fill_id = str(getattr(entry, "id", "") or "")
    if not fill_id:
        return None

    work = getattr(scene, "bmanga_work", None)
    paper = getattr(work, "paper", None) if work is not None else None
    canvas_w_mm = float(getattr(paper, "canvas_width_mm", 182.0) or 182.0)
    canvas_h_mm = float(getattr(paper, "canvas_height_mm", 257.0) or 257.0)

    use_region = bool(getattr(entry, "use_region", False))
    lasso_json = str(getattr(entry, "lasso_points_json", "") or "")
    lasso_points = None
    if lasso_json:
        import json
        try:
            lasso_points = json.loads(lasso_json)
        except (json.JSONDecodeError, TypeError):
            lasso_points = None
        if lasso_points is not None and len(lasso_points) < 3:
            lasso_points = None

    if use_region:
        rw = float(getattr(entry, "region_width_mm", 0.0) or 0.0)
        rh = float(getattr(entry, "region_height_mm", 0.0) or 0.0)
        if rw < 0.1 or rh < 0.1:
            use_region = False

    if use_region:
        mesh_w_mm = rw
        mesh_h_mm = rh
        rx = float(getattr(entry, "region_x_mm", 0.0) or 0.0)
        ry = float(getattr(entry, "region_y_mm", 0.0) or 0.0)
    else:
        mesh_w_mm = canvas_w_mm
        mesh_h_mm = canvas_h_mm

    parent_kind, parent_key, stamp_folder = _resolve_parent_for_entry(entry, page, folder_id)
    mask_info = None
    if parent_kind == "coma" and parent_key and ":" in parent_key:
        try:
            from . import coma_content_mask
            mask_info = coma_content_mask.ensure_viewport_mask_for_parent(
                scene, work, parent_key,
            )
        except Exception:  # noqa: BLE001
            pass

    mat = _ensure_material(entry, canvas_w_mm, canvas_h_mm, mask_info=mask_info)

    mesh = bpy.data.meshes.get(_mesh_name(fill_id))
    if mesh is None:
        mesh = bpy.data.meshes.new(_mesh_name(fill_id))

    if lasso_points is not None and use_region:
        center_x = rx + mesh_w_mm * 0.5
        center_y = ry + mesh_h_mm * 0.5
        if not _rebuild_lasso_mesh(mesh, lasso_points, center_x, center_y, canvas_w_mm, canvas_h_mm):
            _rebuild_mesh(mesh, mm_to_m(mesh_w_mm), mm_to_m(mesh_h_mm))
    else:
        _rebuild_mesh(mesh, mm_to_m(mesh_w_mm), mm_to_m(mesh_h_mm))
    if not mesh.materials:
        mesh.materials.append(mat)
    elif mesh.materials[0] is not mat:
        mesh.materials[0] = mat

    obj_name = _object_name(fill_id)
    obj = on.find_object_by_bmanga_id(fill_id, kind="fill")
    if obj is None:
        obj = bpy.data.objects.get(obj_name)
    if object_preserve.is_preserved(obj):
        obj = None
    if obj is not None and obj.type != "MESH":
        object_preserve.preserve_object(obj, "古いフィル実体を保持")
        obj = None
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        obj.data = mesh

    ox_mm, oy_mm = entry_page_offset_mm(scene, work, entry, page)
    if use_region:
        obj.location.x = mm_to_m(rx + mesh_w_mm * 0.5 + ox_mm)
        obj.location.y = mm_to_m(ry + mesh_h_mm * 0.5 + oy_mm)
    else:
        obj.location.x = mm_to_m(canvas_w_mm * 0.5 + ox_mm)
        obj.location.y = mm_to_m(canvas_h_mm * 0.5 + oy_mm)
    # balloon/image/text と同じく、メッシュ・UV は中心基準のローカル座標で
    # 構築済みのため、location 設定直後に Z回転を足すだけで中心軸回転になる。
    # グラデーションの向き (角度指定・端点指定とも) はメッシュのUV/材質ノード
    # 側でローカル空間に焼き込まれているため、オブジェクト回転と一緒に
    # 剛体としてそのまま回転し、二重回転にはならない。
    # ただし端点指定グラデーション (is_gradient_endpoint_rotation_locked) は、
    # ドラッグ用の始点/終点ハンドル (別オブジェクト) とオーバーレイ接続線
    # (ui/overlay.py) が絶対mm座標を直接参照しておりオブジェクト回転に
    # 追従しないため、operators/object_rotation_fill.py 側で回転リングを
    # 無効化した上、ここでも rotation_deg に値が残っていても常に0扱いにして
    # (トグルの順序に関わらず) 不整合を出さないようにしている。
    # 判定条件は fill_type=="gradient" かつ use_gradient_endpoints の両方
    # (is_gradient_endpoint_rotation_locked に集約。単独条件で判定すると
    # 端点グラデ→ベタ塗りへ変更した直後に回転が永久に無反応になる不整合
    # が起きる)。
    if is_gradient_endpoint_rotation_locked(entry):
        obj.rotation_euler[2] = 0.0
    else:
        obj.rotation_euler[2] = math.radians(float(getattr(entry, "rotation_deg", 0.0) or 0.0))

    _ensure_parent_collection(scene, parent_kind, parent_key)
    los.stamp_layer_object(
        obj,
        kind="fill",
        bmanga_id=fill_id,
        title=str(getattr(entry, "title", "") or fill_id),
        z_index=_fill_z_index(scene, fill_id),
        parent_kind=parent_kind,
        parent_key=parent_key,
        folder_id=stamp_folder,
        scene=scene,
        apply_page_offset=False,
    )
    obj.hide_viewport = not bool(getattr(entry, "visible", True))
    obj.hide_render = not bool(getattr(entry, "visible", True))
    obj.hide_select = False
    if parent_kind == "coma" and parent_key and ":" in parent_key:
        _sync_coma_mask_position(scene, work, parent_key)
    global _HANDLE_WRITEBACK_GUARD
    _HANDLE_WRITEBACK_GUARD = True
    try:
        _ensure_gradient_handles(scene, entry, page, ox_mm, oy_mm)
    finally:
        _HANDLE_WRITEBACK_GUARD = False
    return obj


def _handle_object_name(fill_id: str, end: str) -> str:
    return f"grad_handle_{_safe_token(fill_id)}_{end}"


def _ensure_handle_mesh(name: str) -> bpy.types.Mesh:
    """十字型の小さな面付きメッシュ (レンダーモードでも表示される)."""
    mesh = bpy.data.meshes.get(name)
    if mesh is not None:
        if len(mesh.vertices) == 8 and abs(mesh.vertices[1].co.x - _HANDLE_MESH_ARM_M) < 1e-6:
            return mesh
        bpy.data.meshes.remove(mesh)
        mesh = None
    mesh = bpy.data.meshes.new(name)
    a = _HANDLE_MESH_ARM_M
    t = a * 0.25
    verts = [
        (-a, -t, 0), (a, -t, 0), (a, t, 0), (-a, t, 0),
        (-t, -a, 0), (t, -a, 0), (t, a, 0), (-t, a, 0),
    ]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7)]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


def _ensure_handle_material(end_tag: str) -> bpy.types.Material:
    mat_name = f"BManga_GradHandle_{end_tag}"
    mat = bpy.data.materials.get(mat_name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    emit = tree.nodes.new("ShaderNodeEmission")
    if end_tag == "start":
        emit.inputs["Color"].default_value = (0.1, 0.5, 0.9, 1.0)
    else:
        emit.inputs["Color"].default_value = (0.9, 0.2, 0.2, 1.0)
    emit.inputs["Strength"].default_value = 3.0
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(emit.outputs[0], out.inputs[0])
    mat.diffuse_color = (0.1, 0.5, 0.9, 1.0) if end_tag == "start" else (0.9, 0.2, 0.2, 1.0)
    try:
        mat.update_tag()
    except Exception:  # noqa: BLE001
        pass
    return mat


def _ensure_gradient_handles(
    scene, entry, page, ox_mm: float, oy_mm: float,
) -> None:
    fill_id = str(getattr(entry, "id", "") or "")
    if not fill_id:
        return
    if (
        str(getattr(entry, "fill_type", "") or "") != "gradient"
        or not bool(getattr(entry, "use_gradient_endpoints", False))
    ):
        _remove_gradient_handles(fill_id)
        return
    sx = float(getattr(entry, "gradient_start_x_mm", 0.0) or 0.0)
    sy = float(getattr(entry, "gradient_start_y_mm", 0.0) or 0.0)
    ex = float(getattr(entry, "gradient_end_x_mm", 0.0) or 0.0)
    ey = float(getattr(entry, "gradient_end_y_mm", 0.0) or 0.0)
    visible = bool(getattr(entry, "visible", True))
    for end_tag, lx, ly in (("start", sx, sy), ("end", ex, ey)):
        obj = _find_gradient_handle(fill_id, end_tag)
        handle_mesh_name = f"grad_handle_mesh_{end_tag}"
        handle_mesh = _ensure_handle_mesh(handle_mesh_name)
        is_new = obj is None
        if is_new:
            obj = bpy.data.objects.new(_handle_object_name(fill_id, end_tag), handle_mesh)
        elif obj.type == "EMPTY" or obj.data is not handle_mesh:
            obj.data = handle_mesh
        handle_mat = _ensure_handle_material(end_tag)
        if not obj.data.materials:
            obj.data.materials.append(handle_mat)
        elif obj.data.materials[0] is not handle_mat:
            obj.data.materials[0] = handle_mat
        obj.show_in_front = True
        obj[on.PROP_KIND] = GRADIENT_HANDLE_KIND
        obj[on.PROP_ID] = fill_id
        obj["bmanga_handle_end"] = end_tag
        obj[on.PROP_MANAGED] = True
        obj.location.x = mm_to_m(lx + ox_mm)
        obj.location.y = mm_to_m(ly + oy_mm)
        obj.location.z = _HANDLE_Z_M
        if is_new:
            obj.hide_viewport = True
        obj.hide_render = True
        obj.hide_select = False
        for coll in list(obj.users_collection):
            coll.objects.unlink(obj)
        scene_coll = scene.collection
        if obj.name not in scene_coll.objects:
            scene_coll.objects.link(obj)


def _find_gradient_handle(fill_id: str, end: str):
    for obj in bpy.data.objects:
        if (
            obj.get(on.PROP_KIND) == GRADIENT_HANDLE_KIND
            and str(obj.get(on.PROP_ID, "") or "") == fill_id
            and str(obj.get("bmanga_handle_end", "") or "") == end
            and not object_preserve.is_preserved(obj)
        ):
            return obj
    return None


def _remove_gradient_handles(fill_id: str) -> None:
    for obj in list(bpy.data.objects):
        if object_preserve.is_preserved(obj):
            continue
        if obj.get(on.PROP_KIND) != GRADIENT_HANDLE_KIND:
            continue
        if str(obj.get(on.PROP_ID, "") or "") != fill_id:
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass


def get_gradient_curve_node(fill_id: str):
    """フィルIDからグラデーションマテリアルの FloatCurve ノードを返す."""
    mat_name = _material_name(fill_id)
    mat = bpy.data.materials.get(mat_name)
    if mat is None or not mat.use_nodes:
        return None
    for node in mat.node_tree.nodes:
        if node.type == "CURVE_FLOAT":
            return node
    return None


def get_gradient_curve_points(fill_id: str) -> list[tuple[float, float, str]]:
    """フィルIDのグラデーション濃度カーブポイントを取得."""
    node = get_gradient_curve_node(fill_id)
    if node is None:
        return []
    curve = node.mapping.curves[0]
    return [(p.location[0], p.location[1], p.handle_type) for p in curve.points]


def set_gradient_curve_points(
    fill_id: str,
    points: list[tuple[float, float, str]] | None,
) -> bool:
    """対象の濃度カーブを退避済みポイントへ戻す。"""

    node = get_gradient_curve_node(fill_id)
    if node is None or points is None:
        return False
    _apply_curve_points(node, points)
    return True


def sync_gradient_handle_visibility(context) -> None:
    """選択状態に応じてグラデーションハンドルの表示/非表示を切り替える."""
    from . import object_selection

    keys = set(object_selection.get_keys(context))
    selected_fill_ids: set[str] = set()
    for key in keys:
        kind, _sub, item_id = object_selection.parse_key(key)
        if kind == "fill":
            selected_fill_ids.add(item_id)
        elif kind == "gradient_handle":
            selected_fill_ids.add(item_id)
    for obj in bpy.data.objects:
        if obj.get(on.PROP_KIND) != GRADIENT_HANDLE_KIND:
            continue
        fill_id = str(obj.get(on.PROP_ID, "") or "")
        obj.hide_viewport = fill_id not in selected_fill_ids


def gradient_handle_positions_mm(context, fill_id: str):
    """選択中グラデーションの始点・終点 mm 座標を返す (overlay 用)."""
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    entry = find_fill_entry(scene, fill_id)
    if entry is None:
        return None
    work = getattr(scene, "bmanga_work", None)
    page = page_for_entry(scene, work, entry) if work else None
    ox_mm, oy_mm = entry_page_offset_mm(scene, work, entry, page)
    sx = float(getattr(entry, "gradient_start_x_mm", 0.0) or 0.0) + ox_mm
    sy = float(getattr(entry, "gradient_start_y_mm", 0.0) or 0.0) + oy_mm
    ex = float(getattr(entry, "gradient_end_x_mm", 0.0) or 0.0) + ox_mm
    ey = float(getattr(entry, "gradient_end_y_mm", 0.0) or 0.0) + oy_mm
    return sx, sy, ex, ey


def _on_depsgraph_update_post_handles(scene, depsgraph) -> None:
    global _HANDLE_WRITEBACK_GUARD
    if _HANDLE_WRITEBACK_GUARD or auto_sync_suspended():
        return
    work = getattr(scene, "bmanga_work", None)
    if work is None or not getattr(work, "loaded", False):
        return
    for update in depsgraph.updates:
        obj = getattr(update, "id", None)
        if obj is None or not isinstance(obj, bpy.types.Object):
            continue
        if obj.get(on.PROP_KIND) != GRADIENT_HANDLE_KIND:
            continue
        fill_id = str(obj.get(on.PROP_ID, "") or "")
        end_tag = str(obj.get("bmanga_handle_end", "") or "")
        if not fill_id or end_tag not in {"start", "end"}:
            continue
        entry = find_fill_entry(scene, fill_id)
        if entry is None:
            continue
        page = page_for_entry(scene, work, entry)
        ox_mm, oy_mm = entry_page_offset_mm(scene, work, entry, page)
        from .geom import m_to_mm
        new_x = m_to_mm(obj.location.x) - ox_mm
        new_y = m_to_mm(obj.location.y) - oy_mm
        _HANDLE_WRITEBACK_GUARD = True
        try:
            if end_tag == "start":
                entry.gradient_start_x_mm = new_x
                entry.gradient_start_y_mm = new_y
            else:
                entry.gradient_end_x_mm = new_x
                entry.gradient_end_y_mm = new_y
        finally:
            _HANDLE_WRITEBACK_GUARD = False


def find_fill_entry(scene, fill_id: str):
    coll = getattr(scene, "bmanga_fill_layers", None) if scene is not None else None
    if coll is None:
        return None
    for entry in coll:
        if str(getattr(entry, "id", "") or "") == fill_id:
            return entry
    return None


def cleanup_orphan_fill_objects(scene: bpy.types.Scene) -> int:
    coll = getattr(scene, "bmanga_fill_layers", None) if scene is not None else None
    valid = {str(getattr(entry, "id", "") or "") for entry in coll or []}
    removed = 0
    for obj in list(bpy.data.objects):
        if object_preserve.is_preserved(obj):
            continue
        kind = obj.get(on.PROP_KIND)
        if kind not in {"fill", GRADIENT_HANDLE_KIND}:
            continue
        bid = str(obj.get(on.PROP_ID, "") or "")
        if bid in valid:
            continue
        if kind == GRADIENT_HANDLE_KIND:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:  # noqa: BLE001
                pass
        else:
            object_preserve.preserve_object(obj, "作品データにないフィル実体を保持")
        removed += 1
    return removed


def remove_fill_real_object(fill_id: str) -> bool:
    if not fill_id:
        return False
    _remove_gradient_handles(fill_id)
    removed = False
    for obj in list(bpy.data.objects):
        if object_preserve.is_preserved(obj):
            continue
        if obj.get(on.PROP_KIND) != "fill":
            continue
        bid = str(obj.get(on.PROP_ID, "") or "")
        if bid != fill_id:
            continue
        data = getattr(obj, "data", None)
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            _logger.exception("fill real object: removal failed")
            continue
        if data is not None and getattr(data, "users", 0) == 0:
            try:
                if isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
            except Exception:  # noqa: BLE001
                pass
        removed = True
    mat_name = _material_name(fill_id)
    mat = bpy.data.materials.get(mat_name)
    if mat is not None and getattr(mat, "users", 0) == 0:
        try:
            bpy.data.materials.remove(mat)
        except Exception:  # noqa: BLE001
            pass
    return removed


def sync_all_fill_real_objects(scene: bpy.types.Scene, work) -> int:
    if scene is None or work is None:
        return 0
    coll = getattr(scene, "bmanga_fill_layers", None)
    if coll is None:
        return 0
    count = 0
    for entry in coll:
        page = page_for_entry(scene, work, entry)
        if ensure_fill_real_object(scene=scene, entry=entry, page=page) is not None:
            count += 1
    cleanup_orphan_fill_objects(scene)
    return count


def on_fill_entry_changed(entry) -> bool:
    if auto_sync_suspended():
        return False
    if _HANDLE_WRITEBACK_GUARD:
        return False
    scene = bpy.context.scene if bpy.context is not None else None
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if scene is None or work is None or entry is None:
        return False
    fill_id = str(getattr(entry, "id", "") or "")
    if not fill_id:
        return False
    page = page_for_entry(scene, work, entry)
    ensure_fill_real_object(scene=scene, entry=entry, page=page)
    return True


def register() -> None:
    if _on_depsgraph_update_post_handles not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update_post_handles)


def unregister() -> None:
    if _on_depsgraph_update_post_handles in bpy.app.handlers.depsgraph_update_post:
        try:
            bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update_post_handles)
        except ValueError:
            pass
