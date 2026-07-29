"""FieldSpecを現行実装のcodec・更新callbackへ結び付ける。"""

from __future__ import annotations

import ast
from functools import lru_cache
import json
from pathlib import Path
from typing import Any


# Phase 2では「保存されるはず」という分類名だけを許さない。各永続ownerを、
# 現行の実serializer/deserializerまたはBlender datablock保存へ明示接続する。
OWNER_CODEC_BINDINGS: dict[str, tuple[str, ...]] = {
    "BMangaBalloonEntry": (
        "json:io.schema.balloon_entry_to_dict/io.schema.balloon_entry_from_dict",
    ),
    "BMangaBalloonShapeParams": (
        "json:nested:BMangaBalloonEntry.shape_params",
    ),
    "BMangaBalloonTail": (
        "json:nested:BMangaBalloonEntry.tails",
    ),
    "BMangaBalloonTailPoint": (
        "json:nested:BMangaBalloonEntry.tails[].points",
    ),
    "BMangaComaBorder": (
        "json:io.schema.coma_border_to_dict/io.schema.coma_border_from_dict",
    ),
    "BMangaComaCameraAngleItem": (
        "blend:Scene.bmanga_coma_camera_settings.camera_angles",
    ),
    "BMangaComaCameraResolutionSetting": (
        "blend:Scene.bmanga_coma_camera_resolution_settings",
    ),
    "BMangaComaCameraSettings": (
        "blend:Scene.bmanga_coma_camera_settings",
    ),
    "BMangaComaEntry": (
        "json:io.schema.coma_entry_to_dict/io.schema.coma_entry_from_dict",
    ),
    "BMangaComaGap": (
        "json:io.schema.coma_gap_to_dict/io.schema.coma_gap_from_dict",
    ),
    "BMangaComaVertex": (
        "json:nested:BMangaComaEntry.vertices",
    ),
    "BMangaComaWhiteMargin": (
        "json:io.schema.coma_white_margin_to_dict/io.schema.coma_white_margin_from_dict",
    ),
    "BMangaDisplayItem": (
        "json:io.schema.display_item_to_dict/io.schema.display_item_from_dict",
    ),
    "BMangaEffectLineParams": (
        "json:core.effect_line.effect_params_to_dict/core.effect_line.effect_params_from_dict",
    ),
    "BMangaFillLayer": (
        "json:io.schema.fill_layer_to_dict/io.schema.fill_layer_from_dict",
    ),
    "BMangaImageLayer": (
        "json:io.schema.image_layer_to_dict/io.schema.image_layer_from_dict",
    ),
    "BMangaImagePathLayer": (
        "json:io.image_path_schema.image_path_layer_to_dict/io.image_path_schema.image_path_layer_from_dict",
    ),
    "BMangaLayerFolder": (
        "json:io.schema.layer_folder_to_dict/io.schema.layer_folder_from_dict",
    ),
    "BMangaLayerRef": (
        "json:nested:BMangaComaEntry.layer_refs",
    ),
    "BMangaNombre": (
        "json:io.schema.nombre_to_dict/io.schema.nombre_from_dict",
    ),
    "BMangaOriginalPageRef": (
        "json:nested:BMangaPageEntry.original_pages",
    ),
    "BMangaPageEntry": (
        "json:io.schema.page_entry_to_dict/io.schema.page_entry_from_dict",
        "json:io.schema.page_to_dict/io.schema.page_from_dict",
    ),
    "BMangaPaperSettings": (
        "json:io.schema.paper_to_dict/io.schema.paper_from_dict",
    ),
    "BMangaRasterLayer": (
        "json:io.schema.raster_layer_to_dict/io.schema.raster_layer_from_dict",
    ),
    "BMangaRubySegment": (
        "json:nested:BMangaTextEntry.ruby_spans[].segments",
    ),
    "BMangaRubySpan": (
        "json:nested:BMangaTextEntry.ruby_spans",
    ),
    "BMangaSafeAreaOverlay": (
        "json:io.schema.safe_area_to_dict/io.schema.safe_area_from_dict",
    ),
    "BMangaTextEntry": (
        "json:io.schema.text_entry_to_dict/io.schema.text_entry_from_dict",
    ),
    "BMangaTextFontSpan": (
        "json:nested:BMangaTextEntry.font_spans",
    ),
    "BMangaTextStyleSpan": (
        "json:nested:BMangaTextEntry.style_spans",
    ),
    "BMangaWorkData": (
        "json:io.schema.work_to_dict/io.schema.work_from_dict",
    ),
    "BMangaWorkInfo": (
        "json:io.schema.work_info_to_dict/io.schema.work_info_from_dict",
    ),
    "BMangaPreferences": (
        "userpref:Blender.userpref.blend:BMangaPreferences",
    ),
    "BMangaRubyDictEntry": (
        "userpref:nested:BMangaPreferences.ruby_dictionaries",
    ),
}


@lru_cache(maxsize=8)
def _json_codec_bindings(root: Path) -> dict[str, tuple[str, ...]]:
    payload = json.loads(
        (
            root / "tools" / "settings_contract" / "json_codec_fields.json"
        ).read_text(encoding="utf-8")
    )
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported JSON codec coverage schema")
    return {
        str(field_id): tuple(str(value) for value in values)
        for field_id, values in payload.get("field_bindings", {}).items()
    }


@lru_cache(maxsize=8)
def _cache_signature_bindings(root: Path) -> dict[str, tuple[str, ...]]:
    payload = json.loads(
        (
            root
            / "tools"
            / "settings_contract"
            / "cache_signature_fields.json"
        ).read_text(encoding="utf-8")
    )
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported cache signature coverage schema")
    return {
        str(field_id): tuple(str(value) for value in values)
        for field_id, values in payload.get("field_bindings", {}).items()
    }


def codec_bindings_for(
    root: Path,
    feature: dict[str, Any],
    category: str,
) -> tuple[str, ...]:
    owner_name = str(feature["metadata"]["owner_name"])
    field_name = str(feature["symbol"]).rsplit(".", 1)[-1]
    field_target = f"{owner_name}.{field_name}"
    if category == "persistent_domain":
        try:
            owner_bindings = OWNER_CODEC_BINDINGS[owner_name]
        except KeyError as exc:
            raise ValueError(
                f"schema owner has no concrete codec binding: {owner_name}"
            ) from exc
        bindings = [
            f"blend-rna:{feature['source']}:{field_target}",
        ]
        verified_bindings = _json_codec_bindings(root).get(
            str(feature["field_id"]),
            (),
        )
        declared_json = {
            binding.removeprefix("json:")
            for binding in owner_bindings
            if binding.startswith("json:")
        }
        unknown = set(verified_bindings) - declared_json
        if unknown:
            raise ValueError(
                f"verified JSON adapter is not declared for {field_target}: "
                f"{sorted(unknown)}"
            )
        for json_binding in verified_bindings:
            if not json_binding:
                raise ValueError(
                    f"verified JSON field has no adapter: {field_target}"
                )
            bindings.append(
                f"json-adapter:{json_binding}:{field_target}"
            )
        return tuple(bindings)
    if category == "user_setting":
        try:
            owner_bindings = OWNER_CODEC_BINDINGS[owner_name]
        except KeyError as exc:
            raise ValueError(
                f"schema owner has no concrete codec binding: {owner_name}"
            ) from exc
        return tuple(f"{binding}:{field_target}" for binding in owner_bindings)
    if category == "session_state":
        return (f"session:not_saved:{field_target}",)
    if category == "derived_display":
        return (f"derived:recompute_from_source:{field_target}",)
    return (
        f"external:{feature['target']}:{feature['source']}:{field_target}",
    )


def field_test_ids(field_id: str, category: str) -> tuple[str, ...]:
    ids = ["test:test.test.settings.contract"]
    if category in {"persistent_domain", "user_setting"}:
        ids.extend(
            (
                "test:test.blender.settings.contract.characterization.check",
                f"characterization:{field_id}",
            )
        )
    return tuple(ids)


def declaration_wiring(
    root: Path,
    feature: dict[str, Any],
) -> dict[str, tuple[str, ...] | str]:
    source = str(feature["source"])
    owner_name = str(feature["metadata"]["owner_name"])
    field_name = str(feature["symbol"]).rsplit(".", 1)[-1]
    tree = _parsed(root / source)
    declaration = _property_declaration(tree, owner_name, field_name)
    update = _keyword_expression(declaration, "update")
    getter = _keyword_expression(declaration, "get")
    setter = _keyword_expression(declaration, "set")
    cache_dependencies = _cache_signature_bindings(root).get(
        str(feature["field_id"]),
        (),
    )
    dirty_bindings = (
        (f"{source}:{update}:{owner_name}.{field_name}",)
        if update
        else (f"bpy_rna:implicit_datablock_dirty:{owner_name}.{field_name}",)
    )
    accessors = tuple(
        f"{source}:{kind}={value}:{owner_name}.{field_name}"
        for kind, value in (("get", getter), ("set", setter))
        if value
    )
    return {
        "update_callback": f"{source}:{update}" if update else "",
        "dirty_bindings": dirty_bindings,
        "cache_dependencies": cache_dependencies,
        "accessor_bindings": accessors,
    }


@lru_cache(maxsize=256)
def _parsed(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _property_declaration(
    tree: ast.Module,
    owner_name: str,
    field_name: str,
) -> ast.Call:
    owner = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == owner_name
        ),
        None,
    )
    if owner is None:
        raise ValueError(f"RNA owner declaration is missing: {owner_name}")
    for node in owner.body:
        target = None
        value = None
        if isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value if node.value is not None else node.annotation
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        if (
            isinstance(target, ast.Name)
            and target.id == field_name
            and isinstance(value, ast.Call)
        ):
            return value
    raise ValueError(f"RNA field declaration is missing: {owner_name}.{field_name}")


def _keyword_expression(call: ast.Call, name: str) -> str:
    for keyword in call.keywords:
        if keyword.arg == name:
            return ast.unparse(keyword.value)
    return ""


def _callback_calls(tree: ast.Module, callback: str) -> tuple[str, ...]:
    if not callback or "." in callback:
        return ()
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == callback
        ),
        None,
    )
    if function is None:
        return ()
    return tuple(
        sorted(
            {
                ast.unparse(node.func)
                for node in ast.walk(function)
                if isinstance(node, ast.Call)
            }
        )
    )


__all__ = (
    "OWNER_CODEC_BINDINGS",
    "codec_bindings_for",
    "declaration_wiring",
    "field_test_ids",
)
