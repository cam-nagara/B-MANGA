"""Blender実機用: c00.blend の実ノードを使った B-MANGA Render 完全連動監査."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import zlib
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BLEND = Path(r"D:\TM Dropbox\Share\B-MANGA\c_file\c00.blend")


def _load_render_package():
    package_root = ROOT / "addons" / "b_manga_render"
    spec = importlib.util.spec_from_file_location(
        "bmanga_render_full_flow",
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_render_full_flow"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _png(path: Path, seed: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width = 16
    height = 16
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row.extend(((seed * 37 + x * 11) % 256, (seed * 53 + y * 17) % 256, (seed * 71 + x + y) % 256, 255))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    payload = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", payload) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _safe_name(value: str) -> str:
    return re.sub(r"[\\/:*?\"<>|\s]+", "_", str(value or "").strip()) or "render"


def _iter_output_nodes(render_mod, scene):
    for node in render_mod.command_runner._iter_all_nodes(scene):
        if getattr(node, "type", "") == "OUTPUT_FILE":
            yield node


def _node_file_name(node) -> str:
    name = str(getattr(node, "file_name", "") or "")
    if name:
        return name
    for item in getattr(node, "file_output_items", []) or []:
        item_name = str(getattr(item, "name", "") or "")
        if item_name:
            return item_name
    for socket in getattr(node, "inputs", []) or []:
        socket_name = str(getattr(socket, "name", "") or "")
        if socket_name:
            return socket_name
    return str(getattr(node, "label", "") or getattr(node, "name", "") or "render")


def _node_directory(node, fallback: Path) -> Path:
    directory = str(getattr(node, "directory", "") or getattr(node, "base_path", "") or "")
    if directory:
        return Path(bpy.path.abspath(directory))
    return fallback


def _required_output_pairs(render_mod) -> list[tuple[str, str, str]]:
    pairs = []
    kinds = {
        "SET_OUTPUT_GROUP",
        "RENDER_LAYER",
        "FISHEYE_RENDER_IMAGE_OR_LAYER",
        "FISHEYE_RENDER_FACES_OR_LAYER",
        "FISHEYE_ASSEMBLE_OR_LAYER",
    }
    for preset_name, commands in render_mod.preset_library.BUILTIN_PRESETS.items():
        if render_mod.core.preset_category_of(preset_name) == "LEGACY":
            continue
        for command in commands:
            if command.get("command_type", "") in kinds:
                pairs.append((preset_name, command.get("node_group_name", ""), command.get("label_contains", "")))
    return pairs


def _assert_output_pairs_match(render_mod) -> dict[str, int]:
    counts: dict[str, int] = {}
    missing = []
    for preset_name, group_name, label in _required_output_pairs(render_mod):
        group = bpy.data.node_groups.get(group_name)
        count = 0
        if group is not None:
            for node in render_mod.command_runner._iter_nodes_recursive(group):
                if getattr(node, "type", "") == "OUTPUT_FILE" and render_mod.command_runner._node_matches_label(node, label):
                    count += 1
        key = f"{preset_name} / {group_name} / {label}"
        counts[key] = count
        if count <= 0:
            missing.append(key)
    assert not missing, missing
    return counts


def _find_material() -> bpy.types.Material:
    material = bpy.data.materials.get("マテリアル方舟") or bpy.data.materials.get("マテリアル方舟.001")
    assert material is not None and getattr(material, "use_nodes", False), "マテリアル方舟 が見つかりません"
    return material


def _add_probe_mesh(collection_name: str, material: bpy.types.Material, index: int) -> None:
    coll = bpy.data.collections.get(collection_name)
    if coll is None:
        coll = bpy.data.collections.new(collection_name)
        bpy.context.scene.collection.children.link(coll)
    mesh = bpy.data.meshes.new(f"BMANGA_RENDER_FULL_FLOW_MESH_{collection_name}")
    size = 0.35
    x = (index % 4 - 1.5) * 0.9
    z = (index // 4) * 0.5
    mesh.from_pydata(
        [(x - size, 0, z - size), (x + size, 0, z - size), (x, 0, z + size)],
        [],
        [(0, 1, 2)],
    )
    mesh.update()
    obj = bpy.data.objects.new(f"BMANGA_RENDER_FULL_FLOW_{collection_name}", mesh)
    obj.data.materials.append(material)
    coll.objects.link(obj)


def _prepare_probe_scene(scene) -> None:
    material = _find_material().copy()
    for index, name in enumerate(("キャラ", "キャラアルファ", "背景", "背景MH", "効果", "効果アルファ", "レイアウト", "アタリ", "空", "植物", "エフェクト")):
        _add_probe_mesh(name, material, index)
    if scene.camera is None:
        cam_data = bpy.data.cameras.new("B-MANGA Render完全連動監査カメラ")
        cam = bpy.data.objects.new("B-MANGA Render完全連動監査カメラ", cam_data)
        scene.collection.objects.link(cam)
        scene.camera = cam
    scene.render.resolution_x = 96
    scene.render.resolution_y = 96
    scene.render.resolution_percentage = 100


def _patch_rendering(render_mod, output_root: Path):
    command_runner = render_mod.command_runner
    eevr_bridge = render_mod.eevr_bridge
    generated: dict[str, str] = {}
    calls: list[str] = []
    original_render = command_runner._render
    original_image = eevr_bridge.render_image
    original_faces = eevr_bridge.render_faces
    original_assemble = eevr_bridge.assemble_images
    original_reload = command_runner._reload_images
    output_nodes = list(_iter_output_nodes(render_mod, bpy.context.scene))

    def fake_render(scene, engine: str, sample_count: int) -> None:
        calls.append(f"{engine}:{sample_count}")
        nodes = [node for node in output_nodes if not bool(getattr(node, "mute", False))]
        if not nodes:
            path = output_root / "render_result" / f"render_{len(generated):03d}.png"
            _png(path, len(generated) + 1)
            generated[str(path)] = "Render Result"
            return
        for node in nodes[:12]:
            folder = _node_directory(node, output_root)
            path = folder / f"{_safe_name(_node_file_name(node))}.png"
            _png(path, len(generated) + 1)
            generated[str(path)] = str(getattr(node, "label", "") or getattr(node, "name", ""))

    def fake_eevr(kind: str):
        def run():
            path = output_root / "fisheye" / f"{kind}_{len(generated):03d}.png"
            _png(path, len(generated) + 1)
            generated[str(path)] = kind
            calls.append(kind)
            return {"FINISHED"}

        return run

    command_runner._render = fake_render
    command_runner._reload_images = lambda: 0
    eevr_bridge.render_image = fake_eevr("魚眼")
    eevr_bridge.render_faces = fake_eevr("方向")
    eevr_bridge.assemble_images = fake_eevr("合成")
    return generated, calls, (original_render, original_image, original_faces, original_assemble, original_reload)


def _restore_rendering(render_mod, originals) -> None:
    render_mod.command_runner._render = originals[0]
    render_mod.eevr_bridge.render_image = originals[1]
    render_mod.eevr_bridge.render_faces = originals[2]
    render_mod.eevr_bridge.assemble_images = originals[3]
    render_mod.command_runner._reload_images = originals[4]


def main() -> None:
    blend_path = Path(os.environ.get("BMANGA_C00_BLEND", str(DEFAULT_BLEND)))
    if not blend_path.exists():
        raise FileNotFoundError(blend_path)
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_render_full_flow_"))
    render = None
    try:
        bpy.ops.wm.open_mainfile(filepath=str(blend_path))
        render = _load_render_package()
        scene = bpy.context.scene
        _prepare_probe_scene(scene)
        output_root = temp_root / "passes"
        render.command_runner._set_output_folder(scene, str(output_root))
        output_pair_counts = _assert_output_pairs_match(render)
        aov_counts = {
            "キャラ/落ち影切替": render.command_runner._set_aov_input("キャラ", "落ち影切替", 1),
            "キャラ/透過切替": render.command_runner._set_aov_input("キャラ", "透過切替", 1),
            "背景MH/落ち影切替": render.command_runner._set_aov_input("背景MH", "落ち影切替", 1),
        }
        assert all(count > 0 for count in aov_counts.values()), aov_counts

        bpy.ops.bmanga_render.load_builtin_presets(reset=True)
        state = scene.bmanga_render_state
        generated, calls, originals = _patch_rendering(render, output_root)
        errors: dict[str, str] = {}
        try:
            target_presets = {
                "キャラ",
                "キャラpen",
                "キャラ統合",
                "背景",
                "背景pen",
                "背景統合",
                "効果",
                "効果統合",
            }
            for index, preset in enumerate(state.presets):
                if preset.name not in target_presets:
                    continue
                state.active_preset_index = index
                try:
                    render.command_runner.run_active_preset(bpy.context)
                except Exception as exc:  # noqa: BLE001
                    errors[preset.name] = str(exc)
        finally:
            _restore_rendering(render, originals)
            render.command_runner._restore_session(scene)
        assert not errors, errors
        assert generated, "出力画像が生成されていません"
        leaked = [path for path in generated if str(blend_path.parent) in path and str(output_root) not in path]
        assert not leaked, leaked

        payload = {
            "blend": str(blend_path),
            "preset_count": len(target_presets),
            "render_call_count": len(calls),
            "generated_count": len(generated),
            "aov_counts": aov_counts,
            "output_pair_match_counts": output_pair_counts,
            "sample_outputs": dict(list(generated.items())[:20]),
        }
        out_dir = Path(os.environ.get("BMANGA_RENDER_FULL_FLOW_OUT", "")) if os.environ.get("BMANGA_RENDER_FULL_FLOW_OUT") else temp_root
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "b_manga_render_c00_full_flow.json"
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"BMANGA_RENDER_C00_FULL_FLOW_OK {out_path}")
    finally:
        if render is not None:
            try:
                render.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if not os.environ.get("BMANGA_RENDER_FULL_FLOW_OUT"):
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
