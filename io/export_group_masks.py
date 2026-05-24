"""Helpers for export-time layer group masks."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Sequence


def find_coma_for_parent_key(page: Any, parent_key: str) -> Any | None:
    key = str(parent_key or "")
    if ":" not in key:
        return None
    page_id, coma_id = key.split(":", 1)
    if page_id != str(getattr(page, "id", "") or ""):
        return None
    for panel in getattr(page, "comas", []) or []:
        if str(getattr(panel, "id", "") or "") == coma_id:
            return panel
        if str(getattr(panel, "coma_id", "") or "") == coma_id:
            return panel
    return None


def group_path_for_parent(
    page: Any,
    parent_kind: str,
    parent_key: str,
    fallback: tuple[str, ...],
    *,
    coma_content_group_path: Callable[[Any], tuple[str, ...]],
) -> tuple[str, ...]:
    parent_key = str(parent_key or "")
    if str(parent_kind or "") == "coma" or ":" in parent_key:
        panel = find_coma_for_parent_key(page, parent_key)
        if panel is not None:
            return (*coma_content_group_path(panel), *fallback)
    return fallback


def mask_for_layer(layer: Any, group_masks: dict[tuple[str, ...], Any]) -> Any | None:
    best = None
    best_len = -1
    for path, mask in group_masks.items():
        if len(path) <= best_len:
            continue
        if tuple(layer.group_path[: len(path)]) == path:
            best = mask
            best_len = len(path)
    return best


def apply_mask_to_layer(layer: Any, mask: Any, image_module: Any, image_chops_module: Any) -> Any:
    rgba = layer.image.convert("RGBA")
    local_mask = image_module.new("L", rgba.size, 0)
    inter_left = max(layer.left, mask.left)
    inter_top = max(layer.top, mask.top)
    inter_right = min(layer.right, mask.right)
    inter_bottom = min(layer.bottom, mask.bottom)
    if inter_right > inter_left and inter_bottom > inter_top:
        mask_crop = mask.image.crop(
            (
                inter_left - mask.left,
                inter_top - mask.top,
                inter_right - mask.left,
                inter_bottom - mask.top,
            )
        )
        local_mask.paste(mask_crop, (inter_left - layer.left, inter_top - layer.top))
    alpha = image_chops_module.multiply(rgba.getchannel("A"), local_mask)
    rgba.putalpha(alpha)
    return replace(layer, image=rgba)


def apply_group_masks_to_layers(
    layers: Sequence[Any],
    group_masks: dict[tuple[str, ...], Any],
    image_module: Any,
    image_chops_module: Any,
) -> list[Any]:
    if not group_masks:
        return list(layers)
    out: list[Any] = []
    for layer in layers:
        mask = mask_for_layer(layer, group_masks)
        out.append(apply_mask_to_layer(layer, mask, image_module, image_chops_module) if mask is not None else layer)
    return out


def crop_group_masks(
    masks: dict[tuple[str, ...], Any],
    crop_box: tuple[int, int, int, int],
    export_mask_cls: type,
) -> dict[tuple[str, ...], Any]:
    crop_left, crop_top, crop_right, crop_bottom = crop_box
    out: dict[tuple[str, ...], Any] = {}
    for path, mask in masks.items():
        inter_left = max(mask.left, crop_left)
        inter_top = max(mask.top, crop_top)
        inter_right = min(mask.right, crop_right)
        inter_bottom = min(mask.bottom, crop_bottom)
        if inter_right <= inter_left or inter_bottom <= inter_top:
            continue
        src_box = (
            inter_left - mask.left,
            inter_top - mask.top,
            inter_right - mask.left,
            inter_bottom - mask.top,
        )
        out[path] = export_mask_cls(
            mask.image.crop(src_box),
            inter_left - crop_left,
            inter_top - crop_top,
            mask.name,
        )
    return out
