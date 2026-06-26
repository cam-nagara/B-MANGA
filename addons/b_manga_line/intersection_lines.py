"""B-MANGA Line — 交差線（オブジェクト間の貫通部分）のジオメトリノードセットアップ.

2 つの検出方式を提供する:

Boolean 法:
  Mesh Boolean (Exact) の Intersecting Edges 出力で
  2 つのメッシュが交差するエッジを正確に検出し、チューブ化する。
  低密度メッシュでも滑らかな線が得られるが、
  トポロジーエラーのリスクがある。

SDF 法:
  対象メッシュを SDF Grid に変換し、ソースメッシュの各頂点で
  SDF 値をサンプリングして交差境界を検出する。
  トポロジーエラーが原理的に発生しないが、
  ソースメッシュの頂点密度に精度が依存する。
"""

from __future__ import annotations

import bpy

from .core import (
    INTERSECTION_MODIFIER_NAME,
    INTERSECTION_TREE_BOOLEAN,
    INTERSECTION_TREE_SDF,
    MODIFIER_NAME,
)


# ------------------------------------------------------------------
# 共通: Solidify シェル除外ノード群を構築
# ------------------------------------------------------------------

def _add_shell_strip_nodes(nodes, links, gin):
    """material_index != 0 の面を削除するノード群を追加して返す."""
    mat_idx = nodes.new("GeometryNodeInputMaterialIndex")
    mat_idx.location = (-1200, -250)

    cmp_mat = nodes.new("FunctionNodeCompare")
    cmp_mat.location = (-1050, -250)
    cmp_mat.data_type = "INT"
    cmp_mat.operation = "EQUAL"
    cmp_mat.inputs[3].default_value = 0
    links.new(mat_idx.outputs[0], cmp_mat.inputs[2])

    not_orig = nodes.new("FunctionNodeBooleanMath")
    not_orig.location = (-1050, -380)
    not_orig.operation = "NOT"
    links.new(cmp_mat.outputs[0], not_orig.inputs[0])

    del_shell = nodes.new("GeometryNodeDeleteGeometry")
    del_shell.location = (-900, -200)
    del_shell.domain = "FACE"
    links.new(gin.outputs[0], del_shell.inputs["Geometry"])
    links.new(not_orig.outputs[0], del_shell.inputs["Selection"])

    return del_shell


# ------------------------------------------------------------------
# 共通: 板ポリ対応 — 非多様体ターゲットに厚みを追加
# ------------------------------------------------------------------

def _add_target_solidify(nodes, links, target_geo_output, offset_scale, loc):
    """板ポリ等の非多様体メッシュに Extrude で厚みを追加して閉多様体にする.

    境界エッジが 1 つでもあれば非多様体とみなし Extrude を適用。
    多様体メッシュは Switch でそのまま返す。
    """
    edge_nbr = nodes.new("GeometryNodeInputMeshEdgeNeighbors")
    edge_nbr.location = (loc[0] - 200, loc[1] - 300)

    stat = nodes.new("GeometryNodeAttributeStatistic")
    stat.location = (loc[0], loc[1] - 300)
    stat.data_type = "FLOAT"
    stat.domain = "EDGE"
    links.new(target_geo_output, stat.inputs[0])
    links.new(edge_nbr.outputs[0], stat.inputs[2])

    has_bnd = nodes.new("FunctionNodeCompare")
    has_bnd.location = (loc[0] + 200, loc[1] - 300)
    has_bnd.data_type = "FLOAT"
    has_bnd.operation = "LESS_THAN"
    has_bnd.inputs[1].default_value = 2.0
    links.new(stat.outputs[3], has_bnd.inputs[0])

    normal = nodes.new("GeometryNodeInputNormal")
    normal.location = (loc[0] - 200, loc[1] - 150)

    extrude = nodes.new("GeometryNodeExtrudeMesh")
    extrude.location = (loc[0], loc[1])
    extrude.mode = "FACES"
    extrude.inputs[3].default_value = offset_scale
    links.new(target_geo_output, extrude.inputs[0])
    links.new(normal.outputs[0], extrude.inputs[2])

    switch = nodes.new("GeometryNodeSwitch")
    switch.location = (loc[0] + 200, loc[1])
    switch.input_type = "GEOMETRY"
    links.new(has_bnd.outputs[0], switch.inputs[0])
    links.new(target_geo_output, switch.inputs[1])
    links.new(extrude.outputs[0], switch.inputs[2])

    return switch.outputs[0]


# ------------------------------------------------------------------
# 共通: チューブ生成ノード群を構築
# ------------------------------------------------------------------

def _add_tube_nodes(nodes, links, curve_output, gin, x_offset=0):
    """カーブ → チューブメッシュ → マテリアル設定 → Join を構築して join ノードを返す."""
    circle = nodes.new("GeometryNodeCurvePrimitiveCircle")
    circle.location = (x_offset + 0, -550)
    circle.mode = "RADIUS"
    for inp in circle.inputs:
        if inp.name == "Resolution" and inp.enabled:
            inp.default_value = 4
    links.new(gin.outputs[2], circle.inputs["Radius"])

    c2m = nodes.new("GeometryNodeCurveToMesh")
    c2m.location = (x_offset + 200, -300)
    links.new(curve_output, c2m.inputs[0])
    links.new(circle.outputs[0], c2m.inputs[1])
    if len(c2m.inputs) > 2:
        c2m.inputs[2].default_value = True

    setmat = nodes.new("GeometryNodeSetMaterial")
    setmat.location = (x_offset + 400, -300)
    links.new(c2m.outputs[0], setmat.inputs[0])
    links.new(gin.outputs[3], setmat.inputs["Material"])

    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (x_offset + 700, 0)
    links.new(gin.outputs[0], join.inputs[0])
    links.new(setmat.outputs[0], join.inputs[0])

    return join


# ------------------------------------------------------------------
# 共通: インターフェース定義
# ------------------------------------------------------------------

def _setup_interface(tree):
    """両方式で共通のソケットインターフェースを定義."""
    tree.interface.new_socket(
        name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry",
    )
    tree.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry",
    )
    tree.interface.new_socket(
        name="交差対象", in_out="INPUT", socket_type="NodeSocketObject",
    )
    radius_sock = tree.interface.new_socket(
        name="線の太さ", in_out="INPUT", socket_type="NodeSocketFloat",
    )
    radius_sock.default_value = 0.0005
    radius_sock.min_value = 0.0001
    radius_sock.max_value = 0.05
    tree.interface.new_socket(
        name="マテリアル", in_out="INPUT", socket_type="NodeSocketMaterial",
    )


# ------------------------------------------------------------------
# Boolean 法ノードツリー
# ------------------------------------------------------------------

def _create_boolean_tree() -> bpy.types.NodeTree:
    """Boolean 法の GN ツリーを新規作成."""
    tree = bpy.data.node_groups.new(
        name=INTERSECTION_TREE_BOOLEAN, type="GeometryNodeTree",
    )
    _setup_interface(tree)
    nodes = tree.nodes
    links = tree.links

    gin = nodes.new("NodeGroupInput")
    gin.location = (-1400, 0)

    gout = nodes.new("NodeGroupOutput")
    gout.location = (1400, 0)

    # シェル除外
    del_shell = _add_shell_strip_nodes(nodes, links, gin)

    # Object Info
    obj_info = nodes.new("GeometryNodeObjectInfo")
    obj_info.location = (-900, -500)
    obj_info.transform_space = "RELATIVE"
    links.new(gin.outputs[1], obj_info.inputs["Object"])

    # 板ポリ対応: 非多様体ターゲットに厚みを追加
    target_geo = _add_target_solidify(
        nodes, links, obj_info.outputs[4], 0.0001, (-700, -500),
    )

    # Mesh Boolean (DIFFERENCE, EXACT)
    boolean = nodes.new("GeometryNodeMeshBoolean")
    boolean.location = (-500, -200)
    boolean.operation = "DIFFERENCE"
    boolean.solver = "EXACT"
    links.new(del_shell.outputs["Geometry"], boolean.inputs[0])
    links.new(target_geo, boolean.inputs[1])

    # Separate Geometry — 交差エッジのみ分離
    separate = nodes.new("GeometryNodeSeparateGeometry")
    separate.location = (-300, -200)
    separate.domain = "EDGE"
    links.new(boolean.outputs["Mesh"], separate.inputs["Geometry"])
    links.new(boolean.outputs["Intersecting Edges"], separate.inputs["Selection"])

    # Mesh to Curve
    m2c = nodes.new("GeometryNodeMeshToCurve")
    m2c.location = (-100, -200)
    links.new(separate.outputs["Selection"], m2c.inputs[0])

    # チューブ生成 + Join
    join = _add_tube_nodes(nodes, links, m2c.outputs[0], gin, x_offset=100)
    links.new(join.outputs[0], gout.inputs[0])

    return tree


# ------------------------------------------------------------------
# SDF 法ノードツリー
# ------------------------------------------------------------------

def _create_sdf_tree() -> bpy.types.NodeTree:
    """SDF 法の GN ツリーを新規作成."""
    tree = bpy.data.node_groups.new(
        name=INTERSECTION_TREE_SDF, type="GeometryNodeTree",
    )
    _setup_interface(tree)
    nodes = tree.nodes
    links = tree.links

    gin = nodes.new("NodeGroupInput")
    gin.location = (-1400, 0)

    gout = nodes.new("NodeGroupOutput")
    gout.location = (1600, 0)

    # シェル除外
    del_shell = _add_shell_strip_nodes(nodes, links, gin)

    # 解析用にメッシュを細分化
    subdivide = nodes.new("GeometryNodeSubdivideMesh")
    subdivide.location = (-750, -200)
    subdivide.inputs[1].default_value = 4
    links.new(del_shell.outputs["Geometry"], subdivide.inputs[0])

    # Object Info
    obj_info = nodes.new("GeometryNodeObjectInfo")
    obj_info.location = (-900, -550)
    obj_info.transform_space = "RELATIVE"
    links.new(gin.outputs[1], obj_info.inputs["Object"])

    # 板ポリ対応: 非多様体ターゲットに厚みを追加
    target_geo = _add_target_solidify(
        nodes, links, obj_info.outputs[4], 0.05, (-700, -550),
    )

    # 対象メッシュ → SDF Grid
    mesh_to_sdf = nodes.new("GeometryNodeMeshToSDFGrid")
    mesh_to_sdf.location = (-300, -550)
    mesh_to_sdf.inputs[1].default_value = 0.01
    mesh_to_sdf.inputs[2].default_value = 3
    links.new(target_geo, mesh_to_sdf.inputs[0])

    # ソース頂点位置で SDF をサンプリング
    position = nodes.new("GeometryNodeInputPosition")
    position.location = (-700, -350)

    sample_sdf = nodes.new("GeometryNodeSampleGrid")
    sample_sdf.location = (-500, -450)
    links.new(mesh_to_sdf.outputs[0], sample_sdf.inputs[0])
    links.new(position.outputs[0], sample_sdf.inputs[1])

    # 境界エッジ検出: 隣接頂点の SDF 符号が異なるエッジ
    edge_verts = nodes.new("GeometryNodeInputMeshEdgeVertices")
    edge_verts.location = (-500, -700)

    eval_v1 = nodes.new("GeometryNodeFieldAtIndex")
    eval_v1.location = (-300, -450)
    eval_v1.data_type = "FLOAT"
    eval_v1.domain = "POINT"
    links.new(sample_sdf.outputs[0], eval_v1.inputs[0])
    links.new(edge_verts.outputs[0], eval_v1.inputs[1])

    eval_v2 = nodes.new("GeometryNodeFieldAtIndex")
    eval_v2.location = (-300, -620)
    eval_v2.data_type = "FLOAT"
    eval_v2.domain = "POINT"
    links.new(sample_sdf.outputs[0], eval_v2.inputs[0])
    links.new(edge_verts.outputs[1], eval_v2.inputs[1])

    multiply = nodes.new("ShaderNodeMath")
    multiply.location = (-100, -530)
    multiply.operation = "MULTIPLY"
    links.new(eval_v1.outputs[0], multiply.inputs[0])
    links.new(eval_v2.outputs[0], multiply.inputs[1])

    is_boundary = nodes.new("FunctionNodeCompare")
    is_boundary.location = (50, -530)
    is_boundary.data_type = "FLOAT"
    is_boundary.operation = "LESS_THAN"
    is_boundary.inputs[1].default_value = 0.0
    links.new(multiply.outputs[0], is_boundary.inputs[0])

    # Mesh to Curve
    m2c = nodes.new("GeometryNodeMeshToCurve")
    m2c.location = (200, -350)
    links.new(subdivide.outputs[0], m2c.inputs[0])
    links.new(is_boundary.outputs[0], m2c.inputs[1])

    # Catmull-Rom スプライン化で滑らかにする
    spline_type = nodes.new("GeometryNodeCurveSplineType")
    spline_type.location = (350, -350)
    spline_type.spline_type = "CATMULL_ROM"
    links.new(m2c.outputs[0], spline_type.inputs[0])

    spline_res = nodes.new("GeometryNodeSetSplineResolution")
    spline_res.location = (500, -350)
    spline_res.inputs[2].default_value = 12
    links.new(spline_type.outputs[0], spline_res.inputs[0])

    # チューブ生成 + Join
    join = _add_tube_nodes(nodes, links, spline_res.outputs[0], gin, x_offset=650)
    links.new(join.outputs[0], gout.inputs[0])

    return tree


# ------------------------------------------------------------------
# ツリー取得
# ------------------------------------------------------------------

def _get_or_create_tree(method: str = "BOOLEAN") -> bpy.types.NodeTree:
    """指定方式の GN ツリーを取得または作成."""
    if method == "SDF":
        name = INTERSECTION_TREE_SDF
        creator = _create_sdf_tree
    else:
        name = INTERSECTION_TREE_BOOLEAN
        creator = _create_boolean_tree

    tree = bpy.data.node_groups.get(name)
    if tree is not None:
        if _find_socket_id(tree, "交差対象") is None:
            bpy.data.node_groups.remove(tree)
            return creator()
        if not any(n.bl_idname == "GeometryNodeExtrudeMesh" for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return creator()
        return tree
    return creator()


def _find_socket_id(tree: bpy.types.NodeTree, name: str) -> str | None:
    """ツリーインターフェースからソケット識別子を検索."""
    for item in tree.interface.items_tree:
        if (
            getattr(item, "name", None) == name
            and getattr(item, "in_out", None) == "INPUT"
        ):
            return item.identifier
    return None


# ------------------------------------------------------------------
# 適用 / 削除 / 更新
# ------------------------------------------------------------------

def apply_intersection_lines(
    obj: bpy.types.Object,
    target: bpy.types.Object | None = None,
    thickness: float = 0.0005,
    material: bpy.types.Material | None = None,
    method: str = "BOOLEAN",
) -> bool:
    """交差線 GN モディファイアを適用. 成功時 True."""
    if obj.type != "MESH":
        return False

    tree = _get_or_create_tree(method)

    mod = obj.modifiers.get(INTERSECTION_MODIFIER_NAME)
    if mod is None:
        mod = obj.modifiers.new(name=INTERSECTION_MODIFIER_NAME, type="NODES")
    mod.node_group = tree

    # パラメータ設定
    if target is not None:
        sid_target = _find_socket_id(tree, "交差対象")
        if sid_target is not None:
            mod[sid_target] = target

    sid_thickness = _find_socket_id(tree, "線の太さ")
    if sid_thickness is not None:
        mod[sid_thickness] = thickness

    if material is not None:
        sid_mat = _find_socket_id(tree, "マテリアル")
        if sid_mat is not None:
            mod[sid_mat] = material

    # Solidify（アウトライン）の後ろに配置する
    outline_idx = None
    intersect_idx = None
    for i, m in enumerate(obj.modifiers):
        if m.name == MODIFIER_NAME:
            outline_idx = i
        elif m.name == INTERSECTION_MODIFIER_NAME:
            intersect_idx = i
    if (
        outline_idx is not None
        and intersect_idx is not None
        and intersect_idx < outline_idx
    ):
        obj.modifiers.move(intersect_idx, outline_idx)

    return True


def remove_intersection_lines(obj: bpy.types.Object) -> bool:
    """交差線 GN モディファイアを削除."""
    if obj.type != "MESH":
        return False
    mod = obj.modifiers.get(INTERSECTION_MODIFIER_NAME)
    if mod is None:
        return False
    obj.modifiers.remove(mod)
    return True


def update_parameters(
    obj: bpy.types.Object,
    target: bpy.types.Object | None = ...,
    thickness: float | None = None,
) -> bool:
    """既存モディファイアのパラメータを更新."""
    mod = obj.modifiers.get(INTERSECTION_MODIFIER_NAME)
    if mod is None or mod.node_group is None:
        return False
    tree = mod.node_group
    if target is not ...:
        sid = _find_socket_id(tree, "交差対象")
        if sid is not None:
            mod[sid] = target
    if thickness is not None:
        sid = _find_socket_id(tree, "線の太さ")
        if sid is not None:
            mod[sid] = thickness
    return True
