"""詳細drawerの表示条件とenabled条件を静的抽出する。"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any


_DRAW_CALLS = {"prop_if", "prop_pair"}


def _source(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "<unparseable>"


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _is_descendant(node: ast.AST, roots: list[ast.stmt]) -> bool:
    return any(node is root or node in set(ast.walk(root)) for root in roots)


def _conditions(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> tuple[str, ...]:
    result: list[str] = []
    current = node
    while current in parents:
        parent = parents[current]
        if isinstance(parent, ast.If):
            test = _source(parent.test)
            if _is_descendant(current, parent.orelse):
                test = f"not ({test})"
            result.append(test)
        current = parent
    return tuple(reversed(result))


def _constant_field(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _binding_rows(
    path: Path,
    tree: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for call in (item for item in ast.walk(tree) if isinstance(item, ast.Call)):
        call_name = getattr(call.func, "id", "")
        attribute = getattr(call.func, "attr", "")
        fields: list[ast.AST] = []
        owner = None
        if call_name == "prop_if" and len(call.args) >= 3:
            owner, fields = call.args[1], [call.args[2]]
        elif call_name == "prop_pair" and len(call.args) >= 4:
            owner, fields = call.args[1], [call.args[2], call.args[3]]
        elif attribute == "prop" and len(call.args) >= 2:
            owner, fields = call.args[0], [call.args[1]]
        if owner is None:
            continue
        current = call
        enclosing = ""
        while current in parents:
            current = parents[current]
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                enclosing = current.name
                break
        if enclosing in {"prop", "prop_if", "prop_pair"}:
            continue
        for field in fields:
            field_name = _constant_field(field)
            expression = field_name or _source(field)
            custom_property = '["' in expression and '"]' in expression
            rows.append(
                {
                    "source": path.as_posix(),
                    "line": call.lineno,
                    "call": call_name or attribute,
                    "owner_expression": _source(owner),
                    "field_name": field_name,
                    "field_expression": expression,
                    "visibility_conditions": list(_conditions(call, parents)),
                    "custom_property": custom_property,
                }
            )
    return rows


def _enabled_rows(
    path: Path,
    tree: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> list[dict[str, Any]]:
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        for target in targets:
            if not isinstance(target, ast.Attribute) or target.attr != "enabled":
                continue
            rows.append(
                {
                    "source": path.as_posix(),
                    "line": node.lineno,
                    "layout_expression": _source(target.value),
                    "enabled_expression": _source(value),
                    "visibility_conditions": list(_conditions(node, parents)),
                }
            )
    return rows


def _field_candidates(
    row: dict[str, Any],
    specs: list[dict[str, Any]],
) -> list[str]:
    name = row["field_name"]
    if name:
        return sorted(
            spec["field_id"]
            for spec in specs
            if spec["field_name"] == name
        )
    expression = row["field_expression"]
    if row["custom_property"]:
        return []
    literal_parts = re.findall(r"[A-Za-z][A-Za-z0-9_]+", expression)
    suffixes = tuple(part for part in literal_parts if "_" in part)
    if not suffixes:
        return []
    return sorted(
        spec["field_id"]
        for spec in specs
        if any(part in spec["field_name"] for part in suffixes)
    )


def scan_detail_ui(root: Path, specs: list[dict[str, Any]]) -> dict[str, Any]:
    bindings = []
    enabled_rules = []
    drawer_root = root / "panels" / "detail_drawers"
    for path in sorted(drawer_root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = _parent_map(tree)
        relative = path.relative_to(root)
        for row in _binding_rows(relative, tree, parents):
            row["candidate_field_ids"] = _field_candidates(row, specs)
            if row["custom_property"]:
                row["resolution_contract"] = "custom_property"
            elif row["candidate_field_ids"]:
                row["resolution_contract"] = "field_spec"
            elif row["field_name"]:
                row["resolution_contract"] = "blender_builtin_rna"
            else:
                row["resolution_contract"] = "dynamic_runtime_resolved"
            bindings.append(row)
        enabled_rules.extend(_enabled_rows(relative, tree, parents))
    return {
        "binding_count": len(bindings),
        "enabled_rule_count": len(enabled_rules),
        "bindings": sorted(
            bindings,
            key=lambda row: (row["source"], row["line"], row["field_expression"]),
        ),
        "enabled_rules": sorted(
            enabled_rules,
            key=lambda row: (row["source"], row["line"]),
        ),
    }
