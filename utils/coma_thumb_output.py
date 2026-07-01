"""コマ用blendファイル内の ``thumb.png`` 出力ノード管理."""

from __future__ import annotations

import math
import time
from pathlib import Path

import bpy

from . import log, paths

_logger = log.get_logger(__name__)

THUMB_FILE_NAME = "thumb.png"
THUMB_SOCKET_NAME = "thumb"
THUMB_NODE_NAME = THUMB_FILE_NAME
THUMB_SCALE_NODE_NAME = "BManga_ThumbScale"
DEFAULT_THUMB_SCALE_PERCENTAGE = 12.5
LAST_RENDER_TIME_PROP = "_bmanga_last_thumb_render_time"
LAST_RENDER_PATH_PROP = "_bmanga_last_thumb_render_path"


def expected_thumb_path_for_current_file() -> Path | None:
    """現在の cNN.blend に対応する ``thumb.png`` のパスを返す."""
    filepath = str(getattr(bpy.data, "filepath", "") or "")
    if not filepath:
        return None
    blend_path = Path(filepath)
    if blend_path.name != f"{blend_path.parent.name}.blend":
        return None
    coma_id = blend_path.parent.name
    page_id = blend_path.parent.parent.name
    if not paths.is_valid_coma_id(coma_id) or not paths.is_valid_page_id(page_id):
        return None
    return blend_path.parent / THUMB_FILE_NAME


def ensure_thumb_output_node(scene=None) -> bool:
    """現在の Scene に ``thumb.png`` 用のファイル出力ノードを用意する.

    レンダリング設定や解像度は変更しない。縮小は自動レンダリング時に
    Blender の「解像度スケール」で行い、コンポジター上には縮小用ノードを
    挟まない。
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
    _remove_legacy_thumb_scale_node(tree, socket)
    return True


def resolve_thumb_scale_percentage(scene) -> float:
    """B-MANGA の「コマ画像縮小率」を % 値として返す.

    値が読めない / 範囲外なら 12.5% を採用する。
    """
    work = getattr(scene, "bmanga_work", None)
    if work is None:
        return DEFAULT_THUMB_SCALE_PERCENTAGE
    try:
        pct = float(
            getattr(
                work,
                "page_preview_scale_percentage",
                DEFAULT_THUMB_SCALE_PERCENTAGE,
            )
            or DEFAULT_THUMB_SCALE_PERCENTAGE
        )
    except (TypeError, ValueError):
        pct = DEFAULT_THUMB_SCALE_PERCENTAGE
    pct = max(1.0, min(100.0, pct))
    return pct


def _resolve_thumb_scale_factor(scene) -> float:
    """互換用: 「コマ画像縮小率」を 0..1 の倍率で返す."""
    return resolve_thumb_scale_percentage(scene) / 100.0


def _resolution_scale_int(percentage: float) -> int:
    return max(1, min(32767, int(math.floor(float(percentage) + 0.5))))


def _ensure_compositor_tree(scene):
    tree = getattr(scene, "compositing_node_group", None)
    if tree is None:
        try:
            tree = bpy.data.node_groups.new("B-MANGA Thumbnail", "CompositorNodeTree")
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

    既にユーザーが任意のソースを接続している場合は温存する。B-MANGA が以前
    追加していた縮小用 Scale ノードから来ている場合だけ、Scale 入力側の
    ソースへバイパスする。
    """
    try:
        existing = next((link for link in tree.links if link.to_socket == socket), None)
        if existing is not None:
            source = _source_before_legacy_scale(tree, existing)
            if source is None:
                if _is_legacy_scale_node(getattr(existing, "from_node", None)):
                    try:
                        tree.links.remove(existing)
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    return
            else:
                try:
                    tree.links.remove(existing)
                except Exception:  # noqa: BLE001
                    pass
                tree.links.new(source, socket)
                return
        image_socket = _find_image_output(render_layers)
        if image_socket is not None:
            existing = next((link for link in tree.links if link.to_socket == socket), None)
            if existing is not None:
                try:
                    tree.links.remove(existing)
                except Exception:  # noqa: BLE001
                    pass
            try:
                tree.links.new(image_socket, socket)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        _logger.exception("thumb output: link setup failed")


def _source_before_legacy_scale(tree, link):
    from_node = getattr(link, "from_node", None)
    if from_node is None:
        return None
    if not _is_legacy_scale_node(from_node):
        return None
    scale_input = from_node.inputs[0] if len(from_node.inputs) > 0 else None
    if scale_input is None:
        return None
    incoming = next((item for item in tree.links if item.to_socket == scale_input), None)
    return incoming.from_socket if incoming is not None else None


def _is_legacy_scale_node(node) -> bool:
    return (
        getattr(node, "bl_idname", "") == "CompositorNodeScale"
        and (
            getattr(node, "name", "") == THUMB_SCALE_NODE_NAME
            or getattr(node, "label", "") == THUMB_SCALE_NODE_NAME
        )
    )


def _remove_legacy_thumb_scale_node(tree, thumb_socket) -> None:
    """旧版の縮小用 Scale ノードを、未使用なら取り除く."""
    try:
        for node in list(tree.nodes):
            if not _is_legacy_scale_node(node):
                continue
            output_socket_ptrs = {int(sock.as_pointer()) for sock in node.outputs}
            still_used = any(
                int(link.from_socket.as_pointer()) in output_socket_ptrs
                and link.to_socket != thumb_socket
                for link in tree.links
            )
            if still_used:
                continue
            tree.nodes.remove(node)
    except Exception:  # noqa: BLE001
        _logger.exception("thumb output: legacy scale cleanup failed")


def _is_thumb_output_node(node) -> bool:
    return (
        getattr(node, "bl_idname", "") == "CompositorNodeOutputFile"
        and (
            getattr(node, "name", "") == THUMB_NODE_NAME
            or getattr(node, "label", "") == THUMB_NODE_NAME
        )
    )


def _mute_non_thumb_file_outputs(tree):
    states = []
    if tree is None:
        return states
    for node in tree.nodes:
        if getattr(node, "bl_idname", "") != "CompositorNodeOutputFile":
            continue
        states.append((node, bool(getattr(node, "mute", False))))
        try:
            node.mute = not _is_thumb_output_node(node)
        except Exception:  # noqa: BLE001
            pass
    return states


def _restore_node_mutes(states) -> None:
    for node, muted in states:
        try:
            node.mute = muted
        except Exception:  # noqa: BLE001
            pass


def _recent_render_is_usable(scene, path: Path | None, seconds: float) -> bool:
    if scene is None or path is None or seconds <= 0.0 or not path.is_file():
        return False
    try:
        last_time = float(scene.get(LAST_RENDER_TIME_PROP, 0.0) or 0.0)
    except (TypeError, ValueError):
        return False
    if time.time() - last_time > seconds:
        return False
    return str(scene.get(LAST_RENDER_PATH_PROP, "") or "") == str(path)


def render_thumb_png(context=None, *, skip_if_recent_seconds: float = 0.0) -> bool:
    """``thumb.png`` だけを現在の「コマ画像縮小率」でレンダリングする."""
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return False
    expected_path = expected_thumb_path_for_current_file()
    if _recent_render_is_usable(scene, expected_path, skip_if_recent_seconds):
        return True
    if not ensure_thumb_output_node(scene):
        return False
    tree = getattr(scene, "compositing_node_group", None)
    old_percentage = int(getattr(scene.render, "resolution_percentage", 100))
    new_percentage = _resolution_scale_int(resolve_thumb_scale_percentage(scene))
    mute_states = _mute_non_thumb_file_outputs(tree)
    try:
        scene.render.resolution_percentage = new_percentage
        bpy.ops.render.render(write_still=False)
        try:
            scene[LAST_RENDER_TIME_PROP] = time.time()
            scene[LAST_RENDER_PATH_PROP] = str(expected_path or "")
        except Exception:  # noqa: BLE001
            pass
        if expected_path is not None and not expected_path.is_file():
            _logger.warning("thumb output: expected file was not written: %s", expected_path)
            return False
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("thumb output: render failed")
        return False
    finally:
        try:
            scene.render.resolution_percentage = old_percentage
        except Exception:  # noqa: BLE001
            pass
        _restore_node_mutes(mute_states)


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
