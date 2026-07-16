"""Undo方針の退行を静的に防ぐ契約テスト."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


FINAL_STATE_CONTRACTS = {
    "operators/text_op.py": {"_modal_dragging": "states_differ"},
    "operators/balloon_op.py": {"_finish_drag": "states_differ"},
    "operators/effect_line_op.py": {"_finish_drag": "states_differ"},
    "operators/object_tool_op.py": {
        "_finish_drag": "states_differ",
        "_confirm_free_transform": "states_differ",
    },
    "operators/handle_intercept.py": {"finish_drag": "states_differ"},
    "operators/object_tool_balloon_tail.py": {"point_drag_changed": "states_differ"},
    "operators/object_rotation.py": {"snapshots_changed": "states_differ"},
}

# 段階移行前から残る直接push。件数も固定し、新しい直接呼出しは許さない。
LEGACY_DIRECT_PUSH_COUNTS = {
    "operators/balloon_nurbs_tool_op.py": 1,
    "operators/coma_edge_drag_session.py": 1,
    "operators/coma_edge_move_op.py": 2,
    "operators/coma_knife_cut_op.py": 1,
    "operators/fill_tool_op.py": 1,
    "operators/gradient_tool_op.py": 1,
    "operators/image_path_tool_op.py": 1,
    "operators/layer_move_op.py": 1,
    "operators/page_reorder_drag_op.py": 1,
    "utils/undo_transaction.py": 1,
}


def _tree(relative: str) -> ast.AST:
    path = ROOT / relative
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _attribute_name(node: ast.AST) -> str:
    parts = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def test_micro_edit_paths_compare_final_state() -> None:
    for relative, functions in FINAL_STATE_CONTRACTS.items():
        tree = _tree(relative)
        found = {
            node.name: ast.unparse(node)
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in functions
        }
        assert set(found) == set(functions), relative
        for name, required_call in functions.items():
            assert required_call in found[name], f"{relative}:{name}"


def test_no_new_direct_undo_push_outside_common_helper() -> None:
    counts: Counter[str] = Counter()
    source_roots = (
        "core",
        "io",
        "operators",
        "panels",
        "ui",
        "utils",
        "typography",
        "addons",
    )
    paths = list(ROOT.glob("*.py"))
    for source_root in source_roots:
        paths.extend((ROOT / source_root).rglob("*.py"))
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        direct_count = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and _attribute_name(node.func) == "bpy.ops.ed.undo_push"
        )
        if direct_count:
            counts[relative] += direct_count
    assert dict(counts) == LEGACY_DIRECT_PUSH_COUNTS
