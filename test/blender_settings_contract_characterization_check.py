"""Blender 5.2実機: 全FieldSpecのRNA・変更・取消・blend往復を特性固定する。"""

from __future__ import annotations

import importlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path

import addon_utils
import bpy
from bpy.props import CollectionProperty

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = ROOT.name
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.settings_contract.blender_codec_probe import (  # noqa: E402
    NESTED_JSON_BINDINGS,
    direct_json_codec,
    direct_json_codecs,
)

_CODEC_FAILURES: list[str] = []
_PRESET_FAILURES: list[str] = []
_JSON_CODEC_FIELD_IDS: set[str] = set()
_JSON_CODEC_BINDINGS: dict[str, set[str]] = {}
_BLEND_CODEC_FIELD_IDS: set[str] = set()
_JSON_COVERAGE_PATH = ROOT / "tools" / "settings_contract" / "json_codec_fields.json"
_NESTED_JSON_PATHS = {
    "BMangaBalloonShapeParams": ("BMangaBalloonEntry", ("shape_params",)),
    "BMangaBalloonTail": ("BMangaBalloonEntry", ("tails",)),
    "BMangaBalloonTailPoint": (
        "BMangaBalloonEntry",
        ("tails", "points"),
    ),
    "BMangaComaVertex": ("BMangaComaEntry", ("vertices",)),
    "BMangaLayerRef": ("BMangaComaEntry", ("layer_refs",)),
    "BMangaOriginalPageRef": ("BMangaPageEntry", ("original_pages",)),
    "BMangaRubySpan": ("BMangaTextEntry", ("ruby_spans",)),
    "BMangaRubySegment": (
        "BMangaTextEntry",
        ("ruby_spans", "segments"),
    ),
    "BMangaTextFontSpan": ("BMangaTextEntry", ("font_spans",)),
    "BMangaTextStyleSpan": ("BMangaTextEntry", ("style_spans",)),
}
def _load_addon():
    if str(ROOT.parent) not in sys.path:
        sys.path.insert(0, str(ROOT.parent))
    module = addon_utils.enable(
        PACKAGE_NAME,
        default_set=True,
        persistent=False,
    )
    assert module is not None
    assert bpy.context.preferences.addons.get(PACKAGE_NAME) is not None
    return module


def _scalar(value):
    if isinstance(value, (str, bytes, bool, int, float)) or value is None:
        return value
    try:
        return tuple(value)
    except TypeError:
        return value


def _bounded_alternate(current: float, minimum: float, maximum: float) -> float:
    low = float(minimum)
    high = float(maximum)
    value = float(current)
    if not math.isfinite(low):
        low = value - 100.0
    if not math.isfinite(high):
        high = value + 100.0
    if high <= low:
        return value
    step = min(max((high - low) * 0.125, 1.0e-4), 1.0)
    candidate = value + step
    if candidate > high:
        candidate = value - step
    return max(low, min(high, candidate))


def _alternate(prop, current):
    prop_type = str(prop.type)
    if prop_type == "BOOLEAN":
        return not bool(current)
    if prop_type == "STRING":
        suffix = "__phase2"
        maximum = int(getattr(prop, "length_max", 0) or 0)
        candidate = f"{str(current)}{suffix}"
        return candidate[:maximum] if maximum > 0 else candidate
    if prop_type == "ENUM":
        try:
            identifiers = [
                str(item.identifier)
                for item in prop.enum_items
                if str(item.identifier)
            ]
        except Exception:
            return None
        return next(
            (identifier for identifier in identifiers if identifier != current),
            None,
        )
    if prop_type in {"INT", "FLOAT"} and bool(getattr(prop, "is_array", False)):
        values = list(current)
        if not values:
            return None
        values[0] = _bounded_alternate(
            float(values[0]),
            float(prop.hard_min),
            float(prop.hard_max),
        )
        if prop_type == "INT":
            values[0] = int(round(values[0]))
        return tuple(values)
    if prop_type == "INT":
        return int(
            round(
                _bounded_alternate(
                    int(current),
                    int(prop.hard_min),
                    int(prop.hard_max),
                )
            )
        )
    if prop_type == "FLOAT":
        return _bounded_alternate(
            float(current),
            float(prop.hard_min),
            float(prop.hard_max),
        )
    return None


def _field_alternate(owner_name: str, field_name: str, prop, current):
    if field_name.endswith("_easing_curve"):
        return "0.0000,0.0000;1.0000,0.5000"
    if field_name == "blur_curve_points":
        return "0.0000,0.0000;1.0000,0.5000"
    if field_name.endswith("_json"):
        return '{"phase2":true}'
    return _alternate(prop, current)


def _equivalent(left, right) -> bool:
    left = _scalar(left)
    right = _scalar(right)
    if isinstance(left, tuple) and isinstance(right, tuple):
        if len(left) != len(right):
            return False
        return all(_equivalent(a, b) for a, b in zip(left, right))
    if isinstance(left, float) or isinstance(right, float):
        try:
            return math.isclose(
                float(left),
                float(right),
                rel_tol=0.0,
                abs_tol=1.0e-6,
            )
        except (TypeError, ValueError):
            return False
    return left == right


def _codec_equivalent(left, right) -> bool:
    left = _scalar(left)
    right = _scalar(right)
    if isinstance(left, tuple) and isinstance(right, tuple):
        return len(left) == len(right) and all(
            _codec_equivalent(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, float) or isinstance(right, float):
        try:
            return math.isclose(
                float(left),
                float(right),
                rel_tol=0.0,
                abs_tol=5.0e-3,
            )
        except (TypeError, ValueError):
            return False
    return left == right


def _prepare_direct_codec_context(owner_name: str, field_name: str, subject):
    if owner_name == "BMangaBalloonEntry":
        line_schema = importlib.import_module(
            f"{PACKAGE_NAME}.utils.line_effect_schema"
        )
        conditional = set(line_schema.BALLOON_UNI_FLASH_PARAM_FIELDS)
        if field_name in conditional or field_name.startswith("flash_"):
            subject.line_style = "white_outline"
    elif owner_name == "BMangaPageEntry" and field_name.startswith("tombo_"):
        subject.spread = True
    elif owner_name == "BMangaTextEntry" and field_name.endswith(
        ("_spans", "_ranges")
    ):
        subject.body = "設定契約テスト本文"


def _property_groups(registry):
    by_owner = {}
    for spec in registry.schema_specs:
        if spec.category.value != "persistent_domain":
            continue
        record = by_owner.setdefault(
            spec.owner_name,
            {"source": spec.source, "specs": []},
        )
        assert record["source"] == spec.source
        record["specs"].append(spec)
    return by_owner


def _record_json_binding(field_id: str, binding: str) -> None:
    _JSON_CODEC_FIELD_IDS.add(field_id)
    _JSON_CODEC_BINDINGS.setdefault(field_id, set()).add(binding)


def _assert_direct_json_roundtrip(
    field_id: str,
    owner_name: str,
    subjects,
    subject,
    field_name: str,
    current,
    expected,
    baseline_payloads,
) -> None:
    for label, encoder, decoder, baseline_payload in baseline_payloads:
        payload = encoder(subject)
        if payload == baseline_payload:
            continue
        restored = subjects.add()
        try:
            decoder(restored, payload)
            actual = getattr(restored, field_name)
            if _equivalent(actual, current):
                continue
            if not _codec_equivalent(actual, expected):
                _CODEC_FAILURES.append(
                    f"{owner_name}.{field_name}/{label}: "
                    f"expected={expected!r} actual={_scalar(actual)!r}"
                )
                continue
            _record_json_binding(field_id, label)
        finally:
            subjects.remove(len(subjects) - 1)


def _assert_structural_json_roundtrip(
    field_id: str,
    owner_name: str,
    subjects,
    subject,
    expectation,
    baseline_payloads,
) -> None:
    if expectation[0] == "readonly":
        return
    for label, encoder, decoder, baseline_payload in baseline_payloads:
        payload = encoder(subject)
        if payload == baseline_payload:
            continue
        restored = subjects.add()
        try:
            decoder(restored, payload)
            try:
                _assert_structural_reopened(restored, expectation)
            except (AssertionError, AttributeError, IndexError) as exc:
                _CODEC_FAILURES.append(
                    f"{owner_name}.{expectation[2]}/{label}: {exc}"
                )
                continue
            _record_json_binding(field_id, label)
        finally:
            subjects.remove(len(subjects) - 1)


def _nested_item(parent, path: tuple[str, ...], *, create: bool):
    current = parent
    for attr in path:
        value = getattr(current, attr)
        if hasattr(value, "add"):
            if create:
                current = value.add()
            else:
                assert len(value) == 1, f"nested codec did not restore {attr}"
                current = value[0]
        else:
            current = value
    return current


def _prepare_nested_parent(owner_name: str, parent) -> None:
    if owner_name == "BMangaOriginalPageRef":
        parent.spread = True
    if owner_name.startswith("BMangaText") or owner_name.startswith("BMangaRuby"):
        parent.body = "設定契約テスト本文"


def _prepare_nested_item(owner_name: str, item) -> None:
    if owner_name in {"BMangaRubySpan", "BMangaRubySegment"}:
        item.start = 0
        item.length = 1
        if hasattr(item, "ruby_text"):
            item.ruby_text = "せ"
    elif owner_name in {"BMangaTextFontSpan", "BMangaTextStyleSpan"}:
        item.start = 0
        item.length = 1
        if hasattr(item, "font"):
            item.font = "Phase2Font"


def _characterize_nested_json(registry, subjects_by_owner) -> None:
    for owner_name, (parent_owner, path) in _NESTED_JSON_PATHS.items():
        specs = [
            spec
            for spec in registry.schema_specs
            if spec.owner_name == owner_name
            and spec.category.value == "persistent_domain"
        ]
        parent_subjects = subjects_by_owner[parent_owner]
        encoder, decoder = direct_json_codec(PACKAGE_NAME, parent_owner)
        for spec in specs:
            parent = parent_subjects.add()
            _prepare_nested_parent(owner_name, parent)
            item = _nested_item(parent, path, create=True)
            _prepare_nested_item(owner_name, item)
            if owner_name == "BMangaRubySegment":
                span = parent.ruby_spans[0]
                span.start = 0
                span.length = 5
                span.ruby_text = "せ"
                span.style = "mono"
            prop = item.bl_rna.properties[spec.field_name]
            current = _scalar(getattr(item, spec.field_name))
            baseline_payload = encoder(parent)
            alternate = _alternate(prop, current)
            if alternate is None or _equivalent(alternate, current):
                if str(prop.type) == "COLLECTION":
                    collection = getattr(item, spec.field_name)
                    child = collection.add()
                    if hasattr(child, "x_mm"):
                        child.x_mm = 1.0
                    if hasattr(child, "ruby_text"):
                        child.start = 0
                        child.length = 1
                        child.ruby_text = "せ"
                        if hasattr(item, "style"):
                            item.style = "mono"
                    payload = encoder(parent)
                    restored_parent = parent_subjects.add()
                    try:
                        decoder(restored_parent, payload)
                        restored_item = _nested_item(
                            restored_parent,
                            path,
                            create=False,
                        )
                        restored_collection = getattr(
                            restored_item,
                            spec.field_name,
                        )
                        if len(restored_collection) != 1:
                            _CODEC_FAILURES.append(
                                f"{owner_name}.{spec.field_name}: "
                                "nested collection is not restored"
                            )
                        else:
                            _record_json_binding(
                                spec.field_id,
                                NESTED_JSON_BINDINGS[owner_name],
                            )
                    finally:
                        parent_subjects.remove(len(parent_subjects) - 1)
                else:
                    _CODEC_FAILURES.append(
                        f"{owner_name}.{spec.field_name}: no nested test value"
                    )
                parent_subjects.remove(len(parent_subjects) - 1)
                continue
            setattr(item, spec.field_name, alternate)
            payload = encoder(parent)
            if payload == baseline_payload:
                _CODEC_FAILURES.append(
                    f"{owner_name}.{spec.field_name}: nested field is not encoded"
                )
                parent_subjects.remove(len(parent_subjects) - 1)
                continue
            restored_parent = parent_subjects.add()
            try:
                decoder(restored_parent, payload)
                try:
                    restored = _nested_item(
                        restored_parent,
                        path,
                        create=False,
                    )
                except AssertionError as exc:
                    _CODEC_FAILURES.append(
                        f"{owner_name}.{spec.field_name}: {exc}"
                    )
                    continue
                actual = _scalar(getattr(restored, spec.field_name))
                if not _codec_equivalent(actual, alternate):
                    _CODEC_FAILURES.append(
                        f"{owner_name}.{spec.field_name}: expected={alternate!r} "
                        f"actual={actual!r}"
                    )
                    continue
                _record_json_binding(
                    spec.field_id,
                    NESTED_JSON_BINDINGS[owner_name],
                )
            finally:
                parent_subjects.remove(len(parent_subjects) - 1)
                parent_subjects.remove(len(parent_subjects) - 1)


def _balloon_preset_value(snapshot, spec, style_keys, shape_keys):
    if spec.owner_name == "BMangaBalloonShapeParams":
        assert spec.field_name in shape_keys
        return snapshot["shape_params"][spec.field_name]
    if spec.field_name in style_keys:
        return snapshot[spec.field_name]
    return snapshot["uni_flash_params"][spec.field_name]


def _characterize_balloon_presets(registry, subjects_by_owner) -> None:
    presets = importlib.import_module(f"{PACKAGE_NAME}.io.balloon_presets")
    line_schema = importlib.import_module(
        f"{PACKAGE_NAME}.utils.line_effect_schema"
    )
    style_keys = frozenset(presets.BALLOON_STYLE_KEYS)
    shape_keys = frozenset(presets.BALLOON_SHAPE_PARAM_KEYS)
    uni_keys = frozenset(line_schema.BALLOON_UNI_FLASH_PARAM_FIELDS)
    specs = [
        spec
        for spec in registry.specs
        if "balloon" in spec.preset_families
        and spec.owner_name
        in {"BMangaBalloonEntry", "BMangaBalloonShapeParams"}
    ]
    entries = subjects_by_owner["BMangaBalloonEntry"]
    for spec in specs:
        if (
            spec.owner_name == "BMangaBalloonEntry"
            and spec.field_name not in style_keys | uni_keys
        ):
            _PRESET_FAILURES.append(
                f"{spec.symbol}: registry field is absent from balloon codec"
            )
            continue
        source = entries.add()
        subject = (
            source.shape_params
            if spec.owner_name == "BMangaBalloonShapeParams"
            else source
        )
        prop = subject.bl_rna.properties[spec.field_name]
        current = _scalar(getattr(subject, spec.field_name))
        alternate = _alternate(prop, current)
        baseline = presets.snapshot_style_from_entry(source)
        has_alternate = not (
            alternate is None or _equivalent(alternate, current)
        )
        if has_alternate:
            setattr(subject, spec.field_name, alternate)
        payload = presets.snapshot_style_from_entry(source)
        expected = _balloon_preset_value(
            payload,
            spec,
            style_keys,
            shape_keys,
        )
        if has_alternate and _equivalent(
            _balloon_preset_value(
                baseline,
                spec,
                style_keys,
                shape_keys,
            ),
            expected,
        ):
            _PRESET_FAILURES.append(
                f"{spec.symbol}: preset snapshot did not change"
            )
            entries.remove(len(entries) - 1)
            continue
        restored = entries.add()
        try:
            presets.apply_style_to_entry(restored, payload)
            restored_payload = presets.snapshot_style_from_entry(restored)
            actual = _balloon_preset_value(
                restored_payload,
                spec,
                style_keys,
                shape_keys,
            )
            if not _equivalent(actual, expected):
                _PRESET_FAILURES.append(
                    f"{spec.symbol}: preset apply mismatch "
                    f"expected={expected!r} actual={actual!r}"
                )
        finally:
            entries.remove(len(entries) - 1)
            entries.remove(len(entries) - 1)


def _characterize_user_preferences(registry) -> tuple[int, int, set[str]]:
    """隔離userpref.blendで45 user settingを変更・取消・再読込する。"""

    addon = bpy.context.preferences.addons.get(PACKAGE_NAME)
    assert addon is not None
    prefs = addon.preferences
    preferences_module = importlib.import_module(f"{PACKAGE_NAME}.preferences")
    meldex_receiver = importlib.import_module(
        f"{PACKAGE_NAME}.io.meldex_receiver"
    )
    gpencil_op = importlib.import_module(
        f"{PACKAGE_NAME}.operators.gpencil_op"
    )
    original_restart = meldex_receiver.restart_from_preferences
    original_start = gpencil_op._follow_start
    original_stop = gpencil_op._follow_stop
    disable_env = meldex_receiver.DISABLE_RECEIVER_ENV
    original_disable = os.environ.get(disable_env)
    os.environ[disable_env] = "1"
    preferences_module._USERPREF_SAVE_SUSPENDED += 1
    meldex_receiver.restart_from_preferences = lambda _context=None: True
    gpencil_op._follow_start = lambda: None
    gpencil_op._follow_stop = lambda: None
    expectations = {}
    covered: set[str] = set()
    changed = 0
    structural = 0
    specs = [
        spec
        for spec in registry.schema_specs
        if spec.category.value == "user_setting"
    ]
    try:
        direct_specs = [
            spec for spec in specs if spec.owner_name == "BMangaPreferences"
        ]
        # Meldexを有効化するfieldのcallbackが実サーバーを起動しないよう、
        # 先に隔離用tokenを設定し、receiver本体も上で無害化している。
        if hasattr(prefs, "meldex_token"):
            prefs.meldex_token = "phase2-isolated-token"
        for spec in direct_specs:
            prop = prefs.bl_rna.properties.get(spec.field_name)
            assert prop is not None
            covered.add(spec.field_id)
            assert _expected_property_type(prop) == spec.property_type
            if prop.type == "COLLECTION":
                structural += 1
                continue
            current = _scalar(getattr(prefs, spec.field_name))
            alternate = _alternate(prop, current)
            if spec.field_name.startswith("key_"):
                alternate = "A" if str(current) != "A" else "B"
            if alternate is None or _equivalent(alternate, current):
                structural += 1
                continue
            setattr(prefs, spec.field_name, alternate)
            actual = _scalar(getattr(prefs, spec.field_name))
            assert _equivalent(actual, alternate), (
                f"user setting change failed: {spec.symbol}"
            )
            setattr(prefs, spec.field_name, current)
            assert _equivalent(getattr(prefs, spec.field_name), current), (
                f"user setting cancel failed: {spec.symbol}"
            )
            setattr(prefs, spec.field_name, alternate)
            expectations[spec.field_name] = _scalar(
                getattr(prefs, spec.field_name)
            )
            changed += 1

        ruby_specs = {
            spec.field_name: spec
            for spec in specs
            if spec.owner_name == "BMangaRubyDictEntry"
        }
        dictionaries = prefs.ruby_dictionaries
        dictionaries.clear()
        item = dictionaries.add()
        item.path = "phase2-user-dictionary.tsv"
        item.enabled = False
        assert len(dictionaries) == 1
        covered.update(spec.field_id for spec in ruby_specs.values())
        changed += len(ruby_specs)
        structural += 1  # BMangaPreferences.ruby_dictionaries

        # ここで書くuserprefは認定runnerがcase固有の
        # BLENDER_USER_CONFIGへ隔離している。
        assert bpy.ops.wm.save_userpref() == {"FINISHED"}
        for name in expectations:
            prop = prefs.bl_rna.properties[name]
            default = (
                tuple(prop.default_array)
                if bool(getattr(prop, "is_array", False))
                else prop.default
            )
            setattr(prefs, name, default)
        dictionaries.clear()
        assert bpy.ops.wm.read_userpref() == {"FINISHED"}
        if (
            bpy.context.preferences.addons.get(PACKAGE_NAME) is None
            or bpy.context.preferences.addons[PACKAGE_NAME].preferences is None
        ):
            _load_addon()
        prefs = bpy.context.preferences.addons[PACKAGE_NAME].preferences
        for name, expected in expectations.items():
            assert _equivalent(getattr(prefs, name), expected), (
                f"userpref roundtrip failed: BMangaPreferences.{name}"
            )
        assert len(prefs.ruby_dictionaries) == 1
        restored = prefs.ruby_dictionaries[0]
        assert restored.path == "phase2-user-dictionary.tsv"
        assert restored.enabled is False
        assert bool(getattr(prefs, "meldex_enabled", False))
        assert not meldex_receiver.is_running()
    finally:
        preferences_module._USERPREF_SAVE_SUSPENDED = max(
            0,
            preferences_module._USERPREF_SAVE_SUSPENDED - 1,
        )
        meldex_receiver.restart_from_preferences = original_restart
        gpencil_op._follow_start = original_start
        gpencil_op._follow_stop = original_stop
        if original_disable is None:
            os.environ.pop(disable_env, None)
        else:
            os.environ[disable_env] = original_disable
    return changed, structural, covered


def _expected_property_type(prop) -> str:
    prop_type = str(prop.type)
    if bool(getattr(prop, "is_array", False)):
        return {
            "BOOLEAN": "BoolVectorProperty",
            "INT": "IntVectorProperty",
            "FLOAT": "FloatVectorProperty",
        }[prop_type]
    return {
        "BOOLEAN": "BoolProperty",
        "INT": "IntProperty",
        "FLOAT": "FloatProperty",
        "STRING": "StringProperty",
        "ENUM": "EnumProperty",
        "POINTER": "PointerProperty",
        "COLLECTION": "CollectionProperty",
    }[prop_type]


def _structural_expectation(subject, spec, prop):
    value = getattr(subject, spec.field_name)
    if str(prop.type) == "POINTER":
        child = value
        mode = "pointer"
    elif str(prop.type) == "COLLECTION":
        child = value.add()
        mode = "collection"
    else:
        return ("readonly", spec.field_id, spec.field_name, _scalar(value))
    preferred = {
        "shape_params": ("cloud_bump_width_mm",),
        "tails": ("length_mm",),
        "border": ("width_mm",),
        "white_margin": ("enabled",),
        "paper": ("canvas_width_mm",),
        "safe_area_overlay": ("enabled",),
        "work_info": ("work_name",),
        "coma_gap": ("vertical_mm",),
        "nombre": ("enabled",),
        "points": ("x_mm",),
        "vertices": ("x_mm",),
        "layer_refs": ("layer_id",),
        "font_spans": ("start",),
        "ruby_spans": ("ruby_text",),
        "style_spans": ("start",),
        "segments": ("ruby_text",),
    }.get(spec.field_name, ("id", "path", "start", "enabled"))
    properties = {item.identifier: item for item in child.bl_rna.properties}
    ordered = [properties[name] for name in preferred if name in properties]
    ordered.extend(
        item for name, item in properties.items() if name not in preferred
    )
    for child_prop in ordered:
        if child_prop.identifier == "rna_type" or bool(child_prop.is_readonly):
            continue
        alternate = _field_alternate(
            child.bl_rna.identifier,
            child_prop.identifier,
            child_prop,
            _scalar(getattr(child, child_prop.identifier)),
        )
        if alternate is None:
            continue
        setattr(child, child_prop.identifier, alternate)
        actual = _scalar(getattr(child, child_prop.identifier))
        if _equivalent(actual, alternate):
            return (
                mode,
                spec.field_id,
                spec.field_name,
                child_prop.identifier,
                actual,
            )
    return (mode, spec.field_id, spec.field_name, "", None)


def _assert_structural_reopened(subject, expectation) -> str:
    mode, field_id, field_name, *detail = expectation
    value = getattr(subject, field_name)
    if mode == "readonly":
        assert _equivalent(value, detail[0])
        return field_id
    if mode == "collection":
        assert len(value) == 1, f"blend collection lost: {field_name}"
        child = value[0]
    else:
        child = value
    child_name, expected = detail
    if child_name:
        assert _equivalent(getattr(child, child_name), expected), (
            f"blend structural field lost: {field_name}.{child_name}"
        )
    return field_id


def _verify_json_codec_declarations() -> None:
    bootstrap = os.environ.get("BMANGA_SETTINGS_CODEC_COVERAGE_OUT", "").strip()
    if bootstrap:
        target = Path(bootstrap)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "generator": "test.blender_settings_contract_characterization_check",
            "field_bindings": {
                field_id: sorted(bindings)
                for field_id, bindings in sorted(_JSON_CODEC_BINDINGS.items())
            },
        }
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    declared = json.loads(
        _JSON_COVERAGE_PATH.read_text(encoding="utf-8")
    )["field_bindings"]
    observed = {
        field_id: sorted(bindings)
        for field_id, bindings in sorted(_JSON_CODEC_BINDINGS.items())
    }
    assert observed == declared, (
        "JSON codec declaration mismatch: "
        f"observed={len(observed)} declared={len(declared)}"
    )


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _load_addon()
    contract = importlib.import_module(
        f"{PACKAGE_NAME}.bmanga_core.settings_contract"
    )
    state_adapters = importlib.import_module(
        f"{PACKAGE_NAME}.utils.detail_state_adapters"
    )
    load_settings_registry = contract.load_settings_registry
    restore_rna_state = state_adapters.restore_rna_state
    snapshot_rna_state = state_adapters.snapshot_rna_state

    registry = load_settings_registry()
    owners = _property_groups(registry)
    probe_attrs = []
    expectations = {}
    changed = 0
    structural = 0
    covered_ids: set[str] = set()
    subjects_by_owner = {}
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_phase2_settings_"))
    blend_path = temp_root / "settings_contract.blend"
    try:
        scene = bpy.context.scene
        for index, (owner_name, record) in enumerate(sorted(owners.items())):
            source = str(record["source"])
            specs = record["specs"]
            module_name = source.removesuffix(".py").replace("/", ".")
            owner_module = importlib.import_module(
                f"{PACKAGE_NAME}.{module_name}"
            )
            owner_type = getattr(owner_module, owner_name, None)
            assert owner_type is not None, (
                f"RNA owner is not registered: {owner_name}"
            )
            attr = f"bmanga_phase2_contract_probe_{index:03d}"
            setattr(bpy.types.Scene, attr, CollectionProperty(type=owner_type))
            probe_attrs.append(attr)
            subjects = getattr(scene, attr)
            subjects_by_owner[owner_name] = subjects
            template = subjects.add()
            snapshot = snapshot_rna_state(template, max_depth=8)
            assert snapshot is not None, f"snapshot unavailable: {owner_name}"
            subjects.clear()
            owner_expectations = []
            for spec in specs:
                covered_ids.add(spec.field_id)
                prop = owner_type.bl_rna.properties.get(spec.field_name)
                assert prop is not None, (
                    f"runtime RNA field is missing: {owner_name}.{spec.field_name}"
                )
                assert _expected_property_type(prop) == spec.property_type, (
                    f"RNA type differs: {owner_name}.{spec.field_name}: "
                    f"{prop.type}/{spec.property_type}"
                )
                if bool(prop.is_readonly) and str(prop.type) not in {
                    "POINTER",
                    "COLLECTION",
                }:
                    subject = subjects.add()
                    expectation = _structural_expectation(subject, spec, prop)
                    owner_expectations.append(
                        (len(subjects) - 1, expectation)
                    )
                    structural += 1
                    continue
                subject = subjects.add()
                _prepare_direct_codec_context(
                    owner_name,
                    spec.field_name,
                    subject,
                )
                current = _scalar(getattr(subject, spec.field_name))
                baseline_payloads = tuple(
                    (label, encoder, decoder, encoder(subject))
                    for label, encoder, decoder
                    in direct_json_codecs(PACKAGE_NAME, owner_name)
                )
                alternate = _field_alternate(
                    owner_name,
                    spec.field_name,
                    prop,
                    current,
                )
                if alternate is None or _equivalent(alternate, current):
                    expectation = _structural_expectation(
                        subject, spec, prop
                    )
                    _assert_structural_json_roundtrip(
                        spec.field_id,
                        owner_name,
                        subjects,
                        subject,
                        expectation,
                        baseline_payloads,
                    )
                    owner_expectations.append(
                        (len(subjects) - 1, expectation)
                    )
                    structural += 1
                    continue
                setattr(subject, spec.field_name, alternate)
                actual = _scalar(getattr(subject, spec.field_name))
                assert _equivalent(actual, alternate), (
                    f"field change failed: {owner_name}.{spec.field_name}"
                )
                changed += 1
                # 相互に依存する設定を同時に不正な組合せへせず、FieldSpecごとに
                # 独立した実体で変更→取消→保存を特性固定する。
                restore_rna_state(subject, snapshot)
                restore_rna_state(subject, snapshot)
                assert _equivalent(
                    getattr(subject, spec.field_name),
                    current,
                ), (
                    f"cancel restore failed: {owner_name}.{spec.field_name}"
                )
                _prepare_direct_codec_context(
                    owner_name,
                    spec.field_name,
                    subject,
                )
                setattr(subject, spec.field_name, alternate)
                persisted = _scalar(getattr(subject, spec.field_name))
                assert _equivalent(persisted, alternate), (
                    f"field reapply failed: {owner_name}.{spec.field_name}"
                )
                _assert_direct_json_roundtrip(
                    spec.field_id,
                    owner_name,
                    subjects,
                    subject,
                    spec.field_name,
                    current,
                    persisted,
                    baseline_payloads,
                )
                owner_expectations.append(
                    (
                        len(subjects) - 1,
                        ("scalar", spec.field_id, spec.field_name, persisted),
                    )
                )
            expectations[attr] = owner_expectations

        _characterize_nested_json(registry, subjects_by_owner)
        _characterize_balloon_presets(registry, subjects_by_owner)
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(blend_path), load_ui=False)
        reopened = bpy.context.scene
        for attr, fields in expectations.items():
            subjects = getattr(reopened, attr)
            for item_index, expectation in fields:
                subject = subjects[item_index]
                mode, field_id, field_name, *detail = expectation
                if mode == "scalar":
                    assert _equivalent(
                        getattr(subject, field_name), detail[0]
                    ), (
                        f"blend roundtrip failed: "
                        f"{attr}[{item_index}].{field_name}"
                    )
                    _BLEND_CODEC_FIELD_IDS.add(field_id)
                else:
                    _BLEND_CODEC_FIELD_IDS.add(
                        _assert_structural_reopened(subject, expectation)
                    )
        # userpref読込はアドオンを再登録するため、旧RNA型を参照する一時
        # CollectionPropertyを先に解放してから実行する。
        for attr in reversed(probe_attrs):
            delattr(bpy.types.Scene, attr)
        probe_attrs.clear()
        assert not _CODEC_FAILURES, (
            "JSON codec roundtrip failures:\n" + "\n".join(_CODEC_FAILURES)
        )
        assert not _PRESET_FAILURES, (
            "preset roundtrip failures:\n" + "\n".join(_PRESET_FAILURES)
        )
        _verify_json_codec_declarations()
        pref_changed, pref_structural, pref_covered = (
            _characterize_user_preferences(registry)
        )
        changed += pref_changed
        structural += pref_structural
        covered_ids.update(pref_covered)
        expected_ids = {spec.field_id for spec in registry.schema_specs}
        persistent_ids = {
            spec.field_id
            for spec in registry.schema_specs
            if spec.category.value == "persistent_domain"
        }
        assert _BLEND_CODEC_FIELD_IDS == persistent_ids, (
            "Blender RNA codec coverage mismatch: "
            f"missing={sorted(persistent_ids - _BLEND_CODEC_FIELD_IDS)} "
            f"unexpected={sorted(_BLEND_CODEC_FIELD_IDS - persistent_ids)}"
        )
        assert covered_ids == expected_ids, (
            "schema characterization coverage mismatch: "
            f"missing={sorted(expected_ids - covered_ids)} "
            f"unexpected={sorted(covered_ids - expected_ids)}"
        )
    finally:
        for attr in reversed(probe_attrs):
            try:
                delattr(bpy.types.Scene, attr)
            except AttributeError:
                pass
        addon_utils.disable(
            PACKAGE_NAME,
            default_set=True,
            handle_error=None,
        )

    print(
        "BMANGA_SETTINGS_CONTRACT_CHARACTERIZATION_OK "
        f"fields={len(registry.schema_specs)} changed={changed} "
        f"structural={structural} covered={len(covered_ids)} "
        f"json={len(_JSON_CODEC_FIELD_IDS)} "
        f"blend={len(_BLEND_CODEC_FIELD_IDS)} owners={len(owners) + 2}"
    )


if __name__ == "__main__":
    main()
