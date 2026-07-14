from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


def _module():
    path = Path(__file__).resolve().parents[1] / "utils" / "layer_uid.py"
    spec = importlib.util.spec_from_file_location("bmanga_layer_uid_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_managed_uids_are_deterministic_and_parseable():
    uid = _module()
    expected = {
        "gp": "gp:gp_0123456789ab",
        "effect": "effect:effect_0123456789ab",
        "layer_folder": "layer_folder:layer_folder_0001",
    }
    for kind, value in expected.items():
        stable_id = value.split(":", 1)[1]
        assert uid.make_managed_uid(kind, stable_id) == value
        assert uid.make_managed_uid(kind, stable_id) == value
        parsed = uid.parse_uid(value)
        assert parsed.kind == kind
        assert parsed.key == stable_id
        assert parsed.parts == (stable_id,)
        assert not parsed.is_virtual


def test_saved_link_uids_for_existing_layer_kinds_are_canonical():
    uid = _module()
    expected = {
        "raster:raster_0001": ("raster_0001",),
        "image:image_0001": ("image_0001",),
        "balloon:p0001:balloon_0001": ("p0001", "balloon_0001"),
        "text:__outside__:text_0001": ("__outside__", "text_0001"),
    }
    for value, parts in expected.items():
        parsed = uid.parse_uid(value)
        assert parsed.parts == parts
        assert not parsed.is_virtual
        assert uid.validate_uid(value) == value


def test_virtual_role_uids_match_saved_stack_keys():
    uid = _module()
    expected = {
        uid.make_virtual_uid("outside_group"): "outside_group:__outside__",
        uid.make_virtual_uid("page", "p0001"): "page:p0001",
        uid.make_virtual_uid("coma", "p0001", "c01"): "coma:p0001:c01",
        uid.make_virtual_uid("coma_preview", "p0001", "c01"): (
            "coma_preview:p0001:c01:__preview__"
        ),
        uid.make_virtual_uid("balloon_group", "p0001", "balloon_group_0001"): (
            "balloon_group:p0001:balloon_group_0001"
        ),
    }
    for value, canonical in expected.items():
        assert value == canonical
        parsed = uid.parse_uid(value)
        assert parsed.is_virtual
        assert str(parsed) == canonical


@pytest.mark.parametrize(
    "value",
    (
        "ptr_1234abcd",
        "gp:ptr_1234abcd",
        "effect:ptr_DEADBEEF",
        "gp_folder:ptr_1",
    ),
)
def test_legacy_pointer_uids_are_detected_and_rejected(value):
    uid = _module()
    assert uid.is_legacy_pointer_uid(value)
    assert not uid.is_valid_uid(value)
    with pytest.raises(uid.LayerUIDError):
        uid.parse_uid(value)


@pytest.mark.parametrize(
    "value",
    (
        "",
        "gp:",
        "gp:gp_id:extra",
        "gp:ptr_not_hex",
        "gp: id",
        "GP:gp_0001",
        "unknown:item",
        "outside_group:other",
        "coma:p0001",
        "coma_preview:p0001:c01",
        "balloon_group:p0001:group:extra",
        "raster:p0001:raster_0001",
        "text:text_0001",
        "balloon:p0001:balloon_0001:extra",
    ),
)
def test_invalid_or_noncanonical_uids_are_rejected(value):
    uid = _module()
    assert not uid.is_valid_uid(value)
    with pytest.raises(uid.LayerUIDError):
        uid.validate_uid(value)


def test_uid_builders_do_not_stringify_missing_ids():
    uid = _module()
    with pytest.raises(uid.LayerUIDError):
        uid.make_managed_uid("gp", None)
    with pytest.raises(uid.LayerUIDError):
        uid.make_uid(None, "gp_0001")


def test_detail_data_version_defaults_and_round_trip_contract():
    uid = _module()
    assert uid.detail_data_version_from_mapping({}) == uid.LEGACY_DETAIL_DATA_VERSION
    assert uid.detail_data_version_from_mapping({"detailDataVersion": "bad"}) == 0
    assert uid.detail_data_version_from_mapping({"detailDataVersion": 1}) == 1
    assert uid.detail_data_version_from_mapping({"detailDataVersion": 7}) == 7

    new_work = SimpleNamespace()
    assert uid.detail_data_version_for_save(new_work) == uid.CURRENT_DETAIL_DATA_VERSION

    old_work = SimpleNamespace(detail_data_version=0)
    assert uid.detail_data_version_for_save(old_work) == 0

    future_work = SimpleNamespace(detail_data_version=7)
    assert uid.detail_data_version_for_save(future_work) == 7
