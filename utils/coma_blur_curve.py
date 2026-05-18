"""輪郭ぼかしの濃度カーブ共有ヘルパー."""

from __future__ import annotations

from typing import Iterable, Sequence

try:
    import bpy
except ModuleNotFoundError:  # pragma: no cover - Blender外のJSON処理用
    bpy = None  # type: ignore[assignment]

CURVE_NODE_NAME = "BName_ComaBlurCurve"
CURVE_MATERIAL_PROP = "bname_blur_curve_source"
DEFAULT_CURVE_TEXT = "0.0000,0.0000;1.0000,1.0000"
DEFAULT_POINTS: tuple[tuple[float, float], ...] = ((0.0, 0.0), (1.0, 1.0))


def parse_points(value: object) -> tuple[tuple[float, float], ...]:
    """保存文字列 / JSON 配列を 0..1 の点列へ正規化する."""
    raw: list[tuple[float, float]] = []
    if isinstance(value, str):
        for part in value.split(";"):
            bits = [b.strip() for b in part.split(",")]
            if len(bits) != 2:
                continue
            try:
                raw.append((float(bits[0]), float(bits[1])))
            except ValueError:
                continue
    elif isinstance(value, Iterable):
        for item in value:
            try:
                x, y = item
                raw.append((float(x), float(y)))
            except Exception:  # noqa: BLE001
                continue
    return normalize_points(raw)


def normalize_points(points: Sequence[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    cleaned: list[tuple[float, float]] = []
    for x, y in points:
        cleaned.append((_clamp01(x), _clamp01(y)))
    if len(cleaned) < 2:
        return DEFAULT_POINTS
    cleaned.sort(key=lambda p: p[0])
    if cleaned[0][0] > 1.0e-4:
        cleaned.insert(0, (0.0, cleaned[0][1]))
    else:
        cleaned[0] = (0.0, cleaned[0][1])
    if cleaned[-1][0] < 1.0 - 1.0e-4:
        cleaned.append((1.0, cleaned[-1][1]))
    else:
        cleaned[-1] = (1.0, cleaned[-1][1])

    deduped: list[tuple[float, float]] = []
    for x, y in cleaned:
        if deduped and abs(deduped[-1][0] - x) < 1.0e-4:
            deduped[-1] = (x, y)
        else:
            deduped.append((x, y))
    return tuple(deduped[:16]) if len(deduped) >= 2 else DEFAULT_POINTS


def points_to_text(points: Sequence[tuple[float, float]]) -> str:
    normalized = normalize_points(points)
    return ";".join(f"{x:.4f},{y:.4f}" for x, y in normalized)


def points_to_json(points: Sequence[tuple[float, float]]) -> list[list[float]]:
    return [[round(x, 4), round(y, 4)] for x, y in normalize_points(points)]


def find_curve_node(mat: bpy.types.Material | None):
    nt = getattr(mat, "node_tree", None)
    if nt is None:
        return None
    node = nt.nodes.get(CURVE_NODE_NAME)
    if node is not None and node.bl_idname == "ShaderNodeFloatCurve":
        return node
    for candidate in nt.nodes:
        if candidate.bl_idname == "ShaderNodeFloatCurve" and candidate.name == CURVE_NODE_NAME:
            return candidate
    return None


def read_node_points(node) -> tuple[tuple[float, float], ...]:
    try:
        curve = node.mapping.curves[0]
        return normalize_points([(float(p.location.x), float(p.location.y)) for p in curve.points])
    except Exception:  # noqa: BLE001
        return DEFAULT_POINTS


def apply_points_to_node(node, points: Sequence[tuple[float, float]]) -> None:
    normalized = normalize_points(points)
    try:
        mapping = node.mapping
        mapping.initialize()
        curve = mapping.curves[0]
        while len(curve.points) > 2:
            curve.points.remove(curve.points[-2])
        curve.points[0].location = normalized[0]
        curve.points[-1].location = normalized[-1]
        for x, y in normalized[1:-1]:
            curve.points.new(x, y)
        for point in curve.points:
            point.handle_type = "AUTO"
        mapping.update()
    except Exception:  # noqa: BLE001
        pass


def ensure_curve_node(
    nt: bpy.types.NodeTree,
    *,
    stored_points: object,
    material: bpy.types.Material | None = None,
) -> bpy.types.Node:
    existing = nt.nodes.get(CURVE_NODE_NAME)
    if existing is not None and existing.bl_idname != "ShaderNodeFloatCurve":
        nt.nodes.remove(existing)
        existing = None
    node = existing or nt.nodes.new("ShaderNodeFloatCurve")
    node.name = CURVE_NODE_NAME
    node.label = "輪郭ぼかし"
    node.location = (-80, -250)

    stored_text = points_to_text(parse_points(stored_points))
    last_source = str(material.get(CURVE_MATERIAL_PROP, "") or "") if material is not None else ""
    if existing is not None and last_source == stored_text:
        points = read_node_points(existing)
    else:
        points = parse_points(stored_text)
    apply_points_to_node(node, points)
    if material is not None:
        material[CURVE_MATERIAL_PROP] = stored_text
    return node


def sync_material_curve_to_border(border, mat: bpy.types.Material | None) -> bool:
    node = find_curve_node(mat)
    if node is None or border is None or not hasattr(border, "blur_curve_points"):
        return False
    text = points_to_text(read_node_points(node))
    if str(getattr(border, "blur_curve_points", "") or "") == text:
        return False
    border.blur_curve_points = text
    return True


def active_curve_node_for_coma(coma):
    mat = active_curve_material_for_coma(coma)
    return find_curve_node(mat)


def active_curve_material_for_coma(coma):
    coma_id = str(getattr(coma, "id", "") or getattr(coma, "coma_id", "") or "")
    if not coma_id:
        return None
    exact_owner = _owner_id_for_coma(coma)
    fallback = None
    for obj in bpy.data.objects:
        try:
            if obj.type != "MESH" or not str(obj.name).startswith("coma_plane_"):
                continue
            owner = str(obj.get("bname_coma_plane_owner_id", "") or "")
            if exact_owner and owner != exact_owner:
                continue
            if not exact_owner and not owner.endswith(f":{coma_id}"):
                continue
            mat = obj.data.materials[0] if obj.data.materials else None
            if find_curve_node(mat) is not None:
                if exact_owner:
                    return mat
                fallback = mat
        except Exception:  # noqa: BLE001
            continue
    if fallback is not None:
        return fallback
    if not exact_owner:
        return None
    for obj in bpy.data.objects:
        try:
            if obj.type != "MESH" or not str(obj.name).startswith("coma_plane_"):
                continue
            if str(obj.get("bname_coma_plane_owner_id", "") or "") != exact_owner:
                continue
            return obj.data.materials[0] if obj.data.materials else None
        except Exception:  # noqa: BLE001
            continue
    return None


def _owner_id_for_coma(coma) -> str:
    try:
        target_ptr = int(coma.as_pointer())
    except Exception:  # noqa: BLE001
        target_ptr = 0
    if not target_ptr:
        return ""
    scene = getattr(bpy.context, "scene", None)
    work = getattr(scene, "bname_work", None) if scene is not None else None
    if work is None:
        return ""
    for page in getattr(work, "pages", []) or []:
        page_id = str(getattr(page, "id", "") or "")
        for candidate in getattr(page, "comas", []) or []:
            try:
                if int(candidate.as_pointer()) != target_ptr:
                    continue
                coma_id = str(getattr(candidate, "id", "") or getattr(candidate, "coma_id", "") or "")
                return f"{page_id}:{coma_id}" if page_id and coma_id else ""
            except Exception:  # noqa: BLE001
                continue
    for candidate in getattr(work, "shared_comas", []) or []:
        try:
            if int(candidate.as_pointer()) != target_ptr:
                continue
            coma_id = str(getattr(candidate, "id", "") or getattr(candidate, "coma_id", "") or "")
            return f"outside:{coma_id}" if coma_id else ""
        except Exception:  # noqa: BLE001
            continue
    return ""


def sync_active_coma_curve_to_border(coma) -> bool:
    return sync_material_curve_to_border(getattr(coma, "border", None), active_curve_material_for_coma(coma))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
