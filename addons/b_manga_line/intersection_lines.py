"""B-MANGA Line — 交差線（オブジェクト間の貫通部分）のジオメトリノードセットアップ.

3 つの作成方式を提供する:

ライン素材方式:
  ライン適用済みの他メッシュを参照用オブジェクトとしてまとめ、
  個別ペアのモディファイアを作らずに交差境界をチューブ化する。
  参照側を線幅ぶん広げるため、接しているだけの箇所にも線を作る。

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

交差対象に B-MANGA Line の厚みがある場合:
  元メッシュと太らせたライン面の間を線色で塗る。
  交差対象のライン厚みだけが 2 本線に見えるのを防ぐ。
"""

from __future__ import annotations

import bpy
from mathutils import Vector

from . import modifier_stack, scale_utils
from .core import (
    GENERATED_LINE_ATTR,
    GN_MODIFIER_NAME,
    INTERSECTION_MODIFIER_PREFIX,
    INTERSECTION_TREE_BOOLEAN,
    INTERSECTION_TREE_SDF,
    MODIFIER_NAME,
    PROP_LINES_HIDDEN,
    SHEET_OUTLINE_MODIFIER_NAME,
    VG_INTERSECTION_LINE_WIDTH,
    is_settings_locked,
    iter_intersection_modifiers,
)


_FILL_NODE_LABEL = "BML_TargetLineFill"
# V2: Join Geometry の結合順修正（2026-07-09、素材スロット順バグ）に伴い
# ラベルを世代更新。保存済み.blendの旧ツリー（単一対象・複数対象とも）を
# ラベル不一致で必ず再構築させる（_get_or_create_tree / _get_or_create_multi_tree
# 参照。outline_setup.py の _SHEET_TUBE_ANGLE_SPLIT_LABEL と同方式）。
_GENERATED_LINE_NODE_LABEL = "BML_GeneratedLineMarkV2"
_TARGET_SOCKET = "交差対象"
_THICKNESS_SOCKET = "線の太さ"
_OFFSET_SOCKET = "オフセット"
_TARGET_THICKNESS_SOCKET = "交差対象の線幅"
_MATERIAL_SOCKET = "マテリアル"
_GROUPED_MODIFIER_NAME = f"{INTERSECTION_MODIFIER_PREFIX}Targets"
_GROUPED_TARGET_THRESHOLD = 4
_MULTI_TREE_PREFIX = f"{INTERSECTION_TREE_BOOLEAN}_Multi_"
_SHELL_METHOD = "SHELL"
_DEFERRED_VIEWPORT_PROP = "bml_deferred_intersection_viewport"
_DEFERRED_VIEWPORT_THRESHOLD = 12
_DEFERRED_VIEWPORT_INTERVAL = 0.4
_deferred_viewport_queue: list[tuple[str, str]] = []
_deferred_viewport_timer_running = False


def _has_outline_source(obj: bpy.types.Object) -> bool:
    try:
        return (
            obj.modifiers.get(MODIFIER_NAME) is not None
            or obj.modifiers.get(SHEET_OUTLINE_MODIFIER_NAME) is not None
        )
    except ReferenceError:
        return False


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

    generated_attr = nodes.new("GeometryNodeInputNamedAttribute")
    generated_attr.location = (-1050, -540)
    generated_attr.data_type = "BOOLEAN"
    generated_attr.inputs["Name"].default_value = GENERATED_LINE_ATTR

    generated_marked = nodes.new("FunctionNodeBooleanMath")
    generated_marked.location = (-900, -540)
    generated_marked.operation = "AND"
    links.new(generated_attr.outputs["Exists"], generated_marked.inputs[0])
    links.new(generated_attr.outputs["Attribute"], generated_marked.inputs[1])

    delete_selection = nodes.new("FunctionNodeBooleanMath")
    delete_selection.location = (-900, -380)
    delete_selection.operation = "OR"
    links.new(not_orig.outputs[0], delete_selection.inputs[0])
    links.new(generated_marked.outputs[0], delete_selection.inputs[1])

    del_shell = nodes.new("GeometryNodeDeleteGeometry")
    del_shell.location = (-900, -200)
    del_shell.domain = "FACE"
    links.new(gin.outputs[0], del_shell.inputs["Geometry"])
    links.new(delete_selection.outputs[0], del_shell.inputs["Selection"])

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
# 共通: 交差対象ライン厚みに合わせた半径を構築
# ------------------------------------------------------------------

def _add_target_has_line_faces(nodes, links, target_geo_output, loc):
    """交差対象が B-MANGA Line の厚み面を持つかを返す."""
    mat_idx = nodes.new("GeometryNodeInputMaterialIndex")
    mat_idx.location = (loc[0] - 420, loc[1] - 420)

    stat = nodes.new("GeometryNodeAttributeStatistic")
    stat.location = (loc[0] - 220, loc[1] - 420)
    stat.data_type = "FLOAT"
    stat.domain = "FACE"
    links.new(target_geo_output, stat.inputs[0])
    links.new(mat_idx.outputs[0], stat.inputs[2])

    has_line_faces = nodes.new("FunctionNodeCompare")
    has_line_faces.location = (loc[0], loc[1] - 420)
    has_line_faces.data_type = "FLOAT"
    has_line_faces.operation = "GREATER_THAN"
    has_line_faces.inputs[1].default_value = 0.5
    links.new(stat.outputs["Max"], has_line_faces.inputs[0])
    return has_line_faces.outputs[0]


def _add_effective_radius(nodes, links, gin, has_line_faces, loc):
    """対象にライン厚みがある場合、2本線の間が埋まる半径へ広げる."""
    offset_amount = nodes.new("ShaderNodeMath")
    offset_amount.location = (loc[0] - 420, loc[1] + 160)
    offset_amount.operation = "MULTIPLY"
    links.new(gin.outputs["線の太さ"], offset_amount.inputs[0])
    links.new(gin.outputs[_OFFSET_SOCKET], offset_amount.inputs[1])

    offset_half = nodes.new("ShaderNodeMath")
    offset_half.location = (loc[0] - 220, loc[1] + 160)
    offset_half.operation = "MULTIPLY"
    offset_half.inputs[1].default_value = 0.5
    links.new(offset_amount.outputs[0], offset_half.inputs[0])

    own_radius = nodes.new("ShaderNodeMath")
    own_radius.location = (loc[0], loc[1] + 160)
    own_radius.operation = "ADD"
    links.new(gin.outputs["線の太さ"], own_radius.inputs[0])
    links.new(offset_half.outputs[0], own_radius.inputs[1])

    own_radius_min = nodes.new("ShaderNodeMath")
    own_radius_min.location = (loc[0] + 200, loc[1] + 160)
    own_radius_min.operation = "MAXIMUM"
    own_radius_min.inputs[1].default_value = 0.0
    links.new(own_radius.outputs[0], own_radius_min.inputs[0])

    fill_radius = nodes.new("ShaderNodeMath")
    fill_radius.label = _FILL_NODE_LABEL
    fill_radius.location = loc
    fill_radius.operation = "MULTIPLY"
    fill_radius.inputs[1].default_value = 0.55
    links.new(gin.outputs["交差対象の線幅"], fill_radius.inputs[0])

    fill_radius_offset = nodes.new("ShaderNodeMath")
    fill_radius_offset.location = (loc[0] + 200, loc[1] - 120)
    fill_radius_offset.operation = "ADD"
    links.new(fill_radius.outputs[0], fill_radius_offset.inputs[0])
    links.new(offset_half.outputs[0], fill_radius_offset.inputs[1])

    fill_radius_min = nodes.new("ShaderNodeMath")
    fill_radius_min.location = (loc[0] + 400, loc[1] - 120)
    fill_radius_min.operation = "MAXIMUM"
    fill_radius_min.inputs[1].default_value = 0.0
    links.new(fill_radius_offset.outputs[0], fill_radius_min.inputs[0])

    maximum = nodes.new("ShaderNodeMath")
    maximum.location = (loc[0] + 600, loc[1])
    maximum.operation = "MAXIMUM"
    links.new(own_radius_min.outputs[0], maximum.inputs[0])
    links.new(fill_radius_min.outputs[0], maximum.inputs[1])

    switch = nodes.new("GeometryNodeSwitch")
    switch.location = (loc[0] + 820, loc[1])
    switch.input_type = "FLOAT"
    links.new(has_line_faces, switch.inputs[0])
    links.new(own_radius_min.outputs[0], switch.inputs[1])
    links.new(maximum.outputs[0], switch.inputs[2])
    return switch.outputs[0]


# ------------------------------------------------------------------
# 共通: チューブ生成ノード群を構築
# ------------------------------------------------------------------

def _add_tube_nodes(nodes, links, curve_output, gin, radius_output, x_offset=0):
    """カーブ → チューブメッシュ → マテリアル設定 → Join を構築して join ノードを返す."""
    width_attr = nodes.new("GeometryNodeInputNamedAttribute")
    width_attr.location = (x_offset - 220, -80)
    width_attr.data_type = "FLOAT"
    width_attr.inputs["Name"].default_value = VG_INTERSECTION_LINE_WIDTH

    width_switch = nodes.new("GeometryNodeSwitch")
    width_switch.location = (x_offset - 20, -80)
    width_switch.input_type = "FLOAT"
    width_switch.inputs["False"].default_value = 1.0
    links.new(width_attr.outputs["Exists"], width_switch.inputs["Switch"])
    links.new(width_attr.outputs["Attribute"], width_switch.inputs["True"])

    width_min = nodes.new("ShaderNodeMath")
    width_min.location = (x_offset + 160, -80)
    width_min.operation = "MAXIMUM"
    width_min.inputs[1].default_value = 0.0
    links.new(width_switch.outputs["Output"], width_min.inputs[0])

    width_max = nodes.new("ShaderNodeMath")
    width_max.location = (x_offset + 340, -80)
    width_max.operation = "MINIMUM"
    width_max.inputs[1].default_value = 1.0
    links.new(width_min.outputs[0], width_max.inputs[0])

    circle = nodes.new("GeometryNodeCurvePrimitiveCircle")
    circle.location = (x_offset + 0, -550)
    circle.mode = "RADIUS"
    for inp in circle.inputs:
        if inp.name == "Resolution" and inp.enabled:
            inp.default_value = 4

    links.new(radius_output, circle.inputs["Radius"])

    c2m = nodes.new("GeometryNodeCurveToMesh")
    c2m.location = (x_offset + 200, -300)
    links.new(curve_output, c2m.inputs[0])
    links.new(circle.outputs[0], c2m.inputs[1])
    if "Scale" in c2m.inputs:
        links.new(width_max.outputs[0], c2m.inputs["Scale"])
    if "Fill Caps" in c2m.inputs:
        c2m.inputs["Fill Caps"].default_value = True
    elif len(c2m.inputs) > 2:
        c2m.inputs[2].default_value = True

    mark_generated = nodes.new("GeometryNodeStoreNamedAttribute")
    mark_generated.label = _GENERATED_LINE_NODE_LABEL
    mark_generated.location = (x_offset + 320, -470)
    mark_generated.data_type = "BOOLEAN"
    mark_generated.domain = "FACE"
    mark_generated.inputs["Name"].default_value = GENERATED_LINE_ATTR
    mark_generated.inputs["Value"].default_value = True
    links.new(c2m.outputs[0], mark_generated.inputs["Geometry"])

    setmat = nodes.new("GeometryNodeSetMaterial")
    setmat.location = (x_offset + 500, -300)
    links.new(mark_generated.outputs["Geometry"], setmat.inputs[0])
    links.new(gin.outputs["マテリアル"], setmat.inputs["Material"])

    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (x_offset + 800, 0)
    # Join Geometry のマルチ入力は「後から接続したリンクが先頭（先に評価）」
    # という挙動を持つため、見た目の呼び出し順とは逆にsetmatを先・ginを後に
    # 接続し、結合順を「元メッシュ→ライン」にする（詳細は outline_setup.py の
    # Join 接続コメント参照）。
    links.new(setmat.outputs[0], join.inputs[0])
    links.new(gin.outputs[0], join.inputs[0])

    return join


def _add_tube_line_nodes(nodes, links, curve_output, gin, radius_output, x_offset=0, y_offset=0):
    """カーブから交差線だけを作成して返す."""
    width_attr = nodes.new("GeometryNodeInputNamedAttribute")
    width_attr.location = (x_offset - 220, y_offset - 80)
    width_attr.data_type = "FLOAT"
    width_attr.inputs["Name"].default_value = VG_INTERSECTION_LINE_WIDTH

    width_switch = nodes.new("GeometryNodeSwitch")
    width_switch.location = (x_offset - 20, y_offset - 80)
    width_switch.input_type = "FLOAT"
    width_switch.inputs["False"].default_value = 1.0
    links.new(width_attr.outputs["Exists"], width_switch.inputs["Switch"])
    links.new(width_attr.outputs["Attribute"], width_switch.inputs["True"])

    width_min = nodes.new("ShaderNodeMath")
    width_min.location = (x_offset + 160, y_offset - 80)
    width_min.operation = "MAXIMUM"
    width_min.inputs[1].default_value = 0.0
    links.new(width_switch.outputs["Output"], width_min.inputs[0])

    width_max = nodes.new("ShaderNodeMath")
    width_max.location = (x_offset + 340, y_offset - 80)
    width_max.operation = "MINIMUM"
    width_max.inputs[1].default_value = 1.0
    links.new(width_min.outputs[0], width_max.inputs[0])

    circle = nodes.new("GeometryNodeCurvePrimitiveCircle")
    circle.location = (x_offset, y_offset - 550)
    circle.mode = "RADIUS"
    for inp in circle.inputs:
        if inp.name == "Resolution" and inp.enabled:
            inp.default_value = 4
    links.new(radius_output, circle.inputs["Radius"])

    c2m = nodes.new("GeometryNodeCurveToMesh")
    c2m.location = (x_offset + 200, y_offset - 300)
    links.new(curve_output, c2m.inputs[0])
    links.new(circle.outputs[0], c2m.inputs[1])
    if "Scale" in c2m.inputs:
        links.new(width_max.outputs[0], c2m.inputs["Scale"])
    if "Fill Caps" in c2m.inputs:
        c2m.inputs["Fill Caps"].default_value = True
    elif len(c2m.inputs) > 2:
        c2m.inputs[2].default_value = True

    mark_generated = nodes.new("GeometryNodeStoreNamedAttribute")
    mark_generated.label = _GENERATED_LINE_NODE_LABEL
    mark_generated.location = (x_offset + 320, y_offset - 470)
    mark_generated.data_type = "BOOLEAN"
    mark_generated.domain = "FACE"
    mark_generated.inputs["Name"].default_value = GENERATED_LINE_ATTR
    mark_generated.inputs["Value"].default_value = True
    links.new(c2m.outputs[0], mark_generated.inputs["Geometry"])

    setmat = nodes.new("GeometryNodeSetMaterial")
    setmat.location = (x_offset + 500, y_offset - 300)
    links.new(mark_generated.outputs["Geometry"], setmat.inputs[0])
    links.new(gin.outputs["マテリアル"], setmat.inputs["Material"])
    return setmat.outputs[0]


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
        name=_TARGET_SOCKET, in_out="INPUT", socket_type="NodeSocketObject",
    )
    _setup_line_parameter_interface(tree)


def _setup_multi_interface(tree, count: int):
    """複数交差対象を1モディファイアにまとめるソケットを定義."""
    tree.interface.new_socket(
        name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry",
    )
    tree.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry",
    )
    for index in range(count):
        tree.interface.new_socket(
            name=_multi_target_socket_name(index),
            in_out="INPUT",
            socket_type="NodeSocketObject",
        )
    _setup_line_parameter_interface(tree)


def _setup_line_parameter_interface(tree):
    """交差線ノードで共通の線設定ソケットを定義."""
    radius_sock = tree.interface.new_socket(
        name=_THICKNESS_SOCKET, in_out="INPUT", socket_type="NodeSocketFloat",
    )
    radius_sock.default_value = 0.0005
    radius_sock.min_value = 0.0001
    radius_sock.max_value = 1.0
    offset_sock = tree.interface.new_socket(
        name=_OFFSET_SOCKET, in_out="INPUT", socket_type="NodeSocketFloat",
    )
    offset_sock.default_value = 0.0
    offset_sock.min_value = -1.0
    offset_sock.max_value = 1.0
    target_radius_sock = tree.interface.new_socket(
        name=_TARGET_THICKNESS_SOCKET, in_out="INPUT", socket_type="NodeSocketFloat",
    )
    target_radius_sock.default_value = 0.0
    target_radius_sock.min_value = 0.0
    target_radius_sock.max_value = 1.0
    tree.interface.new_socket(
        name=_MATERIAL_SOCKET, in_out="INPUT", socket_type="NodeSocketMaterial",
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
    has_line_faces = _add_target_has_line_faces(
        nodes, links, target_geo, (-500, -520),
    )
    radius = _add_effective_radius(nodes, links, gin, has_line_faces, (100, -720))

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
    join = _add_tube_nodes(nodes, links, m2c.outputs[0], gin, radius, x_offset=100)
    links.new(join.outputs[0], gout.inputs[0])

    return tree


def _multi_target_socket_name(index: int) -> str:
    return f"{_TARGET_SOCKET} {index + 1}"


def _create_boolean_multi_tree(count: int) -> bpy.types.NodeTree:
    """Boolean 法で複数相手を1モディファイア内に並べる GN ツリー."""
    tree = bpy.data.node_groups.new(
        name=f"{_MULTI_TREE_PREFIX}{count:03d}", type="GeometryNodeTree",
    )
    _setup_multi_interface(tree, count)
    nodes = tree.nodes
    links = tree.links

    gin = nodes.new("NodeGroupInput")
    gin.location = (-1700, 0)

    gout = nodes.new("NodeGroupOutput")
    gout.location = (1800, 0)

    del_shell = _add_shell_strip_nodes(nodes, links, gin)
    line_outputs = []
    for index in range(count):
        y_offset = -900 * index
        obj_info = nodes.new("GeometryNodeObjectInfo")
        obj_info.location = (-1150, -500 + y_offset)
        obj_info.transform_space = "RELATIVE"
        links.new(
            gin.outputs[_multi_target_socket_name(index)],
            obj_info.inputs["Object"],
        )

        target_geo = _add_target_solidify(
            nodes, links, obj_info.outputs[4], 0.0001, (-950, -500 + y_offset),
        )
        has_line_faces = _add_target_has_line_faces(
            nodes, links, target_geo, (-760, -520 + y_offset),
        )
        radius = _add_effective_radius(
            nodes, links, gin, has_line_faces, (-120, -720 + y_offset),
        )

        boolean = nodes.new("GeometryNodeMeshBoolean")
        boolean.location = (-760, -200 + y_offset)
        boolean.operation = "DIFFERENCE"
        boolean.solver = "EXACT"
        links.new(del_shell.outputs["Geometry"], boolean.inputs[0])
        links.new(target_geo, boolean.inputs[1])

        separate = nodes.new("GeometryNodeSeparateGeometry")
        separate.location = (-560, -200 + y_offset)
        separate.domain = "EDGE"
        links.new(boolean.outputs["Mesh"], separate.inputs["Geometry"])
        links.new(boolean.outputs["Intersecting Edges"], separate.inputs["Selection"])

        m2c = nodes.new("GeometryNodeMeshToCurve")
        m2c.location = (-360, -200 + y_offset)
        links.new(separate.outputs["Selection"], m2c.inputs[0])

        line_outputs.append(
            _add_tube_line_nodes(
                nodes,
                links,
                m2c.outputs[0],
                gin,
                radius,
                x_offset=80,
                y_offset=y_offset,
            )
        )

    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (1500, 0)
    # Join Geometry のマルチ入力は「後から接続したリンクが先頭（先に評価）」
    # という挙動を持つため、結合順を「元メッシュ→ライン1→ライン2→...」に
    # するには line_outputs を逆順で先に接続し、gin（元メッシュ）を最後に
    # 接続する必要がある（詳細は outline_setup.py の Join 接続コメント参照）。
    for output in reversed(line_outputs):
        links.new(output, join.inputs[0])
    links.new(gin.outputs[0], join.inputs[0])
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
    has_line_faces = _add_target_has_line_faces(
        nodes, links, target_geo, (-500, -600),
    )
    radius = _add_effective_radius(nodes, links, gin, has_line_faces, (750, -920))

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
    join = _add_tube_nodes(nodes, links, spline_res.outputs[0], gin, radius, x_offset=650)
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
        if _find_socket_id(tree, _TARGET_SOCKET) is None:
            bpy.data.node_groups.remove(tree)
            return creator()
        if _find_socket_id(tree, _TARGET_THICKNESS_SOCKET) is None:
            bpy.data.node_groups.remove(tree)
            return creator()
        if _find_socket_id(tree, _OFFSET_SOCKET) is None:
            bpy.data.node_groups.remove(tree)
            return creator()
        if not any(n.bl_idname == "GeometryNodeExtrudeMesh" for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return creator()
        if method != "SDF" and not _uses_exact_boolean_solver(tree):
            bpy.data.node_groups.remove(tree)
            return creator()
        if not any(getattr(n, "label", "") == _FILL_NODE_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return creator()
        if not any(getattr(n, "label", "") == _GENERATED_LINE_NODE_LABEL for n in tree.nodes):
            bpy.data.node_groups.remove(tree)
            return creator()
        if not _uses_named_attribute(tree, VG_INTERSECTION_LINE_WIDTH):
            bpy.data.node_groups.remove(tree)
            return creator()
        radius_socket = _find_interface_socket(tree, _THICKNESS_SOCKET)
        if radius_socket is not None and getattr(radius_socket, "max_value", 0.0) < 1.0:
            bpy.data.node_groups.remove(tree)
            return creator()
        return tree
    return creator()


def _get_or_create_multi_tree(count: int) -> bpy.types.NodeTree:
    """指定個数の交差対象を受け取る Boolean GN ツリーを取得または作成."""
    safe_count = max(1, int(count))
    name = f"{_MULTI_TREE_PREFIX}{safe_count:03d}"
    tree = bpy.data.node_groups.get(name)
    if tree is not None:
        sockets_ok = all(
            _find_socket_id(tree, _multi_target_socket_name(index)) is not None
            for index in range(safe_count)
        )
        if (
            sockets_ok
            and _find_socket_id(tree, _THICKNESS_SOCKET) is not None
            and _find_socket_id(tree, _OFFSET_SOCKET) is not None
            and any(n.bl_idname == "GeometryNodeMeshBoolean" for n in tree.nodes)
            and _uses_exact_boolean_solver(tree)
            and any(
                getattr(n, "label", "") == _GENERATED_LINE_NODE_LABEL
                for n in tree.nodes
            )
            and _uses_named_attribute(tree, VG_INTERSECTION_LINE_WIDTH)
        ):
            return tree
        bpy.data.node_groups.remove(tree)
    return _create_boolean_multi_tree(safe_count)


def _find_interface_socket(tree: bpy.types.NodeTree, name: str):
    for item in tree.interface.items_tree:
        if (
            getattr(item, "name", None) == name
            and getattr(item, "in_out", None) == "INPUT"
        ):
            return item
    return None


def _uses_named_attribute(tree: bpy.types.NodeTree, attr_name: str) -> bool:
    for node in tree.nodes:
        if node.bl_idname != "GeometryNodeInputNamedAttribute":
            continue
        name_input = node.inputs.get("Name")
        if name_input is not None and name_input.default_value == attr_name:
            return True
    return False


def _uses_exact_boolean_solver(tree: bpy.types.NodeTree) -> bool:
    boolean_nodes = [
        node for node in tree.nodes
        if node.bl_idname == "GeometryNodeMeshBoolean"
    ]
    return bool(boolean_nodes) and all(
        getattr(node, "solver", "") == "EXACT"
        for node in boolean_nodes
    )


def _find_socket_id(tree: bpy.types.NodeTree, name: str) -> str | None:
    """ツリーインターフェースからソケット識別子を検索."""
    for item in tree.interface.items_tree:
        if (
            getattr(item, "name", None) == name
            and getattr(item, "in_out", None) == "INPUT"
        ):
            return item.identifier
    return None


def _ensure_material_slot(
    obj: bpy.types.Object,
    material: bpy.types.Material | None,
) -> None:
    """生成した線素材を後続処理でも素材番号として扱えるようにする."""
    if material is None:
        return
    if not any(slot_mat == material for slot_mat in obj.data.materials):
        obj.data.materials.append(material)


def _outline_world_width(target: bpy.types.Object | None) -> float:
    """交差対象の B-MANGA Line 線幅をワールド上の太さとして取得する."""
    if target is None or target.type != "MESH":
        return 0.0
    mod = target.modifiers.get(MODIFIER_NAME)
    if mod is None:
        from . import outline_setup

        return outline_setup.sheet_outline_world_width(target)
    return scale_utils.world_width_from_modifier(target, mod.thickness)


def _target_outline_thickness(
    source: bpy.types.Object | None,
    target: bpy.types.Object | None,
) -> float:
    """交差対象の線幅を、ソース側のローカル幅へ変換して返す."""
    world_width = _outline_world_width(target)
    if source is None or source.type != "MESH":
        return world_width
    return scale_utils.modifier_thickness_for_world_width(source, world_width)


def _iter_source_scenes(
    obj: bpy.types.Object,
    scene: bpy.types.Scene | None,
) -> list[bpy.types.Scene]:
    scenes: list[bpy.types.Scene] = []
    if scene is not None:
        scenes.append(scene)
    for item in getattr(obj, "users_scene", ()) or ():
        if item is not None and item not in scenes:
            scenes.append(item)
    return scenes


def _auto_targets(
    obj: bpy.types.Object,
    scene: bpy.types.Scene | None = None,
) -> list[bpy.types.Object]:
    from . import plane_filter

    targets: list[bpy.types.Object] = []
    existing_targets = _existing_intersection_targets(obj)
    for src_scene in _iter_source_scenes(obj, scene):
        for candidate in src_scene.objects:
            try:
                candidate_type = candidate.type
                candidate_data = candidate.data
            except ReferenceError:
                continue
            if candidate == obj or candidate_type != "MESH" or candidate_data is None:
                continue
            if not getattr(candidate_data, "polygons", None):
                continue
            if not _has_outline_source(candidate):
                continue
            candidate_settings = getattr(candidate, "bmanga_line_settings", None)
            if (
                not _creation_in_range(candidate, src_scene)
                and candidate.name_full not in existing_targets
            ):
                continue
            if plane_filter.should_exclude_generated_lines(candidate, candidate_settings):
                continue
            if is_settings_locked(candidate) and candidate.name_full not in existing_targets:
                # ロック中オブジェクトとの新規ペア形成はしない
                # （既存ペアは現状維持のため existing_targets にあれば許容する）。
                continue
            candidate_enabled = bool(
                getattr(candidate_settings, "intersection_enabled", False)
            )
            if candidate_enabled and not _source_owns_intersection_pair(
                obj,
                candidate,
                src_scene,
            ):
                continue
            if candidate not in targets:
                targets.append(candidate)
    targets.sort(key=lambda item: item.name_full)
    return targets


def _source_owns_intersection_pair(
    source: bpy.types.Object,
    target: bpy.types.Object,
    scene: bpy.types.Scene | None,
) -> bool:
    """重複防止時、どちら側に交差線を作るかを決める.

    リフレッシュ時のアクティブオブジェクトに依存しない決定的な判定に
    する（アクティブ優先だと更新のたびに持ち主が入れ替わり、両側に
    ペアが残って交差線が二重になる — 2026-07-03 修正）。
    """
    from . import plane_filter

    # シート（板ポリ）のアウトラインはリムのみで立体が無く、
    # ソース側にすると交差判定が空になるため、必ず非シート側に持たせる。
    source_sheet = plane_filter.is_sheet_mesh(source)
    target_sheet = plane_filter.is_sheet_mesh(target)
    if source_sheet != target_sheet:
        return not source_sheet
    source_cost = _intersection_source_cost(source)
    target_cost = _intersection_source_cost(target)
    if source_cost != target_cost:
        return source_cost < target_cost
    return source.name_full < target.name_full


def _intersection_source_cost(obj: bpy.types.Object) -> tuple[int, int, str]:
    """交差線モディファイアを持たせる側を決めるための軽量コスト."""
    mesh = getattr(obj, "data", None)
    if mesh is None:
        return (0, 0, obj.name_full)
    return (len(mesh.polygons), len(mesh.vertices), obj.name_full)


def _creation_in_range(
    obj: bpy.types.Object,
    scene: bpy.types.Scene | None,
) -> bool:
    from . import camera_comp

    return camera_comp.intersection_line_creation_in_range(
        obj,
        scene,
        getattr(obj, "bmanga_line_settings", None),
    )


def _existing_intersection_targets(obj: bpy.types.Object) -> set[str]:
    names: set[str] = set()
    for mod in iter_intersection_modifiers(obj):
        if _is_shell_modifier(mod):
            from . import intersection_shell

            names.update(
                target.name_full
                for target in intersection_shell.modifier_targets(mod)
            )
            continue
        if _is_grouped_modifier(mod):
            names.update(target.name_full for target in _multi_modifier_targets(mod))
            continue
        target = _modifier_target(mod)
        if target is not None:
            names.add(target.name_full)
    return names


def _world_bounds(obj: bpy.types.Object):
    if not obj.bound_box:
        loc = obj.matrix_world.translation
        return (loc.x, loc.x, loc.y, loc.y, loc.z, loc.z)
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return (
        min(corner.x for corner in corners),
        max(corner.x for corner in corners),
        min(corner.y for corner in corners),
        max(corner.y for corner in corners),
        min(corner.z for corner in corners),
        max(corner.z for corner in corners),
    )


def _bounds_overlap(source: bpy.types.Object, target: bpy.types.Object, margin: float) -> bool:
    a_min_x, a_max_x, a_min_y, a_max_y, a_min_z, a_max_z = _world_bounds(source)
    b_min_x, b_max_x, b_min_y, b_max_y, b_min_z, b_max_z = _world_bounds(target)
    return (
        a_min_x <= b_max_x + margin and a_max_x + margin >= b_min_x
        and a_min_y <= b_max_y + margin and a_max_y + margin >= b_min_y
        and a_min_z <= b_max_z + margin and a_max_z + margin >= b_min_z
    )


def _intersection_margin(
    obj: bpy.types.Object,
    target: bpy.types.Object,
    thickness: float,
) -> float:
    return max(
        scale_utils.world_width_from_modifier(obj, thickness),
        _outline_world_width(obj),
        _outline_world_width(target),
        0.001,
    )


def _modifier_suffix(target: bpy.types.Object) -> str:
    raw = target.name_full or target.name or "Object"
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in raw)
    cleaned = cleaned.strip("_") or "Object"
    return cleaned[:48]


def _modifier_name_for_target(target: bpy.types.Object) -> str:
    return f"{INTERSECTION_MODIFIER_PREFIX}{_modifier_suffix(target)}"


def _modifier_target(mod: bpy.types.Modifier):
    tree = getattr(mod, "node_group", None)
    sid = _find_socket_id(tree, _TARGET_SOCKET) if tree is not None else None
    if sid is None:
        return None
    try:
        return mod[sid]
    except (KeyError, TypeError):
        return None


def _is_grouped_modifier(mod: bpy.types.Modifier) -> bool:
    return mod.name.startswith(_GROUPED_MODIFIER_NAME)


def _is_shell_modifier(mod: bpy.types.Modifier) -> bool:
    from . import intersection_shell

    return intersection_shell.is_shell_modifier(mod)


def is_deferred_viewport_modifier(mod: bpy.types.Modifier) -> bool:
    """ビューポート表示の復帰待ち交差線か返す."""
    try:
        return bool(mod.get(_DEFERRED_VIEWPORT_PROP, False))
    except TypeError:
        return False


def _basic_deferred_visibility(obj: bpy.types.Object, settings) -> bool:
    return (
        settings is None
        or bool(getattr(settings, "intersection_enabled", False))
    ) and not bool(obj.get(PROP_LINES_HIDDEN, False))


def _deferred_visibility_rules_enabled(settings) -> bool:
    if settings is None:
        return False
    return bool(
        getattr(settings, "use_camera_culling", False)
        or getattr(settings, "use_intersection_distance_limit", False)
    )


def _set_modifier_parameters(
    mod: bpy.types.Modifier,
    target: bpy.types.Object | None,
    thickness: float | None,
    offset: float | None,
    material: bpy.types.Material | None,
) -> None:
    tree = mod.node_group
    if tree is None:
        return
    sid_target = _find_socket_id(tree, _TARGET_SOCKET)
    if sid_target is not None and target is not None:
        mod[sid_target] = target
    sid_thickness = _find_socket_id(tree, _THICKNESS_SOCKET)
    if sid_thickness is not None and thickness is not None:
        mod[sid_thickness] = thickness
    sid_offset = _find_socket_id(tree, _OFFSET_SOCKET)
    if sid_offset is not None and offset is not None:
        mod[sid_offset] = offset
    sid_target_thickness = _find_socket_id(tree, _TARGET_THICKNESS_SOCKET)
    if sid_target_thickness is not None and target is not None:
        source = getattr(mod, "id_data", None)
        mod[sid_target_thickness] = _target_outline_thickness(source, target)
    sid_mat = _find_socket_id(tree, _MATERIAL_SOCKET)
    if sid_mat is not None and material is not None:
        mod[sid_mat] = material


def _multi_modifier_targets(mod: bpy.types.Modifier) -> list[bpy.types.Object]:
    tree = getattr(mod, "node_group", None)
    if tree is None:
        return []
    targets = []
    index = 0
    while True:
        sid = _find_socket_id(tree, _multi_target_socket_name(index))
        if sid is None:
            break
        try:
            target = mod[sid]
        except (KeyError, TypeError):
            target = None
        if getattr(target, "type", None) == "MESH":
            targets.append(target)
        index += 1
    return targets


def _set_multi_modifier_parameters(
    mod: bpy.types.Modifier,
    targets: list[bpy.types.Object] | None,
    thickness: float | None,
    offset: float | None,
    material: bpy.types.Material | None,
    target_thickness: float | None,
) -> None:
    tree = mod.node_group
    if tree is None:
        return
    if targets is not None:
        for index, target in enumerate(targets):
            sid_target = _find_socket_id(tree, _multi_target_socket_name(index))
            if sid_target is not None:
                mod[sid_target] = target
    sid_thickness = _find_socket_id(tree, _THICKNESS_SOCKET)
    if sid_thickness is not None and thickness is not None:
        mod[sid_thickness] = thickness
    sid_offset = _find_socket_id(tree, _OFFSET_SOCKET)
    if sid_offset is not None and offset is not None:
        mod[sid_offset] = offset
    sid_target_thickness = _find_socket_id(tree, _TARGET_THICKNESS_SOCKET)
    if sid_target_thickness is not None and target_thickness is not None:
        mod[sid_target_thickness] = target_thickness
    sid_mat = _find_socket_id(tree, _MATERIAL_SOCKET)
    if sid_mat is not None and material is not None:
        mod[sid_mat] = material


def _ensure_intersection_width_group(obj: bpy.types.Object) -> None:
    from . import vertex_analysis

    vertex_analysis.ensure_generated_width_storage(obj, VG_INTERSECTION_LINE_WIDTH)


def _position_intersection_modifiers(obj: bpy.types.Object) -> None:
    modifier_stack.reorder_line_modifiers(obj)


def _apply_intersection_modifier(
    obj: bpy.types.Object,
    target: bpy.types.Object,
    tree: bpy.types.NodeTree,
    thickness: float,
    offset: float,
    material: bpy.types.Material | None,
) -> bool:
    name = _modifier_name_for_target(target)
    mod = obj.modifiers.get(name)
    if mod is None:
        mod = obj.modifiers.new(name=name, type="NODES")
    mod.node_group = tree
    _set_modifier_parameters(mod, target, thickness, offset, material)
    return True


def _queue_deferred_viewport_modifier(
    obj: bpy.types.Object,
    mod: bpy.types.Modifier,
) -> None:
    global _deferred_viewport_timer_running
    try:
        mod[_DEFERRED_VIEWPORT_PROP] = True
    except TypeError:
        return
    mod.show_viewport = False
    item = (obj.name_full, mod.name)
    if item not in _deferred_viewport_queue:
        _deferred_viewport_queue.append(item)
    if not _deferred_viewport_timer_running:
        _deferred_viewport_timer_running = True
        bpy.app.timers.register(
            _restore_deferred_viewport_step,
            first_interval=_DEFERRED_VIEWPORT_INTERVAL,
        )


def _restore_deferred_viewport_step():
    global _deferred_viewport_timer_running
    while _deferred_viewport_queue:
        obj_name, mod_name = _deferred_viewport_queue.pop(0)
        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            continue
        mod = obj.modifiers.get(mod_name)
        if mod is None:
            continue
        try:
            if _DEFERRED_VIEWPORT_PROP in mod:
                del mod[_DEFERRED_VIEWPORT_PROP]
        except TypeError:
            pass
        settings = getattr(obj, "bmanga_line_settings", None)
        visible = _basic_deferred_visibility(obj, settings)
        if visible and _deferred_visibility_rules_enabled(settings):
            from . import camera_comp
            if camera_comp.refresh_visibility_objects(bpy.context, [obj]):
                break
        mod.show_viewport = visible
        mod.show_render = visible
        break
    if _deferred_viewport_queue:
        return _DEFERRED_VIEWPORT_INTERVAL
    _deferred_viewport_timer_running = False
    return None


def cancel_deferred_viewport_refresh() -> None:
    """アドオン終了時に未完了の順次表示復帰タイマーを止める."""
    global _deferred_viewport_timer_running
    timers = getattr(bpy.app, "timers", None)
    if timers is not None:
        try:
            if timers.is_registered(_restore_deferred_viewport_step):
                timers.unregister(_restore_deferred_viewport_step)
        except (AttributeError, ValueError):
            pass
    _deferred_viewport_queue.clear()
    _deferred_viewport_timer_running = False


def _defer_heavy_viewport_refresh(objects: list[bpy.types.Object]) -> None:
    mods = [
        (obj, mod)
        for obj in objects
        for mod in iter_intersection_modifiers(obj)
    ]
    if len(mods) <= _DEFERRED_VIEWPORT_THRESHOLD:
        return
    for obj, mod in mods:
        _queue_deferred_viewport_modifier(obj, mod)


def _max_target_thickness(source: bpy.types.Object, targets: list[bpy.types.Object]) -> float:
    if not targets:
        return 0.0
    return max(_target_outline_thickness(source, target) for target in targets)


def _apply_multi_intersection_modifier(
    obj: bpy.types.Object,
    targets: list[bpy.types.Object],
    tree: bpy.types.NodeTree,
    thickness: float,
    offset: float,
    material: bpy.types.Material | None,
) -> bool:
    mod = obj.modifiers.get(_GROUPED_MODIFIER_NAME)
    if mod is None:
        mod = obj.modifiers.new(name=_GROUPED_MODIFIER_NAME, type="NODES")
    mod.node_group = tree
    _set_multi_modifier_parameters(
        mod,
        targets,
        thickness,
        offset,
        material,
        _max_target_thickness(obj, targets),
    )
    return True


# ------------------------------------------------------------------
# 適用 / 削除 / 更新
# ------------------------------------------------------------------

def apply_intersection_lines(
    obj: bpy.types.Object,
    target: bpy.types.Object | None = None,
    thickness: float = 0.0005,
    offset: float = 0.0,
    material: bpy.types.Material | None = None,
    method: str = "BOOLEAN",
    scene: bpy.types.Scene | None = None,
    *,
    signature_cache: dict[int, str] | None = None,
) -> bool:
    """交差線 GN モディファイアを適用. 成功時 True."""
    # 2026-07-03 ユーザー確定: 交差線は「ライン素材（高速）」のみ。旧ファイルへ
    # BOOLEAN/SDF が保存されていても SHELL として扱う（他方式は UI 非公開の内部実装）。
    method = _SHELL_METHOD
    if obj.type != "MESH":
        return False
    from . import plane_filter

    settings = getattr(obj, "bmanga_line_settings", None)
    if plane_filter.should_exclude_generated_lines(obj, settings):
        remove_intersection_lines(obj)
        return True
    if not _creation_in_range(obj, scene):
        if any(iter_intersection_modifiers(obj)):
            update_parameters(obj, thickness=thickness, offset=offset, material=material)
        return True

    _ensure_intersection_width_group(obj)

    if method == _SHELL_METHOD:
        from . import intersection_shell

        for mod in list(iter_intersection_modifiers(obj)):
            if not intersection_shell.is_shell_modifier(mod):
                obj.modifiers.remove(mod)
        return intersection_shell.apply_intersection_shell(
            obj,
            thickness,
            offset,
            material,
            scene,
        )

    if material is not None:
        _ensure_material_slot(obj, material)

    target_candidates = [target] if target is not None else _auto_targets(obj, scene)
    targets = [
        item for item in target_candidates
        if _creation_in_range(item, scene)
        and _bounds_overlap(obj, item, _intersection_margin(obj, item, thickness))
    ]
    use_grouped = (
        target is None
        and method == "BOOLEAN"
        and len(targets) >= _GROUPED_TARGET_THRESHOLD
    )
    tree = _get_or_create_multi_tree(len(targets)) if use_grouped else _get_or_create_tree(method)
    if use_grouped and len(targets) >= _GROUPED_TARGET_THRESHOLD:
        expected_names = {_GROUPED_MODIFIER_NAME}
    else:
        use_grouped = False
        expected_names = {_modifier_name_for_target(item) for item in targets}
    for mod in list(iter_intersection_modifiers(obj)):
        if mod.name not in expected_names:
            if _is_shell_modifier(mod):
                from . import intersection_shell

                intersection_shell.cleanup_target_collection(obj)
            obj.modifiers.remove(mod)

    if use_grouped:
        _apply_multi_intersection_modifier(
            obj,
            targets,
            tree,
            thickness,
            offset,
            material,
        )
    else:
        for item in targets:
            _apply_intersection_modifier(obj, item, tree, thickness, offset, material)
    _position_intersection_modifiers(obj)

    return True


def remove_intersection_lines(obj: bpy.types.Object) -> bool:
    """交差線 GN モディファイアを削除."""
    if obj.type != "MESH":
        return False
    removed = False
    for mod in list(iter_intersection_modifiers(obj)):
        item = (obj.name_full, mod.name)
        while item in _deferred_viewport_queue:
            _deferred_viewport_queue.remove(item)
        if _is_shell_modifier(mod):
            from . import intersection_shell

            intersection_shell.cleanup_target_collection(obj)
        obj.modifiers.remove(mod)
        removed = True
    return removed


def scene_has_enabled_intersections(scene: bpy.types.Scene | None) -> bool:
    """シーン内に交差線オンのメッシュが残っているか返す."""
    if scene is None:
        return False
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        settings = getattr(obj, "bmanga_line_settings", None)
        if settings is not None and getattr(settings, "intersection_enabled", False):
            return True
    return False


def prune_excluded_intersections(scene: bpy.types.Scene | None) -> int:
    """Remove existing intersection modifiers that involve excluded sheet meshes."""
    if scene is None:
        return 0
    from . import outline_setup, plane_filter

    removed = 0
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        obj_settings = getattr(obj, "bmanga_line_settings", None)
        source_excluded = plane_filter.should_exclude_generated_lines(obj, obj_settings)
        for mod in list(iter_intersection_modifiers(obj)):
            if _is_grouped_modifier(mod):
                collection_targets = _multi_modifier_targets(mod)
                if source_excluded or not collection_targets:
                    obj.modifiers.remove(mod)
                    removed += 1
                    continue
                kept_targets = [
                    item for item in collection_targets
                    if not plane_filter.should_exclude_generated_lines(
                        item,
                        getattr(item, "bmanga_line_settings", None),
                    )
                ]
                if len(kept_targets) != len(collection_targets):
                    _refresh_source_intersections(
                        obj,
                        scene,
                        outline_setup,
                        plane_filter,
                    )
                    removed += 1
                continue
            if _is_shell_modifier(mod):
                from . import intersection_shell

                if source_excluded:
                    intersection_shell.cleanup_target_collection(obj)
                    obj.modifiers.remove(mod)
                    removed += 1
                else:
                    intersection_shell.refresh_target_collection(obj, scene)
                continue
            target = _modifier_target(mod)
            target_settings = getattr(target, "bmanga_line_settings", None)
            target_excluded = (
                target is not None
                and plane_filter.should_exclude_generated_lines(target, target_settings)
            )
            if source_excluded or target_excluded:
                obj.modifiers.remove(mod)
                removed += 1
    return removed


def update_parameters(
    obj: bpy.types.Object,
    target: bpy.types.Object | None = ...,
    thickness: float | None = None,
    offset: float | None = None,
    material: bpy.types.Material | None = None,
) -> bool:
    """既存モディファイアのパラメータを更新."""
    changed = False
    for mod in iter_intersection_modifiers(obj):
        if mod.node_group is None:
            continue
        if _is_shell_modifier(mod):
            from . import intersection_shell

            intersection_shell.update_modifier_parameters(
                mod,
                thickness,
                offset,
                material,
            )
            changed = True
            continue
        if _is_grouped_modifier(mod):
            _set_multi_modifier_parameters(
                mod,
                None,
                thickness,
                offset,
                material,
                None,
            )
            changed = True
            continue
        item_target = target if target is not ... else _modifier_target(mod)
        _set_modifier_parameters(mod, item_target, thickness, offset, material)
        changed = True
    return changed


def update_target_width_references(
    scene: bpy.types.Scene | None,
    targets: list[bpy.types.Object] | tuple[bpy.types.Object, ...] | None = None,
) -> int:
    """交差対象側アウトライン幅の参照値を現在の幅へ更新."""
    if scene is None:
        return 0
    target_set = {target.as_pointer() for target in targets} if targets else None
    changed = 0
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        for mod in iter_intersection_modifiers(obj):
            if _is_shell_modifier(mod):
                from . import intersection_shell

                collection = intersection_shell._modifier_target_collection(mod)
                collection_targets = intersection_shell.collection_real_targets(collection)
                if target_set is not None and collection is not None:
                    if not any(
                        item.as_pointer() in target_set
                        for item in collection_targets
                    ):
                        continue
                if intersection_shell.update_target_width_reference(mod):
                    changed += 1
                continue
            if _is_grouped_modifier(mod):
                collection_targets = _multi_modifier_targets(mod)
                if not collection_targets:
                    continue
                if target_set is not None and not any(
                    item.as_pointer() in target_set
                    for item in collection_targets
                ):
                    continue
                _set_multi_modifier_parameters(
                    mod,
                    None,
                    None,
                    None,
                    None,
                    _max_target_thickness(obj, collection_targets),
                )
                changed += 1
                continue
            target = _modifier_target(mod)
            if target is None:
                continue
            if target_set is not None and target.as_pointer() not in target_set:
                continue
            _set_modifier_parameters(mod, target, None, None, None)
            changed += 1
    return changed


def _intersection_refresh_sources(
    scene: bpy.types.Scene,
    sources: list[bpy.types.Object] | tuple[bpy.types.Object, ...] | None = None,
) -> list[bpy.types.Object]:
    objects = []
    seen: set[int] = set()
    source_iter = sources if sources is not None else scene.objects
    for obj in source_iter:
        try:
            if obj.type != "MESH":
                continue
            pointer = obj.as_pointer()
        except ReferenceError:
            continue
        if pointer in seen:
            continue
        seen.add(pointer)
        objects.append(obj)
    active = getattr(getattr(bpy.context, "view_layer", None), "objects", None)
    active_obj = getattr(active, "active", None)
    if active_obj is None or active_obj not in objects:
        return objects
    return [obj for obj in objects if obj != active_obj] + [active_obj]


def _refresh_source_intersections(
    obj: bpy.types.Object,
    scene: bpy.types.Scene,
    outline_setup,
    plane_filter,
) -> bool:
    if not _has_outline_source(obj):
        return False
    settings = getattr(obj, "bmanga_line_settings", None)
    if settings is None:
        return False
    if plane_filter.should_exclude_generated_lines(obj, settings):
        remove_intersection_lines(obj)
        return False
    if not getattr(settings, "intersection_enabled", False):
        remove_intersection_lines(obj)
        return False
    if not _creation_in_range(obj, scene):
        if any(iter_intersection_modifiers(obj)):
            update_parameters(
                obj,
                thickness=scale_utils.modifier_thickness_for_world_width(
                    obj,
                    settings.intersection_thickness,
                ),
                offset=settings.intersection_line_offset,
                material=outline_setup.get_line_material(obj, "intersection"),
            )
            return True
        return False
    apply_intersection_lines(
        obj,
        thickness=scale_utils.modifier_thickness_for_world_width(
            obj,
            settings.intersection_thickness,
        ),
        offset=settings.intersection_line_offset,
        material=outline_setup.get_line_material(obj, "intersection"),
        method="SHELL",
        scene=scene,
    )
    return any(iter_intersection_modifiers(obj))


def refresh_scene_intersections(
    scene: bpy.types.Scene,
    sources: list[bpy.types.Object] | tuple[bpy.types.Object, ...] | None = None,
) -> list[bpy.types.Object]:
    """シーン内の交差線を、現在のメッシュ構成に合わせて作り直す."""
    from . import intersection_shell, outline_setup, plane_filter

    refreshed: list[bpy.types.Object] = []
    refresh_sources = _intersection_refresh_sources(scene, sources)
    for obj in refresh_sources:
        if _refresh_source_intersections(obj, scene, outline_setup, plane_filter):
            refreshed.append(obj)
    intersection_shell.cleanup_orphan_proxies()
    _defer_heavy_viewport_refresh(refreshed)
    return refreshed


# ------------------------------------------------------------------
# 保存済み線方式
# ------------------------------------------------------------------

from . import intersection_cache as _intersection_cache
from .core import INTERSECTION_MODIFIER_NAME as _CACHED_INTERSECTION_MODIFIER_NAME


def _existing_intersection_targets(obj: bpy.types.Object) -> set[str]:
    return _intersection_cache.target_names(obj)


def apply_intersection_lines(
    obj: bpy.types.Object,
    target: bpy.types.Object | None = None,
    thickness: float = 0.0005,
    offset: float = 0.0,
    material: bpy.types.Material | None = None,
    method: str = "BOOLEAN",
    scene: bpy.types.Scene | None = None,
    *,
    signature_cache: dict[int, str] | None = None,
) -> bool:
    """交差線を検出し、保存済み線として適用."""
    del method
    if obj.type != "MESH":
        return False
    from . import plane_filter

    settings = getattr(obj, "bmanga_line_settings", None)
    if plane_filter.should_exclude_generated_lines(obj, settings):
        remove_intersection_lines(obj)
        return True

    existing_names = _existing_intersection_targets(obj)
    if not _creation_in_range(obj, scene):
        if existing_names and any(iter_intersection_modifiers(obj)):
            update_parameters(obj, thickness=thickness, material=material)
        return True

    if target is None:
        reused = _intersection_cache.try_reuse_cached_intersection_lines(
            obj,
            thickness=thickness,
            offset=offset,
            material=material,
            scene=scene,
            signature_cache=signature_cache,
        )
        if reused is not None:
            return reused

    target_candidates = [target] if target is not None else _auto_targets(obj, scene)
    targets: list[bpy.types.Object] = []
    seen_targets: set[int] = set()
    for item in target_candidates:
        try:
            item_type = getattr(item, "type", None)
        except ReferenceError:
            continue
        if (
            item is None
            or item == obj
            or item_type != "MESH"
            or item.as_pointer() in seen_targets
        ):
            continue
        already_cached = item.name_full in existing_names
        if not already_cached and not _creation_in_range(item, scene):
            continue
        if not _bounds_overlap(obj, item, _intersection_margin(obj, item, thickness)):
            continue
        seen_targets.add(item.as_pointer())
        targets.append(item)

    if not targets:
        remove_intersection_lines(obj)
        return True

    return _intersection_cache.apply_cached_intersection_lines(
        obj,
        targets,
        thickness=thickness,
        offset=offset,
        material=material,
        scene=scene,
        signature_cache=signature_cache,
    )


def remove_intersection_lines(obj: bpy.types.Object) -> bool:
    """交差線の表示モディファイアと保存済み線データを削除."""
    if obj.type != "MESH":
        return False
    item = (obj.name_full, _CACHED_INTERSECTION_MODIFIER_NAME)
    while item in _deferred_viewport_queue:
        _deferred_viewport_queue.remove(item)
    return _intersection_cache.remove_cached_intersection_lines(obj)


def is_deferred_viewport_modifier(mod: bpy.types.Modifier) -> bool:
    """保存済み線方式では表示復帰待ちは発生しない."""
    del mod
    return False


def _defer_heavy_viewport_refresh(objects: list[bpy.types.Object]) -> None:
    """保存済み線方式では順次表示復帰処理は不要."""
    del objects


def update_parameters(
    obj: bpy.types.Object,
    target: bpy.types.Object | None = ...,
    thickness: float | None = None,
    offset: float | None = None,
    material: bpy.types.Material | None = None,
) -> bool:
    """保存済み交差線の表示パラメータだけを更新."""
    del target
    if obj.type != "MESH":
        return False
    return _intersection_cache.update_cached_parameters(
        obj,
        thickness=thickness,
        offset=offset,
        material=material,
    )


def modifier_targets(mod: bpy.types.Modifier) -> list[bpy.types.Object]:
    """保存済み交差線の対象オブジェクトを返す."""
    return _intersection_cache.modifier_targets(mod)


def update_target_width_references(
    scene: bpy.types.Scene | None,
    targets: list[bpy.types.Object] | tuple[bpy.types.Object, ...] | None = None,
) -> int:
    """交差対象側アウトライン幅の表示参照を更新."""
    if scene is None:
        return 0
    target_set = {target.as_pointer() for target in targets} if targets else None
    changed = 0
    for obj in scene.objects:
        try:
            obj_type = obj.type
        except ReferenceError:
            continue
        if obj_type != "MESH":
            continue
        mod = obj.modifiers.get(_CACHED_INTERSECTION_MODIFIER_NAME)
        if mod is None:
            continue
        if target_set is not None:
            current_targets = _intersection_cache.modifier_targets(mod)
            if not any(target.as_pointer() in target_set for target in current_targets):
                continue
        if _intersection_cache.update_cached_parameters(obj):
            changed += 1
    return changed


def prune_excluded_intersections(scene: bpy.types.Scene | None) -> int:
    """除外対象が絡む保存済み交差線を更新または削除."""
    if scene is None:
        return 0
    from . import outline_setup, plane_filter

    changed = 0
    for obj in scene.objects:
        try:
            obj_type = obj.type
        except ReferenceError:
            continue
        if obj_type != "MESH":
            continue
        mod = obj.modifiers.get(_CACHED_INTERSECTION_MODIFIER_NAME)
        if mod is None:
            continue
        settings = getattr(obj, "bmanga_line_settings", None)
        if plane_filter.should_exclude_generated_lines(obj, settings):
            if remove_intersection_lines(obj):
                changed += 1
            continue
        current_targets = _intersection_cache.modifier_targets(mod)
        if any(
            plane_filter.should_exclude_generated_lines(
                target,
                getattr(target, "bmanga_line_settings", None),
            )
            for target in current_targets
        ):
            if _refresh_source_intersections(obj, scene, outline_setup, plane_filter):
                changed += 1
    return changed


def _refresh_source_intersections(
    obj: bpy.types.Object,
    scene: bpy.types.Scene,
    outline_setup,
    plane_filter,
    signature_cache: dict[int, str] | None = None,
) -> bool:
    try:
        obj_type = obj.type
    except ReferenceError:
        return False
    if obj_type != "MESH":
        return False
    if is_settings_locked(obj):
        # ロック中は現状維持（新規作成・削除・再構築のいずれもしない）。
        # 解除後の次回更新で決定的な所有権ルールにより自然に正常化される。
        return False
    if not _has_outline_source(obj):
        return False
    settings = getattr(obj, "bmanga_line_settings", None)
    if settings is None:
        return False
    if plane_filter.should_exclude_generated_lines(obj, settings):
        remove_intersection_lines(obj)
        return False
    if not getattr(settings, "intersection_enabled", False):
        remove_intersection_lines(obj)
        return False
    thickness = scale_utils.modifier_thickness_for_world_width(
        obj,
        settings.intersection_thickness,
    )
    material = outline_setup.get_line_material(obj, "intersection")
    if not _creation_in_range(obj, scene):
        if _existing_intersection_targets(obj) and any(iter_intersection_modifiers(obj)):
            update_parameters(obj, thickness=thickness, material=material)
            return True
        return False
    apply_intersection_lines(
        obj,
        thickness=thickness,
        offset=settings.intersection_line_offset,
        material=material,
        scene=scene,
        signature_cache=signature_cache,
    )
    return any(iter_intersection_modifiers(obj))


def refresh_scene_intersections(
    scene: bpy.types.Scene,
    sources: list[bpy.types.Object] | tuple[bpy.types.Object, ...] | None = None,
) -> list[bpy.types.Object]:
    """シーン内の交差線を、保存済み線として更新する."""
    from . import outline_setup, plane_filter

    refreshed: list[bpy.types.Object] = []
    signature_cache: dict[int, str] = {}
    for obj in _intersection_refresh_sources(scene, sources):
        try:
            obj_type = obj.type
        except ReferenceError:
            continue
        if obj_type != "MESH":
            continue
        if _refresh_source_intersections(
            obj,
            scene,
            outline_setup,
            plane_filter,
            signature_cache,
        ):
            refreshed.append(obj)
    _intersection_cache.cleanup_orphan_cache_objects()
    return refreshed
