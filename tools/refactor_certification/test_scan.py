"""Discover repository tests and their legacy AI-audit registration."""

from __future__ import annotations

import ast
from pathlib import Path

from .ids import test_id
from .model import TestCase


def test_files(root: Path) -> list[Path]:
    test_root = root / "test"
    return sorted(
        (path for path in test_root.glob("*.py") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _has_test_items(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                return True
        if isinstance(node, ast.ClassDef):
            if any("TestCase" in ast.unparse(base) for base in node.bases):
                return True
    return False


def execution_kind(path: Path, tree: ast.Module | None = None) -> str:
    """Classify every test-directory source without filename blind spots."""
    parsed = tree or ast.parse(
        path.read_text(encoding="utf-8-sig"),
        filename=str(path),
    )
    if path.name.startswith("blender_"):
        return "blender"
    if (
        path.name.startswith("test_")
        or path.name.endswith("_test.py")
        or _has_test_items(parsed)
    ):
        return "python"
    return "support"


def blender_test_files(root: Path) -> list[Path]:
    return [
        path
        for path in test_files(root)
        if execution_kind(path) == "blender"
    ]


def python_test_files(root: Path) -> list[Path]:
    return [
        path
        for path in test_files(root)
        if execution_kind(path) == "python"
    ]


def assert_unique_test_sources(root: Path) -> list[str]:
    """Return every discovered source and reject duplicate stable IDs."""
    sources = [path.relative_to(root).as_posix() for path in test_files(root)]
    ids = [test_id(source) for source in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate test IDs detected")
    return sources


def _is_main_guard(node: ast.If) -> bool:
    test = node.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def _entrypoint(tree: ast.Module) -> tuple[bool, str]:
    if any(isinstance(node, ast.If) and _is_main_guard(node) for node in tree.body):
        return True, "main_guard"
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            return True, "top_level_call"
        if isinstance(node, (ast.Assert, ast.Raise)):
            return True, "top_level_execution"
    functions = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]
    if functions:
        return True, "pytest_functions"
    return False, "none"


def _audit_registrations(root: Path) -> dict[str, list[str]]:
    path = root / "test" / "bmanga_ai_audit_runner.py"
    if not path.exists():
        return {}
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    registrations: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name != "AuditCase" or len(node.args) < 3:
            continue
        key_node, script_node = node.args[0], node.args[2]
        if not isinstance(key_node, ast.Constant) or not isinstance(script_node, ast.Constant):
            continue
        if not isinstance(key_node.value, str) or not isinstance(script_node.value, str):
            continue
        source = script_node.value.replace("\\", "/")
        registrations.setdefault(source, []).append(key_node.value)
    return {source: sorted(keys) for source, keys in registrations.items()}


def scan_tests(root: Path) -> tuple[list[TestCase], dict[str, str]]:
    registrations = _audit_registrations(root)
    cases: list[TestCase] = []
    texts: dict[str, str] = {}
    for path in test_files(root):
        source = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(text, filename=source)
        has_entrypoint, kind = _entrypoint(tree)
        runner = execution_kind(path, tree)
        audit_keys = registrations.get(source, [])
        case = TestCase(
            test_id=test_id(source),
            source=source,
            execution_kind=runner,
            entrypoint=has_entrypoint,
            entrypoint_kind=kind,
            audit_registered=bool(audit_keys),
            audit_keys=audit_keys,
        )
        cases.append(case)
        if runner != "support":
            texts[case.test_id] = text
    return cases, texts
