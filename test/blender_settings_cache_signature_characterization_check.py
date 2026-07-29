"""Blender 5.2実機: FieldSpecと実cache signatureの依存関係を固定する。"""

from __future__ import annotations

import addon_utils
import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy
from bpy.props import CollectionProperty


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = ROOT.name
ARTIFACT = ROOT / "tools" / "settings_contract" / "cache_signature_fields.json"


def _load_addon():
    if str(ROOT.parent) not in sys.path:
        sys.path.insert(0, str(ROOT.parent))
    module = addon_utils.enable(PACKAGE_NAME, default_set=True, persistent=False)
    assert module is not None
    return module


def _scalar(value):
    if isinstance(value, (str, bytes, bool, int, float)) or value is None:
        return value
    try:
        return tuple(value)
    except TypeError:
        return value


def _alternate(prop, current):
    kind = str(prop.type)
    if kind == "BOOLEAN":
        return not bool(current)
    if kind == "STRING":
        if prop.identifier.endswith("_easing_curve"):
            return "0.0000,0.0000;1.0000,0.5000"
        if prop.identifier.endswith("_json"):
            return '{"phase2":true}'
        return f"{current}__phase2"
    if kind == "ENUM":
        return next(
            (
                str(item.identifier)
                for item in prop.enum_items
                if str(item.identifier) != str(current)
            ),
            None,
        )
    if kind in {"INT", "FLOAT"} and bool(prop.is_array):
        values = list(current)
        if not values:
            return None
        values[0] = min(float(prop.hard_max), float(values[0]) + 0.25)
        if kind == "INT":
            values[0] = int(round(values[0]))
        return tuple(values)
    if kind in {"INT", "FLOAT"}:
        low, high = float(prop.hard_min), float(prop.hard_max)
        value = float(current)
        step = min(max((high - low) * 0.125, 1.0e-4), 1.0)
        candidate = value + step if value + step <= high else value - step
        return int(round(candidate)) if kind == "INT" else candidate
    return None


def _mutate(subject, prop) -> bool:
    if str(prop.type) == "COLLECTION":
        child = getattr(subject, prop.identifier).add()
        return _mutate_first_child(child)
    if str(prop.type) == "POINTER":
        return _mutate_first_child(getattr(subject, prop.identifier))
    current = _scalar(getattr(subject, prop.identifier))
    alternate = _alternate(prop, current)
    if alternate is None:
        return False
    setattr(subject, prop.identifier, alternate)
    return _scalar(getattr(subject, prop.identifier)) != current


def _mutate_first_child(child) -> bool:
    for prop in child.bl_rna.properties:
        if prop.identifier == "rna_type" or bool(prop.is_readonly):
            continue
        if _mutate(child, prop):
            return True
    return False


def _module(name: str):
    return importlib.import_module(f"{PACKAGE_NAME}.{name}")


def _work_info_signature(subject) -> str:
    module = _module("utils.work_info_text_object")
    rows = (
        ("work_name", "display_work_name"),
        ("episode_number", "display_episode"),
        ("subtitle", "display_subtitle"),
        ("author", "display_author"),
        ("page_number_start", "display_page_number"),
        ("page_number_end", "display_page_number"),
    )
    return repr(
        tuple(
            module._item_signature(
                "p0001",
                0,
                text_name,
                getattr(subject, display_name),
                str(getattr(subject, text_name)),
            )
            for text_name, display_name in rows
        )
    )


def _paper_work(*, paper=None, overlay=None, page=None):
    return SimpleNamespace(
        paper=paper,
        safe_area_overlay=overlay,
        pages=[] if page is None else [page],
        work_dir="",
    )


def _evaluators(owner_name: str, subject):
    scene = bpy.context.scene
    if owner_name == "BMangaBalloonEntry":
        effect = _module("utils.balloon_flash_effect_line_mesh")
        white = _module("utils.balloon_flash_white_line_mesh")
        merge = _module("utils.balloon_merge_object")
        return (
            (
                "utils.balloon_flash_effect_line_mesh._mesh_signature[uni_flash]",
                lambda: effect._mesh_signature(subject, "uni_flash"),
            ),
            (
                "utils.balloon_flash_effect_line_mesh._mesh_signature[white_outline]",
                lambda: effect._mesh_signature(subject, "white_outline"),
            ),
            (
                "utils.balloon_flash_white_line_mesh._mesh_signature",
                lambda: white._mesh_signature(
                    subject,
                    "uni_flash",
                    float(getattr(subject, "line_width_mm", 0.3)),
                    str(getattr(subject, "shape", "ellipse")),
                ),
            ),
            (
                "utils.balloon_merge_object._entry_signature",
                lambda: merge._entry_signature(subject),
            ),
        )
    if owner_name == "BMangaBalloonShapeParams":
        merge = _module("utils.balloon_merge_object")
        return ((
            "utils.balloon_merge_object._shape_params_signature",
            lambda: merge._shape_params_signature(subject),
        ),)
    if owner_name == "BMangaBalloonTail":
        merge = _module("utils.balloon_merge_object")
        return ((
            "utils.balloon_merge_object._tail_signature",
            lambda: merge._tail_signature(subject),
        ),)
    if owner_name == "BMangaTextEntry":
        text = _module("utils.text_real_object")
        return ((
            "utils.text_real_object._entry_render_signature",
            lambda: text._entry_render_signature(subject),
        ),)
    if owner_name == "BMangaDisplayItem":
        info = _module("utils.work_info_text_object")
        return ((
            "utils.work_info_text_object._item_signature",
            lambda: info._item_signature("p0001", 0, "work_name", subject, "作品"),
        ),)
    if owner_name == "BMangaWorkInfo":
        return ((
            "utils.work_info_text_object._item_signature",
            lambda: _work_info_signature(subject),
        ),)
    if owner_name == "BMangaPaperSettings":
        preview = _module("utils.page_preview_object")
        work = _paper_work(
            paper=subject,
            overlay=bpy.context.scene.bmanga_work.safe_area_overlay,
        )
        return ((
            "utils.page_preview_object._preview_render_signature",
            lambda: preview._preview_render_signature(
                work, scene, -1, variant="work"
            ),
        ),)
    if owner_name == "BMangaSafeAreaOverlay":
        preview = _module("utils.page_preview_object")
        work = _paper_work(
            paper=bpy.context.scene.bmanga_work.paper,
            overlay=subject,
        )
        return ((
            "utils.page_preview_object._preview_render_signature",
            lambda: preview._preview_render_signature(
                work, scene, -1, variant="work"
            ),
        ),)
    if owner_name == "BMangaPageEntry":
        runtime = _module("utils.page_file_scene")
        subject.id = "p0001"
        work = _paper_work(page=subject)
        return ((
            "utils.page_file_scene.page_runtime_signature",
            lambda: runtime.page_runtime_signature(work, "p0001"),
        ),)
    if owner_name == "BMangaComaEntry":
        mask = _module("utils.coma_content_mask")
        return ((
            "utils.coma_content_mask._image_signature",
            lambda: mask._image_signature(
                subject, (0.0, 0.0, 10.0, 10.0), (64, 64), 72
            ),
        ),)
    return ()


def _observed_bindings(registry) -> dict[str, list[str]]:
    specs_by_owner = {}
    for spec in registry.schema_specs:
        if spec.category.value == "persistent_domain":
            specs_by_owner.setdefault(spec.owner_name, []).append(spec)
    observed: dict[str, list[str]] = {}
    attrs = []
    try:
        for index, (owner_name, specs) in enumerate(sorted(specs_by_owner.items())):
            module_name = specs[0].source.removesuffix(".py").replace("/", ".")
            owner_type = getattr(_module(module_name), owner_name)
            attr = f"bmanga_phase2_signature_probe_{index:03d}"
            setattr(bpy.types.Scene, attr, CollectionProperty(type=owner_type))
            attrs.append(attr)
            collection = getattr(bpy.context.scene, attr)
            for spec in specs:
                subject = collection.add()
                evaluators = _evaluators(owner_name, subject)
                if not evaluators:
                    collection.remove(len(collection) - 1)
                    continue
                before = {label: evaluate() for label, evaluate in evaluators}
                prop = subject.bl_rna.properties[spec.field_name]
                if _mutate(subject, prop):
                    bindings = [
                        (
                            f"signature:{label}:"
                            f"{owner_name}.{spec.field_name}"
                        )
                        for label, evaluate in evaluators
                        if evaluate() != before[label]
                    ]
                    if bindings:
                        observed[spec.field_id] = sorted(bindings)
                collection.remove(len(collection) - 1)
    finally:
        for attr in reversed(attrs):
            delattr(bpy.types.Scene, attr)
    return observed


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    contract = _module("bmanga_core.settings_contract")
    observed = _observed_bindings(contract.load_settings_registry())
    bootstrap = os.environ.get(
        "BMANGA_SETTINGS_CACHE_SIGNATURE_OUT", ""
    ).strip()
    if bootstrap:
        target = Path(bootstrap)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "generator": Path(__file__).name,
                    "field_bindings": observed,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    declared = json.loads(ARTIFACT.read_text(encoding="utf-8"))[
        "field_bindings"
    ]
    assert observed == declared, (
        "cache signature declaration mismatch: "
        f"observed={len(observed)} declared={len(declared)}"
    )
    print(
        "BMANGA_SETTINGS_CACHE_SIGNATURE_CHARACTERIZATION_OK "
        f"fields={len(observed)} "
        f"bindings={sum(len(value) for value in observed.values())}"
    )


if __name__ == "__main__":
    main()
