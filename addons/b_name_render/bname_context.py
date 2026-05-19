"""Read-only helpers for B-Name coma blend context."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import bpy


MANAGED_IMAGE_PROP = "_bname_coma_camera_ref"
SUPPORTED_FISHEYE_PROJECTIONS = {"FISHEYE_EQUIDISTANT", "FISHEYE_EQUISOLID"}
LEGACY_DEFAULT_OUTPUT_NAMES = {"fisheye", "render"}


@dataclass(frozen=True)
class BNameComaContext:
    page_id: str = ""
    coma_id: str = ""
    blend_path: Path | None = None
    passes_dir: Path | None = None
    managed_page_images: tuple[str, ...] = ()

    @property
    def is_bname_coma(self) -> bool:
        return bool(self.page_id and self.coma_id)


def scene_context(scene) -> BNameComaContext:
    page_id = str(getattr(scene, "bname_current_coma_page_id", "") or "")
    coma_id = str(getattr(scene, "bname_current_coma_id", "") or "")
    blend_path = _current_blend_path()
    if not coma_id and blend_path is not None:
        coma_id = _coma_id_from_blend_path(blend_path)
    if not page_id and blend_path is not None:
        page_id = _page_id_from_blend_path(blend_path)
    passes_dir = _passes_dir_from_blend(blend_path, coma_id)
    images = tuple(_managed_page_image_names(scene))
    return BNameComaContext(page_id, coma_id, blend_path, passes_dir, images)


def default_output_folder(scene, requested: str = "") -> str:
    requested = str(requested or "").strip()
    if requested and requested != "//passes/":
        return requested
    context = scene_context(scene)
    if context.passes_dir is not None:
        try:
            context.passes_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return requested or "//passes/"
        return str(context.passes_dir)
    return requested or "//passes/"


def default_output_name(scene, requested: str = "", preset_name: str = "") -> str:
    requested = str(requested or "").strip()
    context = scene_context(scene)
    if requested and not (context.is_bname_coma and requested in LEGACY_DEFAULT_OUTPUT_NAMES):
        return requested
    prefix = context.coma_id or "render"
    suffix = _safe_name(preset_name or "fisheye")
    return f"{prefix}_{suffix}"


def camera_fisheye_projection(camera) -> str:
    camera_data = getattr(camera, "data", None)
    projection = str(getattr(camera_data, "panorama_type", "") or "")
    return projection if projection in SUPPORTED_FISHEYE_PROJECTIONS else ""


def eevr_dome_method_for_projection(projection: str) -> str:
    if projection == "FISHEYE_EQUIDISTANT":
        return "0"
    if projection == "FISHEYE_EQUISOLID":
        return "2"
    return ""


def _current_blend_path() -> Path | None:
    path = str(getattr(bpy.data, "filepath", "") or "")
    return Path(path).resolve() if path else None


def _coma_id_from_blend_path(path: Path) -> str:
    stem = path.stem
    if re.fullmatch(r"c\d{2,}", stem):
        return stem
    parent = path.parent.name
    return parent if re.fullmatch(r"c\d{2,}", parent) else ""


def _page_id_from_blend_path(path: Path) -> str:
    parent = path.parent.parent.name if path.parent.parent else ""
    return parent if re.fullmatch(r"p\d{4,}", parent) else ""


def _passes_dir_from_blend(path: Path | None, coma_id: str) -> Path | None:
    if path is None or not coma_id:
        return None
    if path.parent.name != coma_id:
        return None
    return path.parent / "passes"


def _managed_page_image_names(scene) -> list[str]:
    camera = getattr(scene, "camera", None)
    camera_data = getattr(camera, "data", None)
    if camera_data is None:
        return []
    names: list[str] = []
    for bg in getattr(camera_data, "background_images", []) or []:
        image = getattr(bg, "image", None)
        if image is None:
            continue
        try:
            managed = bool(image.get(MANAGED_IMAGE_PROP, False))
            is_page = bool(image.get("bname_full_page_mask", False)) or str(image.get("bname_kind", "") or "") == "name"
        except Exception:  # noqa: BLE001
            continue
        if managed and is_page:
            names.append(str(getattr(image, "name", "") or ""))
    return names


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", str(value or "").strip())
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned or "render"
