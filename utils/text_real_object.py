"""テキストレイヤーの実オブジェクト同期.

B-Name のテキストは、編集時のカーソルや選択範囲だけをオーバーレイで描き、
本文そのものは透明画像付き Mesh 平面として Blender データに残す。
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import color_space
from . import layer_object_sync as los
from . import log
from . import object_naming as on
from . import text_style
from .geom import mm_to_m, mm_to_px

_logger = log.get_logger(__name__)

TEXT_OBJECT_NAME_PREFIX = "text_"
TEXT_MESH_NAME_PREFIX = "text_mesh_"
TEXT_IMAGE_NAME_PREFIX = "bname_text_image_"
TEXT_MATERIAL_NAME_PREFIX = "BName_Text_"
TEXT_REAL_DPI = 300
TEXT_RENDER_PAD_MM = 1.5
TEXT_Z_BASE = 2000
OUTSIDE_PAGE_ID = "outside"


def text_object_bname_id_for_values(page_id: str, text_id: str) -> str:
    page_id = str(page_id or "").strip()
    text_id = str(text_id or "").strip()
    if page_id and text_id:
        return f"{page_id}:{text_id}"
    return text_id


def text_object_bname_id(page, entry) -> str:
    return text_object_bname_id_for_values(
        _page_id_for_entry(page, entry),
        str(getattr(entry, "id", "") or ""),
    )


def split_text_object_bname_id(bname_id: str) -> tuple[str, str]:
    raw = str(bname_id or "")
    if ":" in raw:
        page_id, text_id = raw.split(":", 1)
        return page_id, text_id
    return "", raw


def find_text_entry(scene, bname_id: str):
    work = getattr(scene, "bname_work", None) if scene is not None else None
    if work is None:
        return None, None
    page_id, text_id = split_text_object_bname_id(bname_id)
    if _is_outside_page_id(page_id):
        for entry in getattr(work, "shared_texts", []):
            if str(getattr(entry, "id", "") or "") == text_id:
                return None, entry
        return None, None
    for page in getattr(work, "pages", []):
        if page_id and str(getattr(page, "id", "") or "") != page_id:
            continue
        for entry in getattr(page, "texts", []):
            if str(getattr(entry, "id", "") or "") == text_id:
                return page, entry
    if not page_id:
        for entry in getattr(work, "shared_texts", []):
            if str(getattr(entry, "id", "") or "") == text_id:
                return None, entry
    return None, None


def find_text_object(page_id: str, text_id: str) -> Optional[bpy.types.Object]:
    full_id = text_object_bname_id_for_values(page_id, text_id)
    obj = on.find_object_by_bname_id(full_id, kind="text")
    if obj is not None:
        return obj
    if not str(page_id or ""):
        obj = on.find_object_by_bname_id(
            text_object_bname_id_for_values(OUTSIDE_PAGE_ID, text_id),
            kind="text",
        )
        if obj is not None:
            return obj
    # 旧バージョンの単独 ID オブジェクト。ensure 時に置き換える。
    return on.find_object_by_bname_id(text_id, kind="text")


def has_visible_text_object(entry, page=None) -> bool:
    text_id = str(getattr(entry, "id", "") or "")
    if not text_id:
        return False
    page_id = _page_id_for_entry(page, entry)
    obj = find_text_object(page_id, text_id)
    if obj is None or obj.type != "MESH":
        return False
    if bool(getattr(obj, "hide_viewport", False)):
        return False
    image = _image_for_object(obj)
    return image is not None and getattr(image, "size", (0, 0))[0] > 0


def _safe_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(value or ""))


def _asset_suffix(page_id: str, text_id: str) -> str:
    return f"{_safe_token(page_id)}_{_safe_token(text_id)}"


def _object_name(page_id: str, text_id: str) -> str:
    return f"{TEXT_OBJECT_NAME_PREFIX}{_asset_suffix(page_id, text_id)}"


def _mesh_name(page_id: str, text_id: str) -> str:
    return f"{TEXT_MESH_NAME_PREFIX}{_asset_suffix(page_id, text_id)}"


def _image_name(page_id: str, text_id: str) -> str:
    return f"{TEXT_IMAGE_NAME_PREFIX}{_asset_suffix(page_id, text_id)}"


def _material_name(page_id: str, text_id: str) -> str:
    return f"{TEXT_MATERIAL_NAME_PREFIX}{_asset_suffix(page_id, text_id)}"


def _is_outside_page_id(page_id: str) -> bool:
    return str(page_id or "") in {OUTSIDE_PAGE_ID, "__outside__"}


def _page_id_for_entry(page, entry) -> str:
    page_id = str(getattr(page, "id", "") or "") if page is not None else ""
    if page_id:
        return page_id
    parent_kind = str(getattr(entry, "parent_kind", "") or "")
    if parent_kind in {"none", "outside"}:
        return OUTSIDE_PAGE_ID
    return ""


def _page_offset_mm(scene, work, page) -> tuple[float, float]:
    if scene is None or work is None or page is None:
        return 0.0, 0.0
    page_id = str(getattr(page, "id", "") or "")
    page_index = -1
    for i, candidate in enumerate(getattr(work, "pages", [])):
        if str(getattr(candidate, "id", "") or "") == page_id:
            page_index = i
            break
    if page_index < 0:
        return 0.0, 0.0
    try:
        from . import page_grid

        return page_grid.page_total_offset_mm(work, scene, page_index)
    except Exception:  # noqa: BLE001
        _logger.exception("text real object: page offset failed")
        return 0.0, 0.0


def _rgba255_from_linear(rgba) -> tuple[int, int, int, int]:
    try:
        srgb = color_space.linear_to_srgb_rgb(tuple(float(c) for c in rgba[:3]))
        alpha = float(rgba[3]) if len(rgba) >= 4 else 1.0
    except Exception:  # noqa: BLE001
        srgb = (0.0, 0.0, 0.0)
        alpha = 1.0
    return (
        int(max(0, min(255, round(srgb[0] * 255.0)))),
        int(max(0, min(255, round(srgb[1] * 255.0)))),
        int(max(0, min(255, round(srgb[2] * 255.0)))),
        int(max(0, min(255, round(alpha * 255.0)))),
    )


def _render_entry_to_pillow(entry):
    from . import python_deps

    python_deps.ensure_bundled_wheels_on_path()
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return None

    from ..typography import export_renderer, layout as text_layout

    width_mm = max(0.1, float(getattr(entry, "width_mm", 0.1) or 0.1))
    height_mm = max(0.1, float(getattr(entry, "height_mm", 0.1) or 0.1))
    stroke_pad = 0.0
    if bool(getattr(entry, "stroke_enabled", False)):
        stroke_pad = max(0.0, float(getattr(entry, "stroke_width_mm", 0.0) or 0.0))
    pad_mm = max(TEXT_RENDER_PAD_MM, stroke_pad + 0.75)
    px_per_mm = mm_to_px(1.0, TEXT_REAL_DPI)
    image_width = max(1, int(round((width_mm + pad_mm * 2.0) * px_per_mm)))
    image_height = max(1, int(round((height_mm + pad_mm * 2.0) * px_per_mm)))
    image = Image.new("RGBA", (image_width, image_height), (0, 0, 0, 0))
    if not str(getattr(entry, "body", "") or ""):
        return image, pad_mm, width_mm, height_mm

    result = text_layout.typeset(entry, pad_mm, pad_mm, width_mm, height_mm)
    stroke_width_px = 0
    stroke_color = (255, 255, 255, 255)
    if bool(getattr(entry, "stroke_enabled", False)):
        stroke_width_px = max(
            1,
            int(round(mm_to_px(float(getattr(entry, "stroke_width_mm", 0.2)), TEXT_REAL_DPI))),
        )
        stroke_color = _rgba255_from_linear(
            getattr(entry, "stroke_color", (1.0, 1.0, 1.0, 1.0))
        )
    font_path = text_style.resolve_font_path(str(getattr(entry, "font", "") or ""))
    export_renderer.render_to_image(
        result,
        image,
        font_path=font_path,
        font_path_for_index=lambda index: text_style.resolve_font_path(
            text_style.font_for_index(entry, index)
        ),
        color_for_index=lambda index: _rgba255_from_linear(
            text_style.color_for_index(entry, index)
        ),
        bold_for_index=lambda index: text_style.bold_for_index(entry, index),
        italic_for_index=lambda index: text_style.italic_for_index(entry, index),
        px_per_mm=px_per_mm,
        color=_rgba255_from_linear(getattr(entry, "color", (0.0, 0.0, 0.0, 1.0))),
        stroke_width_px=stroke_width_px,
        stroke_color=stroke_color,
    )
    return image, pad_mm, width_mm, height_mm


def _ensure_image_data(name: str, pil_image) -> Optional[bpy.types.Image]:
    if pil_image is None:
        return None
    width, height = pil_image.size
    image = bpy.data.images.get(name)
    if image is None:
        image = bpy.data.images.new(name, width=width, height=height, alpha=True)
    elif tuple(image.size) != (width, height):
        try:
            bpy.data.images.remove(image)
        except Exception:  # noqa: BLE001
            pass
        image = bpy.data.images.new(name, width=width, height=height, alpha=True)
    try:
        image.colorspace_settings.name = "sRGB"
    except Exception:  # noqa: BLE001
        pass
    try:
        from PIL import Image as PILImage  # type: ignore

        transpose = getattr(PILImage, "Transpose", PILImage)
        flipped = pil_image.transpose(getattr(transpose, "FLIP_TOP_BOTTOM"))
    except Exception:  # noqa: BLE001
        flipped = pil_image
    rgba = flipped.convert("RGBA")
    pixels = [channel / 255.0 for pixel in rgba.getdata() for channel in pixel]
    try:
        image.pixels.foreach_set(pixels)
        image.update()
    except Exception:  # noqa: BLE001
        _logger.exception("text real object: image pixel upload failed")
    return image


def _ensure_material(name: str, image: Optional[bpy.types.Image]) -> bpy.types.Material:
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    try:
        mat.diffuse_color = (1.0, 1.0, 1.0, 0.0)
    except Exception:  # noqa: BLE001
        pass
    mat.use_nodes = True
    try:
        mat.blend_method = "BLEND"
        mat.use_screen_refraction = False
        mat.show_transparent_back = True
    except Exception:  # noqa: BLE001
        pass
    nt = mat.node_tree
    for node in list(nt.nodes):
        nt.nodes.remove(node)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (300, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (80, 0)
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.location = (-180, 40)
    tex.image = image
    try:
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        nt.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    except Exception:  # noqa: BLE001
        _logger.exception("text real object: material link failed")
    return mat


def _rebuild_mesh(mesh: bpy.types.Mesh, width_mm: float, height_mm: float, pad_mm: float) -> None:
    verts = [
        (mm_to_m(-pad_mm), mm_to_m(-pad_mm), 0.0),
        (mm_to_m(width_mm + pad_mm), mm_to_m(-pad_mm), 0.0),
        (mm_to_m(width_mm + pad_mm), mm_to_m(height_mm + pad_mm), 0.0),
        (mm_to_m(-pad_mm), mm_to_m(height_mm + pad_mm), 0.0),
    ]
    mesh.clear_geometry()
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    mesh.update()
    uv_layer = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
    uvs = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    for loop_index, uv in zip(mesh.polygons[0].loop_indices, uvs, strict=False):
        uv_layer.data[loop_index].uv = uv


def _resolve_parent_for_entry(entry, page, folder_id: str) -> tuple[str, str, str]:
    default_kind = "outside" if page is None else "page"
    parent_kind = str(getattr(entry, "parent_kind", "") or default_kind)
    parent_key = str(getattr(entry, "parent_key", "") or "")
    entry_folder = folder_id or str(getattr(entry, "folder_key", "") or "")
    if parent_kind in {"none", "outside"}:
        return "outside", "", ""
    if parent_kind == "coma" and parent_key:
        return "coma", parent_key, entry_folder
    if parent_kind == "folder" and entry_folder:
        return "folder", entry_folder, entry_folder
    return "page", parent_key or str(getattr(page, "id", "") or ""), entry_folder


def _text_z_index(page, text_id: str) -> int:
    texts = getattr(page, "texts", None)
    if texts is not None:
        for i, entry in enumerate(texts):
            if str(getattr(entry, "id", "") or "") == text_id:
                return TEXT_Z_BASE + (i + 1) * 10
    return TEXT_Z_BASE


def _remove_legacy_empty(text_id: str, keep_obj: Optional[bpy.types.Object] = None) -> None:
    legacy_name = f"text_{text_id}"
    obj = bpy.data.objects.get(legacy_name)
    if obj is None or obj is keep_obj:
        return
    if obj.type == "EMPTY" or str(obj.get(on.PROP_ID, "") or "") == text_id:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            _logger.exception("text real object: legacy empty removal failed")


def _remove_text_object(obj: bpy.types.Object) -> None:
    data = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        _logger.exception("text real object: object removal failed")
        return
    if data is not None and getattr(data, "users", 0) == 0:
        _remove_mesh_or_curve_data(data)


def _remove_duplicate_text_objects(
    full_id: str,
    text_id: str,
    keep_obj: Optional[bpy.types.Object],
) -> None:
    if not full_id and not text_id:
        return
    expected_ids = {value for value in (full_id, text_id) if value}
    for obj in list(bpy.data.objects):
        if obj is keep_obj:
            continue
        if obj.get(on.PROP_KIND) != "text":
            continue
        bid = str(obj.get(on.PROP_ID, "") or "")
        if bid not in expected_ids:
            continue
        _remove_text_object(obj)


def ensure_text_real_object(
    *,
    scene: bpy.types.Scene,
    entry,
    page,
    folder_id: str = "",
) -> Optional[bpy.types.Object]:
    if scene is None or entry is None:
        return None
    text_id = str(getattr(entry, "id", "") or "")
    page_id = _page_id_for_entry(page, entry)
    if not text_id or not page_id:
        return None

    rendered = _render_entry_to_pillow(entry)
    if rendered is None:
        _logger.warning("Pillow が利用できないためテキスト実体を更新できません")
        return None
    pil_image, pad_mm, width_mm, height_mm = rendered
    image = _ensure_image_data(_image_name(page_id, text_id), pil_image)
    mat = _ensure_material(_material_name(page_id, text_id), image)

    mesh = bpy.data.meshes.get(_mesh_name(page_id, text_id))
    if mesh is None:
        mesh = bpy.data.meshes.new(_mesh_name(page_id, text_id))
    _rebuild_mesh(mesh, width_mm, height_mm, pad_mm)
    if not mesh.materials:
        mesh.materials.append(mat)
    elif mesh.materials[0] is not mat:
        mesh.materials[0] = mat

    full_id = text_object_bname_id(page, entry)
    obj_name = _object_name(page_id, text_id)
    obj = on.find_object_by_bname_id(full_id, kind="text")
    if obj is None:
        legacy_obj = on.find_object_by_bname_id(text_id, kind="text")
        if legacy_obj is not None and legacy_obj.type == "MESH":
            obj = legacy_obj
        elif legacy_obj is not None:
            _remove_text_object(legacy_obj)
    if obj is None:
        obj = bpy.data.objects.get(obj_name)
    if obj is not None and obj.type != "MESH":
        _remove_text_object(obj)
        obj = None
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        obj.data = mesh

    _remove_duplicate_text_objects(full_id, text_id, obj)
    _remove_legacy_empty(text_id, keep_obj=obj)

    work = getattr(scene, "bname_work", None)
    ox_mm, oy_mm = _page_offset_mm(scene, work, page)
    obj.location.x = mm_to_m(float(getattr(entry, "x_mm", 0.0) or 0.0) + ox_mm)
    obj.location.y = mm_to_m(float(getattr(entry, "y_mm", 0.0) or 0.0) + oy_mm)

    parent_kind, parent_key, stamp_folder = _resolve_parent_for_entry(entry, page, folder_id)
    los.stamp_layer_object(
        obj,
        kind="text",
        bname_id=full_id,
        title=str(getattr(entry, "body", "") or text_id)[:40],
        z_index=_text_z_index(page, text_id),
        parent_kind=parent_kind,
        parent_key=parent_key,
        folder_id=stamp_folder,
        scene=scene,
        apply_page_offset=False,
    )
    obj.hide_viewport = not bool(getattr(entry, "visible", True))
    obj.hide_render = not bool(getattr(entry, "visible", True))
    obj.hide_select = False
    return obj


def sync_all_text_real_objects(scene: bpy.types.Scene, work) -> int:
    if scene is None or work is None:
        return 0
    count = 0
    for page in getattr(work, "pages", []):
        for entry in getattr(page, "texts", []):
            if ensure_text_real_object(scene=scene, entry=entry, page=page) is not None:
                count += 1
    for entry in getattr(work, "shared_texts", []):
        if ensure_text_real_object(scene=scene, entry=entry, page=None) is not None:
            count += 1
    cleanup_orphan_text_objects(scene, work)
    return count


def on_text_entry_changed(entry) -> bool:
    scene = bpy.context.scene if bpy.context is not None else None
    work = getattr(scene, "bname_work", None) if scene is not None else None
    if scene is None or work is None or entry is None:
        return False
    try:
        target_ptr = int(entry.as_pointer())
    except Exception:  # noqa: BLE001
        target_ptr = 0
    target_id = str(getattr(entry, "id", "") or "")
    for page in getattr(work, "pages", []) or []:
        for candidate in getattr(page, "texts", []) or []:
            candidate_id = str(getattr(candidate, "id", "") or "")
            try:
                same_pointer = bool(target_ptr) and int(candidate.as_pointer()) == target_ptr
            except Exception:  # noqa: BLE001
                same_pointer = False
            same_id = bool(target_id) and candidate_id == target_id
            if not same_pointer and not same_id:
                continue
            return ensure_text_real_object(scene=scene, entry=candidate, page=page) is not None
    for candidate in getattr(work, "shared_texts", []) or []:
        candidate_id = str(getattr(candidate, "id", "") or "")
        try:
            same_pointer = bool(target_ptr) and int(candidate.as_pointer()) == target_ptr
        except Exception:  # noqa: BLE001
            same_pointer = False
        same_id = bool(target_id) and candidate_id == target_id
        if not same_pointer and not same_id:
            continue
        return ensure_text_real_object(scene=scene, entry=candidate, page=None) is not None
    return False


def remove_text_real_object(page_id: str, text_id: str) -> bool:
    removed = False
    full_id = text_object_bname_id_for_values(page_id, text_id)
    for obj in list(bpy.data.objects):
        if obj.get(on.PROP_KIND) != "text":
            continue
        if obj.get(on.PROP_ID) not in {full_id, text_id}:
            continue
        data = obj.data
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed = True
        except Exception:  # noqa: BLE001
            pass
        if data is not None and getattr(data, "users", 0) == 0:
            _remove_mesh_or_curve_data(data)
    for name in (
        _image_name(page_id, text_id),
        _material_name(page_id, text_id),
        _mesh_name(page_id, text_id),
    ):
        _remove_unused_datablock(name)
    return removed


def cleanup_orphan_text_objects(scene: bpy.types.Scene, work) -> int:
    valid: set[str] = set()
    valid_simple: set[str] = set()
    for page in getattr(work, "pages", []) if work is not None else []:
        for entry in getattr(page, "texts", []):
            valid.add(text_object_bname_id(page, entry))
            valid_simple.add(str(getattr(entry, "id", "") or ""))
    for entry in getattr(work, "shared_texts", []) if work is not None else []:
        valid.add(text_object_bname_id_for_values(OUTSIDE_PAGE_ID, str(getattr(entry, "id", "") or "")))
        valid_simple.add(str(getattr(entry, "id", "") or ""))
    removed = 0
    for obj in list(bpy.data.objects):
        if obj.get(on.PROP_KIND) != "text":
            continue
        bid = str(obj.get(on.PROP_ID, "") or "")
        if bid in valid:
            continue
        # 旧 Empty 方式は単独 ID なので、実体化後は全て置き換える。
        if bid in valid_simple or obj.name.startswith(TEXT_OBJECT_NAME_PREFIX):
            data = obj.data
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
                removed += 1
            except Exception:  # noqa: BLE001
                pass
            if data is not None and getattr(data, "users", 0) == 0:
                _remove_mesh_or_curve_data(data)
    return removed


def _image_for_object(obj: bpy.types.Object) -> Optional[bpy.types.Image]:
    mat = obj.active_material
    if mat is None or not getattr(mat, "use_nodes", False):
        return None
    for node in mat.node_tree.nodes:
        if getattr(node, "bl_idname", "") == "ShaderNodeTexImage":
            return getattr(node, "image", None)
    return None


def _remove_mesh_or_curve_data(data) -> None:
    try:
        if isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        elif isinstance(data, bpy.types.Curve):
            bpy.data.curves.remove(data)
    except Exception:  # noqa: BLE001
        pass


def _remove_unused_datablock(name: str) -> None:
    image = bpy.data.images.get(name)
    if image is not None and image.users == 0:
        try:
            bpy.data.images.remove(image)
        except Exception:  # noqa: BLE001
            pass
    mat = bpy.data.materials.get(name)
    if mat is not None and mat.users == 0:
        try:
            bpy.data.materials.remove(mat)
        except Exception:  # noqa: BLE001
            pass
    mesh = bpy.data.meshes.get(name)
    if mesh is not None and mesh.users == 0:
        try:
            bpy.data.meshes.remove(mesh)
        except Exception:  # noqa: BLE001
            pass
