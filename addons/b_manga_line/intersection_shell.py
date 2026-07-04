"""B-MANGA Line fast shell-based intersection lines.

ライン素材のソリッド面と他メッシュ表面の交差境界を使い、
個別ペアのモディファイアを作らずに交差線を作る。
"""

from __future__ import annotations

import bpy

from . import intersection_lines, intersection_shell_node_helpers
from . import modifier_stack, outline_setup, scale_utils
from .core import (
    INTERSECTION_MODIFIER_PREFIX,
    MODIFIER_NAME,
    SHEET_OUTLINE_MODIFIER_NAME,
)


SHELL_TREE_NAME = "BML_Intersection_Shell"
SHELL_MODIFIER_NAME = f"{INTERSECTION_MODIFIER_PREFIX}Shell"
_THICKNESS_SOCKET = "線の太さ"
_OFFSET_SOCKET = "オフセット"
_MATERIAL_SOCKET = "マテリアル"
_TARGET_COLLECTION_SOCKET = "交差対象グループ"
_TARGET_THICKNESS_SOCKET = "交差対象の線幅"
_OWN_OUTLINE_SOCKET = "自分のアウトライン幅"
_OUTLINE_MATERIAL_SOCKET = "アウトライン素材"
_LINE_MATERIAL_INDEX_SOCKET = "ライン素材番号"
_HAS_TARGET_SOCKET = "交差対象あり"
_MIDPOINT_FACTOR_SOCKET = "中間頂点の線幅調整"
_MIDPOINT_JITTER_SOCKET = "中間頂点の乱れ (%)"
_MIDPOINT_ANGLE_SOCKET = "検出角度"
_WIDTH_CURVE_25_SOCKET = "変化グラフ 25%"
_WIDTH_CURVE_50_SOCKET = "変化グラフ 50%"
_WIDTH_CURVE_75_SOCKET = "変化グラフ 75%"
_SHELL_BOOLEAN_NODE_LABEL = "BML_IntersectionShellBoolean"
_SHELL_SURFACE_NODE_LABEL = "BML_IntersectionShellSurface"
_SHELL_UNION_NODE_LABEL = "BML_IntersectionShellTargetUnion"
_TARGET_COLLECTION_PROP = "bml_intersection_shell_target_collection"
_TARGET_COLLECTION_PREFIX = "BML_IntersectionTargets"
_PROXY_SOURCE_PROP = "bml_intersection_shell_proxy_source"
_PROXY_PREFIX = "BML_IntersectionProxy"
_SHELL_RADIUS_NODE_LABEL = "BML_IntersectionShellOwnRadius"
_CURVE_RADIUS_NORMALIZER_LABEL = "BML_IntersectionShellCurveRadius"
_SHELL_COMBINED_THICKNESS_NODE_LABEL = "BML_IntersectionShellCombinedThickness"
_SHELL_PROFILE_NODE_LABEL = "BML_IntersectionShellProfile"
_SHELL_GAP_COVERAGE_NODE_LABEL = "BML_IntersectionShellGapCoverage"
_SHELL_BRANCH_SPLIT_NODE_LABEL = "BML_IntersectionShellPathWidthV15"
_SHELL_SUBDIVIDE_NODE_LABEL = "BML_IntersectionShellPathWidthV15Midpoints"
SHELL_TUBE_PROFILE_RESOLUTION = 12
SHELL_GAP_COVERAGE_FACTOR = 1.08


def is_shell_modifier(mod: bpy.types.Modifier) -> bool:
    return mod.name == SHELL_MODIFIER_NAME


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
    radius_sock = tree.interface.new_socket(
        name=_THICKNESS_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    radius_sock.default_value = 0.0005
    radius_sock.min_value = 0.0001
    radius_sock.max_value = 1.0
    offset_sock = tree.interface.new_socket(
        name=_OFFSET_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    offset_sock.default_value = 1.0
    offset_sock.min_value = -1.0
    offset_sock.max_value = 1.0
    tree.interface.new_socket(
        name=_MATERIAL_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketMaterial",
    )
    tree.interface.new_socket(
        name=_TARGET_COLLECTION_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketCollection",
    )
    target_radius_sock = tree.interface.new_socket(
        name=_TARGET_THICKNESS_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    target_radius_sock.default_value = 0.0
    target_radius_sock.min_value = 0.0
    target_radius_sock.max_value = 1.0
    own_outline_sock = tree.interface.new_socket(
        name=_OWN_OUTLINE_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    own_outline_sock.default_value = 0.0
    own_outline_sock.min_value = 0.0
    own_outline_sock.max_value = 1.0
    tree.interface.new_socket(
        name=_OUTLINE_MATERIAL_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketMaterial",
    )
    line_material_sock = tree.interface.new_socket(
        name=_LINE_MATERIAL_INDEX_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketInt",
    )
    line_material_sock.default_value = 999
    line_material_sock.min_value = 0
    has_target_sock = tree.interface.new_socket(
        name=_HAS_TARGET_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketBool",
    )
    has_target_sock.default_value = False
    factor_sock = tree.interface.new_socket(
        name=_MIDPOINT_FACTOR_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    factor_sock.default_value = 0.0
    factor_sock.min_value = -1.0
    factor_sock.max_value = 1.0
    jitter_sock = tree.interface.new_socket(
        name=_MIDPOINT_JITTER_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    jitter_sock.default_value = 0.0
    jitter_sock.min_value = 0.0
    jitter_sock.max_value = 50.0
    angle_sock = tree.interface.new_socket(
        name=_MIDPOINT_ANGLE_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    angle_sock.default_value = 1.7453292520
    angle_sock.min_value = 0.0
    angle_sock.max_value = 3.1415926536
    if hasattr(angle_sock, "subtype"):
        angle_sock.subtype = "ANGLE"
    for name, default in (
        (_WIDTH_CURVE_25_SOCKET, 0.25),
        (_WIDTH_CURVE_50_SOCKET, 0.50),
        (_WIDTH_CURVE_75_SOCKET, 0.75),
    ):
        sock = tree.interface.new_socket(
            name=name,
            in_out="INPUT",
            socket_type="NodeSocketFloat",
        )
        sock.default_value = default
        sock.min_value = 0.0
        sock.max_value = 1.0


def _create_node_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.new(name=SHELL_TREE_NAME, type="GeometryNodeTree")
    _setup_interface(tree)
    nodes = tree.nodes
    links = tree.links

    gin = nodes.new("NodeGroupInput")
    gin.location = (-1300, 0)
    gout = nodes.new("NodeGroupOutput")
    gout.location = (900, 0)

    collection_info = nodes.new("GeometryNodeCollectionInfo")
    collection_info.location = (-1060, -460)
    collection_info.inputs["Separate Children"].default_value = True
    collection_info.inputs["Reset Children"].default_value = False
    links.new(gin.outputs[_TARGET_COLLECTION_SOCKET], collection_info.inputs["Collection"])

    realize = nodes.new("GeometryNodeRealizeInstances")
    realize.location = (-840, -460)
    links.new(collection_info.outputs["Instances"], realize.inputs["Geometry"])

    # 板ポリ等の非多様体ターゲットはプロキシ側の Solidify で閉じてあるため
    # （_get_or_create_proxy 参照）、ここでの全面押し出しは行わない。
    # 全面押し出しは閉じた立体まで二重壁化し、その継ぎ目が交差エッジとして
    # 誤検出される（縦縞ノイズの原因）。
    target_geo = realize.outputs["Geometry"]

    # 交差線の実効太さ（塗りつぶしチューブの半径用） = 設定値と双方の
    # アウトライン幅の最大値。背面法ハルは元メッシュとの間に構造的な
    # 隙間ができることがある（2026-07-03 ユーザー要望: 元メッシュと
    # アウトライン殻の間を交差線色で塗りつぶす）。塗りつぶしが双方の
    # アウトライン幅を確実に覆うよう、ユーザー設定の交差線幅はあくまで
    # 下限とし、自分・交差対象のアウトライン幅のうち大きい方を実効幅
    # にする。※ 交差位置そのもの（ブーリアンの接触検出）には使わない
    # ——ここに使うと交差判定位置自体が外側へずれ、線が実際の面より
    # 大きくズレた位置に生成される。
    combined_thickness = _add_combined_thickness(nodes, links, gin, (-900, -640))

    # 接触検出: 交差相手を線の太さ（設定値のみ）ぶんだけ法線方向へ
    # 膨らませる。交差線は元の面との交差位置に一本だけ生成する
    # （位置ズレは線の太さの範囲内に収まり、接している相手にも線が出る）。
    inflate_eps = nodes.new("ShaderNodeMath")
    inflate_eps.location = (-640, -700)
    inflate_eps.operation = "MAXIMUM"
    inflate_eps.inputs[1].default_value = 0.0005
    links.new(gin.outputs[_THICKNESS_SOCKET], inflate_eps.inputs[0])

    target_normal = nodes.new("GeometryNodeInputNormal")
    target_normal.location = (-640, -840)

    inflate_offset = nodes.new("ShaderNodeVectorMath")
    inflate_offset.location = (-440, -700)
    inflate_offset.operation = "SCALE"
    links.new(target_normal.outputs["Normal"], inflate_offset.inputs[0])
    links.new(inflate_eps.outputs[0], inflate_offset.inputs["Scale"])

    inflate = nodes.new("GeometryNodeSetPosition")
    inflate.location = (-240, -520)
    links.new(target_geo, inflate.inputs["Geometry"])
    links.new(inflate_offset.outputs["Vector"], inflate.inputs["Offset"])
    target_geo = inflate.outputs["Geometry"]

    # 交差対象同士が重なっていると、結合オペランドの自己交差で
    # DIFFERENCE が空になるため、UNION で清浄な一体メッシュへまとめる。
    # （プロキシ側Solidify化で二重壁が無くなった今は安全に機能する）
    target_union = nodes.new("GeometryNodeMeshBoolean")
    target_union.label = _SHELL_UNION_NODE_LABEL
    target_union.location = (-60, -520)
    target_union.operation = "UNION"
    target_union.solver = "EXACT"
    if "Self Intersection" in target_union.inputs:
        target_union.inputs["Self Intersection"].default_value = True
    links.new(target_geo, target_union.inputs["Mesh 2"])
    target_geo = target_union.outputs["Mesh"]

    radius = _add_shell_radius(nodes, links, combined_thickness, gin, (80, -820))

    # 交差の基準はライン用ソリッド殻ではなく「元の面」
    # （殻基準だと元面側と殻側の2本の交差曲線ができ、二重線になる）。
    # 元面の判定は素材番号の比較ではなく「アウトライン素材の面を除外」で
    # 行う（内部線モディファイアのJOINで素材番号が再マッピングされ、
    # 番号比較だと選択が壊れるため — 2026-07-03 交差線不可視の原因）。
    outline_faces = nodes.new("GeometryNodeMaterialSelection")
    outline_faces.location = (-1060, 200)
    links.new(
        gin.outputs[_OUTLINE_MATERIAL_SOCKET], outline_faces.inputs["Material"]
    )

    line_shell = nodes.new("FunctionNodeBooleanMath")
    line_shell.label = _SHELL_SURFACE_NODE_LABEL
    line_shell.location = (-840, 200)
    line_shell.operation = "NOT"
    links.new(outline_faces.outputs["Selection"], line_shell.inputs[0])

    generated_attr = nodes.new("GeometryNodeInputNamedAttribute")
    generated_attr.location = (-1060, 20)
    generated_attr.data_type = "BOOLEAN"
    generated_attr.inputs["Name"].default_value = intersection_lines.GENERATED_LINE_ATTR

    generated_marked = nodes.new("FunctionNodeBooleanMath")
    generated_marked.location = (-840, 20)
    generated_marked.operation = "AND"
    links.new(generated_attr.outputs["Exists"], generated_marked.inputs[0])
    links.new(generated_attr.outputs["Attribute"], generated_marked.inputs[1])

    not_generated = nodes.new("FunctionNodeBooleanMath")
    not_generated.location = (-620, 20)
    not_generated.operation = "NOT"
    links.new(generated_marked.outputs[0], not_generated.inputs[0])

    shell_and_clean = nodes.new("FunctionNodeBooleanMath")
    shell_and_clean.location = (-380, 120)
    shell_and_clean.operation = "AND"
    links.new(line_shell.outputs[0], shell_and_clean.inputs[0])
    links.new(not_generated.outputs[0], shell_and_clean.inputs[1])

    shell_only = nodes.new("GeometryNodeSeparateGeometry")
    shell_only.location = (-160, 100)
    shell_only.domain = "FACE"
    links.new(gin.outputs[0], shell_only.inputs["Geometry"])
    links.new(shell_and_clean.outputs[0], shell_only.inputs["Selection"])

    active_shell = nodes.new("GeometryNodeSeparateGeometry")
    active_shell.location = (80, 80)
    active_shell.domain = "FACE"
    links.new(shell_only.outputs["Selection"], active_shell.inputs["Geometry"])
    links.new(gin.outputs[_HAS_TARGET_SOCKET], active_shell.inputs["Selection"])

    boolean = nodes.new("GeometryNodeMeshBoolean")
    boolean.label = _SHELL_BOOLEAN_NODE_LABEL
    boolean.location = (320, -120)
    boolean.operation = "DIFFERENCE"
    boolean.solver = "EXACT"
    links.new(active_shell.outputs["Selection"], boolean.inputs["Mesh 1"])
    links.new(target_geo, boolean.inputs["Mesh 2"])

    separate = nodes.new("GeometryNodeSeparateGeometry")
    separate.location = (560, -120)
    separate.domain = "EDGE"
    links.new(boolean.outputs["Mesh"], separate.inputs["Geometry"])
    links.new(boolean.outputs["Intersecting Edges"], separate.inputs["Selection"])

    # ブーリアンの交差エッジは頂点を共有しない細切れ断片になるため、
    # 溶接して連続ループへつなぐ。つながないと「中間頂点の線幅調整」が
    # 断片ごとに適用され、線が毛羽立つ／極細化して見えなくなる。
    weld = nodes.new("GeometryNodeMergeByDistance")
    weld.location = (670, -120)
    weld.inputs["Distance"].default_value = 0.0001
    links.new(separate.outputs["Selection"], weld.inputs["Geometry"])

    m2c = nodes.new("GeometryNodeMeshToCurve")
    m2c.location = (900, -120)
    links.new(weld.outputs["Geometry"], m2c.inputs["Mesh"])

    subdivide_curve = nodes.new("GeometryNodeSubdivideCurve")
    subdivide_curve.label = _SHELL_SUBDIVIDE_NODE_LABEL
    subdivide_curve.location = (1040, -120)
    subdivide_curve.inputs["Cuts"].default_value = 1
    links.new(m2c.outputs["Curve"], subdivide_curve.inputs["Curve"])

    normalized_curve = _add_curve_radius_normalizer(
        nodes, links, subdivide_curve.outputs["Curve"], (1180, -120),
    )
    join = _add_shell_tube_nodes(
        nodes, links, normalized_curve, gin, radius, x_offset=1340,
    )
    links.new(join.outputs[0], gout.inputs[0])
    return tree


def _add_combined_thickness(nodes, links, gin, loc):
    """交差線の実効太さ = ユーザー設定値・自分のアウトライン幅・交差対象の
    アウトライン幅のうち最大のもの.

    ユーザー設定の「線の太さ」はあくまで下限として扱い、どちらかの
    アウトライン幅がそれより太ければそちらを実効値として使う
    （2026-07-03 ユーザー要望: 隙間を交差線色で塗りつぶすには、隙間の
    大きさに直結するアウトライン幅を優先すべき）。背面法ハルの角では
    実際の黒い張り出しが数値上のアウトライン幅をわずかに超えることが
    あるため、アウトライン幅由来の塗りつぶしだけ小さな安全余裕を足す。
    """
    own_cover = nodes.new("ShaderNodeMath")
    own_cover.location = (loc[0] - 220, loc[1] - 80)
    own_cover.operation = "MULTIPLY"
    links.new(gin.outputs[_OWN_OUTLINE_SOCKET], own_cover.inputs[0])
    own_cover.inputs[1].default_value = SHELL_GAP_COVERAGE_FACTOR

    target_cover = nodes.new("ShaderNodeMath")
    target_cover.label = _SHELL_GAP_COVERAGE_NODE_LABEL
    target_cover.location = (loc[0] - 220, loc[1] - 240)
    target_cover.operation = "MULTIPLY"
    links.new(gin.outputs[_TARGET_THICKNESS_SOCKET], target_cover.inputs[0])
    target_cover.inputs[1].default_value = SHELL_GAP_COVERAGE_FACTOR

    own_max = nodes.new("ShaderNodeMath")
    own_max.location = (loc[0], loc[1])
    own_max.operation = "MAXIMUM"
    links.new(gin.outputs[_THICKNESS_SOCKET], own_max.inputs[0])
    links.new(own_cover.outputs[0], own_max.inputs[1])

    combined = nodes.new("ShaderNodeMath")
    combined.label = _SHELL_COMBINED_THICKNESS_NODE_LABEL
    combined.location = (loc[0] + 200, loc[1])
    combined.operation = "MAXIMUM"
    links.new(own_max.outputs[0], combined.inputs[0])
    links.new(target_cover.outputs[0], combined.inputs[1])
    return combined.outputs[0]


def _add_shell_radius(nodes, links, combined_thickness, gin, loc):
    offset_amount = nodes.new("ShaderNodeMath")
    offset_amount.location = (loc[0] - 420, loc[1] + 160)
    offset_amount.operation = "MULTIPLY"
    links.new(combined_thickness, offset_amount.inputs[0])
    links.new(gin.outputs[_OFFSET_SOCKET], offset_amount.inputs[1])

    offset_half = nodes.new("ShaderNodeMath")
    offset_half.location = (loc[0] - 220, loc[1] + 160)
    offset_half.operation = "MULTIPLY"
    offset_half.inputs[1].default_value = 0.5
    links.new(offset_amount.outputs[0], offset_half.inputs[0])

    radius = nodes.new("ShaderNodeMath")
    radius.label = _SHELL_RADIUS_NODE_LABEL
    radius.location = loc
    radius.operation = "ADD"
    links.new(combined_thickness, radius.inputs[0])
    links.new(offset_half.outputs[0], radius.inputs[1])

    radius_min = nodes.new("ShaderNodeMath")
    radius_min.location = (loc[0] + 220, loc[1])
    radius_min.operation = "MAXIMUM"
    radius_min.inputs[1].default_value = 0.0
    links.new(radius.outputs[0], radius_min.inputs[0])
    return radius_min.outputs[0]


def _add_curve_radius_normalizer(nodes, links, curve_output, loc):
    set_radius = nodes.new("GeometryNodeSetCurveRadius")
    set_radius.label = _CURVE_RADIUS_NORMALIZER_LABEL
    set_radius.location = loc
    set_radius.inputs["Radius"].default_value = 1.0
    links.new(curve_output, set_radius.inputs["Curve"])
    return set_radius.outputs["Curve"]


def _add_shell_tube_nodes(nodes, links, curve_output, gin, radius_output, x_offset=0):
    scale = intersection_shell_node_helpers.add_curve_midpoint_width_scale(
        nodes,
        links,
        curve_output,
        gin.outputs[_MIDPOINT_ANGLE_SOCKET],
        gin.outputs[_MIDPOINT_FACTOR_SOCKET],
        (x_offset - 260, -820),
        label=_SHELL_BRANCH_SPLIT_NODE_LABEL + "Angle",
        width_curve_outputs=(
            gin.outputs[_WIDTH_CURVE_25_SOCKET],
            gin.outputs[_WIDTH_CURVE_50_SOCKET],
            gin.outputs[_WIDTH_CURVE_75_SOCKET],
        ),
        jitter_output=gin.outputs[_MIDPOINT_JITTER_SOCKET],
        # Boolean/subdivision contact curves can contain many tiny bends that are
        # not user-visible corners. Treat branch/end points as split points here
        # and avoid making every contact-fragment bend a midpoint-width endpoint.
        angle_split_min_segment_fraction=1.0,
    )

    circle = nodes.new("GeometryNodeCurvePrimitiveCircle")
    circle.label = _SHELL_PROFILE_NODE_LABEL
    circle.location = (x_offset + 0, -550)
    circle.mode = "RADIUS"
    for inp in circle.inputs:
        if inp.name == "Resolution" and inp.enabled:
            inp.default_value = SHELL_TUBE_PROFILE_RESOLUTION
    links.new(radius_output, circle.inputs["Radius"])

    c2m = nodes.new("GeometryNodeCurveToMesh")
    c2m.location = (x_offset + 200, -300)
    links.new(curve_output, c2m.inputs["Curve"])
    links.new(circle.outputs["Curve"], c2m.inputs["Profile Curve"])
    if "Scale" in c2m.inputs:
        links.new(scale, c2m.inputs["Scale"])
    if "Fill Caps" in c2m.inputs:
        c2m.inputs["Fill Caps"].default_value = True

    mark_generated = nodes.new("GeometryNodeStoreNamedAttribute")
    mark_generated.label = intersection_lines._GENERATED_LINE_NODE_LABEL
    mark_generated.location = (x_offset + 320, -470)
    mark_generated.data_type = "BOOLEAN"
    mark_generated.domain = "FACE"
    mark_generated.inputs["Name"].default_value = intersection_lines.GENERATED_LINE_ATTR
    mark_generated.inputs["Value"].default_value = True
    links.new(c2m.outputs["Mesh"], mark_generated.inputs["Geometry"])

    smooth = nodes.new("GeometryNodeSetShadeSmooth")
    smooth.location = (x_offset + 440, -470)
    links.new(mark_generated.outputs["Geometry"], smooth.inputs["Geometry"])

    setmat = nodes.new("GeometryNodeSetMaterial")
    setmat.location = (x_offset + 500, -300)
    links.new(smooth.outputs["Geometry"], setmat.inputs["Geometry"])
    links.new(gin.outputs[_MATERIAL_SOCKET], setmat.inputs["Material"])

    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (x_offset + 800, 0)
    links.new(gin.outputs["Geometry"], join.inputs["Geometry"])
    links.new(setmat.outputs["Geometry"], join.inputs["Geometry"])
    return join


def _find_interface_socket(tree: bpy.types.NodeTree, name: str):
    for item in tree.interface.items_tree:
        if getattr(item, "name", None) == name and getattr(item, "in_out", None) == "INPUT":
            return item
    return None


def _find_socket_id(tree: bpy.types.NodeTree, name: str) -> str | None:
    item = _find_interface_socket(tree, name)
    return getattr(item, "identifier", None) if item is not None else None


def _set_modifier_input_if_changed(
    mod: bpy.types.Modifier,
    sid: str | None,
    value,
    *,
    epsilon: float = 1.0e-9,
) -> bool:
    if sid is None:
        return False
    try:
        current = mod[sid]
    except (KeyError, TypeError):
        current = None
    if isinstance(value, float):
        try:
            if abs(float(current) - value) <= epsilon:
                return False
        except (TypeError, ValueError):
            pass
    elif current == value:
        return False
    mod[sid] = value
    return True


def _tree_uses_generated_mark(tree: bpy.types.NodeTree) -> bool:
    return any(
        getattr(node, "label", "") == intersection_lines._GENERATED_LINE_NODE_LABEL
        for node in tree.nodes
    )


def _tree_has_current_profile_resolution(tree: bpy.types.NodeTree) -> bool:
    profile_nodes = [
        node
        for node in tree.nodes
        if (
            node.bl_idname == "GeometryNodeCurvePrimitiveCircle"
            and getattr(node, "label", "") == _SHELL_PROFILE_NODE_LABEL
        )
    ]
    if not profile_nodes:
        return False
    for node in profile_nodes:
        resolution = next(
            (inp.default_value for inp in node.inputs if inp.name == "Resolution"),
            None,
        )
        if resolution is None or int(resolution) < SHELL_TUBE_PROFILE_RESOLUTION:
            return False
    return True


def _get_or_create_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.get(SHELL_TREE_NAME)
    if tree is not None:
        ok = (
            _find_socket_id(tree, _THICKNESS_SOCKET) is not None
            and _find_socket_id(tree, _OFFSET_SOCKET) is not None
            and _find_socket_id(tree, _MATERIAL_SOCKET) is not None
            and _find_socket_id(tree, _TARGET_COLLECTION_SOCKET) is not None
            and _find_socket_id(tree, _TARGET_THICKNESS_SOCKET) is not None
            and _find_socket_id(tree, _OWN_OUTLINE_SOCKET) is not None
            and _find_socket_id(tree, _OUTLINE_MATERIAL_SOCKET) is not None
            and any(
                node.bl_idname == "GeometryNodeMaterialSelection"
                for node in tree.nodes
            )
            and _find_socket_id(tree, _LINE_MATERIAL_INDEX_SOCKET) is not None
            and _find_socket_id(tree, _HAS_TARGET_SOCKET) is not None
            and _find_socket_id(tree, _MIDPOINT_FACTOR_SOCKET) is not None
            and _find_socket_id(tree, _MIDPOINT_JITTER_SOCKET) is not None
            and _find_socket_id(tree, _MIDPOINT_ANGLE_SOCKET) is not None
            and _find_socket_id(tree, _WIDTH_CURVE_25_SOCKET) is not None
            and _find_socket_id(tree, _WIDTH_CURVE_50_SOCKET) is not None
            and _find_socket_id(tree, _WIDTH_CURVE_75_SOCKET) is not None
            and any(
                node.bl_idname == "GeometryNodeMeshBoolean"
                and getattr(node, "label", "") == _SHELL_BOOLEAN_NODE_LABEL
                for node in tree.nodes
            )
            and any(
                getattr(node, "label", "") == _SHELL_RADIUS_NODE_LABEL
                for node in tree.nodes
            )
            and any(
                getattr(node, "label", "") == _CURVE_RADIUS_NORMALIZER_LABEL
                for node in tree.nodes
            )
            and any(
                getattr(node, "label", "") == _SHELL_SURFACE_NODE_LABEL
                for node in tree.nodes
            )
            # 過去世代のツリー（全面押し出し入り）を排除し、
            # 現行構成（対象UNION清浄化あり）を要求する
            and any(
                getattr(node, "label", "") == _SHELL_UNION_NODE_LABEL
                for node in tree.nodes
            )
            and not any(
                node.bl_idname == "GeometryNodeExtrudeMesh" for node in tree.nodes
            )
            and any(
                node.bl_idname == "GeometryNodeMergeByDistance"
                for node in tree.nodes
            )
            and any(
                getattr(node, "label", "") == _SHELL_SUBDIVIDE_NODE_LABEL
                for node in tree.nodes
            )
            and _tree_uses_generated_mark(tree)
            # 過去世代（実効太さがユーザー設定のみ）を排除し、
            # アウトライン幅を加味した現行構成を要求する
            # （2026-07-03 隙間塗りつぶし要望）
            and any(
                getattr(node, "label", "") == _SHELL_COMBINED_THICKNESS_NODE_LABEL
                for node in tree.nodes
            )
            # v0.3.80: アウトライン幅ベースで太くなった交差線では、
            # 4角断面のキャップが黒いくさび状の切れ込みとして目立つ。
            # 保存済みファイル内の旧ツリーを必ず再構築して、丸い断面に更新する。
            and _tree_has_current_profile_resolution(tree)
            # v0.3.81: アウトライン幅ベースの塗りつぶしに小さな安全余裕を
            # 追加したため、v0.3.80ツリーも再構築する。
            and any(
                getattr(node, "label", "") == _SHELL_GAP_COVERAGE_NODE_LABEL
                for node in tree.nodes
            )
        )
        if ok:
            return tree
        bpy.data.node_groups.remove(tree)
    return _create_node_tree()


def _ensure_surface_slot(obj: bpy.types.Object) -> None:
    if obj.data.materials:
        return
    surface = bpy.data.materials.new(name=f"{obj.name}_Surface")
    surface.use_nodes = True
    obj.data.materials.append(surface)


def _ensure_material_slot(
    obj: bpy.types.Object,
    material: bpy.types.Material | None,
) -> None:
    _ensure_surface_slot(obj)
    if material is None:
        return
    if not any(slot_mat == material for slot_mat in obj.data.materials):
        obj.data.materials.append(material)


def _position_modifier(obj: bpy.types.Object, mod: bpy.types.Modifier) -> None:
    modifier_stack.reorder_line_modifiers(obj)


def update_modifier_parameters(
    mod: bpy.types.Modifier,
    thickness: float | None = None,
    offset: float | None = None,
    material: bpy.types.Material | None = None,
) -> None:
    tree = getattr(mod, "node_group", None)
    obj = getattr(mod, "id_data", None)
    if tree is None or getattr(obj, "type", None) != "MESH":
        return
    if material is not None:
        _ensure_material_slot(obj, material)
    sid_thickness = _find_socket_id(tree, _THICKNESS_SOCKET)
    if thickness is not None:
        _set_modifier_input_if_changed(mod, sid_thickness, thickness)
    sid_offset = _find_socket_id(tree, _OFFSET_SOCKET)
    if offset is not None:
        _set_modifier_input_if_changed(mod, sid_offset, offset)
    sid_material = _find_socket_id(tree, _MATERIAL_SOCKET)
    if material is not None:
        _set_modifier_input_if_changed(mod, sid_material, material)
    sid_target_thickness = _find_socket_id(tree, _TARGET_THICKNESS_SOCKET)
    _set_modifier_input_if_changed(
        mod,
        sid_target_thickness,
        _max_target_thickness(obj, modifier_targets(mod)),
    )
    _set_own_outline_width(mod)
    sid_has_target = _find_socket_id(tree, _HAS_TARGET_SOCKET)
    _set_modifier_input_if_changed(mod, sid_has_target, bool(modifier_targets(mod)))
    _set_width_control_parameters(mod)
    _set_line_material_index(mod)


def _set_width_control_parameters(mod: bpy.types.Modifier) -> None:
    tree = getattr(mod, "node_group", None)
    obj = getattr(mod, "id_data", None)
    settings = getattr(obj, "bmanga_line_settings", None)
    if tree is None or settings is None:
        return
    values = {
        _MIDPOINT_FACTOR_SOCKET: float(
            getattr(settings, "intersection_edge_smooth_factor", 0.0)
        ),
        _MIDPOINT_JITTER_SOCKET: float(
            getattr(settings, "intersection_edge_midpoint_jitter_percent", 0.0)
        ),
        _MIDPOINT_ANGLE_SOCKET: float(
            getattr(settings, "intersection_edge_midpoint_angle", 1.7453292520)
        ),
        _WIDTH_CURVE_25_SOCKET: float(
            getattr(settings, "intersection_edge_width_curve_25", 0.25)
        ),
        _WIDTH_CURVE_50_SOCKET: float(
            getattr(settings, "intersection_edge_width_curve_50", 0.50)
        ),
        _WIDTH_CURVE_75_SOCKET: float(
            getattr(settings, "intersection_edge_width_curve_75", 0.75)
        ),
    }
    for name, value in values.items():
        sid = _find_socket_id(tree, name)
        _set_modifier_input_if_changed(mod, sid, value)


def _collection_name(obj: bpy.types.Object) -> str:
    saved = str(obj.get(_TARGET_COLLECTION_PROP, "") or "")
    if saved:
        return saved
    raw = obj.name_full or obj.name or "Object"
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_") or "Object"
    return f"{_TARGET_COLLECTION_PREFIX}_{cleaned[:48]}_{obj.as_pointer():x}"


def _get_or_create_target_collection(obj: bpy.types.Object) -> bpy.types.Collection:
    name = _collection_name(obj)
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    obj[_TARGET_COLLECTION_PROP] = collection.name
    return collection


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


def _target_candidates(
    obj: bpy.types.Object,
    scene: bpy.types.Scene | None,
) -> list[bpy.types.Object]:
    from . import camera_comp, plane_filter

    targets: list[bpy.types.Object] = []
    for src_scene in _iter_source_scenes(obj, scene):
        for candidate in src_scene.objects:
            if candidate == obj or candidate.type != "MESH" or candidate.data is None:
                continue
            if bool(candidate.get(_PROXY_SOURCE_PROP, "")):
                continue
            if not getattr(candidate.data, "polygons", None):
                continue
            if (
                candidate.modifiers.get(MODIFIER_NAME) is None
                and candidate.modifiers.get(SHEET_OUTLINE_MODIFIER_NAME) is None
            ):
                continue
            settings = getattr(candidate, "bmanga_line_settings", None)
            if plane_filter.should_exclude_generated_lines(candidate, settings):
                continue
            if not camera_comp.intersection_line_creation_in_range(
                candidate, src_scene, settings,
            ):
                continue
            if candidate not in targets:
                targets.append(candidate)
    targets.sort(key=lambda item: item.name_full)
    return targets


def _target_list(
    obj: bpy.types.Object,
    scene: bpy.types.Scene | None,
    thickness: float,
) -> list[bpy.types.Object]:
    targets: list[bpy.types.Object] = []
    for candidate in _target_candidates(obj, scene):
        if not intersection_lines._source_owns_intersection_pair(obj, candidate, scene):
            continue
        margin = intersection_lines._intersection_margin(obj, candidate, thickness)
        if intersection_lines._bounds_overlap(obj, candidate, margin):
            targets.append(candidate)
    return targets


def _remove_mirror_proxy(obj: bpy.types.Object, target: bpy.types.Object) -> None:
    """相手側が同じペアを持っていたら外す（持ち主の重複による二重線の掃除）."""
    name = str(target.get(_TARGET_COLLECTION_PROP, "") or "")
    if not name:
        return
    collection = bpy.data.collections.get(name)
    if collection is None:
        return
    mirror = bpy.data.objects.get(_proxy_name(target, obj))
    if mirror is None:
        return
    if mirror.name in collection.objects:
        collection.objects.unlink(mirror)
    if not mirror.users_collection:
        bpy.data.objects.remove(mirror)


def _sync_target_collection(
    obj: bpy.types.Object,
    scene: bpy.types.Scene | None,
    thickness: float,
) -> tuple[bpy.types.Collection, list[bpy.types.Object]]:
    collection = _get_or_create_target_collection(obj)
    targets = _target_list(obj, scene, thickness)
    proxies = [_get_or_create_proxy(obj, target) for target in targets]
    proxy_set = set(proxies)
    for item in list(collection.objects):
        if item not in proxy_set:
            collection.objects.unlink(item)
            if bool(item.get(_PROXY_SOURCE_PROP, "")) and not item.users_collection:
                bpy.data.objects.remove(item)
    for item in proxies:
        if item.name not in collection.objects:
            collection.objects.link(item)
    for target in targets:
        _remove_mirror_proxy(obj, target)
    return collection, targets


def _proxy_name(source: bpy.types.Object, target: bpy.types.Object) -> str:
    raw = f"{source.name_full}_{target.name_full}"
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_") or "Object"
    return f"{_PROXY_PREFIX}_{cleaned[:48]}_{source.as_pointer():x}_{target.as_pointer():x}"


_PROXY_SOLIDIFY_NAME = "BML_ProxySolidify"
_PROXY_SUBSURF_NAME = "BML_ProxyMidpointSubsurf"
_PROXY_BOUNDARY_SIGNATURE_PROP = "bml_proxy_boundary_signature"
_PROXY_HAS_BOUNDARY_PROP = "bml_proxy_has_boundary"


def _mesh_has_boundary(mesh: bpy.types.Mesh) -> bool:
    counts: dict[tuple[int, int], int] = {}
    for poly in mesh.polygons:
        for key in poly.edge_keys:
            counts[key] = counts.get(key, 0) + 1
    return any(value == 1 for value in counts.values())


def _proxy_needs_thickness(proxy: bpy.types.Object, mesh: bpy.types.Mesh) -> bool:
    """板ポリ等の非多様体メッシュか（結果はプロキシへキャッシュ）."""
    signature = f"{mesh.as_pointer()}:{len(mesh.polygons)}:{len(mesh.edges)}"
    if proxy.get(_PROXY_BOUNDARY_SIGNATURE_PROP) == signature:
        return bool(proxy.get(_PROXY_HAS_BOUNDARY_PROP, False))
    result = _mesh_has_boundary(mesh)
    try:
        proxy[_PROXY_BOUNDARY_SIGNATURE_PROP] = signature
        proxy[_PROXY_HAS_BOUNDARY_PROP] = bool(result)
    except (AttributeError, TypeError, RuntimeError):
        pass
    return result


def _sync_proxy_thickness(proxy: bpy.types.Object) -> None:
    """非多様体ターゲットのプロキシへ厚み付けの Solidify を持たせる.

    GNツリー内の全面押し出しは閉じた立体まで二重壁化してしまうため、
    厚みが必要なプロキシだけ実モディファイアで閉多様体にする。
    """
    mesh = proxy.data
    needs = mesh is not None and _proxy_needs_thickness(proxy, mesh)
    mod = proxy.modifiers.get(_PROXY_SOLIDIFY_NAME)
    if needs:
        if mod is None:
            mod = proxy.modifiers.new(name=_PROXY_SOLIDIFY_NAME, type="SOLIDIFY")
        if mod.thickness != 0.002:
            mod.thickness = 0.002
        if mod.offset != 0.0:
            mod.offset = 0.0
        if not mod.use_rim:
            mod.use_rim = True
    elif mod is not None:
        proxy.modifiers.remove(mod)


def _sync_proxy_subdivision(
    proxy: bpy.types.Object,
    target: bpy.types.Object,
) -> None:
    from . import subdivision_lod

    source_mod = subdivision_lod.auto_subsurf_modifier(target)
    proxy_mod = proxy.modifiers.get(_PROXY_SUBSURF_NAME)
    if source_mod is None:
        if proxy_mod is not None:
            proxy.modifiers.remove(proxy_mod)
        return
    if proxy_mod is None:
        proxy_mod = proxy.modifiers.new(_PROXY_SUBSURF_NAME, "SUBSURF")
    if hasattr(proxy_mod, "subdivision_type") and hasattr(source_mod, "subdivision_type"):
        if proxy_mod.subdivision_type != source_mod.subdivision_type:
            proxy_mod.subdivision_type = source_mod.subdivision_type
    levels = int(getattr(source_mod, "levels", 0))
    render_levels = int(getattr(source_mod, "render_levels", 0))
    show_viewport = bool(getattr(source_mod, "show_viewport", True))
    show_render = bool(getattr(source_mod, "show_render", True))
    if proxy_mod.levels != levels:
        proxy_mod.levels = levels
    if proxy_mod.render_levels != render_levels:
        proxy_mod.render_levels = render_levels
    if proxy_mod.show_viewport != show_viewport:
        proxy_mod.show_viewport = show_viewport
    if proxy_mod.show_render != show_render:
        proxy_mod.show_render = show_render


def _get_or_create_proxy(
    source: bpy.types.Object,
    target: bpy.types.Object,
) -> bpy.types.Object:
    name = _proxy_name(source, target)
    proxy = bpy.data.objects.get(name)
    if proxy is None:
        proxy = bpy.data.objects.new(name=name, object_data=target.data)
    elif proxy.data is not target.data:
        proxy.data = target.data
    expected_matrix = source.matrix_world.inverted() @ target.matrix_world
    if not _matrices_close(proxy.matrix_world, expected_matrix):
        proxy.matrix_world = expected_matrix
    if not proxy.hide_viewport:
        proxy.hide_viewport = True
    if not proxy.hide_render:
        proxy.hide_render = True
    if str(proxy.get(_PROXY_SOURCE_PROP, "") or "") != target.name_full:
        proxy[_PROXY_SOURCE_PROP] = target.name_full
    _sync_proxy_thickness(proxy)
    _sync_proxy_subdivision(proxy, target)
    return proxy


def sync_proxy_subdivision_for_target(target: bpy.types.Object) -> int:
    if target.type != "MESH":
        return 0
    changed = 0
    for proxy in bpy.data.objects:
        if not bool(proxy.get(_PROXY_SOURCE_PROP, "")):
            continue
        if str(proxy.get(_PROXY_SOURCE_PROP, "") or "") != target.name_full:
            continue
        before = [
            (
                mod.name,
                mod.type,
                int(getattr(mod, "levels", -1)) if mod.type == "SUBSURF" else -1,
                int(getattr(mod, "render_levels", -1)) if mod.type == "SUBSURF" else -1,
            )
            for mod in proxy.modifiers
        ]
        _sync_proxy_subdivision(proxy, target)
        after = [
            (
                mod.name,
                mod.type,
                int(getattr(mod, "levels", -1)) if mod.type == "SUBSURF" else -1,
                int(getattr(mod, "render_levels", -1)) if mod.type == "SUBSURF" else -1,
            )
            for mod in proxy.modifiers
        ]
        if before != after:
            changed += 1
    return changed


def _outline_world_width(target: bpy.types.Object | None) -> float:
    if target is None or target.type != "MESH":
        return 0.0
    mod = target.modifiers.get(MODIFIER_NAME)
    if mod is None:
        return outline_setup.sheet_outline_world_width(target)
    return scale_utils.world_width_from_modifier(target, mod.thickness)


def _target_outline_thickness(
    source: bpy.types.Object | None,
    target: bpy.types.Object | None,
) -> float:
    world_width = _outline_world_width(target)
    if source is None or source.type != "MESH":
        return world_width
    return scale_utils.modifier_thickness_for_world_width(source, world_width)


def _max_target_thickness(
    source: bpy.types.Object | None,
    targets: list[bpy.types.Object],
) -> float:
    if not targets:
        return 0.0
    return max(_target_outline_thickness(source, target) for target in targets)


def _set_own_outline_width(mod: bpy.types.Modifier) -> None:
    """二重線の塗りつぶし幅の基準として自分のアウトライン幅を渡す."""
    tree = getattr(mod, "node_group", None)
    obj = getattr(mod, "id_data", None)
    if tree is None or getattr(obj, "type", None) != "MESH":
        return
    sid = _find_socket_id(tree, _OWN_OUTLINE_SOCKET)
    if sid is None:
        return
    outline = obj.modifiers.get(MODIFIER_NAME)
    if outline is not None:
        value = abs(float(outline.thickness))
    else:
        value = scale_utils.modifier_thickness_for_world_width(
            obj,
            outline_setup.sheet_outline_world_width(obj),
        )
    _set_modifier_input_if_changed(mod, sid, value)
    _set_outline_material_socket(mod)


def _set_outline_material_socket(mod: bpy.types.Modifier) -> None:
    """元面判定用に自分のアウトライン素材をツリーへ渡す."""
    tree = getattr(mod, "node_group", None)
    obj = getattr(mod, "id_data", None)
    if tree is None or getattr(obj, "type", None) != "MESH":
        return
    sid = _find_socket_id(tree, _OUTLINE_MATERIAL_SOCKET)
    if sid is None:
        return
    material = outline_setup.get_outline_material(obj)
    if material is not None:
        _set_modifier_input_if_changed(mod, sid, material)


def _modifier_target_collection(mod: bpy.types.Modifier) -> bpy.types.Collection | None:
    tree = getattr(mod, "node_group", None)
    sid = _find_socket_id(tree, _TARGET_COLLECTION_SOCKET) if tree is not None else None
    if sid is None:
        return None
    try:
        collection = mod[sid]
    except (KeyError, TypeError):
        return None
    return collection if isinstance(collection, bpy.types.Collection) else None


def collection_real_targets(collection: bpy.types.Collection | None) -> list[bpy.types.Object]:
    if collection is None:
        return []
    targets: list[bpy.types.Object] = []
    for item in collection.objects:
        source_name = str(item.get(_PROXY_SOURCE_PROP, "") or "")
        source = bpy.data.objects.get(source_name)
        if getattr(source, "type", None) == "MESH" and source not in targets:
            targets.append(source)
    return targets


def _set_target_collection_parameters(
    mod: bpy.types.Modifier,
    collection: bpy.types.Collection,
    targets: list[bpy.types.Object],
) -> None:
    tree = getattr(mod, "node_group", None)
    obj = getattr(mod, "id_data", None)
    if tree is None or getattr(obj, "type", None) != "MESH":
        return
    sid_collection = _find_socket_id(tree, _TARGET_COLLECTION_SOCKET)
    _set_modifier_input_if_changed(mod, sid_collection, collection)
    sid_target_thickness = _find_socket_id(tree, _TARGET_THICKNESS_SOCKET)
    _set_modifier_input_if_changed(
        mod,
        sid_target_thickness,
        _max_target_thickness(obj, targets),
    )
    _set_own_outline_width(mod)
    sid_has_target = _find_socket_id(tree, _HAS_TARGET_SOCKET)
    _set_modifier_input_if_changed(mod, sid_has_target, bool(targets))
    _set_line_material_index(mod)


def modifier_targets(mod: bpy.types.Modifier) -> list[bpy.types.Object]:
    return collection_real_targets(_modifier_target_collection(mod))


def _set_line_material_index(mod: bpy.types.Modifier) -> None:
    tree = getattr(mod, "node_group", None)
    obj = getattr(mod, "id_data", None)
    if tree is None or getattr(obj, "type", None) != "MESH":
        return
    sid_line_material = _find_socket_id(tree, _LINE_MATERIAL_INDEX_SOCKET)
    _set_modifier_input_if_changed(
        mod,
        sid_line_material,
        outline_setup.first_line_material_slot(obj),
    )


def update_target_width_reference(mod: bpy.types.Modifier) -> bool:
    targets = modifier_targets(mod)
    if not targets:
        return False
    collection = _modifier_target_collection(mod)
    if collection is None:
        return False
    _set_target_collection_parameters(mod, collection, targets)
    return True


def refresh_target_collection(
    obj: bpy.types.Object,
    scene: bpy.types.Scene | None,
) -> bool:
    mod = obj.modifiers.get(SHELL_MODIFIER_NAME)
    if mod is None:
        return False
    settings = getattr(obj, "bmanga_line_settings", None)
    if settings is None or not getattr(settings, "intersection_enabled", False):
        obj.modifiers.remove(mod)
        return False
    return apply_intersection_shell(
        obj,
        scale_utils.modifier_thickness_for_world_width(
            obj,
            float(getattr(settings, "intersection_thickness", 0.0003)),
        ),
        float(getattr(settings, "intersection_line_offset", 1.0)),
        outline_setup.get_line_material(obj, "intersection"),
        scene,
    )


def cleanup_orphan_proxies() -> int:
    """持ち主のいない交差対象コレクションとプロキシを掃除する.

    オブジェクトを Blender 標準の削除で消すと、その交差対象コレクションと
    プロキシがファイル内に残り続けるため、シーン更新時に回収する。
    """
    removed = 0
    owners = {
        str(obj.get(_TARGET_COLLECTION_PROP, "") or "")
        for obj in bpy.data.objects
    }
    for collection in list(bpy.data.collections):
        if not collection.name.startswith(_TARGET_COLLECTION_PREFIX):
            continue
        if collection.name in owners:
            continue
        for item in list(collection.objects):
            collection.objects.unlink(item)
            if bool(item.get(_PROXY_SOURCE_PROP, "")) and not item.users_collection:
                bpy.data.objects.remove(item)
                removed += 1
        bpy.data.collections.remove(collection)
    for obj in list(bpy.data.objects):
        if bool(obj.get(_PROXY_SOURCE_PROP, "")) and not obj.users_collection:
            bpy.data.objects.remove(obj)
            removed += 1
    return removed


def cleanup_target_collection(obj: bpy.types.Object) -> None:
    name = str(obj.get(_TARGET_COLLECTION_PROP, "") or "")
    if not name:
        return
    collection = bpy.data.collections.get(name)
    if collection is not None:
        for item in list(collection.objects):
            collection.objects.unlink(item)
            if bool(item.get(_PROXY_SOURCE_PROP, "")) and not item.users_collection:
                bpy.data.objects.remove(item)
        bpy.data.collections.remove(collection)
    try:
        del obj[_TARGET_COLLECTION_PROP]
    except (KeyError, TypeError):
        pass


def apply_intersection_shell(
    obj: bpy.types.Object,
    thickness: float,
    offset: float,
    material: bpy.types.Material | None,
    scene: bpy.types.Scene | None = None,
) -> bool:
    if obj.type != "MESH" or obj.data is None or not obj.data.polygons:
        return False
    _ensure_material_slot(obj, material)
    collection, targets = _sync_target_collection(obj, scene, thickness)
    if not targets:
        mod = obj.modifiers.get(SHELL_MODIFIER_NAME)
        if mod is not None:
            obj.modifiers.remove(mod)
        cleanup_target_collection(obj)
        return True
    tree = _get_or_create_tree()
    mod = obj.modifiers.get(SHELL_MODIFIER_NAME)
    if mod is None:
        mod = obj.modifiers.new(name=SHELL_MODIFIER_NAME, type="NODES")
    mod.node_group = tree
    update_modifier_parameters(mod, thickness, offset, material)
    _set_target_collection_parameters(mod, collection, targets)

    if not mod.show_viewport:
        mod.show_viewport = True
    if not mod.show_render:
        mod.show_render = True
    _position_modifier(obj, mod)
    return True


# ------------------------------------------------------------------
# オブジェクト移動への追従
# プロキシの相対行列は作成時のスナップショットのため、移動を
# depsgraph ハンドラで検知して即時更新し、ペア構成（交差の有無）は
# 移動が止まってから再同期する。
# ------------------------------------------------------------------

# 行列は float32 で保存されるため、それより粗い許容誤差で比較して
# 「書き込み→再比較で不一致→再書き込み」の更新ループを避ける。
_MATRIX_EPS = 1.0e-5
_RESYNC_IDLE_SECONDS = 0.35
_MATRIX_SYNC_INTERVAL = 0.1

_pending_matrix_names: set[str] = set()
_pending_resync_names: set[str] = set()
_resync_timer_active = False
_syncing_transforms = False
_last_move_at = 0.0


def _matrices_close(a, b) -> bool:
    for i in range(4):
        for j in range(4):
            if abs(a[i][j] - b[i][j]) > _MATRIX_EPS:
                return False
    return True


def _shell_sources(scene) -> list[bpy.types.Object]:
    return [
        obj for obj in scene.objects
        if obj.type == "MESH"
        and obj.modifiers.get(SHELL_MODIFIER_NAME) is not None
    ]


def _sync_proxy_matrices(scene, moved_names: set[str]) -> set[str]:
    """移動したオブジェクトに関係するプロキシ行列を追従させる.

    ペア再同期が必要なソース名の集合を返す。
    """
    involved: set[str] = set()
    for source in _shell_sources(scene):
        collection_name = str(source.get(_TARGET_COLLECTION_PROP, "") or "")
        collection = (
            bpy.data.collections.get(collection_name) if collection_name else None
        )
        if collection is None:
            continue
        source_moved = source.name_full in moved_names
        source_inv = None
        for proxy in collection.objects:
            target_name = str(proxy.get(_PROXY_SOURCE_PROP, "") or "")
            target = bpy.data.objects.get(target_name)
            if target is None:
                continue
            if not source_moved and target.name_full not in moved_names:
                continue
            involved.add(source.name_full)
            if source_inv is None:
                source_inv = source.matrix_world.inverted()
            expected = source_inv @ target.matrix_world
            if _matrices_close(proxy.matrix_world, expected):
                continue
            proxy.matrix_world = expected
    return involved


def _collect_moved_mesh_names(depsgraph) -> set[str]:
    moved: set[str] = set()
    for update in getattr(depsgraph, "updates", ()):
        if not getattr(update, "is_updated_transform", False):
            continue
        id_data = getattr(update, "id", None)
        if not isinstance(id_data, bpy.types.Object):
            continue
        if getattr(id_data, "type", None) != "MESH":
            continue
        if bool(id_data.get(_PROXY_SOURCE_PROP, "")):
            continue
        moved.add(id_data.name_full)
    return moved


@bpy.app.handlers.persistent
def _on_depsgraph_update(scene, depsgraph=None):
    """移動したオブジェクト名を記録するだけの軽量ハンドラ.

    depsgraph 評価中の bpy データ書き込みはクラッシュ要因になるため、
    ここでは一切書き込まず、実作業はタイマー（メインループ）へ委ねる。
    """
    global _resync_timer_active, _last_move_at
    if _syncing_transforms or depsgraph is None or scene is None:
        return
    moved = _collect_moved_mesh_names(depsgraph)
    if not moved:
        return
    import time

    _last_move_at = time.monotonic()
    _pending_matrix_names.update(moved)
    _pending_resync_names.update(moved)
    if not _resync_timer_active:
        _resync_timer_active = True
        bpy.app.timers.register(
            _run_follow_timer, first_interval=_MATRIX_SYNC_INTERVAL
        )


def _run_follow_timer():
    """プロキシ行列の追従（即時）とペア再同期（移動停止後）を行う."""
    global _resync_timer_active, _syncing_transforms
    import time

    scene = getattr(bpy.context, "scene", None)
    if scene is None:
        _resync_timer_active = False
        _pending_matrix_names.clear()
        _pending_resync_names.clear()
        return None
    if _pending_matrix_names:
        moved = set(_pending_matrix_names)
        _pending_matrix_names.clear()
        _syncing_transforms = True
        try:
            involved = _sync_proxy_matrices(scene, moved)
        finally:
            _syncing_transforms = False
        for name in moved:
            if name in involved:
                continue
            obj = bpy.data.objects.get(name)
            settings = getattr(obj, "bmanga_line_settings", None) if obj else None
            if settings is None or not getattr(
                settings, "intersection_enabled", False
            ):
                _pending_resync_names.discard(name)
    if time.monotonic() - _last_move_at < _RESYNC_IDLE_SECONDS:
        return _MATRIX_SYNC_INTERVAL
    names = set(_pending_resync_names)
    _pending_resync_names.clear()
    _resync_timer_active = False
    if names:
        _do_pair_resync(scene, names)
    return None


def _do_pair_resync(scene, names: set[str]) -> None:
    """移動が落ち着いた後にペア構成（交差の有無）を再同期する."""
    global _syncing_transforms
    from . import camera_comp, intersection_lines, plane_filter

    moved = [obj for obj in (bpy.data.objects.get(n) for n in names) if obj]
    affected: dict[str, bpy.types.Object] = {}
    for obj in moved:
        if obj.type == "MESH":
            affected[obj.name_full] = obj
    for obj in scene.objects:
        if obj.type != "MESH" or obj.name_full in affected:
            continue
        settings = getattr(obj, "bmanga_line_settings", None)
        has_shell = obj.modifiers.get(SHELL_MODIFIER_NAME) is not None
        enabled = settings is not None and getattr(
            settings, "intersection_enabled", False
        )
        if not (has_shell or enabled):
            continue
        margin = 1.0
        if any(
            intersection_lines._bounds_overlap(obj, item, margin) for item in moved
        ):
            affected[obj.name_full] = obj
    if not affected:
        return None
    _syncing_transforms = True
    try:
        refreshed = []
        for obj in affected.values():
            if intersection_lines._refresh_source_intersections(
                obj, scene, outline_setup, plane_filter
            ):
                refreshed.append(obj)
        if refreshed:
            camera_comp.refresh_objects(
                bpy.context,
                refreshed,
                width_targets=("intersection",),
            )
    finally:
        _syncing_transforms = False
    return None


def register() -> None:
    if _on_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)


def unregister() -> None:
    global _resync_timer_active
    if _on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    if bpy.app.timers.is_registered(_run_follow_timer):
        bpy.app.timers.unregister(_run_follow_timer)
    _resync_timer_active = False
    _pending_matrix_names.clear()
    _pending_resync_names.clear()
