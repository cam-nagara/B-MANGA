"""AST/JSON extraction for B-MANGA, Render, and Line product features."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .ids import (
    canonical_feature_id,
    canonical_field_id,
    feature_id,
    field_id,
)
from .model import Feature


SKIP_DIRS = {
    ".git",
    ".worktrees",
    "__pycache__",
    "test",
    "tests",
    "tools",
    "wheels",
    "_verify",
}
MAIN_DIRS = (
    "core",
    "io",
    "keymap",
    "operators",
    "panels",
    "presets",
    "typography",
    "ui",
    "utils",
)
TARGET_ROOTS = (
    ("bmanga", Path(".")),
    ("render", Path("addons/b_manga_render")),
    ("line", Path("addons/b_manga_line")),
)
PROPERTY_CALL_SUFFIX = "Property"
EXPORT_FORMATS = ("PNG", "JPEG", "TIFF", "PSD", "PDF")


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _target_for_path(relative: str) -> str:
    for target, target_root in TARGET_ROOTS[1:]:
        prefix = target_root.as_posix() + "/"
        if relative == target_root.as_posix() or relative.startswith(prefix):
            return target
    return "bmanga"


def product_python_files(root: Path) -> list[Path]:
    paths = [root / "__init__.py", root / "preferences.py"]
    for dirname in MAIN_DIRS:
        base = root / dirname
        if base.exists():
            paths.extend(base.rglob("*.py"))
    for _, relative in TARGET_ROOTS[1:]:
        base = root / relative
        if base.exists():
            paths.extend(base.rglob("*.py"))
    return sorted(
        {
            path
            for path in paths
            if path.is_file()
            and not any(part in SKIP_DIRS for part in path.relative_to(root).parts)
        },
        key=lambda item: _relative(root, item),
    )


def product_preset_json_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for _, relative in TARGET_ROOTS:
        base = root / relative
        if not base.exists():
            continue
        candidates.extend(
            path
            for path in base.rglob("*.json")
            if any(part.lower() == "presets" for part in path.parts)
        )
    return sorted(
        {
            path
            for path in candidates
            if path.is_file()
            and not any(part in SKIP_DIRS for part in path.relative_to(root).parts)
        },
        key=lambda item: _relative(root, item),
    )


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _constant(node: ast.AST | None, default: Any = "") -> Any:
    if node is None:
        return default
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return default


def _class_values(node: ast.ClassDef) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for child in node.body:
        if isinstance(child, ast.Assign):
            value = _constant(child.value)
            for target in child.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = value
    return values


def _base_names(node: ast.ClassDef) -> set[str]:
    return {_name(base).split(".")[-1] for base in node.bases}


def _call_keywords(call: ast.Call) -> dict[str, Any]:
    return {
        keyword.arg: _constant(keyword.value)
        for keyword in call.keywords
        if keyword.arg
    }


def _property_call(node: ast.AST | None) -> ast.Call | None:
    if not isinstance(node, ast.Call):
        return None
    if _name(node.func).split(".")[-1].lower().endswith(
        PROPERTY_CALL_SUFFIX.lower()
    ):
        return node
    return None


def _iter_class_properties(node: ast.ClassDef) -> Iterable[tuple[str, ast.Call, int]]:
    for child in node.body:
        if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
            call = _property_call(child.value) or _property_call(child.annotation)
            if call:
                yield child.target.id, call, child.lineno
        elif isinstance(child, ast.Assign):
            call = _property_call(child.value)
            if not call:
                continue
            for target in child.targets:
                if isinstance(target, ast.Name):
                    yield target.id, call, child.lineno


def _feature(
    kind: str,
    target: str,
    source: str,
    symbol: str,
    line: int,
    **values: Any,
) -> Feature:
    semantic_key = str(values.pop("semantic_key", symbol))
    legacy_id = feature_id(kind, target, source, symbol)
    return Feature(
        feature_id=canonical_feature_id(kind, target, semantic_key),
        aliases=[legacy_id],
        kind=kind,
        target=target,
        source=source,
        symbol=symbol,
        line=line,
        **values,
    )


def _signature_key(prefix: str, values: Iterable[str]) -> str:
    payload = "\n".join(sorted(str(value) for value in values))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}.{digest}"


def _class_semantic_key(
    kind: str,
    node: ast.ClassDef,
    values: dict[str, Any],
) -> str:
    bl_idname = str(values.get("bl_idname", "")).strip()
    if bl_idname:
        return bl_idname
    if kind == "panel":
        parts = (
            values.get("bl_label", ""),
            values.get("bl_space_type", ""),
            values.get("bl_region_type", ""),
            values.get("bl_category", ""),
        )
        return _signature_key("panel", (str(part) for part in parts))
    fields = [name for name, _call, _line in _iter_class_properties(node)]
    return _signature_key("property-group", fields)


def _class_features(
    target: str, source: str, node: ast.ClassDef
) -> list[Feature]:
    bases = _base_names(node)
    values = _class_values(node)
    found: list[Feature] = []
    for base, kind in (
        ("Operator", "operator"),
        ("Panel", "panel"),
        ("PropertyGroup", "property_group"),
    ):
        if base not in bases:
            continue
        bl_idname = str(values.get("bl_idname", ""))
        label = str(values.get("bl_label", node.name))
        found.append(
            _feature(
                kind,
                target,
                source,
                node.name,
                node.lineno,
                label=label,
                bl_idname=bl_idname,
                ui_location=str(values.get("bl_category", "")),
                metadata={
                    "bases": sorted(bases),
                    "bl_options": sorted(values.get("bl_options", ())),
                },
                semantic_key=_class_semantic_key(kind, node, values),
            )
        )
    found.extend(_property_features(target, source, node, bases))
    return found


def _property_features(
    target: str,
    source: str,
    node: ast.ClassDef,
    bases: set[str],
) -> list[Feature]:
    found: list[Feature] = []
    owner_is_property_group = "PropertyGroup" in bases
    values = _class_values(node)
    owner_key = _class_semantic_key("property_group", node, values)
    for name, call, line in _iter_class_properties(node):
        property_type = _name(call.func).split(".")[-1]
        keywords = _call_keywords(call)
        keyword_names = {keyword.arg for keyword in call.keywords if keyword.arg}
        options = keywords.get("options", ())
        if isinstance(options, (set, tuple, list)):
            options = sorted(options)
        symbol = f"{node.name}.{name}"
        found.append(
            _feature(
                "property",
                target,
                source,
                symbol,
                line,
                label=str(keywords.get("name", name)),
                field_id=canonical_field_id(target, owner_key, name),
                field_aliases=[field_id(target, source, node.name, name)],
                property_type=property_type,
                metadata={
                    "owner_key": owner_key,
                    "owner_is_property_group": owner_is_property_group,
                    "owner_name": node.name,
                    "owner_bases": sorted(bases),
                    "default": keywords.get("default", ""),
                    "min": keywords.get("min", ""),
                    "max": keywords.get("max", ""),
                    "items": keywords.get("items", ""),
                    "subtype": keywords.get("subtype", ""),
                    "unit": keywords.get("unit", ""),
                    "options": options,
                    "has_get": "get" in keyword_names,
                    "has_set": "set" in keyword_names,
                },
                semantic_key=f"{owner_key}.{name}",
            )
        )
    return found


def _string_literals(tree: ast.Module) -> set[str]:
    return {
        str(node.value).upper()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _export_format_features(
    target: str,
    source: str,
    tree: ast.Module,
) -> list[Feature]:
    literals = _string_literals(tree)
    source_upper = source.upper()
    found: list[Feature] = []
    for format_name in EXPORT_FORMATS:
        if format_name not in literals and format_name not in source_upper:
            continue
        found.append(
            _feature(
                "export",
                target,
                source,
                f"format:{format_name}",
                1,
                label=format_name,
                semantic_key=f"format.{format_name}",
                metadata={"export_role": "format", "format": format_name},
            )
        )
    return found


def _operator_secondary_features(feature: Feature) -> list[Feature]:
    bl_idname = feature.bl_idname.lower()
    found: list[Feature] = []
    is_export_entry = "export" in bl_idname and "preset" not in bl_idname
    if is_export_entry:
        found.append(
            _feature(
                "export",
                feature.target,
                feature.source,
                feature.symbol,
                feature.line,
                label=feature.label,
                bl_idname=feature.bl_idname,
                semantic_key=f"entry.{feature.bl_idname}",
                metadata={"operator_feature_id": feature.feature_id, "export_role": "entry"},
            )
        )
        searchable = f"{feature.bl_idname} {feature.label}".upper()
        for format_name in EXPORT_FORMATS:
            if format_name not in searchable:
                continue
            found.append(
                _feature(
                    "export",
                    feature.target,
                    feature.source,
                    f"format:{format_name}",
                    feature.line,
                    label=format_name,
                    semantic_key=f"format.{format_name}",
                    metadata={"export_role": "format", "format": format_name},
                )
            )
    return found


def _builtin_preset_features(
    target: str,
    source: str,
    tree: ast.Module,
) -> list[Feature]:
    found: list[Feature] = []
    family = Path(source).stem
    for node in tree.body:
        target_node = None
        value_node = None
        if isinstance(node, ast.AnnAssign):
            target_node, value_node = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target_node, value_node = node.targets[0], node.value
        if (
            not isinstance(target_node, ast.Name)
            or target_node.id != "BUILTIN_PRESETS"
            or not isinstance(value_node, ast.Dict)
        ):
            continue
        for key in value_node.keys:
            name = _constant(key)
            if not isinstance(name, str):
                continue
            found.append(
                _feature(
                    "preset",
                    target,
                    source,
                    f"BUILTIN_PRESETS[{name}]",
                    getattr(key, "lineno", node.lineno),
                    label=name,
                    semantic_key=f"item.{family}.{name}",
                    metadata={
                        "preset_role": "item",
                        "family": family,
                        "source_kind": "python_builtin",
                    },
                )
            )
    return found


def _shortcut_call(
    target: str,
    source: str,
    node: ast.Call,
    index: int,
) -> Feature | None:
    call_name = _name(node.func)
    terminal_name = call_name.rsplit(".", 1)[-1]
    direct = call_name.endswith("keymap_items.new")
    wrapper = (
        "keymap" in source.lower()
        and terminal_name in {"_add", "_add_window"}
        and node.args
        and isinstance(_constant(node.args[0]), str)
        and "." in str(_constant(node.args[0]))
    )
    if not direct and not wrapper:
        return None
    operator = str(_constant(node.args[0], "")) if node.args else ""
    keywords = _call_keywords(node)
    key_type = str(
        keywords.get("type", _constant(node.args[1], "") if len(node.args) > 1 else "")
    )
    value = str(
        keywords.get("value", _constant(node.args[2], "") if len(node.args) > 2 else "")
    )
    modifiers = {
        name: bool(keywords.get(name, False)) for name in ("ctrl", "shift", "alt")
    }
    semantic_parts = [
        operator or "unknown",
        key_type or "unknown",
        value or "unknown",
        *(name if enabled else "" for name, enabled in modifiers.items()),
    ]
    return _feature(
        "shortcut",
        target,
        source,
        f"{operator or 'unknown'}@{node.lineno}.{index}",
        node.lineno,
        bl_idname=operator,
        metadata={
            "type": key_type,
            "value": value,
            **modifiers,
        },
        semantic_key=".".join(semantic_parts),
    )


def _tuple_shortcuts(
    target: str,
    source: str,
    tree: ast.Module,
    start_index: int,
) -> list[Feature]:
    found: list[Feature] = []
    index = start_index
    if "keymap" in source.lower():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Tuple) or len(node.elts) < 2:
                continue
            operator = _constant(node.elts[0])
            key_type = _constant(node.elts[1])
            if (
                not isinstance(operator, str)
                or not operator.startswith("bmanga.")
                or not isinstance(key_type, str)
            ):
                continue
            index += 1
            symbol = f"{operator}@{node.lineno}.{index}"
            found.append(
                _feature(
                    "shortcut",
                    target,
                    source,
                    symbol,
                    node.lineno,
                    bl_idname=operator,
                    metadata={"type": key_type, "value": "PRESS", "declared_tuple": True},
                    semantic_key=f"{operator}.{key_type}.PRESS",
                )
            )
    return found


def _shortcut_features(target: str, source: str, tree: ast.Module) -> list[Feature]:
    found: list[Feature] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        feature = _shortcut_call(target, source, node, len(found) + 1)
        if feature is not None:
            found.append(feature)
    found.extend(_tuple_shortcuts(target, source, tree, len(found)))
    return found


def scan_python(root: Path, path: Path) -> list[Feature]:
    source = _relative(root, path)
    target = _target_for_path(source)
    text = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(text, filename=source)
    found: list[Feature] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            found.extend(_class_features(target, source, node))
    found.extend(_export_format_features(target, source, tree))
    found.extend(_builtin_preset_features(target, source, tree))
    found.extend(_shortcut_features(target, source, tree))
    for item in list(found):
        if item.kind == "operator":
            found.extend(_operator_secondary_features(item))
    return found


def scan_preset_json(root: Path, path: Path) -> Feature:
    source = _relative(root, path)
    target = _target_for_path(source)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    metadata = {
        "json_type": type(data).__name__,
        "top_level_keys": sorted(data) if isinstance(data, dict) else [],
    }
    preset_type = str(data.get("presetType", "generic")) if isinstance(data, dict) else "generic"
    preset_name = (
        str(data.get("presetName") or data.get("name") or path.stem)
        if isinstance(data, dict)
        else path.stem
    )
    return _feature(
        "preset",
        target,
        source,
        path.stem,
        1,
        label=preset_name,
        metadata=metadata,
        semantic_key=f"item.{preset_type}.{preset_name}",
    )


def _module_constant(tree: ast.Module, name: str, default: Any) -> Any:
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == name
            for target in targets
        ):
            continue
        return _constant(node.value, default)
    return default


def _draft_property(
    source: str,
    name: str,
    source_feature: Feature | None,
) -> Feature:
    class_name = "BMangaLineSettingsDraft"
    owner_key = "runtime.line.settings.draft"
    proxy_field_id = source_feature.field_id if source_feature else ""
    proxy_alias = (
        source_feature.field_aliases[0]
        if source_feature and source_feature.field_aliases
        else ""
    )
    metadata = {
        "owner_key": owner_key,
        "owner_is_property_group": True,
        "owner_name": class_name,
        "owner_bases": ["PropertyGroup"],
        "state_class": "window_manager_transient_proxy",
        "contract_owner": "WindowManager",
        "proxy_field_id": proxy_field_id,
        "proxy_field_alias": proxy_alias,
        "proxy_symbol": (
            source_feature.symbol if source_feature else "Scene.bmanga_line_camera"
        ),
    }
    return _feature(
        "property",
        "line",
        source,
        f"{class_name}.{name}",
        261,
        label=source_feature.label if source_feature else "別カメラ指定",
        field_id=proxy_field_id
        or canonical_field_id("line", owner_key, str(name)),
        field_aliases=[field_id("line", source, class_name, str(name))],
        property_type=(
            source_feature.property_type if source_feature else "PointerProperty"
        ),
        metadata=metadata,
        semantic_key=f"{owner_key}.{name}",
    )


def _line_draft_features(root: Path, features: list[Feature]) -> list[Feature]:
    path = root / "addons" / "b_manga_line" / "settings_draft.py"
    if not path.is_file():
        return []
    source = _relative(root, path)
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=source)
    names = tuple(_module_constant(tree, "DRAFT_FIELDS", ()))
    names += (str(_module_constant(tree, "CAMERA_FIELD", "")),)
    base = {
        feature.symbol.rsplit(".", 1)[-1]: feature
        for feature in features
        if feature.kind == "property"
        and feature.target == "line"
        and feature.symbol.startswith("BMangaLineSettings.")
    }
    found = [
        _feature(
            "property_group",
            "line",
            source,
            "BMangaLineSettingsDraft",
            261,
            label="BMangaLineSettingsDraft",
            metadata={
                "bases": ["PropertyGroup"],
                "state_class": "window_manager_transient_proxy",
                "contract_owner": "WindowManager",
                "generated_from": "BMangaLineSettings",
            },
            semantic_key="runtime.line.settings.draft",
        )
    ]
    found.extend(
        _draft_property(source, str(name), base.get(str(name)))
        for name in names
        if name
    )
    return found


def scan_product_features(root: Path) -> list[Feature]:
    found: list[Feature] = []
    for path in product_python_files(root):
        found.extend(scan_python(root, path))
    found.extend(_line_draft_features(root, found))
    found.extend(scan_preset_json(root, path) for path in product_preset_json_files(root))
    unique: dict[str, Feature] = {}
    for item in found:
        existing = unique.get(item.feature_id)
        if existing is None:
            unique[item.feature_id] = item
            continue
        existing.aliases = sorted(set(existing.aliases + item.aliases))
        existing.field_aliases = sorted(
            set(existing.field_aliases + item.field_aliases)
        )
    return [unique[key] for key in sorted(unique)]
