"""Saved inner-line extraction and display for B-MANGA Liner."""

from __future__ import annotations

from dataclasses import dataclass
import math

import bmesh
import bpy
from mathutils import Vector

from . import (
    curve_smoothing_nodes,
    inner_line_chains,
    intersection_shell_node_helpers,
    modifier_stack,
    vertex_analysis,
)
from .core import (
    GENERATED_LINE_ATTR,
    GN_MODIFIER_NAME,
    INTERSECTION_MODIFIER_NAME,
    INTERSECTION_MODIFIER_PREFIX,
    MODIFIER_NAME,
    OUTLINE_LOCAL_SUBDIVISION_MODIFIER_NAME,
    OUTLINE_WIDTH_ATTR_MODIFIER_NAME,
    PROP_LINES_HIDDEN,
    SELECTION_LINE_MODIFIER_NAME,
    SHEET_OUTLINE_MODIFIER_NAME,
    VG_INNER_LINE_WIDTH,
)


CACHE_TREE_NAME = "BML_InnerLines_Cached"
CACHE_OBJECT_PREFIX = "BML_InnerLineCache"
CACHE_COLLECTION_NAME = "BML_InnerLineCacheObjects"
CACHE_OBJECT_PROP = "bml_inner_cache_object"
CACHE_OWNER_PROP = "bml_inner_cache_owner"

_CACHE_OBJECT_SOCKET = "保存済み稜谷線"
_THICKNESS_SOCKET = "線の太さ"
_OFFSET_SOCKET = "オフセット"
_MATERIAL_SOCKET = "マテリアル"
_MIDPOINT_FACTOR_SOCKET = "中間頂点の線幅調整"
_MIDPOINT_JITTER_SOCKET = "中間頂点の乱れ (%)"
_RESAMPLE_COUNT_SOCKET = "線の分割数"
_WIDTH_CURVE_25_SOCKET = "線幅カーブ25%"
_WIDTH_CURVE_50_SOCKET = "線幅カーブ50%"
_WIDTH_CURVE_75_SOCKET = "線幅カーブ75%"

_NORMAL_ATTR = "BML_InnerCachedNormal"
_WIDTH_ATTR = "BML_InnerCachedWidth"
_SOURCE_INDEX_ATTR = "BML_InnerCachedSourceIndex"
_SUBDIVIDE_LABEL = "BML_InnerCachedSubdivideV2"
_PROFILE_LABEL = "BML_InnerCachedProfile"
# V2: Join Geometry の結合順修正（2026-07-09、素材スロット順バグ）に伴い
# ラベルを世代更新。保存済み.blendの旧ツリーをラベル不一致で必ず再構築させる
# （_tree_valid 参照。outline_setup.py の _SHEET_TUBE_ANGLE_SPLIT_LABEL と同方式）。
_GENERATED_MARK_LABEL = "BML_InnerCachedGeneratedMarkV2"
_PROFILE_RESOLUTION = 12
_MIN_CURVE_TO_MESH_SCALE = 0.04
_EPS = 1.0e-7


def _needs_curve_subdivision(
    midpoint_factor: float | None,
    midpoint_jitter_percent: float | None,
) -> bool:
    try:
        if abs(float(midpoint_factor or 0.0)) > _EPS:
            return True
    except (TypeError, ValueError):
        pass
    try:
        return abs(float(midpoint_jitter_percent or 0.0)) > _EPS
    except (TypeError, ValueError):
        return False


@dataclass
class _CachedLineMesh:
    vertices: list[tuple[float, float, float]]
    edges: list[tuple[int, int]]
    normals: list[tuple[float, float, float]]
    widths: list[float]
    source_indices: list[int]


def _setup_interface(tree: bpy.types.NodeTree) -> None:
    tree.interface.new_socket(
        name="Geometry",
        in_out="INPUT",
        socket_type="NodeSocketGeometry",
    )
    tree.interface.new_socket(
        name="Geometry",
        in_out="OUTPUT",
        socket_type="NodeSocketGeometry",
    )
    tree.interface.new_socket(
        name=_CACHE_OBJECT_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketObject",
    )
    thickness = tree.interface.new_socket(
        name=_THICKNESS_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    thickness.default_value = 0.0005
    thickness.min_value = 0.00001
    thickness.max_value = 1.0
    offset = tree.interface.new_socket(
        name=_OFFSET_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    offset.default_value = 0.0
    offset.min_value = -1.0
    offset.max_value = 1.0
    tree.interface.new_socket(
        name=_MATERIAL_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketMaterial",
    )
    midpoint = tree.interface.new_socket(
        name=_MIDPOINT_FACTOR_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    midpoint.default_value = 0.0
    midpoint.min_value = -1.0
    midpoint.max_value = 1.0
    jitter = tree.interface.new_socket(
        name=_MIDPOINT_JITTER_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    jitter.default_value = 0.0
    jitter.min_value = 0.0
    jitter.max_value = 50.0
    cuts = tree.interface.new_socket(
        name=_RESAMPLE_COUNT_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketInt",
    )
    cuts.default_value = 4
    cuts.min_value = 1
    cuts.max_value = 32
    for name, default in (
        (_WIDTH_CURVE_25_SOCKET, 0.25),
        (_WIDTH_CURVE_50_SOCKET, 0.50),
        (_WIDTH_CURVE_75_SOCKET, 0.75),
    ):
        socket = tree.interface.new_socket(
            name=name,
            in_out="INPUT",
            socket_type="NodeSocketFloat",
        )
        socket.default_value = default
        socket.min_value = 0.0
        socket.max_value = 1.0


def _vector_scale_input(node):
    return node.inputs.get("Scale") or node.inputs[min(3, len(node.inputs) - 1)]


def _create_display_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.new(name=CACHE_TREE_NAME, type="GeometryNodeTree")
    _setup_interface(tree)
    nodes = tree.nodes
    links = tree.links

    gin = nodes.new("NodeGroupInput")
    gin.location = (-980, 0)
    gout = nodes.new("NodeGroupOutput")
    gout.location = (1120, 0)

    obj_info = nodes.new("GeometryNodeObjectInfo")
    obj_info.location = (-780, -260)
    obj_info.transform_space = "RELATIVE"
    links.new(gin.outputs[_CACHE_OBJECT_SOCKET], obj_info.inputs["Object"])

    normal_attr = nodes.new("GeometryNodeInputNamedAttribute")
    normal_attr.location = (-780, -560)
    normal_attr.data_type = "FLOAT_VECTOR"
    normal_attr.inputs["Name"].default_value = _NORMAL_ATTR

    offset_amount = nodes.new("ShaderNodeMath")
    offset_amount.location = (-560, -560)
    offset_amount.operation = "MULTIPLY"
    links.new(gin.outputs[_THICKNESS_SOCKET], offset_amount.inputs[0])
    links.new(gin.outputs[_OFFSET_SOCKET], offset_amount.inputs[1])

    offset_vector = nodes.new("ShaderNodeVectorMath")
    offset_vector.location = (-360, -520)
    offset_vector.operation = "SCALE"
    links.new(normal_attr.outputs["Attribute"], offset_vector.inputs[0])
    links.new(offset_amount.outputs[0], _vector_scale_input(offset_vector))

    set_position = nodes.new("GeometryNodeSetPosition")
    set_position.location = (-540, -260)
    links.new(obj_info.outputs["Geometry"], set_position.inputs["Geometry"])
    links.new(offset_vector.outputs[0], set_position.inputs["Offset"])

    m2c = nodes.new("GeometryNodeMeshToCurve")
    m2c.location = (-320, -260)
    links.new(set_position.outputs["Geometry"], m2c.inputs["Mesh"])

    smoothing_enabled = nodes.new("FunctionNodeCompare")
    smoothing_enabled.data_type = "INT"
    smoothing_enabled.operation = "GREATER_THAN"
    smoothing_enabled.location = (-300, -420)
    smoothing_enabled.inputs[3].default_value = 0
    links.new(gin.outputs[_RESAMPLE_COUNT_SOCKET], smoothing_enabled.inputs[2])
    smooth_curve = curve_smoothing_nodes.add_corner_preserving_bezier(
        nodes,
        links,
        m2c.outputs["Curve"],
        smoothing_enabled.outputs["Result"],
        (-120, -80),
        label="BML Inner Cached Shape",
    )

    subdivide = nodes.new("GeometryNodeSubdivideCurve")
    subdivide.label = _SUBDIVIDE_LABEL
    subdivide.location = (-80, -260)
    links.new(smooth_curve, subdivide.inputs["Curve"])
    links.new(gin.outputs[_RESAMPLE_COUNT_SOCKET], subdivide.inputs["Cuts"])

    midpoint_scale = intersection_shell_node_helpers.add_curve_width_scale(
        nodes,
        links,
        gin,
        120,
        midpoint_factor_socket=_MIDPOINT_FACTOR_SOCKET,
        midpoint_jitter_socket=_MIDPOINT_JITTER_SOCKET,
        width_curve_sockets=(
            _WIDTH_CURVE_25_SOCKET,
            _WIDTH_CURVE_50_SOCKET,
            _WIDTH_CURVE_75_SOCKET,
        ),
        jitter_center_label="BML_InnerCachedJitterCenter",
    )
    width_attr = nodes.new("GeometryNodeInputNamedAttribute")
    width_attr.location = (120, 120)
    width_attr.data_type = "FLOAT"
    width_attr.inputs["Name"].default_value = _WIDTH_ATTR

    width_switch = nodes.new("GeometryNodeSwitch")
    width_switch.location = (300, 120)
    width_switch.input_type = "FLOAT"
    width_switch.inputs["False"].default_value = 1.0
    links.new(width_attr.outputs["Exists"], width_switch.inputs["Switch"])
    links.new(width_attr.outputs["Attribute"], width_switch.inputs["True"])

    combined_scale = nodes.new("ShaderNodeMath")
    combined_scale.location = (480, 120)
    combined_scale.operation = "MULTIPLY"
    links.new(midpoint_scale, combined_scale.inputs[0])
    links.new(width_switch.outputs["Output"], combined_scale.inputs[1])

    safe_scale = nodes.new("ShaderNodeMath")
    safe_scale.location = (660, 120)
    safe_scale.operation = "MAXIMUM"
    safe_scale.inputs[1].default_value = _MIN_CURVE_TO_MESH_SCALE
    links.new(combined_scale.outputs[0], safe_scale.inputs[0])

    radius_half = nodes.new("ShaderNodeMath")
    radius_half.location = (120, -560)
    radius_half.operation = "MULTIPLY"
    radius_half.inputs[1].default_value = 0.5
    links.new(gin.outputs[_THICKNESS_SOCKET], radius_half.inputs[0])

    circle = nodes.new("GeometryNodeCurvePrimitiveCircle")
    circle.label = _PROFILE_LABEL
    circle.location = (340, -560)
    circle.mode = "RADIUS"
    for inp in circle.inputs:
        if inp.name == "Resolution" and inp.enabled:
            inp.default_value = _PROFILE_RESOLUTION
    links.new(radius_half.outputs[0], circle.inputs["Radius"])

    c2m = nodes.new("GeometryNodeCurveToMesh")
    c2m.location = (520, -260)
    links.new(subdivide.outputs["Curve"], c2m.inputs["Curve"])
    links.new(circle.outputs["Curve"], c2m.inputs["Profile Curve"])
    if "Scale" in c2m.inputs:
        links.new(safe_scale.outputs[0], c2m.inputs["Scale"])
    if "Fill Caps" in c2m.inputs:
        c2m.inputs["Fill Caps"].default_value = True

    mark = nodes.new("GeometryNodeStoreNamedAttribute")
    mark.label = _GENERATED_MARK_LABEL
    mark.location = (700, -420)
    mark.data_type = "BOOLEAN"
    mark.domain = "FACE"
    mark.inputs["Name"].default_value = GENERATED_LINE_ATTR
    mark.inputs["Value"].default_value = True
    links.new(c2m.outputs["Mesh"], mark.inputs["Geometry"])

    smooth = nodes.new("GeometryNodeSetShadeSmooth")
    smooth.location = (760, -260)
    links.new(mark.outputs["Geometry"], smooth.inputs["Geometry"])

    setmat = nodes.new("GeometryNodeSetMaterial")
    setmat.location = (900, -260)
    links.new(smooth.outputs["Geometry"], setmat.inputs["Geometry"])
    links.new(gin.outputs[_MATERIAL_SOCKET], setmat.inputs["Material"])

    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (920, 0)
    # Join Geometry のマルチ入力は「後から接続したリンクが先頭（先に評価）」
    # という挙動を持つため、見た目の呼び出し順とは逆にsetmatを先・ginを後に
    # 接続し、結合順を「元メッシュ→ライン」にする（詳細は outline_setup.py の
    # Join 接続コメント参照）。
    links.new(setmat.outputs["Geometry"], join.inputs["Geometry"])
    links.new(gin.outputs["Geometry"], join.inputs["Geometry"])
    links.new(join.outputs["Geometry"], gout.inputs["Geometry"])
    return tree


def _find_socket_id(tree: bpy.types.NodeTree | None, name: str) -> str | None:
    if tree is None:
        return None
    for item in tree.interface.items_tree:
        if getattr(item, "item_type", None) == "SOCKET" and item.name == name:
            return getattr(item, "identifier", None)
    return None


def _tree_valid(tree: bpy.types.NodeTree | None) -> bool:
    if tree is None:
        return False
    return (
        _find_socket_id(tree, _CACHE_OBJECT_SOCKET) is not None
        and _find_socket_id(tree, _OFFSET_SOCKET) is not None
        and any(getattr(node, "label", "") == _SUBDIVIDE_LABEL for node in tree.nodes)
        and any(getattr(node, "label", "") == _GENERATED_MARK_LABEL for node in tree.nodes)
    )


def is_cached_modifier(mod: bpy.types.Modifier | None) -> bool:
    return bool(mod is not None and _tree_valid(getattr(mod, "node_group", None)))


def _get_or_create_display_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.get(CACHE_TREE_NAME)
    if _tree_valid(tree):
        assert tree is not None
        return tree
    # 選択外オブジェクトの線が無音で消える問題(2026-07-09)への対策。
    # 詳細は modifier_stack.replace_shared_node_tree のdocstring参照。
    return modifier_stack.replace_shared_node_tree(
        CACHE_TREE_NAME, tree, _create_display_tree
    )


def _set_modifier_input_if_changed(
    mod: bpy.types.Modifier,
    socket_id: str | None,
    value,
) -> None:
    if socket_id is None:
        return
    try:
        old = mod[socket_id]
    except (KeyError, TypeError):
        old = None
    if old == value:
        return
    mod[socket_id] = value


def _ensure_material_slot(obj: bpy.types.Object, material: bpy.types.Material | None) -> None:
    if material is None or obj.type != "MESH" or obj.data is None:
        return
    if not any(slot.material == material for slot in obj.material_slots):
        obj.data.materials.append(material)


def _line_modifier_names() -> tuple[str, ...]:
    return (
        MODIFIER_NAME,
        OUTLINE_LOCAL_SUBDIVISION_MODIFIER_NAME,
        OUTLINE_WIDTH_ATTR_MODIFIER_NAME,
        SHEET_OUTLINE_MODIFIER_NAME,
        GN_MODIFIER_NAME,
        SELECTION_LINE_MODIFIER_NAME,
        INTERSECTION_MODIFIER_NAME,
    )


def _is_line_modifier(mod: bpy.types.Modifier) -> bool:
    if mod.name == OUTLINE_LOCAL_SUBDIVISION_MODIFIER_NAME:
        from . import outline_local_subdivision

        return outline_local_subdivision.is_modifier(mod)
    if mod.name in _line_modifier_names() or mod.name.startswith(
        INTERSECTION_MODIFIER_PREFIX
    ):
        return True
    from . import outline_local_subdivision

    return outline_local_subdivision.is_modifier(mod)


def _disabled_line_modifiers(objects: list[bpy.types.Object]):
    states = []
    seen: set[int] = set()
    for obj in objects:
        if getattr(obj, "type", None) != "MESH":
            continue
        pointer = obj.as_pointer()
        if pointer in seen:
            continue
        seen.add(pointer)
        for mod in obj.modifiers:
            if not _is_line_modifier(mod):
                continue
            states.append((mod, bool(mod.show_viewport), bool(mod.show_render)))
            mod.show_viewport = False
            mod.show_render = False
    return states


def _restore_modifier_states(states) -> None:
    for mod, show_viewport, show_render in states:
        try:
            mod.show_viewport = show_viewport
            mod.show_render = show_render
        except ReferenceError:
            continue


def _cache_collection(scene: bpy.types.Scene | None) -> bpy.types.Collection:
    collection = bpy.data.collections.get(CACHE_COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(CACHE_COLLECTION_NAME)
    scene = scene or getattr(bpy.context, "scene", None)
    child_names = {child.name for child in scene.collection.children} if scene else set()
    if scene is not None and collection.name not in child_names:
        try:
            scene.collection.children.link(collection)
        except RuntimeError:
            pass
    return collection


def _cache_name(owner: bpy.types.Object) -> str:
    saved = str(owner.get(CACHE_OBJECT_PROP, "") or "")
    if saved:
        return saved
    raw = owner.name_full or owner.name or "Object"
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_") or "Object"
    return f"{CACHE_OBJECT_PREFIX}_{cleaned[:48]}_{owner.as_pointer():x}"


def _get_cache_object(owner: bpy.types.Object) -> bpy.types.Object | None:
    name = str(owner.get(CACHE_OBJECT_PROP, "") or "")
    if not name:
        return None
    obj = bpy.data.objects.get(name)
    return obj if getattr(obj, "type", None) == "MESH" else None


def _get_or_create_cache_object(
    owner: bpy.types.Object,
    scene: bpy.types.Scene | None,
) -> bpy.types.Object:
    name = _cache_name(owner)
    obj = bpy.data.objects.get(name)
    if obj is None:
        mesh = bpy.data.meshes.new(name=f"{name}_Mesh")
        obj = bpy.data.objects.new(name=name, object_data=mesh)
    collection = _cache_collection(scene)
    if obj.name not in {item.name for item in collection.objects}:
        collection.objects.link(obj)
    obj.hide_viewport = True
    obj.hide_render = True
    obj[CACHE_OWNER_PROP] = owner.name_full
    owner[CACHE_OBJECT_PROP] = obj.name
    return obj


def _remove_cache_object(owner: bpy.types.Object) -> bool:
    cache = _get_cache_object(owner)
    removed = False
    if cache is not None:
        mesh = cache.data
        bpy.data.objects.remove(cache)
        removed = True
        if mesh is not None and not mesh.users:
            bpy.data.meshes.remove(mesh)
    try:
        del owner[CACHE_OBJECT_PROP]
    except (KeyError, TypeError):
        pass
    return removed


def _edge_angle_selected(edge, threshold: float) -> bool:
    if len(edge.link_faces) < 2:
        return False
    try:
        return edge.calc_face_angle() + _EPS >= threshold
    except ValueError:
        return False


def _edge_int_attr(mesh: bpy.types.Mesh, attr_name: str, index: int) -> int | None:
    attr = mesh.attributes.get(attr_name)
    if (
        attr is None
        or getattr(attr, "domain", None) != "EDGE"
        or index >= len(attr.data)
    ):
        return None
    try:
        return int(getattr(attr.data[index], "value", 0))
    except (TypeError, ValueError):
        return None


def _selected_edge_graph(
    mesh: bpy.types.Mesh,
    angle: float,
    chain_id_attr: str,
):
    selected_edges: set[int] = set()
    edge_vertices: dict[int, tuple[int, int]] = {}
    vertex_edges: dict[int, set[int]] = {}
    neighbors: dict[int, set[int]] = {}
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        has_chain_attr = mesh.attributes.get(chain_id_attr) is not None
        for edge in bm.edges:
            chain_id = _edge_int_attr(mesh, chain_id_attr, edge.index)
            if has_chain_attr and (chain_id is None or chain_id <= 0):
                continue
            if not _edge_angle_selected(edge, angle):
                continue
            v1 = edge.verts[0].index
            v2 = edge.verts[1].index
            selected_edges.add(edge.index)
            edge_vertices[edge.index] = (v1, v2)
            vertex_edges.setdefault(v1, set()).add(edge.index)
            vertex_edges.setdefault(v2, set()).add(edge.index)
            neighbors.setdefault(v1, set()).add(v2)
            neighbors.setdefault(v2, set()).add(v1)
    finally:
        bm.free()
    return selected_edges, edge_vertices, vertex_edges, neighbors


def _component_edges(
    selected_edges: set[int],
    edge_vertices: dict[int, tuple[int, int]],
    vertex_edges: dict[int, set[int]],
) -> list[list[int]]:
    components: list[list[int]] = []
    visited: set[int] = set()
    for start in sorted(selected_edges):
        if start in visited:
            continue
        stack = [start]
        component: list[int] = []
        while stack:
            edge_index = stack.pop()
            if edge_index in visited or edge_index not in selected_edges:
                continue
            visited.add(edge_index)
            component.append(edge_index)
            for vertex in edge_vertices.get(edge_index, ()):
                for next_edge in vertex_edges.get(vertex, ()):
                    if next_edge not in visited:
                        stack.append(next_edge)
        if component:
            components.append(sorted(component))
    return components


def _safe_normal(vector: Vector) -> Vector:
    if vector.length <= _EPS:
        return Vector((0.0, 0.0, 1.0))
    return vector.normalized()


def _source_width(owner: bpy.types.Object, vertex_index: int) -> float:
    if vertex_index >= len(owner.data.vertices):
        return 1.0
    return vertex_analysis.stored_width_weight(owner, VG_INNER_LINE_WIDTH, vertex_index)


def _build_cache_mesh_data(
    owner: bpy.types.Object,
    mesh: bpy.types.Mesh,
    *,
    angle: float,
    chain_id_attr: str,
) -> _CachedLineMesh:
    mesh.update()
    selected_edges, edge_vertices, vertex_edges, _neighbors = _selected_edge_graph(
        mesh,
        angle,
        chain_id_attr,
    )
    if not selected_edges:
        return _CachedLineMesh([], [], [], [], [])

    vertices: list[tuple[float, float, float]] = []
    edges: list[tuple[int, int]] = []
    normals: list[tuple[float, float, float]] = []
    widths: list[float] = []
    source_indices: list[int] = []
    components = _component_edges(selected_edges, edge_vertices, vertex_edges)
    for component in components:
        vertex_map: dict[int, int] = {}

        def index_for(source_index: int) -> int:
            found = vertex_map.get(source_index)
            if found is not None:
                return found
            source_vertex = mesh.vertices[source_index]
            normal = _safe_normal(Vector(source_vertex.normal))
            vertex_map[source_index] = len(vertices)
            vertices.append(tuple(source_vertex.co))
            normals.append(tuple(normal))
            widths.append(_source_width(owner, source_index))
            source_indices.append(int(source_index))
            return vertex_map[source_index]

        for edge_index in component:
            v1, v2 = edge_vertices[edge_index]
            a = index_for(v1)
            b = index_for(v2)
            if a != b:
                edges.append((a, b))
    return _CachedLineMesh(vertices, edges, normals, widths, source_indices)


def _evaluated_inner_mesh_data(
    owner: bpy.types.Object,
    *,
    angle: float,
    chain_id_attr: str,
) -> _CachedLineMesh:
    states = _disabled_line_modifiers([owner])
    try:
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = owner.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        try:
            return _build_cache_mesh_data(
                owner,
                mesh,
                angle=angle,
                chain_id_attr=chain_id_attr,
            )
        finally:
            eval_obj.to_mesh_clear()
    finally:
        _restore_modifier_states(states)


def _write_cache_mesh(cache: bpy.types.Object, data: _CachedLineMesh) -> None:
    old_mesh = cache.data
    mesh = bpy.data.meshes.new(name=f"{cache.name}_Mesh")
    mesh.from_pydata(data.vertices, data.edges, [])
    mesh.update()
    attr = mesh.attributes.new(_NORMAL_ATTR, "FLOAT_VECTOR", "POINT")
    for index, normal in enumerate(data.normals):
        if index < len(attr.data):
            attr.data[index].vector = normal
    width_attr = mesh.attributes.new(_WIDTH_ATTR, "FLOAT", "POINT")
    for index, width in enumerate(data.widths):
        if index < len(width_attr.data):
            width_attr.data[index].value = float(width)
    source_attr = mesh.attributes.new(_SOURCE_INDEX_ATTR, "INT", "POINT")
    for index, source_index in enumerate(data.source_indices):
        if index < len(source_attr.data):
            source_attr.data[index].value = int(source_index)
    cache.data = mesh
    if old_mesh is not None and not old_mesh.users:
        bpy.data.meshes.remove(old_mesh)


def _set_width_control_parameters(
    mod: bpy.types.Modifier,
    *,
    midpoint_factor: float | None = None,
    midpoint_jitter_percent: float | None = None,
    resample_count: int | None = None,
    width_curve_25: float | None = None,
    width_curve_50: float | None = None,
    width_curve_75: float | None = None,
) -> None:
    tree = getattr(mod, "node_group", None)
    owner = getattr(mod, "id_data", None)
    settings = getattr(owner, "bmanga_line_settings", None)
    values = {
        _MIDPOINT_FACTOR_SOCKET: midpoint_factor,
        _MIDPOINT_JITTER_SOCKET: midpoint_jitter_percent,
        _RESAMPLE_COUNT_SOCKET: resample_count,
        _WIDTH_CURVE_25_SOCKET: width_curve_25,
        _WIDTH_CURVE_50_SOCKET: width_curve_50,
        _WIDTH_CURVE_75_SOCKET: width_curve_75,
    }
    if settings is not None:
        if values[_MIDPOINT_FACTOR_SOCKET] is None:
            values[_MIDPOINT_FACTOR_SOCKET] = float(
                getattr(settings, "inner_edge_smooth_factor", 0.0)
                if bool(getattr(settings, "auto_subdivision_for_midpoint", False))
                else 0.0
            )
        if values[_MIDPOINT_JITTER_SOCKET] is None:
            values[_MIDPOINT_JITTER_SOCKET] = float(
                getattr(settings, "inner_edge_midpoint_jitter_percent", 0.0)
            )
        if values[_WIDTH_CURVE_25_SOCKET] is None:
            values[_WIDTH_CURVE_25_SOCKET] = float(
                getattr(settings, "inner_edge_width_curve_25", 0.25)
            )
        if values[_WIDTH_CURVE_50_SOCKET] is None:
            values[_WIDTH_CURVE_50_SOCKET] = float(
                getattr(settings, "inner_edge_width_curve_50", 0.50)
            )
        if values[_WIDTH_CURVE_75_SOCKET] is None:
            values[_WIDTH_CURVE_75_SOCKET] = float(
                getattr(settings, "inner_edge_width_curve_75", 0.75)
            )
    if values[_RESAMPLE_COUNT_SOCKET] is None:
        from . import subdivision_lod

        smooth_requested = bool(
            settings is not None
            and getattr(settings, "auto_subdivision_for_midpoint", False)
        )
        values[_RESAMPLE_COUNT_SOCKET] = subdivision_lod.display_resample_count(
            smooth_requested or _needs_curve_subdivision(
                values[_MIDPOINT_FACTOR_SOCKET],
                values[_MIDPOINT_JITTER_SOCKET],
            )
        )
    else:
        from . import subdivision_lod

        smooth_requested = bool(
            settings is not None
            and getattr(settings, "auto_subdivision_for_midpoint", False)
        )
        values[_RESAMPLE_COUNT_SOCKET] = subdivision_lod.display_resample_count(
            smooth_requested or _needs_curve_subdivision(
                values[_MIDPOINT_FACTOR_SOCKET],
                values[_MIDPOINT_JITTER_SOCKET],
            ),
            values[_RESAMPLE_COUNT_SOCKET],
        )
    for name, value in values.items():
        if value is None:
            continue
        _set_modifier_input_if_changed(mod, _find_socket_id(tree, name), value)


def _ensure_width_storage(owner: bpy.types.Object) -> None:
    if owner.type != "MESH" or owner.data is None:
        return
    vertex_analysis.ensure_generated_width_storage(owner, VG_INNER_LINE_WIDTH)


def _sync_cache_widths_from_owner(owner: bpy.types.Object) -> None:
    cache = _get_cache_object(owner)
    if cache is None or cache.data is None:
        return
    width_attr = cache.data.attributes.get(_WIDTH_ATTR)
    source_attr = cache.data.attributes.get(_SOURCE_INDEX_ATTR)
    if width_attr is None or source_attr is None:
        return
    if getattr(width_attr, "domain", None) != "POINT":
        return
    if getattr(source_attr, "domain", None) != "POINT":
        return
    for index, item in enumerate(width_attr.data):
        if index >= len(source_attr.data):
            break
        source_index = int(getattr(source_attr.data[index], "value", -1))
        item.value = _source_width(owner, source_index)
    cache.data.update()


def update_cached_parameters(
    owner: bpy.types.Object,
    *,
    thickness: float | None = None,
    offset: float | None = None,
    material: bpy.types.Material | None = None,
    midpoint_factor: float | None = None,
    midpoint_jitter_percent: float | None = None,
    resample_count: int | None = None,
    width_curve_25: float | None = None,
    width_curve_50: float | None = None,
    width_curve_75: float | None = None,
) -> bool:
    mod = owner.modifiers.get(GN_MODIFIER_NAME)
    if mod is None or not is_cached_modifier(mod):
        return False
    tree = mod.node_group
    _sync_cache_widths_from_owner(owner)
    if material is not None:
        _ensure_material_slot(owner, material)
        _set_modifier_input_if_changed(mod, _find_socket_id(tree, _MATERIAL_SOCKET), material)
    if thickness is not None:
        _set_modifier_input_if_changed(mod, _find_socket_id(tree, _THICKNESS_SOCKET), thickness)
    if offset is not None:
        _set_modifier_input_if_changed(mod, _find_socket_id(tree, _OFFSET_SOCKET), offset)
    _set_width_control_parameters(
        mod,
        midpoint_factor=midpoint_factor,
        midpoint_jitter_percent=midpoint_jitter_percent,
        resample_count=resample_count,
        width_curve_25=width_curve_25,
        width_curve_50=width_curve_50,
        width_curve_75=width_curve_75,
    )
    return True


def _apply_display_modifier(
    owner: bpy.types.Object,
    cache: bpy.types.Object,
    *,
    thickness: float,
    offset: float,
    material: bpy.types.Material | None,
    midpoint_factor: float,
    midpoint_jitter_percent: float,
    resample_count: int | None,
    width_curve_25: float,
    width_curve_50: float,
    width_curve_75: float,
    enable: bool,
) -> None:
    tree = _get_or_create_display_tree()
    mod = owner.modifiers.get(GN_MODIFIER_NAME)
    if mod is None:
        mod = owner.modifiers.new(name=GN_MODIFIER_NAME, type="NODES")
    mod.node_group = tree
    _ensure_material_slot(owner, material)
    _set_modifier_input_if_changed(mod, _find_socket_id(tree, _CACHE_OBJECT_SOCKET), cache)
    _set_modifier_input_if_changed(mod, _find_socket_id(tree, _THICKNESS_SOCKET), thickness)
    _set_modifier_input_if_changed(mod, _find_socket_id(tree, _OFFSET_SOCKET), offset)
    _set_modifier_input_if_changed(mod, _find_socket_id(tree, _MATERIAL_SOCKET), material)
    _set_width_control_parameters(
        mod,
        midpoint_factor=midpoint_factor,
        midpoint_jitter_percent=midpoint_jitter_percent,
        resample_count=resample_count,
        width_curve_25=width_curve_25,
        width_curve_50=width_curve_50,
        width_curve_75=width_curve_75,
    )
    visible = bool(enable) and not bool(owner.get(PROP_LINES_HIDDEN, False))
    mod.show_viewport = visible
    mod.show_render = visible
    modifier_stack.reorder_line_modifiers(owner)


def remove_cached_inner_lines(owner: bpy.types.Object) -> bool:
    removed = _remove_cache_object(owner)
    mod = owner.modifiers.get(GN_MODIFIER_NAME)
    if mod is not None:
        owner.modifiers.remove(mod)
        removed = True
    return removed


def apply_cached_inner_lines(
    owner: bpy.types.Object,
    *,
    angle: float,
    thickness: float,
    offset: float,
    material: bpy.types.Material | None,
    midpoint_angle: float | None,
    midpoint_factor: float,
    midpoint_jitter_percent: float,
    resample_count: int | None,
    width_curve_25: float,
    width_curve_50: float,
    width_curve_75: float,
    chain_id_attr: str = inner_line_chains.CHAIN_ID_ATTR,
    marked_attr_name: str | None = None,
    scene: bpy.types.Scene | None = None,
    enable: bool = True,
) -> bool:
    if owner.type != "MESH" or owner.data is None:
        return False
    _ensure_width_storage(owner)
    inner_line_chains.update_chain_id_attribute(
        owner,
        float(angle),
        False,
        midpoint_angle,
        chain_id_attr=chain_id_attr,
        marked_attr_name=marked_attr_name,
    )
    data = _evaluated_inner_mesh_data(
        owner,
        angle=max(0.0, float(angle) - 1.0e-7),
        chain_id_attr=chain_id_attr,
    )
    if not data.edges:
        remove_cached_inner_lines(owner)
        return True
    cache = _get_or_create_cache_object(owner, scene)
    cache.matrix_world = owner.matrix_world.copy()
    _write_cache_mesh(cache, data)
    _apply_display_modifier(
        owner,
        cache,
        thickness=thickness,
        offset=offset,
        material=material,
        midpoint_factor=midpoint_factor,
        midpoint_jitter_percent=midpoint_jitter_percent,
        resample_count=resample_count,
        width_curve_25=width_curve_25,
        width_curve_50=width_curve_50,
        width_curve_75=width_curve_75,
        enable=enable,
    )
    return True


def cleanup_orphan_cache_objects() -> int:
    owners = {
        str(owner.get(CACHE_OBJECT_PROP, "") or "")
        for owner in bpy.data.objects
        if getattr(owner, "type", None) == "MESH"
    }
    removed = 0
    for obj in list(bpy.data.objects):
        if not obj.name.startswith(CACHE_OBJECT_PREFIX):
            continue
        if obj.name in owners:
            continue
        mesh = obj.data
        bpy.data.objects.remove(obj)
        removed += 1
        if mesh is not None and not mesh.users:
            bpy.data.meshes.remove(mesh)
    return removed
