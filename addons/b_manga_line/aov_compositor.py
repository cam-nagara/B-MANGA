"""B-MANGA Line AOV compositor helpers."""

from __future__ import annotations

from pathlib import Path

import bpy

from .core import (
    AOV_COMPOSITE_NAME,
    AOV_INNER_LINES_NAME,
    AOV_INTERSECTION_LINES_NAME,
    AOV_OBJECT_MASK_NAME,
    AOV_OUTLINE_RAW_NAME,
    AOV_SELECTION_LINES_NAME,
)


NODE_PREFIX = "BML_LineAOVComposite"
TREE_NAME = "B-MANGA Line AOV Composite"
GROUP_TREE_NAME = "BML_LineAOVCompositeGroup"

# ------------------------------------------------------------------
# バンプ線（Phase A1, 2026-07-09）
# ------------------------------------------------------------------
# 「線画合成ノードを作成」機能（手動・NODE_PREFIX="BML_LineAOVComposite"）とは
# 独立したライフサイクルで、レンダー画像(F12結果/最終コンポジット)への
# アルファオーバー自動合成を管理する。オン/オフで自分のノードだけを
# 追加・撤去し、ユーザーの既存コンポジットノードには一切触れない。
BUMP_COMPOSITE_NODE_PREFIX = "BML_BumpLineComposite"
BUMP_GROUP_OUTPUT_NAME = f"{BUMP_COMPOSITE_NODE_PREFIX}_GroupOutput"
BUMP_OUTPUT_SOCKET_NAME = f"{BUMP_COMPOSITE_NODE_PREFIX}_Image"

# mm→px 太らせ幅キャリブレーション（2026-07-09実測、_verify/2026-07-09_bml_bump_line_probe/
# と追加検証 _verify/2026-07-09_bml_bump_line_calibration/ で確定）。
#
# 単純に dpi/25.4*mm の目標px値をそのまま Dilate の Size に使うと、Sobel+しきい値
# 検出が既に持つ「生のエッジ幅」の分だけ太りすぎる（実測で目標の2.5〜3.4倍）。
# 単一フランク（法線が1回だけ変化する段差）のテストで実測した「Dilate=0時の
# 生エッジ幅」は Eevee=2px、Cycles=4px（エンジンによってサンプリング特性が
# 異なるため差が出る）。この値を Size の算出時に差し引くことで、
# 最終幅 ≈ 生エッジ幅 + 2*round((目標px-生エッジ幅)/2) ≈ 目標px（±1px程度）
# になることを実測確認済み（0.2/0.3/0.5mm @600dpiいずれも誤差1px未満）。
_BUMP_RAW_EDGE_PX_EEVEE = 2.0
_BUMP_RAW_EDGE_PX_CYCLES = 4.0

# 感度(0-1, UI表示) -> Sobel長さのしきい値(絶対値)。感度0.65 (既定値) で
# しきい値≈0.135となり、Phase A0 検証3で機能確認済みの 0.15 に近い水準。
_BUMP_THRESHOLD_MAX = 0.35  # 感度0.0（最も鈍感）
_BUMP_THRESHOLD_MIN = 0.02  # 感度1.0（最も敏感）

# オブジェクトマスク(Cryptomatte)のErode量に足す安全マージン。Dilateで太らせる
# 分だけシルエット側へ滲み出さないよう、Erodeは常にDilateより少し大きくする
# （Phase A0検証3で有効性実証済みの考え方を踏襲）。
BUMP_MASK_ERODE_MARGIN_PX = 2


def bump_edge_threshold(sensitivity: float) -> float:
    """UIの感度(0-1)をSobel長さのしきい値(絶対値)へ変換する."""
    s = max(0.0, min(1.0, float(sensitivity)))
    return _BUMP_THRESHOLD_MAX + (_BUMP_THRESHOLD_MIN - _BUMP_THRESHOLD_MAX) * s


def bump_raw_edge_px(scene: bpy.types.Scene) -> float:
    """レンダーエンジンごとの実測済みSobel生エッジ幅(px)を返す."""
    engine = str(getattr(scene.render, "engine", "") or "")
    if engine == "CYCLES":
        return _BUMP_RAW_EDGE_PX_CYCLES
    return _BUMP_RAW_EDGE_PX_EEVEE


def bump_dilate_size(target_total_px: float, raw_edge_px: float) -> int:
    """目標px幅からDilate(Size)を逆算する（較正式。上記コメント参照）."""
    size = (float(target_total_px) - float(raw_edge_px)) / 2.0
    return max(0, round(size))


def _ensure_compositor_tree(scene: bpy.types.Scene) -> bpy.types.NodeTree:
    tree = getattr(scene, "compositing_node_group", None)
    if tree is None:
        tree = bpy.data.node_groups.new(TREE_NAME, "CompositorNodeTree")
        scene.compositing_node_group = tree
    try:
        scene.use_nodes = True
    except Exception:  # noqa: BLE001
        pass
    return tree


def _owned_by(node: bpy.types.Node, prefix: str) -> bool:
    return node.name.startswith(prefix) or node.label.startswith(prefix)


def _clear_nodes_by_prefix(tree: bpy.types.NodeTree, prefix: str) -> None:
    for node in list(tree.nodes):
        if _owned_by(node, prefix):
            tree.nodes.remove(node)


def _owned(node: bpy.types.Node) -> bool:
    return _owned_by(node, NODE_PREFIX)


def _clear_owned_nodes(tree: bpy.types.NodeTree) -> None:
    _clear_nodes_by_prefix(tree, NODE_PREFIX)


def _socket(node: bpy.types.Node, collection: str, name: str):
    sockets = getattr(node, collection)
    socket = sockets.get(name)
    if socket is None:
        raise RuntimeError(f"{node.name} に {name} ソケットがありません")
    return socket


def _aov_socket(rlayers: bpy.types.Node, name: str):
    socket = rlayers.outputs.get(name)
    if socket is None:
        raise RuntimeError(f"{name} AOV が Render Layers に見つかりません")
    return socket


def _new_owned_node(
    tree: bpy.types.NodeTree,
    node_type: str,
    suffix: str,
    location: tuple[float, float],
    *,
    prefix: str = NODE_PREFIX,
) -> bpy.types.Node:
    node = tree.nodes.new(node_type)
    node.name = f"{prefix}_{suffix}"
    node.label = node.name
    node.location = location
    return node


def _new_vector_math(
    tree: bpy.types.NodeTree,
    suffix: str,
    operation: str,
    location: tuple[float, float],
    *,
    prefix: str = NODE_PREFIX,
) -> bpy.types.Node:
    node = _new_owned_node(tree, "ShaderNodeVectorMath", suffix, location, prefix=prefix)
    node.operation = operation
    return node


def _new_math(
    tree: bpy.types.NodeTree,
    suffix: str,
    operation: str,
    location: tuple[float, float],
    *,
    clamp: bool = False,
    prefix: str = NODE_PREFIX,
) -> bpy.types.Node:
    node = _new_owned_node(tree, "ShaderNodeMath", suffix, location, prefix=prefix)
    node.operation = operation
    if hasattr(node, "use_clamp"):
        node.use_clamp = clamp
    return node


def _link_vector_math(
    tree: bpy.types.NodeTree,
    node: bpy.types.Node,
    socket_a,
    socket_b,
):
    tree.links.new(socket_a, node.inputs[0])
    tree.links.new(socket_b, node.inputs[1])
    return node.outputs["Vector"]


def _link_math(
    tree: bpy.types.NodeTree,
    node: bpy.types.Node,
    socket_a,
    socket_b,
):
    tree.links.new(socket_a, node.inputs[0])
    tree.links.new(socket_b, node.inputs[1])
    return node.outputs["Value"]


def _alpha_socket(
    tree: bpy.types.NodeTree,
    image_socket,
    suffix: str,
    location: tuple[float, float],
    *,
    prefix: str = NODE_PREFIX,
):
    node = _new_owned_node(tree, "CompositorNodeSeparateColor", suffix, location, prefix=prefix)
    tree.links.new(image_socket, _socket(node, "inputs", "Image"))
    return _socket(node, "outputs", "Alpha")


def _invert_value(
    tree: bpy.types.NodeTree,
    value_socket,
    suffix: str,
    location: tuple[float, float],
    *,
    prefix: str = NODE_PREFIX,
):
    node = _new_math(tree, suffix, "SUBTRACT", location, prefix=prefix)
    node.inputs[0].default_value = 1.0
    tree.links.new(value_socket, node.inputs[1])
    return node.outputs["Value"]


def _add_file_output(tree: bpy.types.NodeTree, output_path: Path, image_socket) -> None:
    node = _new_owned_node(tree, "CompositorNodeOutputFile", "FileOutput", (920.0, -260.0))
    if hasattr(node, "directory"):
        node.directory = str(output_path.parent)
        node.file_name = ""
    if hasattr(node, "base_path"):
        node.base_path = str(output_path.parent)
    fmt = getattr(node, "format", None)
    if fmt is not None:
        fmt.media_type = "IMAGE"
        fmt.file_format = "PNG"
        fmt.color_mode = "RGBA"
    name = output_path.stem
    items = getattr(node, "file_output_items", None)
    if items is not None:
        for item in list(items):
            items.remove(item)
        items.new("RGBA", name)
        target_input = node.inputs.get(name)
    else:
        slots = getattr(node, "file_slots", None)
        if slots is not None:
            slots.clear()
            slots.new(name)
        target_input = next((s for s in node.inputs if getattr(s, "enabled", True)), None)
    if target_input is None:
        raise RuntimeError("ファイル出力ソケットを作成できません")
    tree.links.new(image_socket, target_input)


def _build_bump_line_socket(
    tree: bpy.types.NodeTree,
    rlayers: bpy.types.Node,
    scene: bpy.types.Scene,
    view_layer: bpy.types.ViewLayer,
    *,
    matte_id: str,
    color: tuple[float, float, float, float],
    threshold_sensitivity: float,
    dilate_px: int,
    prefix: str,
    base_x: float = -700.0,
    base_y: float = -1300.0,
) -> bpy.types.NodeSocket:
    """標準Normalパス + Cryptomatte(Object) からバンプ線のRGBAソケットを作る.

    戻り値は色が既にマスクで乗算済み・アルファ=マスクの合成済みイメージ
    （他線種のAOV入力と同じ形）。ノード構築は Blender 5.1 のAPI変更を
    踏まえたレシピ（_verify/2026-07-09_bml_bump_line_probe/results.md
    「Blender 5.1 API上の注意点」節で実証済み）に従う:
      - CompositorNodeFilter は filter_type ではなく
        inputs["Type"].default_value = "Sobel"（Menuソケット）
      - CompositorNodeDilateErode は distance ではなく
        inputs["Size"].default_value（負値でErode）
      - CompositorNodeCryptomatteV2 は matte_id にカンマ区切りのオブジェクト名
    """
    view_layer.use_pass_normal = True
    view_layer.use_pass_cryptomatte_object = True

    cm = _new_owned_node(tree, "CompositorNodeCryptomatteV2", "Cryptomatte", (base_x, base_y - 260.0), prefix=prefix)
    cm.source = "RENDER"
    cm.scene = scene
    cm.layer_name = f"{view_layer.name}.CryptoObject"
    tree.links.new(_socket(rlayers, "outputs", "Image"), _socket(cm, "inputs", "Image"))
    cm.matte_id = matte_id

    # Dilateで太らせる分(dilate_px)より確実に大きくErodeして、生成される
    # バンプ線がシルエット側へ滲み出さない安全マージンを持たせる
    # （Phase A0検証3で実証済みの考え方）。
    erode_mask = _new_owned_node(tree, "CompositorNodeDilateErode", "ErodeObjectMask", (base_x + 200.0, base_y - 260.0), prefix=prefix)
    erode_mask.inputs["Size"].default_value = -(max(0, int(dilate_px)) + BUMP_MASK_ERODE_MARGIN_PX)
    tree.links.new(_socket(cm, "outputs", "Matte"), erode_mask.inputs["Mask"])

    sobel = _new_owned_node(tree, "CompositorNodeFilter", "Sobel", (base_x, base_y + 200.0), prefix=prefix)
    sobel.inputs["Type"].default_value = "Sobel"
    tree.links.new(_socket(rlayers, "outputs", "Normal"), sobel.inputs["Image"])

    sep = _new_owned_node(tree, "CompositorNodeSeparateColor", "SobelSeparate", (base_x + 200.0, base_y + 200.0), prefix=prefix)
    tree.links.new(sobel.outputs["Image"], sep.inputs["Image"])

    mr = _new_math(tree, "SquareR", "MULTIPLY", (base_x + 420.0, base_y + 320.0), prefix=prefix)
    tree.links.new(sep.outputs["Red"], mr.inputs[0])
    tree.links.new(sep.outputs["Red"], mr.inputs[1])
    mg = _new_math(tree, "SquareG", "MULTIPLY", (base_x + 420.0, base_y + 220.0), prefix=prefix)
    tree.links.new(sep.outputs["Green"], mg.inputs[0])
    tree.links.new(sep.outputs["Green"], mg.inputs[1])
    mb = _new_math(tree, "SquareB", "MULTIPLY", (base_x + 420.0, base_y + 120.0), prefix=prefix)
    tree.links.new(sep.outputs["Blue"], mb.inputs[0])
    tree.links.new(sep.outputs["Blue"], mb.inputs[1])

    add_rg = _new_math(tree, "AddRG", "ADD", (base_x + 620.0, base_y + 220.0), prefix=prefix)
    tree.links.new(mr.outputs["Value"], add_rg.inputs[0])
    tree.links.new(mg.outputs["Value"], add_rg.inputs[1])
    add_rgb = _new_math(tree, "AddRGB", "ADD", (base_x + 820.0, base_y + 180.0), prefix=prefix)
    tree.links.new(add_rg.outputs["Value"], add_rgb.inputs[0])
    tree.links.new(mb.outputs["Value"], add_rgb.inputs[1])
    length_n = _new_math(tree, "Length", "SQRT", (base_x + 1020.0, base_y + 180.0), prefix=prefix)
    tree.links.new(add_rgb.outputs["Value"], length_n.inputs[0])

    threshold_node = _new_math(tree, "Threshold", "GREATER_THAN", (base_x + 1220.0, base_y + 180.0), prefix=prefix)
    threshold_node.inputs[1].default_value = bump_edge_threshold(threshold_sensitivity)
    tree.links.new(length_n.outputs["Value"], threshold_node.inputs[0])

    masked = _new_math(tree, "MaskBySilhouette", "MULTIPLY", (base_x + 1220.0, base_y - 40.0), prefix=prefix)
    tree.links.new(threshold_node.outputs["Value"], masked.inputs[0])
    tree.links.new(erode_mask.outputs["Mask"], masked.inputs[1])

    dilate = _new_owned_node(tree, "CompositorNodeDilateErode", "Dilate", (base_x + 1420.0, base_y - 40.0), prefix=prefix)
    dilate.inputs["Size"].default_value = max(0, int(dilate_px))
    tree.links.new(masked.outputs["Value"], dilate.inputs["Mask"])

    aa = _new_owned_node(tree, "CompositorNodeAntiAliasing", "AntiAlias", (base_x + 1620.0, base_y - 40.0), prefix=prefix)
    tree.links.new(dilate.outputs["Mask"], aa.inputs["Image"])

    # AntiAliasing の Image 出力は R=G=B=マスク値・A=1.0固定（Value->Color変換の
    # 仕様。Phase A0検証3で確認済み）。赤チャンネルをマスク値として取り出す。
    mask_sep = _new_owned_node(tree, "CompositorNodeSeparateColor", "MaskSeparate", (base_x + 1820.0, base_y - 40.0), prefix=prefix)
    tree.links.new(aa.outputs["Image"], mask_sep.inputs["Image"])
    mask_value = mask_sep.outputs["Red"]

    rgb = _new_owned_node(tree, "CompositorNodeRGB", "Color", (base_x + 1620.0, base_y - 280.0), prefix=prefix)
    rgb.outputs[0].default_value = tuple(color)

    colorize = _new_vector_math(tree, "Colorize", "MULTIPLY", (base_x + 2020.0, base_y - 40.0), prefix=prefix)
    tree.links.new(rgb.outputs[0], colorize.inputs[0])
    tree.links.new(mask_value, colorize.inputs[1])

    set_alpha = _new_owned_node(tree, "CompositorNodeSetAlpha", "SetAlpha", (base_x + 2220.0, base_y - 40.0), prefix=prefix)
    tree.links.new(colorize.outputs["Vector"], _socket(set_alpha, "inputs", "Image"))
    tree.links.new(mask_value, _socket(set_alpha, "inputs", "Alpha"))

    return _socket(set_alpha, "outputs", "Image")


def _bump_line_render_targets(scene: bpy.types.Scene) -> list[bpy.types.Object]:
    """バンプ線有効・非ロックのメッシュオブジェクトを名前順で列挙する."""
    from . import core

    targets = []
    for obj in scene.objects:
        if obj.type != "MESH" or obj.data is None:
            continue
        if core.is_settings_locked(obj):
            continue
        settings = getattr(obj, "bmanga_line_settings", None)
        if settings is None or not bool(getattr(settings, "bump_line_enabled", False)):
            continue
        targets.append(obj)
    targets.sort(key=lambda o: o.name)
    return targets


def _bump_line_style(targets: list[bpy.types.Object]) -> tuple[tuple[float, ...], float, float]:
    """バンプ線対象オブジェクト群の見た目パラメータを決定する.

    バンプ線は画像空間の単一チェーンで処理するため、色・太さ・感度は
    シーン内で1系統のみになる（オブジェクト単位で異なる値を設定しても
    個別には反映されない、v1の既知の仕様上の制約）。名前順で最初の
    オブジェクトの設定値を採用し、決定的な挙動にする。
    """
    settings = targets[0].bmanga_line_settings
    return (
        tuple(settings.bump_line_color),
        float(settings.bump_line_thickness),
        float(settings.bump_line_threshold),
    )


def _composite_output_socket_owned_by_us(tree: bpy.types.NodeTree) -> bpy.types.NodeTreeInterfaceSocket | None:
    for item in tree.interface.items_tree:
        if item.in_out == "OUTPUT" and item.name == BUMP_OUTPUT_SOCKET_NAME:
            return item
    return None


def _ensure_bump_passthrough(
    scene: bpy.types.Scene,
    tree: bpy.types.NodeTree,
    own_socket: bpy.types.NodeTreeInterfaceSocket,
) -> None:
    """自分が占有中のOUTPUTソケットへ、素のCombinedパスを直結する.

    バンプ線オフ時の安全なフォールバック（sync_bump_line_render_composite
    の docstring 参照）。RenderLayers→GroupOutput の2ノードのみで、
    重い処理は一切含まない。
    """
    view_layer = scene.view_layers[0] if scene.view_layers else None
    if view_layer is None:
        return
    rlayers = _new_owned_node(
        tree, "CompositorNodeRLayers", "PassthroughRenderLayers", (-400.0, -600.0),
        prefix=BUMP_COMPOSITE_NODE_PREFIX,
    )
    try:
        rlayers.scene = scene
    except Exception:  # noqa: BLE001
        pass
    try:
        rlayers.layer = view_layer.name
    except Exception:  # noqa: BLE001
        pass
    gout = _new_owned_node(
        tree, "NodeGroupOutput", "GroupOutput", (0.0, -600.0),
        prefix=BUMP_COMPOSITE_NODE_PREFIX,
    )
    gout.name = BUMP_GROUP_OUTPUT_NAME
    gout.label = gout.name
    tree.links.new(_socket(rlayers, "outputs", "Image"), gout.inputs[own_socket.name])


def sync_bump_line_render_composite(scene: bpy.types.Scene) -> bool:
    """バンプ線をレンダー画像(最終コンポジット結果)へアルファオーバー合成する.

    Blender 5.1 の新コンポジターアーキテクチャでは、シーンの最終出力は
    CompositorNodeComposite ノード（廃止された）ではなく、
    scene.compositing_node_group の「最初のOUTPUTインターフェースソケット」
    へ NodeGroupOutput 経由で繋いだ画像になる（RenderSettings.use_compositing
    のドキュメント、および実機検証で確認済み）。

    バンプ線有効・非ロックのオブジェクトが1つも無ければ、以前に自分が
    追加した処理ノード（Cryptomatte・エッジ検出等の重い部分）を撤去する。
    ただし一度でも最初のOUTPUTソケットを自分が占有した後は、そのソケットと
    RenderLayers→GroupOutputの直結（素のCombinedパス）だけは残す —
    Blender 5.1では「OUTPUTソケットは存在するがGroupOutputノードが無い/
    何も繋がっていない」状態の compositing_node_group をシーンに割り当てると
    bpy.ops.render.render(write_still=True) が例外を出さずにファイルを
    一切保存しなくなることを実機確認したため（2026-07-09）。「オフ時は
    ノード撤去」の趣旨（重い処理ノードの除去）は保ちつつ、レンダー
    パイプライン自体を壊さないための最小限の必須逸脱。
    他の仕組みが既に最初のOUTPUTソケットを占有している場合は、
    自動合成をスキップして既存の設定を保護する。

    戻り値: 実際にレンダー画像へ合成されたら True。
    """
    targets = _bump_line_render_targets(scene)
    tree = getattr(scene, "compositing_node_group", None)

    if not targets:
        if tree is None:
            return False
        own_socket = _composite_output_socket_owned_by_us(tree)
        _clear_nodes_by_prefix(tree, BUMP_COMPOSITE_NODE_PREFIX)
        if own_socket is not None:
            _ensure_bump_passthrough(scene, tree, own_socket)
        return False

    tree = _ensure_compositor_tree(scene)
    scene.render.use_compositing = True
    _clear_nodes_by_prefix(tree, BUMP_COMPOSITE_NODE_PREFIX)

    view_layer = scene.view_layers[0] if scene.view_layers else None
    if view_layer is None:
        return False

    existing_outputs = [item for item in tree.interface.items_tree if item.in_out == "OUTPUT"]
    own_socket = next((item for item in existing_outputs if item.name == BUMP_OUTPUT_SOCKET_NAME), None)
    if own_socket is None and existing_outputs:
        # 他の仕組み(ユーザーの手動設定 or 他機能)が最終出力を既に占有している。
        # v0.3.92の「既存のコンポジット設定を壊さない」方針に従い触れない。
        print(
            "[B-MANGA Liner] バンプ線: 既存のコンポジター最終出力があるため"
            "レンダー画像への自動合成をスキップしました"
        )
        return False
    if own_socket is None:
        own_socket = tree.interface.new_socket(
            name=BUMP_OUTPUT_SOCKET_NAME, in_out="OUTPUT", socket_type="NodeSocketColor",
        )

    rlayers = _new_owned_node(
        tree, "CompositorNodeRLayers", "RenderLayers", (-1400.0, -600.0),
        prefix=BUMP_COMPOSITE_NODE_PREFIX,
    )
    try:
        rlayers.scene = scene
    except Exception:  # noqa: BLE001
        pass
    try:
        rlayers.layer = view_layer.name
    except Exception:  # noqa: BLE001
        pass

    color, thickness_mm, threshold = _bump_line_style(targets)
    from . import camera_comp

    target_px = camera_comp.target_pixels_for_mm(scene, thickness_mm)
    dilate_px = bump_dilate_size(target_px, bump_raw_edge_px(scene))
    matte_id = ", ".join(obj.name for obj in targets)

    bump_socket = _build_bump_line_socket(
        tree, rlayers, scene, view_layer,
        matte_id=matte_id,
        color=color,
        threshold_sensitivity=threshold,
        dilate_px=dilate_px,
        prefix=BUMP_COMPOSITE_NODE_PREFIX,
    )

    alpha_over = _new_owned_node(
        tree, "CompositorNodeAlphaOver", "AlphaOver", (700.0, -600.0),
        prefix=BUMP_COMPOSITE_NODE_PREFIX,
    )
    # 注意: このBlender 5.1ビルドのCompositorNodeAlphaOverはソケット順が
    # (Background, Foreground, Factor) で、Fac/Image/Imageという旧来の
    # 想定順ではない（実機確認済み、2026-07-09）。名前で明示的に指定する。
    tree.links.new(_socket(rlayers, "outputs", "Image"), _socket(alpha_over, "inputs", "Background"))
    tree.links.new(bump_socket, _socket(alpha_over, "inputs", "Foreground"))

    gout = _new_owned_node(
        tree, "NodeGroupOutput", "GroupOutput", (1000.0, -600.0),
        prefix=BUMP_COMPOSITE_NODE_PREFIX,
    )
    gout.name = BUMP_GROUP_OUTPUT_NAME
    gout.label = gout.name
    tree.links.new(alpha_over.outputs["Image"], gout.inputs[own_socket.name])
    return True


def _create_line_composite_group(
    *,
    bump_matte_id: str | None = None,
    bump_color: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0),
    bump_threshold: float = 0.65,
    bump_dilate_px: int = 0,
    bump_scene: bpy.types.Scene | None = None,
    bump_view_layer: bpy.types.ViewLayer | None = None,
) -> bpy.types.NodeTree:
    """線画合成グループを（再）構築する.

    バンプ線は他4線種と異なりRenderLayersの直接AOV出力ではなく、
    Normalパス+Cryptomatteからその場で合成する必要がある。この処理を
    グループの外（呼び出し元の setup_line_aov_compositor 側のシーンツリー）
    に置くと、`test/blender_b_manga_line_aov_composite_check.py` が検証する
    「シーンツリーにはRenderLayers/Group/Result/FileOutput以外の処理ノードを
    直接置かない」というノード所有権の既存規約に違反する。そのため
    バンプ線のCryptomatte/Normal取得用RenderLayersもこのグループ内部に
    自己完結で持たせる（bump_scene/bump_view_layer はそのための参照）。
    """
    old = bpy.data.node_groups.get(GROUP_TREE_NAME)
    if old is not None:
        bpy.data.node_groups.remove(old)

    tree = bpy.data.node_groups.new(GROUP_TREE_NAME, "CompositorNodeTree")
    for name in (
        AOV_OUTLINE_RAW_NAME,
        AOV_OBJECT_MASK_NAME,
        AOV_INNER_LINES_NAME,
        AOV_INTERSECTION_LINES_NAME,
        AOV_SELECTION_LINES_NAME,
    ):
        tree.interface.new_socket(
            name=name,
            in_out="INPUT",
            socket_type="NodeSocketColor",
        )
    tree.interface.new_socket(
        name=AOV_COMPOSITE_NAME,
        in_out="OUTPUT",
        socket_type="NodeSocketColor",
    )

    gin = tree.nodes.new("NodeGroupInput")
    gin.name = f"{NODE_PREFIX}_GroupInput"
    gin.label = gin.name
    gin.location = (-980.0, 0.0)
    gout = tree.nodes.new("NodeGroupOutput")
    gout.name = f"{NODE_PREFIX}_GroupOutput"
    gout.label = gout.name
    gout.location = (1240.0, -20.0)

    outline_raw_socket = _socket(gin, "outputs", AOV_OUTLINE_RAW_NAME)
    object_mask_socket = _socket(gin, "outputs", AOV_OBJECT_MASK_NAME)
    inner_lines_socket = _socket(gin, "outputs", AOV_INNER_LINES_NAME)
    intersection_lines_socket = _socket(gin, "outputs", AOV_INTERSECTION_LINES_NAME)
    selection_lines_socket = _socket(gin, "outputs", AOV_SELECTION_LINES_NAME)

    if bump_matte_id and bump_scene is not None and bump_view_layer is not None:
        bump_rlayers = _new_owned_node(tree, "CompositorNodeRLayers", "BumpRenderLayers", (-980.0, -1300.0))
        try:
            bump_rlayers.scene = bump_scene
        except Exception:  # noqa: BLE001
            pass
        try:
            bump_rlayers.layer = bump_view_layer.name
        except Exception:  # noqa: BLE001
            pass
        bump_lines_socket = _build_bump_line_socket(
            tree, bump_rlayers, bump_scene, bump_view_layer,
            matte_id=bump_matte_id,
            color=bump_color,
            threshold_sensitivity=bump_threshold,
            dilate_px=bump_dilate_px,
            prefix=NODE_PREFIX,
            base_x=-700.0,
            base_y=-1300.0,
        )
    else:
        # バンプ線有効オブジェクトが無い場合は透明な定数を使い、
        # グループのトポロジーは常に一定に保つ（他4線種の入力と対称）。
        empty_bump = _new_owned_node(tree, "CompositorNodeRGB", "BumpLinesEmpty", (-700.0, -1300.0))
        empty_bump.outputs[0].default_value = (0.0, 0.0, 0.0, 0.0)
        bump_lines_socket = empty_bump.outputs[0]

    invert_mask = _new_owned_node(tree, "CompositorNodeInvert", "InvertObjectMask", (-740.0, -120.0))
    invert_mask.inputs["Factor"].default_value = 1.0
    invert_mask.inputs["Invert Color"].default_value = True
    invert_mask.inputs["Invert Alpha"].default_value = False
    tree.links.new(object_mask_socket, _socket(invert_mask, "inputs", "Color"))

    outline_mul = _new_vector_math(tree, "OutlineMinusSurface", "MULTIPLY", (-440.0, 40.0))
    outline_only = _link_vector_math(
        tree,
        outline_mul,
        outline_raw_socket,
        _socket(invert_mask, "outputs", "Color"),
    )

    outline_alpha = _alpha_socket(tree, outline_raw_socket, "OutlineRawAlpha", (-720.0, -320.0))
    object_alpha = _alpha_socket(tree, object_mask_socket, "ObjectMaskAlpha", (-720.0, -460.0))
    inverted_object_alpha = _invert_value(tree, object_alpha, "InvertObjectMaskAlpha", (-460.0, -460.0))
    outline_alpha_mul = _new_math(tree, "OutlineMinusSurfaceAlpha", "MULTIPLY", (-220.0, -360.0))
    outline_only_alpha = _link_math(tree, outline_alpha_mul, outline_alpha, inverted_object_alpha)

    inner_alpha = _alpha_socket(tree, inner_lines_socket, "InnerLinesAlpha", (-400.0, -620.0))
    inverted_inner_alpha = _invert_value(tree, inner_alpha, "InvertInnerLinesAlpha", (-120.0, -620.0))
    outline_without_inner = _new_vector_math(tree, "OutlineWithoutInnerLines", "MULTIPLY", (-120.0, 150.0))
    outline_color_for_inner = _link_vector_math(
        tree,
        outline_without_inner,
        outline_only,
        inverted_inner_alpha,
    )

    add_inner = _new_vector_math(tree, "AddInnerLines", "ADD", (100.0, 80.0))
    outline_and_inner = _link_vector_math(
        tree,
        add_inner,
        outline_color_for_inner,
        inner_lines_socket,
    )

    intersection_alpha = _alpha_socket(
        tree,
        intersection_lines_socket,
        "IntersectionLinesAlpha",
        (-120.0, -760.0),
    )
    inverted_intersection_alpha = _invert_value(
        tree,
        intersection_alpha,
        "InvertIntersectionLinesAlpha",
        (160.0, -760.0),
    )
    color_without_intersection = _new_vector_math(
        tree,
        "ColorWithoutIntersectionLines",
        "MULTIPLY",
        (360.0, 80.0),
    )
    outline_inner_for_intersection = _link_vector_math(
        tree,
        color_without_intersection,
        outline_and_inner,
        inverted_intersection_alpha,
    )

    add_intersection = _new_vector_math(tree, "AddIntersectionLines", "ADD", (600.0, 0.0))
    outline_inner_intersection = _link_vector_math(
        tree,
        add_intersection,
        outline_inner_for_intersection,
        intersection_lines_socket,
    )

    selection_alpha = _alpha_socket(
        tree,
        selection_lines_socket,
        "SelectionLinesAlpha",
        (360.0, -900.0),
    )
    inverted_selection_alpha = _invert_value(
        tree,
        selection_alpha,
        "InvertSelectionLinesAlpha",
        (600.0, -900.0),
    )
    color_without_selection = _new_vector_math(
        tree,
        "ColorWithoutSelectionLines",
        "MULTIPLY",
        (760.0, 80.0),
    )
    line_color_for_selection = _link_vector_math(
        tree,
        color_without_selection,
        outline_inner_intersection,
        inverted_selection_alpha,
    )

    add_selection = _new_vector_math(tree, "AddSelectionLines", "ADD", (980.0, 0.0))
    selection_color_socket = _link_vector_math(
        tree,
        add_selection,
        line_color_for_selection,
        selection_lines_socket,
    )

    add_outline_inner_alpha = _new_math(tree, "AddOutlineInnerAlpha", "ADD", (100.0, -400.0), clamp=True)
    outline_inner_alpha = _link_math(tree, add_outline_inner_alpha, outline_only_alpha, inner_alpha)
    add_final_alpha = _new_math(tree, "AddIntersectionAlpha", "ADD", (360.0, -460.0), clamp=True)
    outline_inner_intersection_alpha = _link_math(tree, add_final_alpha, outline_inner_alpha, intersection_alpha)
    add_selection_alpha = _new_math(tree, "AddSelectionAlpha", "ADD", (600.0, -520.0), clamp=True)
    selection_alpha_socket = _link_math(
        tree,
        add_selection_alpha,
        outline_inner_intersection_alpha,
        selection_alpha,
    )

    # バンプ線を最後に合流（既存の線種加算チェーンと同じアルファオーバー方式）。
    bump_alpha = _alpha_socket(tree, bump_lines_socket, "BumpLinesAlpha", (760.0, -1040.0))
    inverted_bump_alpha = _invert_value(tree, bump_alpha, "InvertBumpLinesAlpha", (980.0, -1040.0))
    color_without_bump = _new_vector_math(tree, "ColorWithoutBumpLines", "MULTIPLY", (1160.0, 80.0))
    line_color_for_bump = _link_vector_math(
        tree,
        color_without_bump,
        selection_color_socket,
        inverted_bump_alpha,
    )
    add_bump = _new_vector_math(tree, "AddBumpLines", "ADD", (1380.0, 0.0))
    final_color_socket = _link_vector_math(
        tree,
        add_bump,
        line_color_for_bump,
        bump_lines_socket,
    )
    add_bump_alpha = _new_math(tree, "AddBumpAlpha", "ADD", (980.0, -580.0), clamp=True)
    final_alpha_socket = _link_math(
        tree,
        add_bump_alpha,
        selection_alpha_socket,
        bump_alpha,
    )

    set_alpha = _new_owned_node(tree, "CompositorNodeSetAlpha", "SetTransparentLineAlpha", (1620.0, -20.0))
    tree.links.new(final_color_socket, _socket(set_alpha, "inputs", "Image"))
    tree.links.new(final_alpha_socket, _socket(set_alpha, "inputs", "Alpha"))
    tree.links.new(_socket(set_alpha, "outputs", "Image"), _socket(gout, "inputs", AOV_COMPOSITE_NAME))
    return tree


def setup_line_aov_compositor(
    scene: bpy.types.Scene,
    *,
    output_path: str | Path | None = None,
) -> bpy.types.NodeTree:
    """Create a line-only compositor output from split B-MANGA Line AOVs.

    The raw inverted-hull outline AOV contains the inflated source surface.
    Multiplying it by the inverted source-object mask removes that fill, then
    inner lines and intersection lines are added back without subtraction.
    """
    tree = _ensure_compositor_tree(scene)
    _clear_owned_nodes(tree)

    view_layer = scene.view_layers[0] if scene.view_layers else None

    bump_targets = _bump_line_render_targets(scene)
    bump_kwargs: dict = {}
    if bump_targets and view_layer is not None:
        color, thickness_mm, threshold = _bump_line_style(bump_targets)
        from . import camera_comp

        target_px = camera_comp.target_pixels_for_mm(scene, thickness_mm)
        dilate_px = bump_dilate_size(target_px, bump_raw_edge_px(scene))
        bump_kwargs = {
            "bump_matte_id": ", ".join(obj.name for obj in bump_targets),
            "bump_color": color,
            "bump_threshold": threshold,
            "bump_dilate_px": dilate_px,
            "bump_scene": scene,
            "bump_view_layer": view_layer,
        }
    group_tree = _create_line_composite_group(**bump_kwargs)

    rlayers = _new_owned_node(tree, "CompositorNodeRLayers", "RenderLayers", (-840.0, 0.0))
    try:
        rlayers.scene = scene
    except Exception:  # noqa: BLE001
        pass
    try:
        rlayers.layer = view_layer.name if view_layer is not None else scene.view_layers[0].name
    except Exception:  # noqa: BLE001
        pass

    outline_raw_socket = _aov_socket(rlayers, AOV_OUTLINE_RAW_NAME)
    object_mask_socket = _aov_socket(rlayers, AOV_OBJECT_MASK_NAME)
    inner_lines_socket = _aov_socket(rlayers, AOV_INNER_LINES_NAME)
    intersection_lines_socket = _aov_socket(rlayers, AOV_INTERSECTION_LINES_NAME)
    selection_lines_socket = _aov_socket(rlayers, AOV_SELECTION_LINES_NAME)

    group = _new_owned_node(tree, "CompositorNodeGroup", "Group", (-420.0, -20.0))
    group.node_tree = group_tree
    tree.links.new(outline_raw_socket, _socket(group, "inputs", AOV_OUTLINE_RAW_NAME))
    tree.links.new(object_mask_socket, _socket(group, "inputs", AOV_OBJECT_MASK_NAME))
    tree.links.new(inner_lines_socket, _socket(group, "inputs", AOV_INNER_LINES_NAME))
    tree.links.new(intersection_lines_socket, _socket(group, "inputs", AOV_INTERSECTION_LINES_NAME))
    tree.links.new(selection_lines_socket, _socket(group, "inputs", AOV_SELECTION_LINES_NAME))
    final_socket = _socket(group, "outputs", AOV_COMPOSITE_NAME)

    result = _new_owned_node(tree, "NodeReroute", "Result", (-80.0, -20.0))
    result.label = AOV_COMPOSITE_NAME
    tree.links.new(final_socket, result.inputs[0])
    if output_path is not None:
        _add_file_output(tree, Path(output_path), result.outputs[0])
    return tree
