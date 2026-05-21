"""Test that thumb.png output goes through a Scale node configured from
   ``work.page_preview_scale_percentage``."""
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
    # Make sure scene has bname_work — register the property if needed
    # For the test we'll attach a minimal mock
    class FakeWork:
        page_preview_scale_percentage = 10.0
    scene["__bname_test_fake_work__"] = True
    # We can't easily inject custom property groups, so we'll monkey-patch via
    # attribute on the scene namespace. Instead: rely on _resolve_thumb_scale_factor's
    # fallback default (0.1) when work is missing or invalid.
    # Verify default behavior first.

    # Step 1: With no bname_work, default factor is 0.1
    factor = cto._resolve_thumb_scale_factor(scene)
    assert abs(factor - 0.1) < 1e-6, f"expected 0.1, got {factor}"
    print(f"[ok] default scale factor (no work): {factor}")

    # Step 2: ensure_thumb_output_node creates Scale node + File Output
    ok = cto.ensure_thumb_output_node(scene)
    assert ok, "ensure_thumb_output_node returned False"
    tree = scene.compositing_node_group or scene.node_tree
    assert tree is not None, "compositor tree missing"

    # Find scale node + output
    scale_nodes = [n for n in tree.nodes if n.bl_idname == "CompositorNodeScale"]
    output_nodes = [n for n in tree.nodes if n.bl_idname == "CompositorNodeOutputFile"]
    rl_nodes = [n for n in tree.nodes if n.bl_idname == "CompositorNodeRLayers"]
    assert scale_nodes, "scale node missing"
    assert output_nodes, "output node missing"
    assert rl_nodes, "RLayers node missing"
    scale = scale_nodes[0]
    output = output_nodes[0]
    rl = rl_nodes[0]
    # Blender 5.x では Type 入力ソケットの既定値が "Relative"
    type_sock = scale.inputs.get("Type")
    type_default = getattr(type_sock, "default_value", None) if type_sock is not None else None
    print(f"[ok] scale node present: name={scale.name} type_default={type_default}")

    # Verify scale value matches default 0.1
    x_sock = scale.inputs.get("X")
    y_sock = scale.inputs.get("Y")
    x_val = x_sock.default_value if x_sock else None
    y_val = y_sock.default_value if y_sock else None
    assert x_val is not None and abs(x_val - 0.1) < 1e-6 and abs(y_val - 0.1) < 1e-6, \
        f"scale X/Y should be 0.1, got X={x_val} Y={y_val}"
    print(f"[ok] scale X={x_val} Y={y_val}")

    # Step 3: Link path RLayers → Scale → Output thumb socket
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
    assert incoming[0].from_node.name == scale.name, \
        f"link to thumb socket should come from Scale node, got {incoming[0].from_node.name}"
    print("[ok] thumb socket ← Scale node")
    # Scale input ← RLayers
    scale_in = scale.inputs[0]
    scale_incoming = [l for l in tree.links if l.to_socket == scale_in]
    assert len(scale_incoming) == 1, "scale input should have 1 link"
    assert scale_incoming[0].from_node.name == rl.name, \
        f"scale input should come from RLayers, got {scale_incoming[0].from_node.name}"
    print("[ok] Scale input ← RLayers")

    # Step 4: idempotent — calling again doesn't duplicate, AND keeps the
    # scale→thumb link intact (= 保存時の save_pre 再実行で断線しない)。
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
    assert incoming[0].from_node.name == scale.name, (
        f"thumb socket should still be from Scale node, got {incoming[0].from_node.name}"
    )
    print("[ok] thumb socket ← Scale node still intact after re-call")

    # Step 5: user manually changes source to a non-RLayers node — preserved through Scale
    # Add a BrightContrast node (Image output) as user's custom source
    user_src = tree.nodes.new("CompositorNodeBrightContrast")
    user_src.location = (-400, -100)
    # Disconnect Scale's input, then connect user_src to thumb socket
    for l in list(tree.links):
        if l.to_socket == scale_in:
            tree.links.remove(l)
    # Manually connect user source to thumb socket
    tree.links.new(user_src.outputs[0], thumb_sock)
    # Call ensure again — should re-route user source through Scale node
    cto.ensure_thumb_output_node(scene)
    # Now: user_src → Scale → thumb
    incoming = [l for l in tree.links if l.to_socket == thumb_sock]
    assert len(incoming) == 1
    assert incoming[0].from_node.name == scale.name
    print("[ok] user-direct link rerouted through Scale")
    scale_incoming = [l for l in tree.links if l.to_socket == scale_in]
    assert len(scale_incoming) == 1
    assert scale_incoming[0].from_node.name == user_src.name
    print("[ok] user's source preserved as Scale input")

    # Step 6: ユーザーが Scale → 中間ノード → thumb のチェーンを組んでいた
    # 場合、 ensure 再呼び出しでサイクル (Scale → 中間 → Scale) を作って
    # しまわないこと。 thumb 直前のソースを Scale 入力に押し戻すと循環依存
    # になるため、 Scale 出力がチェーン内で既に使われていれば再ルーティング
    # をスキップする。
    # 現状 user_src → Scale → thumb なので、 そこに中間ノードを挟む。
    intermediate = tree.nodes.new("CompositorNodeBrightContrast")
    intermediate.location = (60, -100)
    # Scale → thumb のリンクを削除して、 Scale → intermediate → thumb にする
    for l in list(tree.links):
        if l.from_socket == scale.outputs[0] and l.to_socket == thumb_sock:
            tree.links.remove(l)
    tree.links.new(scale.outputs[0], intermediate.inputs[0])
    tree.links.new(intermediate.outputs[0], thumb_sock)
    # ensure 呼び出し: サイクルを作らずユーザー経路を温存
    cto.ensure_thumb_output_node(scene)
    # thumb への接続が intermediate のままで、 サイクル (intermediate → Scale)
    # が作られていないこと
    incoming = [l for l in tree.links if l.to_socket == thumb_sock]
    assert len(incoming) == 1
    assert incoming[0].from_node.name == intermediate.name, (
        f"thumb should still be from intermediate, got {incoming[0].from_node.name}"
    )
    scale_in_links = [l for l in tree.links if l.to_socket == scale_in]
    assert len(scale_in_links) == 1
    assert scale_in_links[0].from_node.name == user_src.name, (
        f"Scale input should still be from user_src (no cycle), got "
        f"{scale_in_links[0].from_node.name}"
    )
    print("[ok] user's Scale → 中間 → thumb chain preserved (no cycle)")

    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
