"""B-MANGA Line — 内部線（稜線・谷線）のジオメトリノードセットアップ.

Edge Angle ノードでメッシュの折れ目を検出し、
そのエッジに沿った細いチューブ状ジオメトリを生成する。
"""

from __future__ import annotations

import math

import bpy

from .core import GN_MODIFIER_NAME, GN_TREE_NAME, MODIFIER_NAME, VG_LINE_WIDTH


# ------------------------------------------------------------------
# ノードツリー構築
# ------------------------------------------------------------------

def _create_node_tree() -> bpy.types.NodeTree:
    """内部線用ジオメトリノードツリーを新規作成."""
    tree = bpy.data.node_groups.new(name=GN_TREE_NAME, type="GeometryNodeTree")

    # --- インターフェース定義 ---
    tree.interface.new_socket(
        name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
    )
    tree.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )
    angle_sock = tree.interface.new_socket(
        name="検出角度", in_out="INPUT", socket_type="NodeSocketFloat"
    )
    angle_sock.default_value = math.radians(30)
    angle_sock.min_value = math.radians(1)
    angle_sock.max_value = math.radians(180)
    if hasattr(angle_sock, "subtype"):
        angle_sock.subtype = "ANGLE"

    radius_sock = tree.interface.new_socket(
        name="線の太さ", in_out="INPUT", socket_type="NodeSocketFloat"
    )
    radius_sock.default_value = 0.0005
    radius_sock.min_value = 0.0001
    radius_sock.max_value = 0.05

    tree.interface.new_socket(
        name="マテリアル", in_out="INPUT", socket_type="NodeSocketMaterial"
    )

    nodes = tree.nodes
    links = tree.links

    # --- ノード配置 ---
    gin = nodes.new("NodeGroupInput")
    gin.location = (-800, 0)

    gout = nodes.new("NodeGroupOutput")
    gout.location = (800, 0)

    # Edge Angle: エッジの二面角を取得
    edge_angle = nodes.new("GeometryNodeInputMeshEdgeAngle")
    edge_angle.location = (-600, -200)

    # Compare: 角度 > 閾値 → 折れ目エッジを選択
    compare = nodes.new("FunctionNodeCompare")
    compare.location = (-400, -200)
    compare.data_type = "FLOAT"
    compare.operation = "GREATER_THAN"
    links.new(edge_angle.outputs[0], compare.inputs["A"])  # Unsigned Angle
    links.new(gin.outputs[1], compare.inputs["B"])  # 検出角度

    # Mesh to Curve: 選択エッジをカーブに変換
    m2c = nodes.new("GeometryNodeMeshToCurve")
    m2c.location = (-200, -200)
    links.new(gin.outputs[0], m2c.inputs[0])  # Geometry → Mesh
    links.new(compare.outputs[0], m2c.inputs[1])  # Selection

    # Curve Circle: チューブ断面
    circle = nodes.new("GeometryNodeCurvePrimitiveCircle")
    circle.location = (-200, -400)
    circle.mode = "RADIUS"
    for inp in circle.inputs:
        if inp.name == "Resolution" and inp.enabled:
            inp.default_value = 4
    links.new(gin.outputs[2], circle.inputs["Radius"])  # 線の太さ → Radius

    # Curve to Mesh: カーブをチューブメッシュに変換
    c2m = nodes.new("GeometryNodeCurveToMesh")
    c2m.location = (0, -200)
    links.new(m2c.outputs[0], c2m.inputs[0])  # Curve
    links.new(circle.outputs[0], c2m.inputs[1])  # Profile Curve
    if len(c2m.inputs) > 2:
        c2m.inputs[2].default_value = True  # Fill Caps

    # Set Material: マテリアル入力ソケットから割り当て
    setmat = nodes.new("GeometryNodeSetMaterial")
    setmat.location = (200, -200)
    links.new(c2m.outputs[0], setmat.inputs[0])
    links.new(gin.outputs[3], setmat.inputs["Material"])

    # Join Geometry: 元メッシュ + 内部線ジオメトリ
    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (500, 0)
    links.new(gin.outputs[0], join.inputs[0])  # 元ジオメトリ
    links.new(setmat.outputs[0], join.inputs[0])  # 内部線ジオメトリ

    links.new(join.outputs[0], gout.inputs[0])

    return tree


def _get_or_create_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.get(GN_TREE_NAME)
    if tree is not None:
        if _find_socket_id(tree, "マテリアル") is None:
            bpy.data.node_groups.remove(tree)
            return _create_node_tree()
        return tree
    return _create_node_tree()


def _find_socket_id(tree: bpy.types.NodeTree, name: str) -> str | None:
    """ツリーインターフェースからソケット識別子を検索."""
    for item in tree.interface.items_tree:
        if getattr(item, "name", None) == name and getattr(item, "in_out", None) == "INPUT":
            return item.identifier
    return None


# ------------------------------------------------------------------
# 適用 / 削除 / 更新
# ------------------------------------------------------------------

def apply_inner_lines(
    obj: bpy.types.Object,
    angle: float = 0.5236,
    thickness: float = 0.0005,
    material: bpy.types.Material | None = None,
) -> bool:
    """内部線 GN モディファイアを適用. 成功時 True."""
    if obj.type != "MESH":
        return False

    tree = _get_or_create_tree()

    # 既存モディファイアを更新 or 新規作成
    mod = obj.modifiers.get(GN_MODIFIER_NAME)
    if mod is None:
        mod = obj.modifiers.new(name=GN_MODIFIER_NAME, type="NODES")
    mod.node_group = tree

    # パラメータ設定
    sid_angle = _find_socket_id(tree, "検出角度")
    sid_thickness = _find_socket_id(tree, "線の太さ")
    if sid_angle is not None:
        mod[sid_angle] = angle
    if sid_thickness is not None:
        mod[sid_thickness] = thickness

    # マテリアル
    if material is not None:
        sid_mat = _find_socket_id(tree, "マテリアル")
        if sid_mat is not None:
            mod[sid_mat] = material

    # 頂点グループ: 元メッシュ頂点 = weight 1.0
    vg = obj.vertex_groups.get(VG_LINE_WIDTH)
    if vg is None:
        vg = obj.vertex_groups.new(name=VG_LINE_WIDTH)
    vg.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")

    # 内部線は Solidify（アウトライン）の前に配置する
    # 後だとシェルのエッジまで検出してしまう
    outline_idx = None
    inner_idx = None
    for i, m in enumerate(obj.modifiers):
        if m.name == MODIFIER_NAME:
            outline_idx = i
        elif m.name == GN_MODIFIER_NAME:
            inner_idx = i
    if outline_idx is not None and inner_idx is not None and inner_idx > outline_idx:
        obj.modifiers.move(inner_idx, outline_idx)

    return True


def remove_inner_lines(obj: bpy.types.Object) -> bool:
    """内部線 GN モディファイアを削除."""
    if obj.type != "MESH":
        return False
    mod = obj.modifiers.get(GN_MODIFIER_NAME)
    if mod is None:
        return False
    obj.modifiers.remove(mod)
    return True


def update_parameters(
    obj: bpy.types.Object,
    angle: float | None = None,
    thickness: float | None = None,
) -> bool:
    """既存モディファイアのパラメータを更新."""
    mod = obj.modifiers.get(GN_MODIFIER_NAME)
    if mod is None or mod.node_group is None:
        return False
    tree = mod.node_group
    if angle is not None:
        sid = _find_socket_id(tree, "検出角度")
        if sid is not None:
            mod[sid] = angle
    if thickness is not None:
        sid = _find_socket_id(tree, "線の太さ")
        if sid is not None:
            mod[sid] = thickness
    return True
