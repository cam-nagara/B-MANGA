"""Blender上の実データを詳細設定の固定対象へ変換する。

入口で一度だけ対象を解決し、以後は :class:`DetailTarget` に保持した参照と
永続IDを使う。画面上の効果線表示物は編集本体の管理Objectへ正規化する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    import bpy
except ModuleNotFoundError:  # 純Python契約テストからもimportできるようにする
    bpy = None  # type: ignore[assignment]

from .detail_dialog import (
    DetailContractError,
    DetailTarget,
    DetailTargetIdentity,
    DetailTargetNotFoundError,
    normalize_detail_kind,
)


SUPPORTED_ACTUAL_KINDS = frozenset(
    {
        "page",
        "coma",
        "gp",
        "layer_folder",
        "image",
        "image_path",
        "raster",
        "fill",
        "balloon",
        "text",
        "effect",
    }
)
REJECTED_STACK_KINDS = frozenset(
    {"outside", "outside_group", "coma_preview", "balloon_group", "effect_legacy"}
)


def actual_detail_kind_is_supported(kind: str) -> bool:
    """実レイヤーの詳細設定を開ける種別かを入口共通で判定する。"""

    raw_kind = str(kind or "").strip().lower()
    if raw_kind in REJECTED_STACK_KINDS:
        return False
    try:
        normalized = normalize_detail_kind(raw_kind)
    except DetailContractError:
        return False
    return normalized in SUPPORTED_ACTUAL_KINDS


def can_open_actual_detail(kind: str, target) -> bool:
    """一覧や右クリックに詳細設定を表示してよい実体かを返す。"""

    return target is not None and actual_detail_kind_is_supported(kind)


@dataclass(frozen=True, slots=True)
class _LocatedTarget:
    kind: str
    stable_id: str
    data: Any
    object_ref: Any = None
    owner_id: str = ""


def is_pointer_derived_uid(value: object) -> bool:
    """メモリアドレス由来の旧UID表現を保守的に検出する。"""

    text = str(value or "").strip().lower()
    if not text:
        return False
    try:
        from . import layer_uid

        if layer_uid.is_legacy_pointer_uid(text):
            return True
    except Exception:
        # Blender外の契約テストではlayer_uidを読めないため、下の文字列判定へ進む。
        pass
    parts = text.replace("-", "_").split(":")
    return any(part == "ptr" or part.startswith("ptr_") for part in parts)


def resolve_target_from_stack(context, stack_uid: str) -> DetailTarget:
    """正規UIDに一致した一覧行を一度だけ解決して固定対象を返す。"""

    uid = str(stack_uid or "").strip()
    if not uid or is_pointer_derived_uid(uid):
        raise DetailContractError("永続IDではないレイヤーは詳細設定を開けません")
    item = _find_stack_item(context, uid)
    if item is None:
        raise DetailTargetNotFoundError(uid)
    kind = str(getattr(item, "kind", "") or "").strip()
    _require_actual_kind(kind)
    from . import layer_stack

    resolved = layer_stack.resolve_stack_item(context, item)
    return _target_from_stack_result(context, uid, item, resolved)


def _find_stack_item(context, uid: str):
    scene = getattr(context, "scene", None)
    stack = getattr(scene, "bmanga_layer_stack", None) if scene is not None else None
    if stack is None:
        return None
    from . import layer_stack

    for item in stack:
        if layer_stack.stack_item_uid(item) == uid:
            return item
    return None


def _target_from_stack_result(context, uid: str, item, resolved) -> DetailTarget:
    kind = str(getattr(item, "kind", "") or "").strip()
    if not resolved or resolved.get("target") is None:
        raise DetailTargetNotFoundError(uid)
    data = resolved["target"]
    obj = resolved.get("object")
    stable_id = _stable_id_for_stack_item(kind, item, data, obj)
    params = _target_params(
        context,
        kind,
        owner_id=str(getattr(resolved.get("page"), "id", "") or ""),
    )
    namespace = _target_namespace(kind, data)
    return DetailTarget(
        kind,
        stable_id,
        uid,
        data,
        object_ref=obj,
        params=params,
        namespace=namespace,
    )


def _stable_id_for_stack_item(kind: str, item, data, obj) -> str:
    if kind in {"gp", "effect"}:
        from . import layer_object_model

        value = layer_object_model.stable_id(obj)
    elif kind == "layer_folder":
        from . import layer_folder

        value = layer_folder.folder_key(data)
    elif kind == "coma":
        value = str(getattr(item, "key", "") or "")
    elif kind in {"balloon", "text"}:
        # フキダシ／テキストIDはページごとに再利用される。一覧キーに含まれる
        # ページIDを落とすと、別ページの同名要素へ誤適用される。
        owner_id, separator, entry_id = str(getattr(item, "key", "") or "").partition(":")
        if kind == "text":
            from . import text_real_object

            if owner_id == "__outside__":
                owner_id = text_real_object.OUTSIDE_PAGE_ID
            value = (
                text_real_object.text_object_bmanga_id_for_values(owner_id, entry_id)
                if separator
                else ""
            )
        else:
            value = f"{owner_id}:{entry_id}" if separator and owner_id and entry_id else ""
    else:
        value = str(getattr(data, "id", "") or getattr(item, "key", "") or "")
    if not value or is_pointer_derived_uid(value):
        raise DetailContractError("詳細対象に永続IDがありません")
    return value


def resolve_target_from_object(context, object_or_id, kind: str = "") -> DetailTarget:
    """Objectまたはkind+安定IDから実対象を解決する。"""

    obj = object_or_id if _looks_like_object(object_or_id) else None
    if obj is not None:
        obj = normalize_effect_controller_object(obj)
        resolved_kind = _object_value(obj, "bmanga_kind")
        stable_id = _object_value(obj, "bmanga_id")
    else:
        resolved_kind = str(kind or "").strip()
        stable_id = str(object_or_id or "").strip()
    _require_actual_kind(resolved_kind)
    if not stable_id or is_pointer_derived_uid(stable_id):
        raise DetailContractError("詳細対象に永続IDがありません")
    located = _locate_target(context, resolved_kind, stable_id, preferred_object=obj)
    if located is None:
        raise DetailTargetNotFoundError(f"{resolved_kind}:{stable_id}")
    stack_uid = _matching_stack_uid(context, located)
    params = _target_params(context, resolved_kind, owner_id=located.owner_id)
    return DetailTarget(
        resolved_kind,
        located.stable_id,
        stack_uid,
        located.data,
        object_ref=located.object_ref,
        params=params,
        namespace=_target_namespace(resolved_kind, located.data),
    )


def resolve_target_from_selected_object(context, obj=None) -> DetailTarget:
    """3Dビュー／アウトライナーの選択から対象を一度だけ確定する。"""

    if obj is not None:
        return resolve_target_from_object(context, obj)
    for candidate in _selected_object_candidates(context):
        try:
            return resolve_target_from_object(context, candidate)
        except (DetailContractError, DetailTargetNotFoundError):
            continue
    raise DetailTargetNotFoundError("selected object")


def _selected_object_candidates(context):
    seen: set[int] = set()
    selected_ids = tuple(getattr(context, "selected_ids", None) or ())
    active = (getattr(context, "active_object", None),)
    selected_objects = tuple(getattr(context, "selected_objects", None) or ())
    area = getattr(context, "area", None)
    groups = (
        (selected_ids, active, selected_objects)
        if str(getattr(area, "type", "") or "") == "OUTLINER"
        else (active, selected_objects, selected_ids)
    )
    view_layer = getattr(context, "view_layer", None)
    active = getattr(getattr(view_layer, "objects", None), "active", None)
    for candidate in (*groups, (active,)):
        for obj in candidate:
            if not _looks_like_object(obj) or id(obj) in seen:
                continue
            seen.add(id(obj))
            yield obj


def normalize_effect_controller_object(obj):
    """効果線の表示Mesh／補助Objectなら非表示の管理Objectへ戻す。"""

    if obj is None:
        return None
    controller_id = _object_value(obj, "bmanga_effect_controller_id")
    if not controller_id:
        return obj
    from . import layer_object_model

    controller = layer_object_model.find_layer_object("effect", controller_id)
    return controller if controller is not None else obj


def _locate_target(context, kind: str, stable_id: str, *, preferred_object=None):
    if kind in {"gp", "effect"}:
        return _locate_object_layer(kind, stable_id, preferred_object)
    if kind in {"image", "image_path", "raster", "fill"}:
        return _locate_scene_entry(context, kind, stable_id, preferred_object)
    if kind in {"balloon", "text"}:
        return _locate_page_entry(context, kind, stable_id, preferred_object)
    if kind == "page":
        return _locate_page(context, stable_id)
    if kind == "coma":
        return _locate_coma(context, stable_id)
    if kind == "layer_folder":
        return _locate_folder(context, stable_id)
    return None


def _locate_object_layer(kind: str, stable_id: str, preferred_object=None):
    from . import layer_object_model

    obj = preferred_object
    if not layer_object_model.is_layer_object(obj, kind):
        obj = layer_object_model.find_layer_object(kind, stable_id)
    if obj is None or layer_object_model.stable_id(obj) != stable_id:
        return None
    data = layer_object_model.content_layer(obj)
    return _LocatedTarget(kind, stable_id, data, obj) if data is not None else None


_SCENE_COLLECTIONS = {
    "image": "bmanga_image_layers",
    "image_path": "bmanga_image_path_layers",
    "raster": "bmanga_raster_layers",
    "fill": "bmanga_fill_layers",
}


def _locate_scene_entry(context, kind: str, stable_id: str, preferred_object=None):
    scene = getattr(context, "scene", None)
    collection = getattr(scene, _SCENE_COLLECTIONS[kind], None) if scene is not None else None
    entry = _find_entry(collection, stable_id)
    if entry is None:
        return None
    obj = preferred_object or _find_managed_object(kind, stable_id)
    return _LocatedTarget(kind, stable_id, entry, obj)


def _locate_page_entry(context, kind: str, stable_id: str, preferred_object=None):
    work = _work(context)
    if work is None:
        return None
    if kind == "text":
        return _locate_text_entry(work, stable_id, preferred_object)
    return _locate_balloon_entry(context, work, stable_id, preferred_object)


def _locate_balloon_entry(context, work, stable_id: str, preferred_object=None):
    owner_id, separator, entry_id = str(stable_id or "").partition(":")
    if separator and owner_id and entry_id:
        return _locate_balloon_entry_by_owner(
            work,
            owner_id,
            entry_id,
            preferred_object,
        )
    if preferred_object is not None:
        located = _locate_balloon_entry_from_stack(context, preferred_object)
        if located is not None:
            return located
        owner_id = _balloon_owner_from_object(work, preferred_object)
        if owner_id:
            located = _locate_balloon_entry_by_owner(
                work,
                owner_id,
                stable_id,
                preferred_object,
            )
            if located is not None:
                return located
    # 旧Object IDだけを受け取った場合も、一意な作品だけは安全に開ける。
    # 複数ページに同じIDがあれば先頭を推測せず中止する。
    matches = _matching_balloon_entries(work, stable_id)
    if len(matches) != 1:
        return None
    owner_id, entry = matches[0]
    canonical = f"{owner_id}:{stable_id}"
    obj = preferred_object or _find_managed_object("balloon", stable_id)
    return _LocatedTarget("balloon", canonical, entry, obj, owner_id)


def _locate_balloon_entry_by_owner(work, owner_id: str, entry_id: str, preferred_object=None):
    if owner_id == "__outside__":
        entry = _find_entry(getattr(work, "shared_balloons", None), entry_id)
    else:
        page = next(
            (
                candidate
                for candidate in getattr(work, "pages", ())
                if str(getattr(candidate, "id", "") or "") == owner_id
            ),
            None,
        )
        entry = _find_entry(getattr(page, "balloons", None), entry_id) if page is not None else None
    if entry is None:
        return None
    canonical = f"{owner_id}:{entry_id}"
    obj = preferred_object or _find_managed_object("balloon", entry_id)
    return _LocatedTarget("balloon", canonical, entry, obj, owner_id)


def _locate_balloon_entry_from_stack(context, preferred_object):
    scene = getattr(context, "scene", None)
    stack = getattr(scene, "bmanga_layer_stack", None) if scene is not None else None
    if stack is None:
        return None
    from . import layer_stack

    for item in stack:
        if str(getattr(item, "kind", "") or "") != "balloon":
            continue
        resolved = layer_stack.resolve_stack_item(context, item)
        if not resolved or resolved.get("object") is not preferred_object:
            continue
        entry = resolved.get("target")
        page = resolved.get("page")
        owner_id = str(getattr(page, "id", "") or "__outside__")
        entry_id = str(getattr(entry, "id", "") or "")
        if entry is None or not entry_id:
            return None
        canonical = f"{owner_id}:{entry_id}"
        return _LocatedTarget("balloon", canonical, entry, preferred_object, owner_id)
    return None


def _matching_balloon_entries(work, entry_id: str):
    matches = []
    for page in getattr(work, "pages", ()):
        entry = _find_entry(getattr(page, "balloons", None), entry_id)
        if entry is not None:
            matches.append((str(getattr(page, "id", "") or ""), entry))
    outside = _find_entry(getattr(work, "shared_balloons", None), entry_id)
    if outside is not None:
        matches.append(("__outside__", outside))
    return matches


def _balloon_owner_from_object(work, obj) -> str:
    """Objectの保存済み所属キーからページを一意に逆引きする。"""

    parent_key = _object_value(obj, "bmanga_parent_key")
    if not parent_key:
        return "__outside__"
    pages = tuple(getattr(work, "pages", ()))
    direct = [
        str(getattr(page, "id", "") or "")
        for page in pages
        if str(getattr(page, "id", "") or "") == parent_key
    ]
    if len(direct) == 1:
        return direct[0]
    coma_owners = []
    for page in pages:
        if _find_coma(getattr(page, "comas", ()), parent_key) is not None:
            coma_owners.append(str(getattr(page, "id", "") or ""))
    if len(coma_owners) == 1:
        return coma_owners[0]
    return ""


def _locate_text_entry(work, stable_id: str, preferred_object=None):
    from . import text_real_object

    owner_id, separator, entry_id = str(stable_id or "").partition(":")
    if not separator or not owner_id or not entry_id:
        # ページを欠く旧IDは重複時に区別できないため、推測で先頭へ結び付けない。
        return None
    if owner_id in {"__outside__", text_real_object.OUTSIDE_PAGE_ID}:
        entry = _find_entry(getattr(work, "shared_texts", None), entry_id)
        if entry is None:
            return None
        canonical = text_real_object.text_object_bmanga_id_for_values(
            text_real_object.OUTSIDE_PAGE_ID,
            entry_id,
        )
        obj = preferred_object or _find_managed_object("text", canonical)
        return _LocatedTarget("text", canonical, entry, obj, text_real_object.OUTSIDE_PAGE_ID)
    page = next(
        (
            candidate
            for candidate in getattr(work, "pages", ())
            if str(getattr(candidate, "id", "") or "") == owner_id
        ),
        None,
    )
    if page is None:
        return None
    entry = _find_entry(getattr(page, "texts", None), entry_id)
    if entry is None:
        return None
    canonical = text_real_object.text_object_bmanga_id_for_values(owner_id, entry_id)
    obj = preferred_object or _find_managed_object("text", canonical)
    return _LocatedTarget("text", canonical, entry, obj, owner_id)


def _locate_page(context, stable_id: str):
    work = _work(context)
    entry = _find_entry(getattr(work, "pages", None), stable_id) if work is not None else None
    return _LocatedTarget("page", stable_id, entry) if entry is not None else None


def _locate_coma(context, stable_id: str):
    work = _work(context)
    if work is None:
        return None
    requested_owner, separator, requested_id = stable_id.partition(":")
    for owner_id, collection in _coma_collections(work):
        if separator and owner_id != requested_owner:
            continue
        entry_id = requested_id if separator else stable_id
        entry = _find_coma(collection, entry_id)
        if entry is not None:
            canonical = f"{owner_id}:{entry_id}"
            return _LocatedTarget("coma", canonical, entry, owner_id=owner_id)
    return None


def _coma_collections(work):
    for page in getattr(work, "pages", ()):
        yield str(getattr(page, "id", "") or ""), getattr(page, "comas", ())
    yield "__outside__", getattr(work, "shared_comas", ())


def _find_coma(collection, stable_id: str):
    for entry in collection or ():
        value = str(getattr(entry, "coma_id", "") or getattr(entry, "id", "") or "")
        if value == stable_id:
            return entry
    return None


def _locate_folder(context, stable_id: str):
    work = _work(context)
    if work is None:
        return None
    from . import layer_folder

    for entry in getattr(work, "layer_folders", ()):
        if layer_folder.folder_key(entry) == stable_id:
            return _LocatedTarget("layer_folder", stable_id, entry)
    return None


def _matching_stack_uid(context, located: _LocatedTarget) -> str | None:
    scene = getattr(context, "scene", None)
    stack = getattr(scene, "bmanga_layer_stack", None) if scene is not None else None
    if stack is None:
        return None
    from . import layer_stack

    for item in stack:
        if str(getattr(item, "kind", "") or "") != located.kind:
            continue
        resolved = layer_stack.resolve_stack_item(context, item)
        if _resolved_matches(located, resolved):
            uid = layer_stack.stack_item_uid(item)
            return None if is_pointer_derived_uid(uid) else uid
    return None


def _resolved_matches(located: _LocatedTarget, resolved) -> bool:
    if not resolved or resolved.get("target") is None:
        return False
    if located.object_ref is not None and resolved.get("object") is located.object_ref:
        return True
    target = resolved.get("target")
    if target is located.data:
        return True
    return _safe_rna_equal(target, located.data)


def target_is_live(context, target_or_identity) -> bool:
    """固定対象が同じkind／安定IDのまま存在するかを検証する。"""

    identity = (
        DetailTargetIdentity.from_target(target_or_identity)
        if isinstance(target_or_identity, DetailTarget)
        else target_or_identity
    )
    if not isinstance(identity, DetailTargetIdentity):
        return False
    try:
        target = _resolve_identity(context, identity)
    except (DetailContractError, DetailTargetNotFoundError, ReferenceError):
        return False
    return target.kind == identity.kind and target.stable_id == identity.stable_id


def _resolve_identity(context, identity: DetailTargetIdentity) -> DetailTarget:
    if identity.stack_uid:
        target = resolve_target_from_stack(context, identity.stack_uid)
        if target.stable_id != identity.stable_id:
            raise DetailTargetNotFoundError(identity.stable_id)
        return target
    return resolve_target_from_object(context, identity.stable_id, identity.kind)


def make_target_liveness_validator(context):
    """DetailSessionへ渡せる固定contextの生存確認関数を返す。"""

    return lambda identity: target_is_live(context, identity)


def _effect_params(context):
    scene = getattr(context, "scene", None)
    params = getattr(scene, "bmanga_effect_line_params", None) if scene is not None else None
    if params is None:
        raise DetailContractError("効果線の設定が初期化されていません")
    return params


def _target_params(context, kind: str, *, owner_id: str = ""):
    if kind == "effect":
        return _effect_params(context)
    if kind not in {"balloon", "text"}:
        return None
    page = _locate_page(context, owner_id)
    return {
        "page_id": str(owner_id or ""),
        "page": page.data if page is not None else None,
    }


def _target_namespace(kind: str, data) -> str | None:
    if kind != "fill":
        return None
    return "gradient" if str(getattr(data, "fill_type", "solid") or "solid") == "gradient" else "fill"


def _require_actual_kind(kind: str) -> None:
    if not actual_detail_kind_is_supported(kind):
        raise DetailContractError(f"詳細設定の実体がない種別です: {kind or 'unknown'}")


def _find_entry(collection, stable_id: str):
    for entry in collection or ():
        if str(getattr(entry, "id", "") or "") == stable_id:
            return entry
    return None


def _work(context):
    scene = getattr(context, "scene", None)
    return getattr(scene, "bmanga_work", None) if scene is not None else None


def _find_managed_object(kind: str, stable_id: str):
    if bpy is None:
        return None
    from . import object_naming

    return object_naming.find_object_by_bmanga_id(stable_id, kind=kind)


def _looks_like_object(value) -> bool:
    return value is not None and hasattr(value, "get") and hasattr(value, "name")


def _object_value(obj, key: str) -> str:
    try:
        return str(obj.get(key, "") or "").strip()
    except (AttributeError, ReferenceError):
        return ""


def _safe_rna_equal(left, right) -> bool:
    try:
        return bool(left == right)
    except Exception:
        # 破棄済みRNA参照は同一対象とみなさず、安全側で再解決を拒否する。
        return False


__all__ = [
    "actual_detail_kind_is_supported",
    "can_open_actual_detail",
    "REJECTED_STACK_KINDS",
    "SUPPORTED_ACTUAL_KINDS",
    "is_pointer_derived_uid",
    "make_target_liveness_validator",
    "normalize_effect_controller_object",
    "resolve_target_from_object",
    "resolve_target_from_selected_object",
    "resolve_target_from_stack",
    "target_is_live",
]
