"""B-MANGA設定項目のBlender非依存契約。

FieldSpecは現行RNAの置き換えではなく、Phase 3で新Domain schemaを組み立てる
ための正規化済み台帳である。全Propertyを明示分類し、保存対象と
Session/派生値を同じcodecへ混ぜない。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence


class SettingsContractError(ValueError):
    """FieldSpecまたは単位変換契約の違反。"""


class FieldCategory(str, Enum):
    USER_SETTING = "user_setting"
    PERSISTENT_DOMAIN = "persistent_domain"
    SESSION_STATE = "session_state"
    DERIVED_DISPLAY = "derived_display"
    EXTERNAL_INTEGRATION = "external_integration"


class UnitConversion(str, Enum):
    IDENTITY = "identity"
    UI_DEGREES_INTERNAL_RADIANS = "ui_degrees_internal_radians"
    UI_SRGB_INTERNAL_LINEAR = "ui_srgb_internal_linear"
    UI_MM_INTERNAL_MM = "ui_mm_internal_mm"
    UI_PERCENT_INTERNAL_PERCENT = "ui_percent_internal_percent"
    BLENDER_SCENE_LENGTH = "blender_scene_length"


_SCHEMA_CATEGORIES = frozenset(
    {FieldCategory.USER_SETTING, FieldCategory.PERSISTENT_DOMAIN}
)
_REQUIRED_POLICIES = (
    "save_policy",
    "codec_policy",
    "preset_policy",
    "dirty_policy",
    "cache_policy",
    "test_policy",
)


@dataclass(frozen=True, slots=True)
class FieldSpec:
    field_id: str
    aliases: tuple[str, ...]
    target: str
    source: str
    symbol: str
    owner_name: str
    field_name: str
    property_type: str
    state_class: str
    category: FieldCategory
    classification_reason: str
    schema_member: bool
    schema_decision: str
    legacy_save_policy: str
    save_policy: str
    codec_policy: str
    codec_bindings: tuple[str, ...]
    preset_policy: str
    preset_families: tuple[str, ...]
    dirty_policy: str
    dirty_bindings: tuple[str, ...]
    cache_policy: str
    cache_dependencies: tuple[str, ...]
    test_policy: str
    unit_conversion: UnitConversion
    update_callback: str
    accessor_bindings: tuple[str, ...]
    ui_location: str
    input_contract: str
    cancel_contract: str
    save_reload_contract: str
    test_ids: tuple[str, ...]
    default: Any = None
    minimum: Any = None
    maximum: Any = None

    def __post_init__(self) -> None:
        for name in (
            "field_id",
            "target",
            "source",
            "symbol",
            "owner_name",
            "field_name",
            "property_type",
            "state_class",
            "classification_reason",
            "schema_decision",
            "legacy_save_policy",
            "ui_location",
            "input_contract",
            "cancel_contract",
            "save_reload_contract",
        ):
            if not str(getattr(self, name) or "").strip():
                raise SettingsContractError(f"{name} is required")
        object.__setattr__(self, "category", FieldCategory(self.category))
        object.__setattr__(
            self,
            "unit_conversion",
            UnitConversion(self.unit_conversion),
        )
        for name in _REQUIRED_POLICIES:
            if not str(getattr(self, name) or "").strip():
                raise SettingsContractError(
                    f"{self.field_id}: {name} must be declared"
                )
        expected_schema_member = self.category in _SCHEMA_CATEGORIES
        if bool(self.schema_member) != expected_schema_member:
            raise SettingsContractError(
                f"{self.field_id}: schema_member/category mismatch"
            )
        if (
            self.preset_policy == "included"
            and not self.preset_families
        ):
            raise SettingsContractError(
                f"{self.field_id}: preset families are required"
            )
        if (
            self.preset_policy != "included"
            and self.preset_families
        ):
            raise SettingsContractError(
                f"{self.field_id}: excluded preset has families"
            )
        if not self.codec_bindings:
            raise SettingsContractError(
                f"{self.field_id}: concrete codec binding is required"
            )
        if not self.dirty_bindings:
            raise SettingsContractError(
                f"{self.field_id}: concrete dirty binding is required"
            )
        if self.cache_policy == "invalidate_declared_dependents":
            if not self.cache_dependencies:
                raise SettingsContractError(
                    f"{self.field_id}: cache dependencies are required"
                )
        elif self.cache_dependencies:
            raise SettingsContractError(
                f"{self.field_id}: cache dependencies require invalidation policy"
            )
        if self.schema_member:
            characterization_id = f"characterization:{self.field_id}"
            if characterization_id not in self.test_ids:
                raise SettingsContractError(
                    f"{self.field_id}: individual characterization ID is required"
                )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FieldSpec":
        values = dict(data)
        for name in (
            "accessor_bindings",
            "aliases",
            "cache_dependencies",
            "codec_bindings",
            "dirty_bindings",
            "preset_families",
            "test_ids",
        ):
            values[name] = tuple(values.get(name, ()))
        return cls(**values)


class SettingsRegistry:
    """ID、旧alias、RNA owner/nameの三方向からFieldSpecを引く。"""

    def __init__(self, specs: Sequence[FieldSpec]) -> None:
        by_id: dict[str, FieldSpec] = {}
        by_alias: dict[str, FieldSpec] = {}
        by_owner_field: dict[tuple[str, str], FieldSpec] = {}
        for spec in specs:
            canonical = by_id.get(spec.field_id)
            if canonical is None:
                by_id[spec.field_id] = spec
            elif (
                canonical.target != spec.target
                or canonical.category is not spec.category
                or canonical.property_type != spec.property_type
                or canonical.unit_conversion is not spec.unit_conversion
            ):
                raise SettingsContractError(
                    f"incompatible field projections: {spec.field_id}"
                )
            owner_key = (spec.owner_name, spec.field_name)
            existing = by_owner_field.get(owner_key)
            if existing is not None and existing.field_id != spec.field_id:
                raise SettingsContractError(
                    f"duplicate RNA field: {owner_key}"
                )
            by_owner_field[owner_key] = spec
            for alias in spec.aliases:
                previous = by_alias.get(alias)
                if previous is not None and previous.field_id != spec.field_id:
                    raise SettingsContractError(
                        f"field alias collision: {alias}"
                    )
                by_alias[alias] = spec
        self._specs = tuple(specs)
        self._by_id = MappingProxyType(by_id)
        self._by_alias = MappingProxyType(by_alias)
        self._by_owner_field = MappingProxyType(by_owner_field)
        self._owner_names = frozenset(
            owner_name for owner_name, _field_name in by_owner_field
        )

    @property
    def specs(self) -> tuple[FieldSpec, ...]:
        return self._specs

    @property
    def canonical_specs(self) -> tuple[FieldSpec, ...]:
        return tuple(self._by_id.values())

    @property
    def schema_specs(self) -> tuple[FieldSpec, ...]:
        return tuple(
            spec for spec in self.canonical_specs if spec.schema_member
        )

    def get(self, field_id_or_alias: str) -> FieldSpec:
        key = str(field_id_or_alias or "").strip()
        spec = self._by_id.get(key) or self._by_alias.get(key)
        if spec is None:
            raise SettingsContractError(f"unknown field ID: {key}")
        return spec

    def get_rna_field(self, owner_name: str, field_name: str) -> FieldSpec:
        key = (
            str(owner_name or "").strip(),
            str(field_name or "").strip(),
        )
        try:
            return self._by_owner_field[key]
        except KeyError as exc:
            raise SettingsContractError(
                f"unregistered RNA field: {key[0]}.{key[1]}"
            ) from exc

    def owns_rna_owner(self, owner_name: str) -> bool:
        return str(owner_name or "").strip() in self._owner_names


def _default_registry_path() -> Path:
    return Path(__file__).with_name("settings_field_specs.json")


@lru_cache(maxsize=4)
def load_settings_registry(path: str | Path | None = None) -> SettingsRegistry:
    source = Path(path) if path is not None else _default_registry_path()
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise SettingsContractError("unsupported settings registry schema")
    specs = tuple(
        FieldSpec.from_dict(item)
        for item in payload.get("field_specs", ())
    )
    expected = int(
        payload.get("summary", {}).get("property_binding_count", -1)
    )
    if expected != len(specs):
        raise SettingsContractError("settings registry count mismatch")
    return SettingsRegistry(specs)


def rna_owner_name(owner: Any) -> str:
    rna = getattr(owner, "bl_rna", None)
    identifier = str(getattr(rna, "identifier", "") or "").strip()
    if identifier:
        return identifier
    return type(owner).__name__


def require_rna_field(owner: Any, field_name: str) -> FieldSpec | None:
    """詳細UIへ描くRNA fieldがFieldSpec済みであることを保証する。"""

    name = str(field_name or "").strip()
    if name.startswith('["') and name.endswith('"]'):
        raise SettingsContractError(
            "custom properties require an explicit non-RNA UI contract"
        )
    registry = load_settings_registry()
    owner_name = rna_owner_name(owner)
    if not registry.owns_rna_owner(owner_name):
        owner_module = str(getattr(type(owner), "__module__", "") or "")
        app_owned = owner_name.startswith(
            ("BManga", "BMANGA_", "_BManga")
        ) or any(
            marker in owner_module
            for marker in (
                ".bmanga_core",
                ".core",
                ".operators",
                ".preferences",
            )
        )
        if app_owned:
            raise SettingsContractError(
                f"unregistered B-MANGA RNA owner: {owner_name}"
            )
        # Blender組込みRNAだけはB-MANGAの保存schemaへ取り込まない。
        return None
    return registry.get_rna_field(owner_name, name)


def to_internal(
    value: Any,
    conversion: UnitConversion | str,
    *,
    scale_length: float = 1.0,
) -> Any:
    conversion = UnitConversion(conversion)
    if conversion in {
        UnitConversion.IDENTITY,
        UnitConversion.UI_MM_INTERNAL_MM,
        UnitConversion.UI_PERCENT_INTERNAL_PERCENT,
    }:
        return value
    if conversion is UnitConversion.UI_DEGREES_INTERNAL_RADIANS:
        return math.radians(float(value))
    if conversion is UnitConversion.UI_SRGB_INTERNAL_LINEAR:
        return _convert_color(value, _srgb_channel_to_linear)
    if conversion is UnitConversion.BLENDER_SCENE_LENGTH:
        scale = _positive_scale(scale_length)
        return float(value) / scale
    raise SettingsContractError(f"unsupported conversion: {conversion}")


def to_ui(
    value: Any,
    conversion: UnitConversion | str,
    *,
    scale_length: float = 1.0,
) -> Any:
    conversion = UnitConversion(conversion)
    if conversion in {
        UnitConversion.IDENTITY,
        UnitConversion.UI_MM_INTERNAL_MM,
        UnitConversion.UI_PERCENT_INTERNAL_PERCENT,
    }:
        return value
    if conversion is UnitConversion.UI_DEGREES_INTERNAL_RADIANS:
        return math.degrees(float(value))
    if conversion is UnitConversion.UI_SRGB_INTERNAL_LINEAR:
        return _convert_color(value, _linear_channel_to_srgb)
    if conversion is UnitConversion.BLENDER_SCENE_LENGTH:
        scale = _positive_scale(scale_length)
        return float(value) * scale
    raise SettingsContractError(f"unsupported conversion: {conversion}")


def _positive_scale(value: float) -> float:
    scale = float(value)
    if not math.isfinite(scale) or scale <= 0.0:
        raise SettingsContractError("scale_length must be positive")
    return scale


def _convert_color(value: Any, converter) -> tuple[float, ...]:
    channels = tuple(float(channel) for channel in value)
    if len(channels) not in {3, 4}:
        raise SettingsContractError("color conversion requires RGB or RGBA")
    converted = tuple(converter(channel) for channel in channels[:3])
    return converted + channels[3:]


def _srgb_channel_to_linear(value: float) -> float:
    channel = max(0.0, min(1.0, float(value)))
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _linear_channel_to_srgb(value: float) -> float:
    channel = max(0.0, min(1.0, float(value)))
    if channel <= 0.0031308:
        return channel * 12.92
    return 1.055 * (channel ** (1.0 / 2.4)) - 0.055


__all__ = (
    "FieldCategory",
    "FieldSpec",
    "SettingsContractError",
    "SettingsRegistry",
    "UnitConversion",
    "load_settings_registry",
    "require_rna_field",
    "rna_owner_name",
    "to_internal",
    "to_ui",
)
