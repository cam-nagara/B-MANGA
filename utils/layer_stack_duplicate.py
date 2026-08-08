"""統合レイヤー一覧の種類別通常複製処理。"""

from __future__ import annotations

from . import log


_logger = log.get_logger(__name__)


def duplicate_item(context, item) -> bool:
    kind = str(getattr(item, "kind", "") or "")
    if kind in {"gp", "effect"}:
        return _duplicate_native_layer(context, item)
    if kind == "layer_folder":
        return _duplicate_layer_folder(context, item)
    if kind == "image":
        return _duplicate_image(context, item)
    if kind == "image_path":
        return _duplicate_image_path(context, item)
    if kind == "raster":
        from ..operators.layer_clipboard_op import duplicate_raster_item

        return duplicate_raster_item(context, item)
    if kind == "balloon":
        return _duplicate_balloon(context, item)
    if kind == "text":
        return _duplicate_text(context, item)
    if kind == "fill":
        return _duplicate_fill(context, item)
    return False


def _duplicate_native_layer(context, item) -> bool:
    from . import layer_object_model, layer_stack

    active_index = int(
        getattr(context.scene, "bmanga_active_layer_stack_index", -1)
    )
    if not layer_stack.select_stack_index(context, active_index):
        return False
    try:
        if item.kind == "effect":
            return _duplicate_effect(context, item)
        resolved = layer_stack.resolve_stack_item(context, item)
        source = resolved.get("object") if resolved is not None else None
        if source is None:
            return False
        title = _unique_name(
            {
                layer_object_model.display_title(obj)
                for obj in layer_object_model.iter_layer_objects("gp")
            },
            f"{layer_object_model.display_title(source)} 複製",
        )
        duplicate = layer_object_model.duplicate_gp_object(
            source,
            bmanga_id=layer_object_model.make_stable_id("gp"),
            title=title,
            z_order=layer_object_model.z_index(source) + 1,
        )
        if duplicate is None:
            return False
        _select_object(context, duplicate)
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("native layer duplication failed: %s", item.kind)
        return False


def _duplicate_effect(context, item) -> bool:
    from ..operators import effect_line_link_op
    from . import layer_stack

    resolved = layer_stack.resolve_stack_item(context, item)
    source_obj = resolved.get("object") if resolved is not None else None
    source_layer = resolved.get("target") if resolved is not None else None
    _dest_obj, dest_layer = effect_line_link_op.duplicate_effect_entry(
        context,
        source_obj,
        source_layer,
        linked=False,
        ui_parent_key=str(getattr(item, "parent_key", "") or ""),
    )
    return dest_layer is not None


def _select_object(context, obj) -> None:
    try:
        context.view_layer.objects.active = obj
        obj.select_set(True)
    except Exception:  # noqa: BLE001
        # 選択同期に失敗しても複製済みNative実体の作成自体は有効。
        _logger.debug("duplicated native object selection failed", exc_info=True)


def _duplicate_layer_folder(context, item) -> bool:
    from ..core.work import get_work
    from . import layer_folder, layer_stack
    from .layer_hierarchy import OUTSIDE_STACK_KEY

    work = get_work(context)
    resolved = layer_stack.resolve_stack_item(context, item)
    source = resolved.get("target") if resolved is not None else None
    folders = getattr(work, "layer_folders", None) if work is not None else None
    if source is None or folders is None:
        return False
    existing_titles = {
        str(getattr(folder, "title", "") or "") for folder in folders
    }
    target = folders.add()
    target.id = layer_folder.ensure_unique_folder_id(work)
    target.title = _unique_name(
        existing_titles,
        f"{str(getattr(source, 'title', '') or 'フォルダ')} 複製",
    )
    target.parent_key = str(
        getattr(source, "parent_key", "") or OUTSIDE_STACK_KEY
    )
    target.expanded = bool(getattr(source, "expanded", True))
    target.visible = bool(getattr(source, "visible", True))
    target.locked = bool(getattr(source, "locked", False))
    context.scene.bmanga_active_layer_kind = "layer_folder"
    if hasattr(context.scene, "bmanga_active_layer_folder_key"):
        context.scene.bmanga_active_layer_folder_key = target.id
    return True


def _duplicate_image(context, item) -> bool:
    from . import layer_stack

    resolved = layer_stack.resolve_stack_item(context, item)
    source = resolved.get("target") if resolved is not None else None
    collection = getattr(context.scene, "bmanga_image_layers", None)
    if source is None or collection is None:
        return False
    target_id = _next_id(collection, "image")
    existing_titles = {
        str(getattr(entry, "title", "") or "") for entry in collection
    }
    snapshot = _image_snapshot(source)
    target = collection.add()
    target.id = target_id
    _apply_image_snapshot(snapshot, target)
    target.title = _unique_name(
        existing_titles,
        f"{str(getattr(source, 'title', '') or '画像')} 複製",
    )
    context.scene.bmanga_active_image_layer_index = len(collection) - 1
    context.scene.bmanga_active_layer_kind = "image"
    return True


def _duplicate_image_path(context, item) -> bool:
    from ..io import schema
    from . import image_path_object, layer_stack

    resolved = layer_stack.resolve_stack_item(context, item)
    source = resolved.get("target") if resolved is not None else None
    collection = getattr(context.scene, "bmanga_image_path_layers", None)
    if source is None or collection is None:
        return False
    target_id = _next_id(collection, "image_path")
    existing_titles = {
        str(getattr(entry, "title", "") or "") for entry in collection
    }
    target = collection.add()
    with image_path_object.suspend_auto_sync():
        schema.image_path_layer_from_dict(
            target,
            schema.image_path_layer_to_dict(source),
            opacity_percent=True,
        )
        target.id = target_id
        target.title = _unique_name(
            existing_titles,
            f"{str(getattr(source, 'title', '') or 'パターンカーブ')} 複製",
        )
    image_path_object.on_image_path_entry_changed(target)
    context.scene.bmanga_active_image_path_layer_index = len(collection) - 1
    context.scene.bmanga_active_layer_kind = "image_path"
    return True


def _duplicate_balloon(context, item) -> bool:
    from ..core.work import get_work
    from ..io import schema
    from ..operators.balloon_op import _allocate_balloon_id
    from . import layer_stack

    resolved = layer_stack.resolve_stack_item(context, item)
    source = resolved.get("target") if resolved is not None else None
    page = resolved.get("page") if resolved is not None else None
    if source is None:
        return False
    if page is None:
        work = get_work(context)
        collection = (
            getattr(work, "shared_balloons", None) if work is not None else None
        )
        if collection is None:
            return False
        target = collection.add()
        target_id = _next_id(collection, "shared_balloon")
    else:
        collection = page.balloons
        target = collection.add()
        target_id = _allocate_balloon_id(page)
    schema.balloon_entry_from_dict(target, schema.balloon_entry_to_dict(source))
    target.id = target_id
    if page is not None:
        page.active_balloon_index = len(collection) - 1
    context.scene.bmanga_active_layer_kind = "balloon"
    return True


def _duplicate_text(context, item) -> bool:
    from ..core.work import get_work
    from ..io import schema
    from ..operators.text_op import _allocate_text_id
    from . import layer_stack

    resolved = layer_stack.resolve_stack_item(context, item)
    source = resolved.get("target") if resolved is not None else None
    page = resolved.get("page") if resolved is not None else None
    if source is None:
        return False
    if page is None:
        work = get_work(context)
        collection = getattr(work, "shared_texts", None) if work is not None else None
        if collection is None:
            return False
        target = collection.add()
        target_id = _next_id(collection, "shared_text")
    else:
        collection = page.texts
        target = collection.add()
        target_id = _allocate_text_id(page)
    schema.text_entry_from_dict(target, schema.text_entry_to_dict(source))
    target.id = target_id
    target.x_mm += 5.0
    target.y_mm -= 5.0
    if page is not None:
        page.active_text_index = len(collection) - 1
    context.scene.bmanga_active_layer_kind = "text"
    return True


def _duplicate_fill(context, item) -> bool:
    from ..io import schema
    from . import fill_real_object, layer_stack

    resolved = layer_stack.resolve_stack_item(context, item)
    source = resolved.get("target") if resolved is not None else None
    collection = getattr(context.scene, "bmanga_fill_layers", None)
    if source is None or collection is None:
        return False
    target_id = _next_id(collection, "fill")
    existing_titles = {
        str(getattr(entry, "title", "") or "") for entry in collection
    }
    target = collection.add()
    with fill_real_object.suspend_auto_sync():
        schema.fill_layer_from_dict(
            target,
            schema.fill_layer_to_dict(source),
        )
        target.id = target_id
        target.title = _unique_name(
            existing_titles,
            f"{str(getattr(source, 'title', '') or '塗り')} 複製",
        )
    fill_real_object.on_fill_entry_changed(target)
    context.scene.bmanga_active_fill_layer_index = len(collection) - 1
    context.scene.bmanga_active_layer_kind = "fill"
    return True


def _next_id(collection, prefix: str) -> str:
    used = {str(getattr(entry, "id", "") or "") for entry in collection}
    index = 1
    while f"{prefix}_{index:04d}" in used:
        index += 1
    return f"{prefix}_{index:04d}"


def _image_snapshot(source) -> dict[str, object]:
    snapshot: dict[str, object] = {}
    for key in (
        "title", "filepath", "x_mm", "y_mm", "width_mm", "height_mm",
        "rotation_deg", "flip_x", "flip_y", "visible", "locked", "opacity",
        "blend_mode", "brightness", "contrast", "binarize_enabled",
        "binarize_threshold", "tint_color", "parent_kind", "parent_key",
        "folder_key",
    ):
        try:
            value = getattr(source, key)
            snapshot[key] = tuple(value) if key == "tint_color" else value
        except (AttributeError, TypeError):
            # Blender版やレイヤー種別によって存在しない任意RNA fieldは対象外。
            continue
    return snapshot


def _apply_image_snapshot(snapshot: dict[str, object], target) -> None:
    for key, value in snapshot.items():
        try:
            setattr(target, key, value)
        except (AttributeError, TypeError):
            # 元entryに無い任意RNA fieldは複製先でも設定しない。
            continue


def _unique_name(existing: set[str], base: str) -> str:
    if base not in existing:
        return base
    index = 1
    while True:
        candidate = f"{base}.{index:03d}"
        if candidate not in existing:
            return candidate
        index += 1


__all__ = ("duplicate_item",)
