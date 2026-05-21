"""コマ用blendファイル内の ``thumb.png`` 出力ノード管理."""

from __future__ import annotations

import bpy

from . import log

_logger = log.get_logger(__name__)

THUMB_FILE_NAME = "thumb.png"
THUMB_SOCKET_NAME = "thumb"
THUMB_NODE_NAME = THUMB_FILE_NAME
THUMB_SCALE_NODE_NAME = "BName_ThumbScale"


def ensure_thumb_output_node(scene=None) -> bool:
    """現在の Scene に ``thumb.png`` 用のファイル出力ノードを用意する.

    レンダリング設定や解像度は変更しない。 コンポジターの Render Layers 画像を
    B-Name の「コマ画像縮小率」 (``work.page_preview_scale_percentage``) で
    リスケールして ``//thumb.png`` に保存する。 縮小率を 10% にすれば
    ``thumb.png`` も 10% サイズで出力されるため、 ページ一覧用の軽量サムネが
    そのまま得られる。
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
    scale_node = _ensure_thumb_scale_node(tree, scene)
    _ensure_link(tree, render_layers, socket, scale_node=scale_node)
    return True


def _resolve_thumb_scale_factor(scene) -> float:
    """B-Name の「コマ画像縮小率」 を 0..1 の倍率として返す.

    値が読めない / 範囲外なら 0.1 (10%, 既定値) を採用する。
    """
    work = getattr(scene, "bname_work", None)
    if work is None:
        return 0.1
    try:
        pct = float(getattr(work, "page_preview_scale_percentage", 10.0) or 10.0)
    except (TypeError, ValueError):
        pct = 10.0
    pct = max(1.0, min(100.0, pct))
    return pct / 100.0


def _ensure_thumb_scale_node(tree, scene):
    """``thumb.png`` 出力前の Scale ノードを用意し、 縮小率を反映する."""
    if tree is None:
        return None
    node = None
    for n in tree.nodes:
        if n.bl_idname != "CompositorNodeScale":
            continue
        if n.name == THUMB_SCALE_NODE_NAME or n.label == THUMB_SCALE_NODE_NAME:
            node = n
            break
    if node is None:
        try:
            node = tree.nodes.new("CompositorNodeScale")
            node.name = THUMB_SCALE_NODE_NAME
            node.label = THUMB_SCALE_NODE_NAME
            node.location = (-100.0, 120.0)
        except Exception:  # noqa: BLE001
            _logger.exception("thumb output: scale node create failed")
            return None
    # Blender 4.x の ``node.space = "RELATIVE"`` プロパティは 5.x で
    # 入力ソケット ``Type`` (既定 "Relative") に置き換えられた。 念のため
    # 旧式へも対応する。
    try:
        if hasattr(node, "space"):
            node.space = "RELATIVE"
    except Exception:  # noqa: BLE001
        pass
    try:
        type_sock = node.inputs.get("Type")
        if type_sock is not None and getattr(type_sock, "type", "") == "MENU":
            try:
                type_sock.default_value = "Relative"
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    scale = _resolve_thumb_scale_factor(scene)
    try:
        # 名前で探す (Blender 4.x/5.x 共通)。 見つからない場合は最初の数値
        # ソケットを順に X, Y として採用するフォールバック。
        x_sock = node.inputs.get("X")
        y_sock = node.inputs.get("Y")
        if x_sock is None or y_sock is None:
            value_sockets = [s for s in node.inputs if getattr(s, "type", "") == "VALUE"]
            if x_sock is None and value_sockets:
                x_sock = value_sockets[0]
            if y_sock is None and len(value_sockets) > 1:
                y_sock = value_sockets[1]
        if x_sock is not None:
            x_sock.default_value = scale
        if y_sock is not None:
            y_sock.default_value = scale
    except Exception:  # noqa: BLE001
        _logger.exception("thumb output: scale value set failed")
    return node


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


def _ensure_link(tree, render_layers, socket, scale_node=None) -> None:
    """``thumb`` ソケットへ既定の接続を確保し、 Scale ノードを必ず途中に挟む.

    ユーザーがソース側 (例: ``効果統合`` の出力) を選んでいる場合は温存し、
    そのソースを Scale ノードの入力へ繋ぎ替える。 ``thumb`` 直前は常に
    Scale ノードを経由するため、 縮小率の変更がすぐ出力に反映される。
    """
    try:
        if scale_node is None:
            # Scale ノード生成失敗時のフォールバック: 旧挙動 (直結) で動かす
            for link in tree.links:
                if link.to_socket == socket:
                    return
            image_socket = _find_image_output(render_layers)
            if image_socket is None:
                return
            tree.links.new(image_socket, socket)
            return

        scale_in = scale_node.inputs[0] if len(scale_node.inputs) > 0 else None
        scale_out = scale_node.outputs[0] if len(scale_node.outputs) > 0 else None
        if scale_in is None or scale_out is None:
            return

        # 1) ``thumb`` ソケット直前を必ず Scale ノードからにする。
        # Blender の bpy_struct は再アクセスごとに別の Python オブジェクトを
        # 返すことがあり、 ``is`` 比較は同じノードでも False になり得る。
        # 名前比較で安定して判定する。
        scale_node_name = getattr(scale_node, "name", None)
        thumb_already_from_scale = False
        existing_to_thumb = None
        for link in list(tree.links):
            if link.to_socket == socket:
                from_node_name = getattr(link.from_node, "name", None)
                if scale_node_name is not None and from_node_name == scale_node_name:
                    thumb_already_from_scale = True
                else:
                    existing_to_thumb = link
                break

        # 2) Scale ノードの入力ソースを決める。
        #    - ユーザーが ``thumb`` に何か繋いでいたら、そのソースを Scale 入力に移す。
        #    - そうでなく Scale 入力が空なら、 RLayers の画像出力を既定で繋ぐ。
        if existing_to_thumb is not None:
            preserved_source = existing_to_thumb.from_socket
            try:
                tree.links.remove(existing_to_thumb)
            except Exception:  # noqa: BLE001
                pass
            # Scale 入力に既存ソースを繋ぎ直す (既存リンクがあれば置換)
            for link in list(tree.links):
                if link.to_socket == scale_in:
                    try:
                        tree.links.remove(link)
                    except Exception:  # noqa: BLE001
                        pass
            try:
                tree.links.new(preserved_source, scale_in)
            except Exception:  # noqa: BLE001
                _logger.exception("thumb output: re-route to scale input failed")
        else:
            # Scale 入力が無接続なら既定値を繋ぐ
            scale_in_has_link = any(link.to_socket == scale_in for link in tree.links)
            if not scale_in_has_link:
                image_socket = _find_image_output(render_layers)
                if image_socket is not None:
                    try:
                        tree.links.new(image_socket, scale_in)
                    except Exception:  # noqa: BLE001
                        _logger.exception("thumb output: default scale input link failed")

        # 3) Scale 出力 → thumb ソケット
        if not thumb_already_from_scale:
            try:
                tree.links.new(scale_out, socket)
            except Exception:  # noqa: BLE001
                _logger.exception("thumb output: scale->thumb link failed")
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
