"""Phase 1: 静的Operator入力と実登録RNA/``bpy.ops``署名を照合する。"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.refactor_certification.source_scan import (
    product_python_files,
    scan_product_features,
)


OUT_PATH = Path(
    os.environ.get(
        "BMANGA_PHASE1_OPERATOR_SIGNATURE_OUT",
        str(
            ROOT
            / "_verify"
            / "2026-07-29_full_refactor_phase1"
            / "operator_signature.json"
        ),
    )
)
PACKAGES = (
    ("bmanga_phase1_main", ROOT),
    ("bmanga_phase1_render", ROOT / "addons" / "b_manga_render"),
    ("bmanga_phase1_line", ROOT / "addons" / "b_manga_line"),
)
TYPE_MAP = {
    "BoolProperty": "BOOLEAN",
    "CollectionProperty": "COLLECTION",
    "EnumProperty": "ENUM",
    "FloatProperty": "FLOAT",
    "FloatVectorProperty": "FLOAT",
    "IntProperty": "INT",
    "IntVectorProperty": "INT",
    "PointerProperty": "POINTER",
    "StringProperty": "STRING",
}
FILE_HELPER_INPUTS = {
    "ImportHelper": {"directory", "filepath", "files"},
    "ExportHelper": {"check_existing", "filepath"},
}


def _load_package(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(
        name,
        path / "__init__.py",
        submodule_search_locations=[str(path)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"パッケージを読み込めません: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _static_inputs() -> dict[str, dict[str, dict[str, object]]]:
    features = scan_product_features(ROOT)
    grouped: dict[str, dict[str, dict[str, object]]] = {
        str(feature.bl_idname): {}
        for feature in features
        if feature.kind == "operator" and str(feature.bl_idname)
    }
    for feature in features:
        metadata = feature.metadata
        if (
            feature.kind != "property"
            or "Operator" not in metadata.get("owner_bases", [])
        ):
            continue
        idname = str(metadata["owner_key"])
        identifier = str(feature.symbol).rsplit(".", 1)[-1]
        grouped.setdefault(idname, {})[identifier] = {
            "feature_id": feature.feature_id,
            "property_type": feature.property_type,
        }
    return grouped


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _static_bpy_ops_calls() -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []
    for path in product_python_files(ROOT):
        tree = ast.parse(
            path.read_text(encoding="utf-8-sig"),
            filename=str(path),
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _name(node.func)
            parts = name.split(".")
            if len(parts) != 4 or parts[:2] != ["bpy", "ops"]:
                continue
            dynamic_keywords = sum(keyword.arg is None for keyword in node.keywords)
            calls.append(
                {
                    "bl_idname": ".".join(parts[2:]),
                    "source": path.relative_to(ROOT).as_posix(),
                    "line": int(node.lineno),
                    "keywords": sorted(
                        keyword.arg for keyword in node.keywords if keyword.arg
                    ),
                    "dynamic_keywords": dynamic_keywords,
                }
            )
    return sorted(
        calls,
        key=lambda row: (
            str(row["source"]),
            int(row["line"]),
            str(row["bl_idname"]),
        ),
    )


def _operator_wrapper(idname: str):
    namespace, operator = idname.split(".", 1)
    return getattr(getattr(bpy.ops, namespace), operator)


def _rna_input_rows(idname: str) -> tuple[dict[str, object], set[str]]:
    rna = _operator_wrapper(idname).get_rna_type()
    cls = getattr(bpy.types, rna.identifier, None)
    helpers = (
        {base.__name__ for base in cls.__mro__} & set(FILE_HELPER_INPUTS)
        if cls is not None
        else set()
    )
    rows = {
        prop.identifier: prop
        for prop in rna.properties
        if prop.identifier != "rna_type" and bool(prop.is_runtime)
    }
    return rows, helpers


def _known_mixin_inputs(helpers: set[str]) -> set[str]:
    result: set[str] = set()
    for helper in helpers:
        result.update(FILE_HELPER_INPUTS[helper])
    return result


def _validate_property(
    idname: str,
    identifier: str,
    feature: dict[str, object],
    runtime_prop,
) -> str:
    expected = TYPE_MAP.get(str(feature["property_type"]))
    if expected is None:
        return f"未対応Property型 {feature['property_type']}"
    if runtime_prop.type != expected:
        return f"RNA型 {runtime_prop.type} != {expected}"
    if runtime_prop.is_readonly and expected != "COLLECTION":
        return "呼出し入力がreadonly"
    return ""


def _compare_operator(
    idname: str,
    expected: dict[str, dict[str, object]],
) -> dict[str, object]:
    try:
        actual, helpers = _rna_input_rows(idname)
    except KeyError:
        return {
            "bl_idname": idname,
            "inputs": sorted(expected),
            "input_count": len(expected),
            "operator_missing": True,
            "missing": sorted(expected),
            "unexpected": [],
            "type_errors": [],
        }
    missing = sorted(set(expected) - set(actual))
    mixin_inputs = sorted((set(actual) - set(expected)) & _known_mixin_inputs(helpers))
    unexpected = sorted(set(actual) - set(expected) - set(mixin_inputs))
    type_errors = []
    for identifier, feature in expected.items():
        if identifier not in actual:
            continue
        error = _validate_property(idname, identifier, feature, actual[identifier])
        if error:
            type_errors.append(f"{identifier}: {error}")
    return {
        "bl_idname": idname,
        "inputs": sorted(expected),
        "input_count": len(expected),
        "operator_missing": False,
        "missing": missing,
        "mixin_inputs": mixin_inputs,
        "unexpected": unexpected,
        "type_errors": type_errors,
    }


def _compare_call(call: dict[str, object]) -> dict[str, object]:
    row = dict(call)
    idname = str(call["bl_idname"])
    try:
        actual, _helpers = _rna_input_rows(idname)
    except (AttributeError, KeyError, RuntimeError) as exc:
        row["operator_missing"] = True
        row["unexpected_inputs"] = list(call["keywords"])
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row
    row["operator_missing"] = False
    row["unexpected_inputs"] = sorted(set(call["keywords"]) - set(actual))
    row["error"] = ""
    return row


def _run() -> None:
    packages = []
    try:
        for name, path in PACKAGES:
            module = _load_package(name, path)
            module.register()
            packages.append(module)
        expected = _static_inputs()
        rows = [
            _compare_operator(idname, fields)
            for idname, fields in sorted(expected.items())
        ]
        calls = [_compare_call(call) for call in _static_bpy_ops_calls()]
        operator_missing = [row["bl_idname"] for row in rows if row["operator_missing"]]
        missing_count = sum(len(row["missing"]) for row in rows)
        unexpected_count = sum(len(row["unexpected"]) for row in rows)
        type_error_count = sum(len(row["type_errors"]) for row in rows)
        call_operator_missing = [
            f"{row['source']}:{row['line']}:{row['bl_idname']}"
            for row in calls
            if row["operator_missing"]
        ]
        call_input_errors = [
            (
                f"{row['source']}:{row['line']}:{row['bl_idname']}:"
                f"{','.join(row['unexpected_inputs'])}"
            )
            for row in calls
            if row["unexpected_inputs"]
        ]
        dynamic_call_keywords = [
            f"{row['source']}:{row['line']}:{row['bl_idname']}"
            for row in calls
            if int(row["dynamic_keywords"]) > 0
        ]
        payload = {
            "schema_version": 1,
            "operators": rows,
            "operator_count": len(rows),
            "input_count": sum(row["input_count"] for row in rows),
            "calls": calls,
            "call_count": len(calls),
            "operator_missing": operator_missing,
            "unresolved_missing": missing_count,
            "unresolved_unexpected": unexpected_count,
            "unresolved_type_errors": type_error_count,
            "call_operator_missing": call_operator_missing,
            "unresolved_call_inputs": call_input_errors,
            "dynamic_call_keywords": dynamic_call_keywords,
        }
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        if (
            operator_missing
            or missing_count
            or unexpected_count
            or type_error_count
            or call_operator_missing
            or call_input_errors
            or dynamic_call_keywords
        ):
            raise AssertionError(
                "Operator署名差分 "
                f"operators={len(operator_missing)} missing={missing_count} "
                f"unexpected={unexpected_count} types={type_error_count} "
                f"call_operators={len(call_operator_missing)} "
                f"call_inputs={len(call_input_errors)} "
                f"dynamic_calls={len(dynamic_call_keywords)}; "
                f"details={OUT_PATH}"
            )
        print(
            "BMANGA_PHASE1_OPERATOR_SIGNATURE_CHECK_OK "
            f"operators={payload['operator_count']} "
            f"inputs={payload['input_count']} calls={payload['call_count']}",
            flush=True,
        )
    finally:
        for module in reversed(packages):
            try:
                module.unregister()
            except Exception:
                traceback.print_exc()


if __name__ == "__main__":
    try:
        _run()
    except Exception:
        traceback.print_exc()
        raise
