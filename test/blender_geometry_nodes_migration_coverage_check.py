"""Blender実機用: フキダシ/効果線のノード移植項目を棚卸しする."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from collections import deque
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / ".codex" / "geometry_nodes_migration"
OUT_JSON = OUT_DIR / "migration_coverage.json"
OUT_MD = OUT_DIR / "migration_coverage.md"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_gn_migration",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_gn_migration"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _node_links(group):
    links_from = {}
    for link in group.links:
        if not getattr(link, "is_valid", True):
            continue
        links_from.setdefault(link.from_socket, []).append(link.to_socket)
    return links_from


def _reachable_to_geometry(group, input_socket) -> bool:
    links_from = _node_links(group)
    seen = set()
    queue = deque([input_socket])
    while queue:
        socket = queue.popleft()
        if socket in seen:
            continue
        seen.add(socket)
        for to_socket in links_from.get(socket, []):
            node = to_socket.node
            if getattr(node, "bl_idname", "") == "NodeGroupOutput" and getattr(to_socket, "name", "") == "Geometry":
                return True
            for output in getattr(node, "outputs", []) or []:
                queue.append(output)
    return False


def _input_sockets(group):
    input_node = next(node for node in group.nodes if getattr(node, "bl_idname", "") == "NodeGroupInput")
    return {
        str(getattr(socket, "name", "") or ""): socket
        for socket in getattr(input_node, "outputs", []) or []
    }


def _coverage_for_group(bridge, kind: str) -> dict:
    group = bridge.ensure_node_group(kind)
    sockets = _input_sockets(group)
    specs = [spec.name for spec in bridge._GROUP_SOCKETS[kind]]
    non_geometry = {"線素材", "塗り素材", "始点コマ枠オブジェクト"}
    reachable = []
    passthrough_only = []
    missing = []
    for name in specs:
        if name in non_geometry:
            continue
        socket = sockets.get(name)
        if socket is None:
            missing.append(name)
            continue
        if _reachable_to_geometry(group, socket):
            reachable.append(name)
        else:
            passthrough_only.append(name)
    return {
        "kind": kind,
        "reachable": reachable,
        "passthrough_only": passthrough_only,
        "missing": missing,
    }


def _common_shape_usage(bridge) -> dict:
    common_name = getattr(bridge, "_COMMON_SHAPE_GROUP_NAME", "")
    common_group = bpy.data.node_groups.get(common_name)
    usage = {"balloon": 0, "effect_line": 0}
    if common_group is None:
        return {
            "group_name": common_name,
            "exists": False,
            "usage": usage,
            "missing_inputs": [],
        }
    expected_inputs = [
        spec.name
        for spec in getattr(bridge, "_COMMON_SHAPE_INPUT_SOCKETS", ())
    ]
    input_node = next(
        node for node in common_group.nodes if getattr(node, "bl_idname", "") == "NodeGroupInput"
    )
    actual_inputs = {
        str(getattr(socket, "name", "") or "")
        for socket in getattr(input_node, "outputs", []) or []
    }
    for kind in usage:
        group = bridge.ensure_node_group(kind)
        for node in group.nodes:
            if getattr(node, "bl_idname", "") == "GeometryNodeGroup" and getattr(node, "node_tree", None) is common_group:
                usage[kind] += 1
    return {
        "group_name": common_name,
        "exists": True,
        "usage": usage,
        "missing_inputs": [name for name in expected_inputs if name not in actual_inputs],
    }


def _write_report(results: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Geometry Nodes 移植棚卸し", ""]
    for result in results:
        if result["kind"] == "common_shape_usage":
            lines.append("## 共通形状グループ")
            lines.append("")
            lines.append(f"- グループ: {result['group_name']}")
            lines.append(f"- 存在: {'あり' if result['exists'] else 'なし'}")
            lines.append(f"- フキダシからの利用数: {result['usage'].get('balloon', 0)}")
            lines.append(f"- 効果線からの利用数: {result['usage'].get('effect_line', 0)}")
            lines.append("### 入力欠落")
            if result["missing_inputs"]:
                for name in result["missing_inputs"]:
                    lines.append(f"- {name}")
            else:
                lines.append("- なし")
            lines.append("")
            continue
        title = "効果線" if result["kind"] == "effect_line" else "フキダシ"
        lines.append(f"## {title}")
        lines.append("")
        lines.append("### 生成結果へ接続済み")
        for name in result["reachable"]:
            lines.append(f"- {name}")
        lines.append("")
        lines.append("### 入力はあるが生成結果への接続を要確認")
        for name in result["passthrough_only"]:
            lines.append(f"- {name}")
        lines.append("")
        lines.append("### 入力欠落")
        for name in result["missing"]:
            lines.append(f"- {name}")
        if not result["missing"]:
            lines.append("- なし")
        lines.append("")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    mod = None
    temp_root = Path(tempfile.mkdtemp(prefix="bname_gn_migration_"))
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "MigrationCoverage.bname"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")
        from bname_dev_gn_migration.utils import geometry_nodes_bridge as bridge

        results = [
            _coverage_for_group(bridge, "effect_line"),
            _coverage_for_group(bridge, "balloon"),
        ]
        common_usage = _common_shape_usage(bridge)
        results.append({"kind": "common_shape_usage", **common_usage})
        _write_report(results)
        bad_missing = [result for result in results if result.get("missing")]
        if not common_usage["exists"]:
            bad_missing.append({"kind": "common_shape_usage", "missing": [common_usage["group_name"]]})
        if common_usage["missing_inputs"]:
            bad_missing.append({"kind": "common_shape_usage", "missing": common_usage["missing_inputs"]})
        if int(common_usage["usage"].get("balloon", 0)) < 1:
            bad_missing.append({"kind": "common_shape_usage", "missing": ["フキダシの共通形状利用"]})
        if int(common_usage["usage"].get("effect_line", 0)) < 2:
            bad_missing.append({"kind": "common_shape_usage", "missing": ["効果線の始点/終点共通形状利用"]})
        if bad_missing:
            raise AssertionError(f"ノード入力が欠落しています: {bad_missing}")
        print(f"BNAME_GEOMETRY_NODES_MIGRATION_COVERAGE_OK {OUT_MD}", flush=True)
    finally:
        if mod is not None:
            mod.unregister()


if __name__ == "__main__":
    main()
