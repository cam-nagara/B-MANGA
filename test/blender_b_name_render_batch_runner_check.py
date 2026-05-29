"""連続実行ランナー（tools/render_batch/runner.py）のエンドツーエンド検証。

1) アドオンを有効化し、コンポジタ出力付きの小さなシーンとプリセットを作る
2) 一時 .blend に保存する
3) その .blend に対し blender --background ... --python runner.py -- --run
   を別プロセスで起動し（計測ログ env も渡す）
4) 結果サマリ JSON と計測ログ JSON を検証する
   - 結果が ok
   - 出力 PNG が生成された
   - 計測ログに renders[] があり、所要時間と出力ファイルが記録されている

Blender 内から実行する::

    blender --background --python test/blender_b_name_render_batch_runner_check.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import bpy

REPO_ROOT = Path(__file__).resolve().parents[1]
ADDON_PKG = "b_name_render"
RUNNER = REPO_ROOT / "tools" / "render_batch" / "runner.py"


def _enable_addon():
    addons_dir = REPO_ROOT / "addons"
    if str(addons_dir) not in sys.path:
        sys.path.insert(0, str(addons_dir))
    import importlib

    module = importlib.import_module(ADDON_PKG)
    if hasattr(module, "register"):
        try:
            module.register()
        except Exception:  # noqa: BLE001
            pass
    return module


def _make_preset(state, name: str, folder: str):
    preset = state.presets.add()
    preset.name = name
    for kind, setup in (
        ("STATE_BEGIN", None),
        ("SET_OUTPUT_FOLDER", lambda c: setattr(c, "folder_path", folder)),
        ("SET_OUTPUT_NAME", lambda c: setattr(c, "text_value", "runner_out")),
        ("RENDER", lambda c: (setattr(c, "engine", "BLENDER_EEVEE"), setattr(c, "sample_count", 1), setattr(c, "label_contains", "本番"))),
        ("STATE_END", None),
    ):
        cmd = preset.commands.add()
        cmd.command_type = kind
        cmd.enabled = True
        if setup:
            setup(cmd)
    return preset


def _compositor_tree(scene):
    """Blender 5.1 の新コンポジタ（compositing_node_group）優先で木を確保する。

    旧 ``scene.node_tree``/``use_nodes`` は 5.1 で廃止されたため、
    まず ``compositing_node_group`` を作って割り当てる。無い古い版では
    旧APIにフォールバックする。
    """
    if hasattr(scene, "compositing_node_group"):
        tree = scene.compositing_node_group
        if tree is None:
            tree = bpy.data.node_groups.new("BNameBatchComp", "CompositorNodeTree")
            scene.compositing_node_group = tree
        return tree
    scene.use_nodes = True
    return scene.node_tree


def _build_scene(out_folder: str) -> None:
    scene = bpy.context.scene
    tree = _compositor_tree(scene)
    nodes = tree.nodes
    links = tree.links
    for node in list(nodes):
        nodes.remove(node)
    rlayers = nodes.new("CompositorNodeRLayers")
    out = nodes.new("CompositorNodeOutputFile")
    # Blender 5.1 は出力先が ``directory`` ＋ ``file_output_items``。
    # 旧版は ``base_path`` ＋ ``file_slots``。両対応する。
    if hasattr(out, "directory"):
        out.directory = out_folder
    if hasattr(out, "base_path"):
        out.base_path = out_folder
    if hasattr(out, "file_output_items"):
        items = out.file_output_items
        if not items:
            items.new("RGBA", "Image")
    elif hasattr(out, "file_slots") and not out.file_slots:
        out.file_slots.new("Image")
    # 出力ノードの最初の画像入力を RenderLayers の Image に接続する。
    image_socket = rlayers.outputs.get("Image")
    target_input = next((s for s in out.inputs if getattr(s, "enabled", True)), out.inputs[0])
    links.new(image_socket, target_input)
    scene.render.resolution_x = 64
    scene.render.resolution_y = 64
    scene.render.resolution_percentage = 100
    _make_preset(scene.bname_render_state, "fixture", out_folder)


def _blender_exe() -> str:
    return str(bpy.app.binary_path or "")


def _run() -> int:
    _enable_addon()

    work = Path(tempfile.mkdtemp(prefix="bname_runner_"))
    out_folder = str(work / "out")
    Path(out_folder).mkdir(parents=True, exist_ok=True)
    blend_path = str(work / "fixture.blend")
    result_path = str(work / "result.json")
    log_path = str(work / "timing.json")

    _build_scene(out_folder)
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)

    env = dict(os.environ)
    env["BNAME_BATCH_LOG"] = log_path

    cmd = [
        _blender_exe(),
        "--background",
        "--factory-startup",
        blend_path,
        "--python",
        str(RUNNER),
        "--",
        "--run",
        "--preset",
        "fixture",
        "--result",
        result_path,
        "--addon-dir",
        str(REPO_ROOT / "addons"),
    ]
    print(f"[batch-runner] launching: {cmd}")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
    print("[batch-runner] --- child stdout ---")
    print(proc.stdout)
    if proc.stderr.strip():
        print("[batch-runner] --- child stderr ---")
        print(proc.stderr)

    ok = True

    # 1) 結果サマリ
    if not Path(result_path).exists():
        print("[batch-runner] FAIL: result.json が無い")
        return 1
    result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    print(f"[batch-runner] result={result}")
    if not result.get("ok"):
        print(f"[batch-runner] FAIL: 実行が失敗: {result.get('error')}")
        ok = False

    # 2) PNG 生成
    produced = sorted(Path(out_folder).rglob("*.png"))
    print(f"[batch-runner] produced={[str(p) for p in produced]}")
    if not produced:
        print("[batch-runner] FAIL: PNG が出ていない")
        ok = False

    # 3) 計測ログ
    if not Path(log_path).exists():
        print("[batch-runner] FAIL: 計測ログ timing.json が無い")
        return 1
    log = json.loads(Path(log_path).read_text(encoding="utf-8"))
    print(f"[batch-runner] timing={json.dumps(log, ensure_ascii=False)}")
    if not log.get("renders"):
        print("[batch-runner] FAIL: renders[] が空")
        ok = False
    else:
        r0 = log["renders"][0]
        if not (r0.get("elapsed_seconds", 0) >= 0 and "started_at" in r0):
            print("[batch-runner] FAIL: レンダー計測が不正")
            ok = False
        if not r0.get("outputs"):
            print("[batch-runner] WARN: outputs 空（生成ファイル検出に失敗）")
    if not (log.get("elapsed_seconds", 0) >= 0 and log.get("finished_at")):
        print("[batch-runner] FAIL: プリセット全体の計測が不正")
        ok = False

    print("[batch-runner] OK" if ok else "[batch-runner] FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_run())
