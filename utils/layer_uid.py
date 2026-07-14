"""統合レイヤー一覧で使う永続 UID の純 Python 契約.

Blender の ``as_pointer()``、表示名、一覧 index は保存後に安定しないため、
管理オブジェクト／フォルダーの永続 ID と行種別だけから UID を組み立てる。
このモジュールは移行前検査からも使えるよう Blender API に依存しない。
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
import re
from typing import Any


CURRENT_DETAIL_DATA_VERSION = 1
LEGACY_DETAIL_DATA_VERSION = 0
DETAIL_DATA_VERSION_KEY = "detailDataVersion"
SCENE_DETAIL_DATA_VERSION_PROP = "bmanga_detail_data_version"

MANAGED_UID_KINDS = frozenset({"gp", "effect", "layer_folder"})
# リンク保存には管理Objectだけでなく、Scene上の実レイヤーとページ内要素も入る。
# いずれも表示名やindexではなく保存済みIDから決定できるため、正規UIDとして
# 厳密に検証する。balloon/textはページIDを含む2要素のキーを必須にする。
DIRECT_ENTRY_UID_KINDS = frozenset({"raster", "image"})
PAGE_ENTRY_UID_KINDS = frozenset({"balloon", "text"})
VIRTUAL_UID_KINDS = frozenset(
    {"page", "coma", "outside_group", "coma_preview", "balloon_group"}
)
SUPPORTED_UID_KINDS = (
    MANAGED_UID_KINDS
    | DIRECT_ENTRY_UID_KINDS
    | PAGE_ENTRY_UID_KINDS
    | VIRTUAL_UID_KINDS
)

OUTSIDE_GROUP_KEY = "__outside__"
COMA_PREVIEW_MARKER = "__preview__"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_LEGACY_POINTER_UID_RE = re.compile(
    r"^(?:[a-z][a-z0-9_]*:)?ptr_[0-9a-f]+$",
    re.IGNORECASE,
)


class LayerUIDError(ValueError):
    """正規 UID として扱えない値を示す。"""


@dataclass(frozen=True, slots=True)
class LayerUID:
    """検証済みの正規 UID。"""

    kind: str
    key: str
    parts: tuple[str, ...]
    is_virtual: bool

    @property
    def value(self) -> str:
        return f"{self.kind}:{self.key}"

    def __str__(self) -> str:
        return self.value


def is_legacy_pointer_uid(value: object) -> bool:
    """旧 ``ptr_<address>`` UID なら True を返す。"""
    if not isinstance(value, str):
        return False
    return _LEGACY_POINTER_UID_RE.fullmatch(value.strip()) is not None


def _require_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise LayerUIDError(f"{label} must be a trimmed string")
    if not _IDENTIFIER_RE.fullmatch(value):
        raise LayerUIDError(f"invalid {label}: {value!r}")
    return value


def _managed_parts(kind: str, key: str) -> tuple[str, ...]:
    if ":" in key:
        raise LayerUIDError(f"managed UID key cannot contain ':': {key!r}")
    stable_id = _require_identifier(key, f"{kind} stable id")
    if stable_id.lower().startswith("ptr_"):
        raise LayerUIDError("legacy pointer value is not a stable id")
    return (stable_id,)


def _page_entry_parts(kind: str, key: str) -> tuple[str, ...]:
    parts = tuple(key.split(":"))
    if len(parts) != 2:
        raise LayerUIDError(f"{kind} UID requires page id and entry id")
    owner, entry_id = parts
    if owner != OUTSIDE_GROUP_KEY:
        _require_identifier(owner, f"{kind} key part 1")
    _require_identifier(entry_id, f"{kind} key part 2")
    return parts


def _virtual_parts(kind: str, key: str) -> tuple[str, ...]:
    parts = tuple(key.split(":"))
    if kind == "outside_group":
        if parts != (OUTSIDE_GROUP_KEY,):
            raise LayerUIDError("outside_group must use the canonical outside key")
        return parts
    if kind == "page":
        expected = 1
    elif kind in {"coma", "balloon_group"}:
        expected = 2
    elif kind == "coma_preview":
        expected = 3
        if len(parts) == expected and parts[-1] != COMA_PREVIEW_MARKER:
            raise LayerUIDError("coma_preview must end with the preview marker")
    else:
        raise LayerUIDError(f"unsupported virtual UID kind: {kind!r}")
    if len(parts) != expected:
        raise LayerUIDError(f"{kind} UID requires {expected} key parts")
    checked = parts[:-1] if kind == "coma_preview" else parts
    for index, part in enumerate(checked):
        _require_identifier(part, f"{kind} key part {index + 1}")
    return parts


def parse_uid(value: object) -> LayerUID:
    """UID を厳密に解析し、非正規値なら ``LayerUIDError`` を送出する。"""
    if not isinstance(value, str) or value != value.strip():
        raise LayerUIDError("UID must be a trimmed string")
    if is_legacy_pointer_uid(value):
        raise LayerUIDError("legacy pointer UID is not canonical")
    kind, separator, key = value.partition(":")
    if not separator or not kind or not key:
        raise LayerUIDError(f"invalid UID syntax: {value!r}")
    if kind not in SUPPORTED_UID_KINDS:
        raise LayerUIDError(f"unsupported UID kind: {kind!r}")
    if kind in MANAGED_UID_KINDS | DIRECT_ENTRY_UID_KINDS:
        parts = _managed_parts(kind, key)
        virtual = False
    elif kind in PAGE_ENTRY_UID_KINDS:
        parts = _page_entry_parts(kind, key)
        virtual = False
    else:
        parts = _virtual_parts(kind, key)
        virtual = True
    return LayerUID(kind=kind, key=key, parts=parts, is_virtual=virtual)


def validate_uid(value: object) -> str:
    """検証済みの正規 UID 文字列を返す。"""
    return parse_uid(value).value


def is_valid_uid(value: object) -> bool:
    try:
        parse_uid(value)
    except LayerUIDError:
        return False
    return True


def make_uid(kind: str, key: str) -> str:
    """既存の ``kind`` と保存済み ``key`` から正規 UID を作る。"""
    if not isinstance(kind, str) or not isinstance(key, str):
        raise LayerUIDError("UID kind and key must be strings")
    return validate_uid(f"{kind}:{key}")


def make_managed_uid(kind: str, stable_id: str) -> str:
    """管理 GP／効果線／汎用フォルダーの安定 UID を作る。"""
    if kind not in MANAGED_UID_KINDS:
        raise LayerUIDError(f"not a managed UID kind: {kind!r}")
    return make_uid(kind, stable_id)


def make_virtual_uid(role: str, *owner_ids: str) -> str:
    """保存実体を持たない一覧行の決定的 UID を作る。"""
    if role == "outside_group":
        if owner_ids:
            raise LayerUIDError("outside_group takes no owner id")
        key = OUTSIDE_GROUP_KEY
    elif role == "page":
        if len(owner_ids) != 1:
            raise LayerUIDError("page requires one owner id")
        key = owner_ids[0]
    elif role in {"coma", "balloon_group"}:
        if len(owner_ids) != 2:
            raise LayerUIDError(f"{role} requires two owner ids")
        key = ":".join(owner_ids)
    elif role == "coma_preview":
        if len(owner_ids) != 2:
            raise LayerUIDError("coma_preview requires page and coma ids")
        key = ":".join((*owner_ids, COMA_PREVIEW_MARKER))
    else:
        raise LayerUIDError(f"unsupported virtual UID role: {role!r}")
    return make_uid(role, key)


def normalize_detail_data_version(value: object, *, default: int) -> int:
    """非負整数の形式版だけを受け入れ、未知の将来版も保持する。"""
    fallback = max(0, int(default))
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value if value >= 0 else fallback
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return fallback


def detail_data_version_from_mapping(data: object) -> int:
    """旧 work.json の項目欠落を形式版 0 として読む。"""
    if not isinstance(data, Mapping):
        return LEGACY_DETAIL_DATA_VERSION
    return normalize_detail_data_version(
        data.get(DETAIL_DATA_VERSION_KEY),
        default=LEGACY_DETAIL_DATA_VERSION,
    )


def detail_data_version_for_save(work: Any) -> int:
    """通常保存で現在の形式版を落とさず、未定義の新規作品は現行版にする。"""
    return normalize_detail_data_version(
        getattr(work, "detail_data_version", CURRENT_DETAIL_DATA_VERSION),
        default=CURRENT_DETAIL_DATA_VERSION,
    )


def scene_detail_data_version(scene: Any) -> int:
    """ページ用blendに保存した形式版を読む。欠落は旧版として扱う。"""

    if scene is None:
        return LEGACY_DETAIL_DATA_VERSION
    try:
        value = scene.get(SCENE_DETAIL_DATA_VERSION_PROP)
    except (AttributeError, TypeError):
        return LEGACY_DETAIL_DATA_VERSION
    return normalize_detail_data_version(
        value,
        default=LEGACY_DETAIL_DATA_VERSION,
    )


def stamp_scene_detail_data_version(scene: Any) -> None:
    """現在のページ形式版をSceneへ永続スタンプする。"""

    if scene is None:
        raise LayerUIDError("scene is required for detail data version stamp")
    scene[SCENE_DETAIL_DATA_VERSION_PROP] = CURRENT_DETAIL_DATA_VERSION
