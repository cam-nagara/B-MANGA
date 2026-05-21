"""コマ用blendファイル内の ``thumb.png`` 出力ノード管理."""

from __future__ import annotations

import bpy

from . import log

_logger = log.get_logger(__name__)

THUMB_FILE_NAME = "thumb.png"
THUMB_SOCKET_NAME = "thumb"
THUMB_NODE_NAME = THUMB_FILE_NAME


def ensure_thumb_output_node(scene=None) -> bool:
    """現在の Scene に ``thumb.png`` 用のファイル出力ノードを用意する.

    レンダリング設定や解像度は変更しない。コンポジターの Render Layers 画像を
    ``//thumb.png`` に保存する出力ノードだけを整える。
    """
    scene = scene or bpy.context.scene
    if scene is None:
        return False
    tree = _ensure_compositor_tree(scene)
    if tree is None:
        return False
    output = _ensure_output_node(tree)
    render_layers = _ensure_render_layers_node(tree)
    if output is None or render_layers is None:
        return False
    _ensure_output_format(output)
    socket = _ensure_thumb_socket(output)
    if socket is None:
        return False
    _ensure_link(tree, render_layers, socket)
    return True


def _ensure_compositor_tree(scene):
    tree = getattr(scene, "compositing_node_group", None)
    if tree is None:
        try:
            tree = bpy.data.node_groups.new("B-Name Thumbnail", "CompositorNodeTree")
            scene.compositing_node_group = tree
        except Exception:  # noqa: BLE001
            _logger.exception("thumb output: compositor tree create failed")
            return None
    try:
        scene.use_nodes = True
    except Exception:  # noqa: BLE001
        _logger.exception("thumb output: compositor enable failed")
    return tree


def _ensure_render_layers_node(tree):
    for node in tree.nodes:
        if getattr(node, "bl_idname", "") == "CompositorNodeRLayers":
            return node
    try:
        node = tree.nodes.new("CompositorNodeRLayers")
        node.location = (-320.0, 120.0)
        return node
    except Exception:  # noqa: BLE001
        _logger.exception("thumb output: render layers node create failed")
        return None


def _ensure_output_node(tree):
    for node in tree.nodes:
        if (
            getattr(node, "bl_idname", "") == "CompositorNodeOutputFile"
            and (node.name == THUMB_NODE_NAME or node.label == THUMB_NODE_NAME)
        ):
            break
    else:
        try:
            node = tree.nodes.new("CompositorNodeOutputFile")
            node.location = (80.0, 120.0)
        except Exception:  # noqa: BLE001
            _logger.exception("thumb output: output node create failed")
            return None
    node.name = THUMB_NODE_NAME
    node.label = THUMB_NODE_NAME
    try:
        node.directory = "//"
        node.file_name = ""
    except Exception:  # noqa: BLE001
        _logger.exception("thumb output: output path setup failed")
    return node


def _ensure_output_format(node) -> None:
    fmt = getattr(node, "format", None)
    if fmt is None:
        return
    try:
        fmt.media_type = "IMAGE"
        fmt.file_format = "PNG"
        fmt.color_mode = "RGBA"
    except Exception:  # noqa: BLE001
        _logger.exception("thumb output: png format setup failed")


def _ensure_thumb_socket(node):
    items = getattr(node, "file_output_items", None)
    if items is None:
        return _legacy_thumb_socket(node)
    try:
        for item in tuple(items):
            if item.name != THUMB_SOCKET_NAME:
                items.remove(item)
        item = items.get(THUMB_SOCKET_NAME)
        if item is None:
            item = items.new("RGBA", THUMB_SOCKET_NAME)
        item.name = THUMB_SOCKET_NAME
        item.override_node_format = False
    except Exception:  # noqa: BLE001
        _logger.exception("thumb output: output item setup failed")
        return None
    try:
        return node.inputs[THUMB_SOCKET_NAME]
    except Exception:  # noqa: BLE001
        _logger.exception("thumb output: output socket lookup failed")
        return None


def _legacy_thumb_socket(node):
    slots = getattr(node, "file_slots", None)
    if slots is None:
        return None
    try:
        while len(slots) > 0:
            slots.remove(slots[0])
        slot = slots.new(THUMB_SOCKET_NAME)
        slot.path = "thumb"
        node.base_path = "//"
        node.format.file_format = "PNG"
        node.format.color_mode = "RGBA"
        return node.inputs[slot.name]
    except Exception:  # noqa: BLE001
        _logger.exception("thumb output: legacy output setup failed")
        return None


def _ensure_link(tree, render_layers, socket) -> None:
    """``thumb`` ソケットへ既定の接続を確保する.

    ユーザーが手動で別ソースへ繋ぎ替えている場合は触らない。 何も繋がっていない
    時だけ、 最初の Render Layers ノードの画像出力へ規定値として接続する。
    """
    try:
        for link in tree.links:
            if link.to_socket == socket:
                # 既にユーザー or 過去の設定で何か繋がっている。 触らない。
                return
        image_socket = _find_image_output(render_layers)
        if image_socket is None:
            return
        tree.links.new(image_socket, socket)
    except Exception:  # noqa: BLE001
        _logger.exception("thumb output: link setup failed")


def _find_image_output(render_layers):
    """Render Layers ノードの画像出力ソケットを返す.

    多言語化された Blender では ``name`` が "画像" 等になるため、
    ``identifier`` (常に "Image") 優先で探し、 見つからなければ最初の
    RGBA/COLOR 出力にフォールバックする。
    """
    if render_layers is None:
        return None
    for sock in render_layers.outputs:
        if getattr(sock, "identifier", "") == "Image":
            return sock
    for sock in render_layers.outputs:
        if getattr(sock, "name", "") == "Image":
            return sock
    for sock in render_layers.outputs:
        if getattr(sock, "type", "") in {"RGBA", "COLOR"}:
            return sock
    return None
