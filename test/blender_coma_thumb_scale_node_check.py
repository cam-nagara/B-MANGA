"""Test that thumb.png output is direct and uses render resolution scale."""
from __future__ import annotations

import sys
import types
import importlib.util
import os.path

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _build_pkg():
    pkg = types.ModuleType("bnt")
    pkg.__path__ = [ROOT]
    sys.modules["bnt"] = pkg
    for sub in ("utils", "core"):
        m = types.ModuleType(f"bnt.{sub}")
        m.__path__ = [f"{ROOT}/{sub}"]
        sys.modules[f"bnt.{sub}"] = m


def _load(qn, p):
    s = importlib.util.spec_from_file_location(qn, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[qn] = m
    s.loader.exec_module(m)
    return m


def _setup_module():
    _build_pkg()
    _load("bnt.utils.log", f"{ROOT}/utils/log.py")
    return _load("bnt.utils.coma_thumb_output", f"{ROOT}/utils/coma_thumb_output.py")


def main() -> int:
    cto = _setup_module()
    scene = bpy.context.scene
    # Step 1: With no bmanga_work, default factor is 12.5%
    factor = cto._resolve_thumb_scale_factor(scene)
    assert abs(factor - 0.125) < 1e-6, f"expected 0.125, got {factor}"
    print(f"[ok] default scale factor (no work): {factor}")

    # Step 2: ensure_thumb_output_node creates File Output without Scale node
    ok = cto.ensure_thumb_output_node(scene)
    assert ok, "ensure_thumb_output_node returned False"
    tree = scene.compositing_node_group or scene.node_tree
    assert tree is not None, "compositor tree missing"

    # Find output
    scale_nodes = [
        n for n in tree.nodes
        if n.bl_idname == "CompositorNodeScale" and n.name == cto.THUMB_SCALE_NODE_NAME
    ]
    output_nodes = [n for n in tree.nodes if n.bl_idname == "CompositorNodeOutputFile"]
    rl_nodes = [n for n in tree.nodes if n.bl_idname == "CompositorNodeRLayers"]
    assert not scale_nodes, "legacy B-MANGA scale node should not be created"
    assert output_nodes, "output node missing"
    assert rl_nodes, "RLayers node missing"
    output = output_nodes[0]
    rl = rl_nodes[0]

    # Step 3: Link path RLayers → Output thumb socket
    # Find thumb socket
    thumb_sock = None
    for sock in output.inputs:
        if sock.name == cto.THUMB_SOCKET_NAME:
            thumb_sock = sock
            break
    assert thumb_sock is not None, "thumb socket missing"
    # Walk links from thumb backward
    incoming = [l for l in tree.links if l.to_socket == thumb_sock]
    assert len(incoming) == 1, f"expected 1 link to thumb socket, got {len(incoming)}"
    assert incoming[0].from_node.name == rl.name, \
        f"link to thumb socket should come from RLayers, got {incoming[0].from_node.name}"
    print("[ok] thumb socket ← RLayers")

    # Step 4: idempotent — calling again doesn't duplicate, AND keeps the
    # RLayers→thumb link intact (= 保存時の save_pre 再実行で断線しない)。
    # bpy_struct の identity は再アクセスごとに別 Python obj になり得るため、
    # ``link.from_node is scale_node`` 比較が壊れて再呼び出しで誤って既存
    # リンクを除去する回帰があった。 ここで明示的に保持を検証する。
    n_before = len(tree.nodes)
    cto.ensure_thumb_output_node(scene)
    cto.ensure_thumb_output_node(scene)
    n_after = len(tree.nodes)
    assert n_before == n_after, f"node count changed: {n_before} → {n_after}"
    print(f"[ok] idempotent: {n_after} nodes")
    incoming = [l for l in tree.links if l.to_socket == thumb_sock]
    assert len(incoming) == 1, (
        f"thumb socket should still have exactly 1 incoming link, got {len(incoming)}"
    )
    assert incoming[0].from_node.name == rl.name, (
        f"thumb socket should still be from RLayers, got {incoming[0].from_node.name}"
    )
    print("[ok] thumb socket ← RLayers still intact after re-call")

    # Step 5: user manually changes source to a non-RLayers node — preserved directly
    # Add a BrightContrast node (Image output) as user's custom source
    user_src = tree.nodes.new("CompositorNodeBrightContrast")
    user_src.location = (-400, -100)
    for l in list(tree.links):
        if l.to_socket == thumb_sock:
            tree.links.remove(l)
    # Manually connect user source to thumb socket
    tree.links.new(user_src.outputs[0], thumb_sock)
    # Call ensure again — should keep user source direct
    cto.ensure_thumb_output_node(scene)
    # Now: user_src → thumb
    incoming = [l for l in tree.links if l.to_socket == thumb_sock]
    assert len(incoming) == 1
    assert incoming[0].from_node.name == user_src.name
    print("[ok] user's source preserved directly")

    # Step 6: legacy B-MANGA Scale → thumb は、Scale 入力のソース → thumb へ
    # バイパスされ、未使用 Scale は取り除かれる。
    legacy_scale = tree.nodes.new("CompositorNodeScale")
    legacy_scale.name = cto.THUMB_SCALE_NODE_NAME
    legacy_scale.label = cto.THUMB_SCALE_NODE_NAME
    for l in list(tree.links):
        if l.to_socket == thumb_sock:
            tree.links.remove(l)
    tree.links.new(user_src.outputs[0], legacy_scale.inputs[0])
    tree.links.new(legacy_scale.outputs[0], thumb_sock)
    cto.ensure_thumb_output_node(scene)
    incoming = [l for l in tree.links if l.to_socket == thumb_sock]
    assert len(incoming) == 1
    assert incoming[0].from_node.name == user_src.name
    assert not any(n.name == cto.THUMB_SCALE_NODE_NAME for n in tree.nodes)
    print("[ok] legacy Scale bypassed and removed")

    # Step 7: 入力が切れた旧 Scale が残っていても、既定の RLayers に戻す。
    legacy_scale = tree.nodes.new("CompositorNodeScale")
    legacy_scale.name = cto.THUMB_SCALE_NODE_NAME
    legacy_scale.label = cto.THUMB_SCALE_NODE_NAME
    for l in list(tree.links):
        if l.to_socket == thumb_sock:
            tree.links.remove(l)
    tree.links.new(legacy_scale.outputs[0], thumb_sock)
    cto.ensure_thumb_output_node(scene)
    incoming = [l for l in tree.links if l.to_socket == thumb_sock]
    assert len(incoming) == 1
    assert incoming[0].from_node.name == rl.name
    assert not any(n.name == cto.THUMB_SCALE_NODE_NAME for n in tree.nodes)
    print("[ok] dangling legacy Scale replaced with RLayers")

    # Step 8: 非 thumb の File Output は自動サムネレンダー中だけ mute 対象。
    other = tree.nodes.new("CompositorNodeOutputFile")
    other.name = "other.png"
    other.label = "other.png"
    other.mute = False
    states = cto._mute_non_thumb_file_outputs(tree)
    assert output.mute is False
    assert other.mute is True
    cto._restore_node_mutes(states)
    assert other.mute is False
    print("[ok] non-thumb file outputs muted/restored")

    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
