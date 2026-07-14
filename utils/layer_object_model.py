"""GP・効果線の「1管理Object＝1レイヤー」共通操作。

通常経路は Object の永続 ``bmanga_id`` を正本とする。Grease Pencil 内部の
``content`` レイヤーは描画データの格納先に限定し、一覧の識別子には使わない。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional
import uuid

import bpy

from . import log
from . import object_naming as on

_logger = log.get_logger(__name__)

LAYER_OBJECT_KINDS = frozenset({"gp", "effect"})
PROP_USER_VISIBLE = "bmanga_user_visible"
PROP_USER_LOCKED = "bmanga_user_locked"
CONTENT_LAYER_NAME = "content"
INTERNAL_MASK_LAYER_NAME = "__bmanga_mask"


def make_stable_id(kind: str) -> str:
    normalized = str(kind or "").strip()
    if normalized not in LAYER_OBJECT_KINDS:
        raise ValueError(f"unsupported layer object kind: {kind!r}")
    for length in (12, 16, 24, 32):
        candidate = f"{normalized}_{uuid.uuid4().hex[:length]}"
        if on.find_object_by_bmanga_id(candidate, kind=normalized) is None:
            return candidate
    raise RuntimeError("安定IDを生成できませんでした")


def stable_id(obj: bpy.types.Object | None) -> str:
    if obj is None:
        return ""
    return str(obj.get(on.PROP_ID, "") or "").strip()


def layer_kind(obj: bpy.types.Object | None) -> str:
    if obj is None:
        return ""
    return str(obj.get(on.PROP_KIND, "") or "").strip()


def is_layer_object(obj: bpy.types.Object | None, kind: str = "") -> bool:
    if obj is None or getattr(obj, "type", "") != "GREASEPENCIL":
        return False
    actual = layer_kind(obj)
    if actual not in LAYER_OBJECT_KINDS:
        return False
    if kind and actual != str(kind):
        return False
    return bool(obj.get(on.PROP_MANAGED, False) and stable_id(obj))


def iter_layer_objects(kind: str = "") -> Iterable[bpy.types.Object]:
    objects = [obj for obj in bpy.data.objects if is_layer_object(obj, kind)]
    objects.sort(
        key=lambda obj: (
            int(obj.get(on.PROP_Z_INDEX, 0) or 0),
            stable_id(obj),
        ),
        reverse=True,
    )
    yield from objects


def find_layer_object(kind: str, bmanga_id: str) -> Optional[bpy.types.Object]:
    candidate = on.find_object_by_bmanga_id(str(bmanga_id or ""), kind=str(kind or ""))
    return candidate if is_layer_object(candidate, kind) else None


def content_layer(obj: bpy.types.Object | None):
    if not is_layer_object(obj):
        return None
    layers = getattr(getattr(obj, "data", None), "layers", None)
    if layers is None or len(layers) == 0:
        return None
    layer = layers.get(CONTENT_LAYER_NAME)
    if layer is not None:
        return layer
    if len(layers) == 1:
        return layers[0]
    active = getattr(layers, "active", None)
    return active if active is not None else layers[0]


def validate_single_content_layer(obj: bpy.types.Object | None) -> tuple[bool, str]:
    if not is_layer_object(obj):
        return False, "B-MANGAの手描き／効果線オブジェクトではありません"
    layers = getattr(getattr(obj, "data", None), "layers", None)
    content_layers = [
        layer
        for layer in (layers or ())
        if str(getattr(layer, "name", "") or "") != INTERNAL_MASK_LAYER_NAME
    ]
    if len(content_layers) != 1:
        return False, f"内部描画レイヤー数が1ではありません（{len(content_layers)}）"
    if content_layer(obj) is None:
        return False, "内部描画レイヤーが見つかりません"
    return True, ""


def display_title(obj: bpy.types.Object | None) -> str:
    if obj is None:
        return ""
    title = str(obj.get(on.PROP_TITLE, "") or "").strip()
    if title:
        return title
    parsed = on.parse_canonical_name(str(getattr(obj, "name", "") or ""))
    return str(parsed[2]).strip() if parsed is not None else str(obj.name)


def parent_key(obj: bpy.types.Object | None) -> str:
    if obj is None:
        return ""
    return str(obj.get(on.PROP_PARENT_KEY, "") or "")


def folder_id(obj: bpy.types.Object | None) -> str:
    if obj is None:
        return ""
    return str(obj.get(on.PROP_FOLDER_ID, "") or "")


def z_index(obj: bpy.types.Object | None) -> int:
    if obj is None:
        return 0
    try:
        return int(obj.get(on.PROP_Z_INDEX, 0) or 0)
    except (TypeError, ValueError):
        return 0


def user_visible(obj: bpy.types.Object | None) -> bool:
    if not is_layer_object(obj):
        return False
    if PROP_USER_VISIBLE in obj:
        return bool(obj.get(PROP_USER_VISIBLE, True))
    layer = content_layer(obj)
    if layer is not None and hasattr(layer, "hide"):
        return not bool(layer.hide)
    return not bool(getattr(obj, "hide_viewport", False))


def user_locked(obj: bpy.types.Object | None) -> bool:
    if not is_layer_object(obj):
        return False
    if PROP_USER_LOCKED in obj:
        return bool(obj.get(PROP_USER_LOCKED, False))
    layer = content_layer(obj)
    if layer is not None and hasattr(layer, "lock"):
        return bool(layer.lock)
    return bool(getattr(obj, "hide_select", False))


def initialize_user_state(obj: bpy.types.Object | None) -> None:
    if not is_layer_object(obj):
        return
    if PROP_USER_VISIBLE not in obj:
        obj[PROP_USER_VISIBLE] = user_visible(obj)
    if PROP_USER_LOCKED not in obj:
        obj[PROP_USER_LOCKED] = user_locked(obj)


def _effect_aux_objects(obj: bpy.types.Object) -> tuple[bpy.types.Object, ...]:
    try:
        from . import effect_line_object as elo
        from . import effect_line_path as elp

        values = (
            elo.find_effect_display_object(obj),
            elo.find_effect_frame_source_object(obj),
            elo.find_effect_density_source_object(obj),
            elp.find_effect_line_image_object(obj),
            elp.find_effect_base_path_object(obj),
        )
    except Exception:  # noqa: BLE001
        values = ()
    return tuple(candidate for candidate in values if candidate is not None)


def set_user_visible(obj: bpy.types.Object | None, visible: bool) -> bool:
    if not is_layer_object(obj):
        return False
    value = bool(visible)
    obj[PROP_USER_VISIBLE] = value
    layer = content_layer(obj)
    if layer is not None and hasattr(layer, "hide"):
        layer.hide = not value
    if layer_kind(obj) == "effect":
        obj.hide_viewport = True
        obj.hide_render = True
        for aux in _effect_aux_objects(obj):
            try:
                aux.hide_viewport = not value
                aux.hide_render = not value
            except Exception:  # noqa: BLE001
                pass
    else:
        obj.hide_viewport = not value
        obj.hide_render = not value
    return True


def set_user_locked(obj: bpy.types.Object | None, locked: bool) -> bool:
    if not is_layer_object(obj):
        return False
    value = bool(locked)
    obj[PROP_USER_LOCKED] = value
    layer = content_layer(obj)
    if layer is not None and hasattr(layer, "lock"):
        layer.lock = value
    obj.hide_select = value
    if layer_kind(obj) == "effect":
        for aux in _effect_aux_objects(obj):
            try:
                aux.hide_select = value
            except Exception:  # noqa: BLE001
                pass
    return True


def set_display_title(obj: bpy.types.Object | None, title: str) -> bool:
    if not is_layer_object(obj):
        return False
    clean = str(title or "").strip()
    if not clean:
        return False
    obj[on.PROP_TITLE] = clean
    on.assign_canonical_name(obj, layer_kind(obj), z_index(obj), layer_kind(obj), clean)
    return True


def set_folder_id(obj: bpy.types.Object | None, value: str) -> bool:
    if not is_layer_object(obj):
        return False
    obj[on.PROP_FOLDER_ID] = str(value or "")
    return True


def set_parent_key(obj: bpy.types.Object | None, value: str) -> bool:
    if not is_layer_object(obj):
        return False
    obj[on.PROP_PARENT_KEY] = str(value or "")
    layer = content_layer(obj)
    if layer is not None:
        try:
            from . import gp_layer_parenting

            gp_layer_parenting.set_parent_key(layer, str(value or ""))
        except Exception:  # noqa: BLE001
            _logger.exception("object layer parent metadata sync failed")
    return True


def remove_layer_object(obj: bpy.types.Object | None) -> bool:
    if not is_layer_object(obj):
        return False
    if layer_kind(obj) == "effect":
        try:
            from . import effect_line_object as elo

            elo.delete_effect_display_object(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("effect display cleanup failed")
        for aux in _effect_aux_objects(obj):
            try:
                bpy.data.objects.remove(aux, do_unlink=True)
            except Exception:  # noqa: BLE001
                pass
    data = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        _logger.exception("layer object removal failed")
        return False
    if data is not None and getattr(data, "users", 1) == 0:
        try:
            blocks = getattr(bpy.data, "grease_pencils_v3", None) or getattr(
                bpy.data, "grease_pencils", None
            )
            if blocks is not None:
                blocks.remove(data)
        except Exception:  # noqa: BLE001
            _logger.exception("orphan GP data removal failed")
    return True


def remove_all_layer_objects() -> int:
    """現在の作品に属する個別GP／効果線Objectを全て解放する。"""

    targets = list(iter_layer_objects())
    removed = 0
    for obj in targets:
        if remove_layer_object(obj):
            removed += 1
    return removed


def duplicate_gp_object(
    source: bpy.types.Object | None,
    *,
    bmanga_id: str,
    title: str,
    z_order: int,
) -> Optional[bpy.types.Object]:
    if not is_layer_object(source, "gp") or not bmanga_id:
        return None
    copied = source.copy()
    copied.data = source.data.copy()
    copied.animation_data_clear()
    for collection in tuple(getattr(source, "users_collection", ()) or ()):
        collection.objects.link(copied)
    on.stamp_identity(
        copied,
        kind="gp",
        bmanga_id=bmanga_id,
        title=title,
        z_index=int(z_order),
        parent_key=parent_key(source),
        folder_id=folder_id(source),
    )
    on.assign_canonical_name(copied, "gp", int(z_order), "gp", title)
    initialize_user_state(copied)
    from . import gpencil

    gpencil.ensure_unique_object_materials(copied)
    return copied
