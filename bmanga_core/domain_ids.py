"""B-MANGA Domainで使う不変UID契約。"""

from __future__ import annotations

import re
import uuid
from enum import StrEnum
from typing import Callable


class UIDError(ValueError):
    """UIDが正規形式ではない。"""


class UIDKind(StrEnum):
    PROJECT = "project"
    PAGE = "page"
    COMA = "coma"
    NODE = "node"
    LINK = "link"
    ASSET = "asset"


_UID_RE = re.compile(
    r"^(project|page|coma|node|link|asset)_([0-9a-f]{32})$"
)
_TokenFactory = Callable[[], object]


def validate_uid(value: object, kind: UIDKind | str | None = None) -> str:
    """正規UIDを検証し、同じ文字列を返す。"""

    if not isinstance(value, str) or value != value.strip():
        raise UIDError("UID must be a trimmed string")
    match = _UID_RE.fullmatch(value)
    if match is None:
        raise UIDError(f"invalid UID: {value!r}")
    if kind is not None and match.group(1) != UIDKind(kind).value:
        raise UIDError(f"UID kind mismatch: {value!r}")
    return value


def uid_kind(value: object) -> UIDKind:
    return UIDKind(validate_uid(value).partition("_")[0])


def new_uid(
    kind: UIDKind | str,
    *,
    token_factory: _TokenFactory = uuid.uuid4,
) -> str:
    """衝突しない新規UIDを生成する。"""

    normalized = UIDKind(kind)
    token = token_factory()
    hex_value = str(getattr(token, "hex", token)).replace("-", "").lower()
    if not re.fullmatch(r"[0-9a-f]{32}", hex_value):
        raise UIDError("token factory must return 128-bit hexadecimal data")
    return validate_uid(f"{normalized.value}_{hex_value}", normalized)


def derived_uid(
    kind: UIDKind | str,
    namespace_uid: str,
    stable_key: str,
) -> str:
    """既存の安定keyをDomain UIDへ決定的に射影する。"""

    normalized = UIDKind(kind)
    namespace = validate_uid(namespace_uid)
    key = str(stable_key or "").strip()
    if not key:
        raise UIDError("stable key is required")
    token = uuid.uuid5(uuid.NAMESPACE_URL, f"bmanga:{namespace}:{key}")
    return new_uid(normalized, token_factory=lambda: token)


def is_uid(value: object, kind: UIDKind | str | None = None) -> bool:
    try:
        validate_uid(value, kind)
    except (UIDError, ValueError):
        return False
    return True


__all__ = (
    "UIDError",
    "UIDKind",
    "derived_uid",
    "is_uid",
    "new_uid",
    "uid_kind",
    "validate_uid",
)
