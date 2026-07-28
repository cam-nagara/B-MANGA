"""Stable, readable identifiers used by the certification catalog."""

from __future__ import annotations

import hashlib
import re


_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")


def slug(value: str) -> str:
    """Return a stable ASCII identifier component."""
    normalized = value.replace("\\", "/").lower()
    return _SEPARATOR_RE.sub(".", normalized).strip(".")


def feature_id(kind: str, target: str, source: str, symbol: str) -> str:
    """Return the legacy source-bound identifier (kept as an alias)."""
    return ":".join(
        ("feature", slug(kind), slug(target), slug(source), _canonical_key(symbol))
    )


def field_id(target: str, source: str, owner: str, field: str) -> str:
    """Return the legacy source-bound field identifier (kept as an alias)."""
    return ":".join(
        (
            "field",
            slug(target),
            slug(source),
            _canonical_key(owner),
            _canonical_key(field),
        )
    )


def canonical_feature_id(kind: str, target: str, semantic_key: str) -> str:
    """Return an identifier that is independent of Python file/class names."""
    return ":".join(
        ("feature", slug(kind), slug(target), _canonical_key(semantic_key))
    )


def canonical_field_id(target: str, owner_key: str, field: str) -> str:
    """Return a source-independent RNA/property field identifier."""
    return ":".join(
        (
            "field",
            slug(target),
            _canonical_key(owner_key),
            _canonical_key(field),
        )
    )


def _canonical_key(value: str) -> str:
    """Keep a readable prefix while preserving Unicode/normalization uniqueness."""
    text = str(value)
    readable = slug(text) or "value"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{readable}.{digest}"


def target_from_bl_idname(bl_idname: str, fallback: str = "bmanga") -> str:
    prefix = bl_idname.lower().split(".", 1)[0]
    if prefix == "bmanga_line":
        return "line"
    if prefix == "bmanga_render":
        return "render"
    if prefix == "bmanga":
        return "bmanga"
    return fallback


def test_id(source: str) -> str:
    return f"test:{slug(source.removesuffix('.py'))}"
