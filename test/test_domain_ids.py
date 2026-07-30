from __future__ import annotations

from types import SimpleNamespace

import pytest

from bmanga_core.domain_ids import (
    UIDError,
    UIDKind,
    derived_uid,
    is_uid,
    new_uid,
    validate_uid,
)


TOKEN = "0123456789abcdef0123456789abcdef"


def test_uid_kind_and_exact_canonical_form():
    value = new_uid(
        UIDKind.PROJECT,
        token_factory=lambda: SimpleNamespace(hex=TOKEN),
    )
    assert value == f"project_{TOKEN}"
    assert validate_uid(value, UIDKind.PROJECT) == value
    assert is_uid(value)
    assert not is_uid(value.upper())
    with pytest.raises(UIDError):
        validate_uid(value, UIDKind.PAGE)


def test_derived_uid_is_stable_and_namespace_scoped():
    project = f"project_{TOKEN}"
    page = derived_uid(UIDKind.PAGE, project, "page-1")
    assert page == derived_uid(UIDKind.PAGE, project, "page-1")
    assert page != derived_uid(UIDKind.PAGE, project, "page-2")
    assert validate_uid(page, UIDKind.PAGE) == page


@pytest.mark.parametrize(
    "value",
    [
        "",
        " page_0123456789abcdef0123456789abcdef",
        "p0001",
        "page_0123",
        "unknown_0123456789abcdef0123456789abcdef",
    ],
)
def test_legacy_and_ambiguous_ids_are_rejected(value):
    with pytest.raises(UIDError):
        validate_uid(value)
