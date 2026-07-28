"""Phase 0: Blender 5.2登録後のRNA・キーマップ実測台帳を出力する。

静的ASTでは拾えない動的PropertyGroup、実行時追加Property、設定由来の
KeyMapItemを、実際に3アドオンを登録した状態から収集する。
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sys
import traceback
from collections import Counter
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.refactor_certification.ids import (
    canonical_feature_id,
    canonical_field_id,
    feature_id as legacy_feature_id,
    field_id as legacy_field_id,
    target_from_bl_idname,
)
from tools.refactor_certification.contracts import freeze_contracts
from tools.refactor_certification.registry import apply_registry, load_registry
from tools.refactor_certification.source_scan import scan_product_features
OUT_PATH = Path(
    os.environ.get(
        "BMANGA_PHASE0_RUNTIME_OUT",
        str(ROOT / "_verify" / "2026-07-28_full_refactor_phase0" / "runtime_catalog.json"),
    )
)
PACKAGES = (
    ("bmanga_phase0_main", ROOT),
    ("bmanga_phase0_render", ROOT / "addons" / "b_manga_render"),
    ("bmanga_phase0_line", ROOT / "addons" / "b_manga_line"),
)
RNA_BASES = (
    bpy.types.Operator,
    bpy.types.Panel,
    bpy.types.PropertyGroup,
    bpy.types.Menu,
    bpy.types.UIList,
    bpy.types.AddonPreferences,
)
RUNTIME_OWNERS = (
    bpy.types.Scene,
    bpy.types.WindowManager,
    bpy.types.Object,
    bpy.types.Collection,
    bpy.types.Material,
    bpy.types.Image,
    bpy.types.ViewLayer,
)
RUNTIME_OWNER_STATE_OVERRIDES = {
    ("Scene", "bmanga_work"): "persistent_domain",
    ("Scene", "bmanga_raster_layers"): "persistent_domain",
    ("Scene", "bmanga_image_layers"): "persistent_domain",
    ("Scene", "bmanga_image_path_layers"): "persistent_domain",
    ("Scene", "bmanga_fill_layers"): "persistent_domain",
    ("Scene", "bmanga_effect_line_params"): "multi_context_projection",
    ("Scene", "bmanga_layer_stack"): "derived_display",
    ("Scene", "bmanga_layer_stack_visible"): "derived_display",
    ("Scene", "bmanga_layer_stack_inline_edit_uid"): "derived_display",
    ("Scene", "bmanga_line_presets"): "user_preset",
    ("Scene", "bmanga_line_preset_name"): "user_preset",
}
RUNTIME_SCENE_SESSION_PROPERTIES = {
    "bmanga_active_effect_layer_name",
    "bmanga_active_fill_layer_index",
    "bmanga_active_image_layer_index",
    "bmanga_active_image_path_layer_index",
    "bmanga_active_layer_folder_key",
    "bmanga_active_layer_kind",
    "bmanga_active_layer_stack_index",
    "bmanga_active_layer_stack_visible_index",
    "bmanga_active_page_number",
    "bmanga_active_raster_layer_index",
    "bmanga_collapsed_balloon_group_keys",
    "bmanga_coma_camera_resolution_settings_index",
    "bmanga_current_coma_id",
    "bmanga_current_coma_page_id",
    "bmanga_current_page_id",
    "bmanga_line_mesh_optimize_error",
    "bmanga_line_mesh_optimize_result",
    "bmanga_line_preset_index",
    "bmanga_line_quad_repair_error",
    "bmanga_line_quad_repair_result",
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


def _package_classes(prefix: str) -> list[type]:
    found: dict[tuple[str, str], type] = {}
    for module_name, module in tuple(sys.modules.items()):
        if module is None or not (module_name == prefix or module_name.startswith(f"{prefix}.")):
            continue
        for value in vars(module).values():
            if not inspect.isclass(value):
                continue
            try:
                is_rna = any(issubclass(value, base) for base in RNA_BASES)
            except TypeError:
                continue
            if is_rna and value not in RNA_BASES:
                found[(value.__module__, value.__qualname__)] = value
    return [found[key] for key in sorted(found)]


def _rna_properties(cls: type) -> list[dict[str, object]]:
    rna = getattr(cls, "bl_rna", None)
    if rna is None:
        return []
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for prop in rna.properties:
        if prop.identifier == "rna_type" or prop.identifier in seen:
            continue
        seen.add(prop.identifier)
        rows.append(
            {
                "identifier": prop.identifier,
                "name": prop.name,
                "type": prop.type,
                "is_readonly": bool(prop.is_readonly),
                "is_runtime": bool(prop.is_runtime),
            }
        )
    return rows


def _class_target(cls: type, fallback: str) -> str:
    return target_from_bl_idname(str(getattr(cls, "bl_idname", "")), fallback)


def _runtime_source(cls: type) -> str:
    path = inspect.getsourcefile(cls)
    if not path:
        return "<runtime>"
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return "<runtime>"


def _class_semantic_key(cls: type, kind: str, properties: list[dict[str, object]]) -> str:
    bl_idname = str(getattr(cls, "bl_idname", "")).strip()
    if bl_idname:
        return bl_idname
    values = []
    if kind == "Panel":
        values = [
            str(getattr(cls, name, ""))
            for name in ("bl_label", "bl_space_type", "bl_region_type", "bl_category")
        ]
        prefix = "panel"
    else:
        values = [
            str(prop["identifier"])
            for prop in properties
            if kind != "PropertyGroup" or prop["identifier"] != "name"
        ]
        prefix = (
            "property-group"
            if kind == "PropertyGroup"
            else kind.replace(" ", "-").lower()
        )
    import hashlib

    digest = hashlib.sha256("\n".join(sorted(values)).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}.{digest}"


def _property_records(
    cls: type,
    properties: list[dict[str, object]],
    target: str,
    source: str,
    semantic_key: str,
    feature_aliases: dict[str, str],
    field_aliases: dict[str, str],
) -> list[dict[str, object]]:
    property_rows = []
    for prop in properties:
        identifier = str(prop["identifier"])
        property_symbol = f"{cls.__qualname__}.{identifier}"
        property_alias = legacy_feature_id(
            "property", target, source, property_symbol
        )
        field_alias = legacy_field_id(
            target, source, cls.__qualname__, identifier
        )
        property_rows.append(
            {
                **prop,
                "feature_id": feature_aliases.get(
                    property_alias,
                    canonical_feature_id(
                        "property", target, f"{semantic_key}.{identifier}"
                    ),
                ),
                "field_id": field_aliases.get(
                    field_alias,
                    canonical_field_id(target, semantic_key, identifier),
                ),
                "field_aliases": [field_alias],
            }
        )
    return property_rows


def _class_record(
    cls: type,
    package_target: str,
    feature_aliases: dict[str, str],
    field_aliases: dict[str, str],
) -> dict[str, object]:
    base_name = next(
        (base.__name__ for base in RNA_BASES if issubclass(cls, base)),
        "RNA",
    )
    properties = _rna_properties(cls)
    source = _runtime_source(cls)
    target = _class_target(cls, package_target)
    semantic_key = _class_semantic_key(cls, base_name, properties)
    kind = {
        "Operator": "operator",
        "Panel": "panel",
        "PropertyGroup": "property_group",
    }.get(base_name, base_name.lower())
    class_alias = legacy_feature_id(kind, target, source, cls.__qualname__)
    class_id = feature_aliases.get(
        class_alias,
        canonical_feature_id(kind, target, semantic_key),
    )
    return {
        "module": cls.__module__,
        "class": cls.__qualname__,
        "kind": base_name,
        "target": target,
        "feature_id": class_id,
        "aliases": [class_alias],
        "bl_idname": str(getattr(cls, "bl_idname", "")),
        "bl_label": str(getattr(cls, "bl_label", "")),
        "properties": _property_records(
            cls,
            properties,
            target,
            source,
            semantic_key,
            feature_aliases,
            field_aliases,
        ),
    }


def _keymap_row(keymap, item) -> dict[str, object]:
    target = target_from_bl_idname(item.idname)
    modifiers = (
        "ctrl" if item.ctrl else "",
        "shift" if item.shift else "",
        "alt" if item.alt else "",
        "oskey" if item.oskey else "",
    )
    semantic_key = ".".join(
        (
            keymap.name,
            keymap.space_type,
            keymap.region_type,
            item.idname,
            item.type,
            item.value,
            *modifiers,
        )
    )
    return {
        "keymap": keymap.name,
        "space_type": keymap.space_type,
        "region_type": keymap.region_type,
        "idname": item.idname,
        "type": item.type,
        "value": item.value,
        "shift": bool(item.shift),
        "ctrl": bool(item.ctrl),
        "alt": bool(item.alt),
        "oskey": bool(item.oskey),
        "active": bool(item.active),
        "feature_id": canonical_feature_id("shortcut", target, semantic_key),
    }


def _keymap_records() -> list[dict[str, object]]:
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig is None:
        return []
    rows: list[dict[str, object]] = []
    for keymap in keyconfig.keymaps:
        for item in keymap.keymap_items:
            if item.idname.startswith(("bmanga.", "bmanga_line.", "bmanga_render.")):
                rows.append(_keymap_row(keymap, item))
    return sorted(
        rows,
        key=lambda row: (
            str(row["keymap"]),
            str(row["idname"]),
            str(row["type"]),
            str(row["value"]),
        ),
    )


def _runtime_owner_properties() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for owner in RUNTIME_OWNERS:
        for prop in owner.bl_rna.properties:
            identifier = str(prop.identifier)
            if not identifier.startswith(("bmanga", "b_manga")):
                continue
            if identifier.startswith("bmanga_render"):
                target = "render"
            elif identifier.startswith("bmanga_line"):
                target = "line"
            else:
                target = "bmanga"
            owner_key = f"rna-owner.{owner.__name__}"
            state_class = _runtime_owner_state(owner.__name__, identifier)
            rows.append(
                {
                    "owner": owner.__name__,
                    "identifier": identifier,
                    "name": prop.name,
                    "type": prop.type,
                    "is_readonly": bool(prop.is_readonly),
                    "is_runtime": bool(prop.is_runtime),
                    "feature_id": canonical_feature_id(
                        "property", target, f"{owner_key}.{identifier}"
                    ),
                    "field_id": canonical_field_id(target, owner_key, identifier),
                    "runtime_classification": "contracted_runtime_owner",
                    "contract": _runtime_owner_contract(
                        owner.__name__,
                        identifier,
                        state_class,
                    ),
                }
            )
    return sorted(rows, key=lambda row: (str(row["owner"]), str(row["identifier"])))


def _runtime_owner_state(owner: str, identifier: str) -> str:
    override = RUNTIME_OWNER_STATE_OVERRIDES.get((owner, identifier))
    if override is not None:
        return override
    if owner == "WindowManager":
        return "window_manager_transient"
    if owner == "Scene" and identifier in RUNTIME_SCENE_SESSION_PROPERTIES:
        return "scene_session"
    return "persistent_blender_property"


def _runtime_prerequisite(state_class: str) -> str:
    if state_class == "persistent_domain":
        return "作品JSON・page JSONと対応するScene projection"
    if state_class == "user_preset":
        return "B-MANGA Next専用user-config JSON"
    if state_class == "derived_display":
        return "正本domain・Object階層から再構築する表示projection"
    if state_class == "multi_context_projection":
        return "Scene編集状態とdomain正本を同期するcontext projection"
    if state_class in {"window_manager_transient", "scene_session"}:
        return "現在のBlenderセッション"
    return "保存対象Blender datablock"


def _runtime_save_contract(state_class: str) -> str:
    if state_class == "persistent_domain":
        return "作品codecを正本として保存・再読込し、Scene projectionを再構築する"
    if state_class == "user_preset":
        return "Next専用user-configへ保存し、blend値を正本にしない"
    if state_class == "derived_display":
        return "保存対象外の派生表示として正本から再構築する"
    if state_class == "multi_context_projection":
        return "context側だけを正本にせず、確定時にdomain正本へ反映する"
    if state_class in {"window_manager_transient", "scene_session"}:
        return "セッション終了時に破棄し、作品へ永続化しない"
    return "blend保存・再読込で同じ値を復元する"


def _runtime_undo_contract(state_class: str) -> str:
    if state_class in {"window_manager_transient", "scene_session", "derived_display"}:
        return "セッション・派生状態のため作品Undoへ直接保存しない"
    if state_class == "user_preset":
        return "プリセット保存Commandでのみuser-configを更新する"
    return "正本を変更するCommandと同じUndo単位で往復する"


def _runtime_owner_contract(
    owner: str,
    identifier: str,
    state_class: str,
) -> dict[str, str]:
    return {
        "contract_basis": "phase0_runtime_ownership_v2",
        "state_class": state_class,
        "contract_owner": owner,
        "ui_location": f"{owner}.{identifier}",
        "prerequisite_file_role": _runtime_prerequisite(state_class),
        "input_contract": "登録RNAの型・範囲・enumに従うUI値",
        "success_contract": "所有datablockへ一度だけ反映する",
        "cancel_contract": "取消時は直前値を維持する",
        "undo_redo_contract": _runtime_undo_contract(state_class),
        "save_reload_contract": _runtime_save_contract(state_class),
        "visual_expectation": "対応UI/overlayへ同じ表示値を反映する",
        "artifact_expectation": "not_applicable: 外部成果物を直接所有しない",
        "performance_probe": "1変更あたりupdate・全件走査・再生成回数を観測する",
    }


def _summary(
    packages: list[dict[str, object]],
    keymaps: list[dict[str, object]],
    owner_properties: list[dict[str, object]],
) -> dict[str, int]:
    classes = [record for package in packages for record in package["classes"]]
    return {
        "packages": len(packages),
        "classes": len(classes),
        "operators": sum(record["kind"] == "Operator" for record in classes),
        "panels": sum(record["kind"] == "Panel" for record in classes),
        "property_groups": sum(record["kind"] == "PropertyGroup" for record in classes),
        "class_properties": sum(len(record["properties"]) for record in classes),
        "runtime_owner_properties": len(owner_properties),
        "keymap_items": len(keymaps),
    }


def _runtime_feature_ids(
    packages: list[dict[str, object]],
    keymaps: list[dict[str, object]],
    owner_properties: list[dict[str, object]],
) -> set[str]:
    identifiers = {
        str(record["feature_id"])
        for package in packages
        for record in package["classes"]
    }
    identifiers.update(
        str(prop["feature_id"])
        for package in packages
        for record in package["classes"]
        for prop in record["properties"]
    )
    identifiers.update(str(row["feature_id"]) for row in keymaps)
    identifiers.update(str(row["feature_id"]) for row in owner_properties)
    return identifiers


def _runtime_field_ids(
    packages: list[dict[str, object]],
    owner_properties: list[dict[str, object]],
) -> set[str]:
    identifiers = {
        str(prop["field_id"])
        for package in packages
        for record in package["classes"]
        for prop in record["properties"]
    }
    identifiers.update(str(row["field_id"]) for row in owner_properties)
    return identifiers


def _missing_runtime_ids(
    packages: list[dict[str, object]],
    keymaps: list[dict[str, object]],
    owner_properties: list[dict[str, object]],
) -> tuple[int, int]:
    missing_features = 0
    missing_fields = 0
    for package in packages:
        for record in package["classes"]:
            missing_features += not bool(record.get("feature_id"))
            for prop in record["properties"]:
                missing_features += not bool(prop.get("feature_id"))
                missing_fields += not bool(prop.get("field_id"))
    for row in (*keymaps, *owner_properties):
        missing_features += not bool(row.get("feature_id"))
        if "identifier" in row:
            missing_fields += not bool(row.get("field_id"))
    return int(missing_features), int(missing_fields)


def _union_summary(
    static_ids: set[str],
    runtime_ids: set[str],
    static_field_ids: set[str],
    runtime_field_ids: set[str],
    missing_ids: tuple[int, int],
) -> dict[str, int]:
    union = static_ids | runtime_ids
    field_union = static_field_ids | runtime_field_ids
    return {
        "static_features": len(static_ids),
        "runtime_features": len(runtime_ids),
        "union_features": len(union),
        "runtime_only_features": len(runtime_ids - static_ids),
        "static_only_features": len(static_ids - runtime_ids),
        "union_fields": len(field_union),
        "missing_generated_feature_ids": missing_ids[0],
        "missing_generated_field_ids": missing_ids[1],
    }


def _registered_packages(
    feature_aliases: dict[str, str],
    field_aliases: dict[str, str],
) -> list[dict[str, object]]:
    targets = {
        "bmanga_phase0_main": "bmanga",
        "bmanga_phase0_render": "render",
        "bmanga_phase0_line": "line",
    }
    packages: list[dict[str, object]] = []
    for name, path in PACKAGES:
        records = [
            _class_record(cls, targets[name], feature_aliases, field_aliases)
            for cls in _package_classes(name)
        ]
        packages.append(
            {
                "package": name,
                "source": path.relative_to(ROOT).as_posix() if path != ROOT else ".",
                "classes": records,
            }
        )
    return packages


def _alias_conflicts(
    packages: list[dict[str, object]],
    feature_aliases: dict[str, str],
    field_aliases: dict[str, str],
) -> tuple[int, int]:
    feature_conflicts = 0
    field_conflicts = 0
    for package in packages:
        for record in package["classes"]:
            for alias in record["aliases"]:
                expected = feature_aliases.get(alias)
                feature_conflicts += bool(
                    expected and expected != record["feature_id"]
                )
            for prop in record["properties"]:
                for alias in prop["field_aliases"]:
                    expected = field_aliases.get(alias)
                    field_conflicts += bool(
                        expected and expected != prop["field_id"]
                    )
    return int(feature_conflicts), int(field_conflicts)


def _registered_static_features():
    features = scan_product_features(ROOT)
    apply_registry(features, load_registry(ROOT))
    freeze_contracts(features)
    return features


def _annotate_package_contracts(
    packages: list[dict[str, object]],
    static_ids: set[str],
) -> Counter:
    counts: Counter = Counter()
    containers = {"AddonPreferences", "Menu", "UIList"}
    for package in packages:
        for record in package["classes"]:
            if record["feature_id"] in static_ids:
                classification = "matched_static_contract"
            elif record["kind"] in containers:
                classification = "blender_registration_container"
            else:
                classification = "unresolved_product_class"
            record["runtime_classification"] = classification
            counts[classification] += 1
            for prop in record["properties"]:
                if prop["feature_id"] in static_ids:
                    prop_class = "matched_static_contract"
                elif not prop["is_runtime"]:
                    prop_class = "blender_inherited"
                else:
                    prop_class = "unresolved_product_property"
                prop["runtime_classification"] = prop_class
                counts[prop_class] += 1
    return counts


def _annotate_keymap_contracts(
    keymaps: list[dict[str, object]],
    static_ids: set[str],
    operator_ids: dict[str, str],
) -> Counter:
    counts: Counter = Counter()
    for row in keymaps:
        if row["feature_id"] in static_ids:
            classification = "matched_static_contract"
        elif row["idname"] in operator_ids:
            classification = "runtime_binding_variant"
            row["operator_feature_id"] = operator_ids[row["idname"]]
        else:
            classification = "unresolved_product_keymap"
        row["runtime_classification"] = classification
        counts[classification] += 1
    return counts


def _runtime_contract_summary(
    packages: list[dict[str, object]],
    keymaps: list[dict[str, object]],
    owner_properties: list[dict[str, object]],
    static_features,
) -> dict[str, object]:
    static_ids = {feature.feature_id for feature in static_features}
    operator_ids = {
        feature.bl_idname: feature.feature_id
        for feature in static_features
        if feature.kind == "operator" and feature.bl_idname
    }
    counts = _annotate_package_contracts(packages, static_ids)
    counts.update(_annotate_keymap_contracts(keymaps, static_ids, operator_ids))
    counts["contracted_runtime_owner"] += len(owner_properties)
    unresolved = sum(
        count for name, count in counts.items() if name.startswith("unresolved_")
    )
    return {
        "runtime_classifications": dict(sorted(counts.items())),
        "unresolved_runtime_product_features": unresolved,
        "unresolved_runtime_product_fields": counts[
            "unresolved_product_property"
        ],
        "runtime_owner_contracts": len(owner_properties),
    }


def _static_only_contract_summary(
    static_features,
    runtime_ids: set[str],
) -> dict[str, object]:
    counts: Counter = Counter()
    for feature in static_features:
        if feature.feature_id in runtime_ids:
            continue
        state = str(feature.metadata.get("state_class", ""))
        if feature.kind == "property" and state == "operator_input":
            classification = "declared_operator_input_not_exposed_by_bl_rna"
        elif feature.kind == "preset":
            classification = "source_defined_preset"
        elif feature.kind == "shortcut":
            classification = "source_shortcut_definition"
        elif feature.kind == "export":
            classification = "source_export_contract"
        else:
            classification = "unresolved_static_product_feature"
        counts[classification] += 1
    return {
        "static_only_classifications": dict(sorted(counts.items())),
        "unresolved_static_product_features": counts[
            "unresolved_static_product_feature"
        ],
        "declared_operator_inputs_not_exposed_by_bl_rna": counts[
            "declared_operator_input_not_exposed_by_bl_rna"
        ],
    }


def _runtime_payload(
    static_features,
    aliases: dict[str, str],
    field_aliases: dict[str, str],
    packages: list[dict[str, object]],
    keymaps: list[dict[str, object]],
    owner_properties: list[dict[str, object]],
) -> dict[str, object]:
    static_ids = {feature.feature_id for feature in static_features}
    static_fields = {
        feature.field_id for feature in static_features if feature.field_id
    }
    runtime_ids = _runtime_feature_ids(packages, keymaps, owner_properties)
    runtime_fields = _runtime_field_ids(packages, owner_properties)
    return {
        "schema_version": 4,
        "blender_version": bpy.app.version_string,
        "packages": packages,
        "runtime_owner_properties": owner_properties,
        "keymaps": keymaps,
        "feature_union": sorted(static_ids | runtime_ids),
        "field_union": sorted(static_fields | runtime_fields),
        "feature_aliases": dict(sorted(aliases.items())),
        "field_aliases": dict(sorted(field_aliases.items())),
    }


def _static_alias_maps(static_features) -> tuple[dict[str, str], dict[str, str]]:
    aliases = {
        alias: feature.feature_id
        for feature in static_features
        for alias in feature.aliases
    }
    field_aliases = {
        alias: feature.field_id
        for feature in static_features
        for alias in feature.field_aliases
    }
    return aliases, field_aliases


def _register_all(loaded: list[tuple[str, object]]) -> None:
    for name, path in PACKAGES:
        module = _load_package(name, path)
        module.register()
        loaded.append((name, module))


def _collect_payload(loaded: list[tuple[str, object]]) -> dict[str, object]:
    static_features = _registered_static_features()
    aliases, field_aliases = _static_alias_maps(static_features)
    _register_all(loaded)
    packages = _registered_packages(aliases, field_aliases)
    keymaps = _keymap_records()
    owner_properties = _runtime_owner_properties()
    static_ids = {feature.feature_id for feature in static_features}
    static_field_ids = {
        feature.field_id for feature in static_features if feature.field_id
    }
    runtime_ids = _runtime_feature_ids(packages, keymaps, owner_properties)
    runtime_field_ids = _runtime_field_ids(packages, owner_properties)
    payload = _runtime_payload(
        static_features,
        aliases,
        field_aliases,
        packages,
        keymaps,
        owner_properties,
    )
    payload["summary"] = _summary(packages, keymaps, owner_properties)
    payload["summary"].update(
        _runtime_contract_summary(
            packages,
            keymaps,
            owner_properties,
            static_features,
        )
    )
    payload["summary"].update(
        _static_only_contract_summary(static_features, runtime_ids)
    )
    alias_conflicts = _alias_conflicts(packages, aliases, field_aliases)
    payload["summary"].update(
        _union_summary(
            static_ids,
            runtime_ids,
            static_field_ids,
            runtime_field_ids,
            _missing_runtime_ids(packages, keymaps, owner_properties),
        )
    )
    payload["summary"]["feature_alias_conflicts"] = alias_conflicts[0]
    payload["summary"]["field_alias_conflicts"] = alias_conflicts[1]
    return payload


def _assert_runtime_owner_overrides(payload: dict[str, object]) -> None:
    rows = {
        (str(row["owner"]), str(row["identifier"])): str(
            row["contract"]["state_class"]
        )
        for row in payload["runtime_owner_properties"]
    }
    assert set(RUNTIME_OWNER_STATE_OVERRIDES).issubset(rows)
    for key, expected_state in RUNTIME_OWNER_STATE_OVERRIDES.items():
        assert rows[key] == expected_state, (key, rows[key], expected_state)
    session_keys = {("Scene", identifier) for identifier in RUNTIME_SCENE_SESSION_PROPERTIES}
    assert session_keys.issubset(rows)
    assert all(rows[key] == "scene_session" for key in session_keys)


def _write_and_assert(payload: dict[str, object]) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    summary = payload["summary"]
    for key in (
        "operators",
        "property_groups",
        "class_properties",
        "runtime_owner_properties",
        "keymap_items",
    ):
        assert summary[key] > 0
    assert summary["missing_generated_feature_ids"] == 0
    assert summary["missing_generated_field_ids"] == 0
    assert summary["unresolved_runtime_product_features"] == 0
    assert summary["unresolved_runtime_product_fields"] == 0
    assert summary["unresolved_static_product_features"] == 0
    assert summary["feature_alias_conflicts"] == 0
    assert summary["field_alias_conflicts"] == 0
    keymap_ids = [row["feature_id"] for row in payload["keymaps"]]
    assert len(keymap_ids) == len(set(keymap_ids))
    _assert_runtime_owner_overrides(payload)
    print(f"BMANGA_PHASE0_RUNTIME_CATALOG_OK {OUT_PATH}", flush=True)


def _unregister(loaded: list[tuple[str, object]]) -> None:
    for _name, module in reversed(loaded):
        try:
            module.unregister()
        except Exception:
            traceback.print_exc()


def main() -> None:
    loaded: list[tuple[str, object]] = []
    try:
        payload = _collect_payload(loaded)
        _write_and_assert(payload)
    finally:
        _unregister(loaded)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)
    sys.stdout.flush()
    os._exit(0)
