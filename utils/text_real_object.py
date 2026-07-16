"""テキストレイヤーの実オブジェクト同期.

B-MANGA のテキストは、編集時のカーソルや選択範囲だけをオーバーレイで描き、
本文そのものは透明画像付き Mesh 平面として Blender データに残す。
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from typing import Optional

import bpy

from . import color_space
from . import free_transform
from . import layer_object_sync as los
from . import log
from . import object_naming as on
from . import object_preserve
from . import text_layout_bounds
from . import text_style
from ..typography import ruby_presentation
from .geom import Rect, mm_to_m, mm_to_px, q_to_mm

_logger = log.get_logger(__name__)

TEXT_OBJECT_NAME_PREFIX = "text_"
TEXT_MESH_NAME_PREFIX = "text_mesh_"
TEXT_IMAGE_NAME_PREFIX = "bmanga_text_image_"
TEXT_MATERIAL_NAME_PREFIX = "BManga_Text_"
TEXT_REAL_DPI = 300
TEXT_RENDER_PAD_MM = 1.5
TEXT_Z_BASE = 2000
OUTSIDE_PAGE_ID = "outside"
_TEXT_RENDER_SIGNATURE_PROP = "bmanga_text_render_signature"
_TEXT_PREVIEW_HIDDEN_PROP = "bmanga_text_preview_hidden"
_AUTO_SYNC_SUSPEND_DEPTH = 0


@contextmanager
def suspend_auto_sync():
    """Temporarily skip expensive text image rebuilds from property callbacks."""
    global _AUTO_SYNC_SUSPEND_DEPTH
    _AUTO_SYNC_SUSPEND_DEPTH += 1
    try:
        yield
    finally:
        _AUTO_SYNC_SUSPEND_DEPTH = max(0, _AUTO_SYNC_SUSPEND_DEPTH - 1)


def auto_sync_suspended() -> bool:
    return _AUTO_SYNC_SUSPEND_DEPTH > 0


def text_object_bmanga_id_for_values(page_id: str, text_id: str) -> str:
    page_id = str(page_id or "").strip()
    text_id = str(text_id or "").strip()
    if page_id and text_id:
        return f"{page_id}:{text_id}"
    return text_id


def text_object_bmanga_id(page, entry) -> str:
    return text_object_bmanga_id_for_values(
        _page_id_for_entry(page, entry),
        str(getattr(entry, "id", "") or ""),
    )


def split_text_object_bmanga_id(bmanga_id: str) -> tuple[str, str]:
    raw = str(bmanga_id or "")
    if ":" in raw:
        page_id, text_id = raw.split(":", 1)
        return page_id, text_id
    return "", raw


def find_text_entry(scene, bmanga_id: str):
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if work is None:
        return None, None
    page_id, text_id = split_text_object_bmanga_id(bmanga_id)
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
    full_id = text_object_bmanga_id_for_values(page_id, text_id)
    obj = on.find_object_by_bmanga_id(full_id, kind="text")
    if obj is not None:
        return obj
    if not str(page_id or ""):
        obj = on.find_object_by_bmanga_id(
            text_object_bmanga_id_for_values(OUTSIDE_PAGE_ID, text_id),
            kind="text",
        )
        if obj is not None:
            return obj
    # 旧バージョンの単独 ID オブジェクト。ensure 時に置き換える。
    return on.find_object_by_bmanga_id(text_id, kind="text")


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


def _preview_hidden(obj: bpy.types.Object) -> bool:
    return bool(obj.get(_TEXT_PREVIEW_HIDDEN_PROP, False))


def _set_preview_hidden_marker(obj: bpy.types.Object, hidden: bool) -> None:
    if hidden:
        obj[_TEXT_PREVIEW_HIDDEN_PROP] = True
        return
    try:
        del obj[_TEXT_PREVIEW_HIDDEN_PROP]
    except Exception:  # noqa: BLE001
        obj[_TEXT_PREVIEW_HIDDEN_PROP] = False


def set_text_object_preview_hidden(entry, page=None, *, hidden: bool) -> None:
    text_id = str(getattr(entry, "id", "") or "")
    if not text_id:
        return
    obj = find_text_object(_page_id_for_entry(page, entry), text_id)
    if obj is None:
        return
    _set_preview_hidden_marker(obj, bool(hidden))
    obj.hide_viewport = bool(hidden) or not bool(getattr(entry, "visible", True))
    obj.hide_render = obj.hide_viewport


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


def _render_pad_mm(entry) -> float:
    stroke_pad = 0.0
    if bool(getattr(entry, "stroke_enabled", False)):
        stroke_pad = max(0.0, float(getattr(entry, "stroke_width_mm", 0.0) or 0.0))
    pad = max(TEXT_RENDER_PAD_MM, stroke_pad + 0.75)
    if len(getattr(entry, "ruby_spans", []) or []) > 0:
        try:
            from ..typography import ruby as text_ruby

            pad = text_ruby.render_pad_mm_for_entry(entry, minimum=pad)
        except Exception:  # noqa: BLE001
            pad = max(pad, q_to_mm(float(getattr(entry, "font_size_q", 20.0) or 20.0)) * 0.75 + 1.0)
    return pad


def _render_entry_to_pillow(entry):
    from . import python_deps

    python_deps.ensure_bundled_wheels_on_path()
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return None

    from ..typography import export_renderer, layout as text_layout, ruby as text_ruby

    width_mm = max(0.1, float(getattr(entry, "width_mm", 0.1) or 0.1))
    height_mm = max(0.1, float(getattr(entry, "height_mm", 0.1) or 0.1))
    pad_mm = _render_pad_mm(entry)
    px_per_mm = mm_to_px(1.0, TEXT_REAL_DPI)
    image_width = max(1, int(round((width_mm + pad_mm * 2.0) * px_per_mm)))
    image_height = max(1, int(round((height_mm + pad_mm * 2.0) * px_per_mm)))
    image = Image.new("RGBA", (image_width, image_height), (0, 0, 0, 0))
    if not str(getattr(entry, "body", "") or ""):
        return image, pad_mm, width_mm, height_mm

    inner = text_layout_bounds.text_inner_rect(Rect(0.0, 0.0, width_mm, height_mm))
    result = text_layout.typeset(
        entry,
        pad_mm + inner.x,
        pad_mm + inner.y,
        inner.width,
        inner.height,
    )
    ruby_placements = text_ruby.compute_for_entry(result.placements, entry)
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
        ruby_placements=ruby_placements,
        writing_mode=str(getattr(entry, "writing_mode", "horizontal") or "horizontal"),
    )
    return image, pad_mm, width_mm, height_mm


def _mesh_dimensions_for_entry(entry) -> tuple[float, float, float]:
    width_mm = max(0.1, float(getattr(entry, "width_mm", 0.1) or 0.1))
    height_mm = max(0.1, float(getattr(entry, "height_mm", 0.1) or 0.1))
    return width_mm, height_mm, _render_pad_mm(entry)


def _float_sig(value) -> float:
    try:
        return round(float(value), 6)
    except Exception:  # noqa: BLE001
        return 0.0


def _rgba_sig(value) -> tuple[float, float, float, float]:
    try:
        return tuple(_float_sig(value[i]) for i in range(4))  # type: ignore[index]
    except Exception:  # noqa: BLE001
        return (0.0, 0.0, 0.0, 1.0)


def _vec2_sig(value) -> tuple[float, float]:
    try:
        return _float_sig(value[0]), _float_sig(value[1])  # type: ignore[index]
    except Exception:  # noqa: BLE001
        return (0.0, 0.0)


def _entry_render_signature(entry) -> str:
    return repr((
        str(getattr(entry, "body", "") or ""),
        _float_sig(getattr(entry, "width_mm", 0.0)),
        _float_sig(getattr(entry, "height_mm", 0.0)),
        str(getattr(entry, "writing_mode", "vertical") or "vertical"),
        # 生のフォント指定ではなく解決結果を署名へ含める。標準フォント
        # プリファレンスの変更が、フォント未指定テキストの再レンダリングに
        # 反映されるようにするため (解決結果が変われば署名も変わる)。
        text_style.resolve_font_path(str(getattr(entry, "font", "") or "")),
        _float_sig(getattr(entry, "font_size_q", 20.0)),
        bool(getattr(entry, "font_bold", False)),
        bool(getattr(entry, "font_italic", False)),
        _rgba_sig(getattr(entry, "color", (0.0, 0.0, 0.0, 1.0))),
        _float_sig(getattr(entry, "line_height", 1.4)),
        _float_sig(getattr(entry, "letter_spacing", 0.0)),
        _float_sig(getattr(entry, "ruby_line_height", 1.8)),
        _float_sig(ruby_presentation.gap_em_from_entry(entry)),
        _float_sig(getattr(entry, "ruby_letter_spacing", 0.0)),
        _float_sig(getattr(entry, "ruby_size_percent", 50.0)),
        ruby_presentation.resolve_font_path(entry),
        str(getattr(entry, "ruby_font_preset", "inherit") or "inherit"),
        str(getattr(entry, "ruby_align", "center") or "center"),
        str(getattr(entry, "ruby_small_kana", "keep") or "keep"),
        str(getattr(entry, "ruby_default_style", "group") or "group"),
        _ruby_detail_signature(entry),
        bool(getattr(entry, "stroke_enabled", False)),
        _float_sig(getattr(entry, "stroke_width_mm", 0.0)),
        _rgba_sig(getattr(entry, "stroke_color", (1.0, 1.0, 1.0, 1.0))),
        text_style.all_spans_snapshot(entry),
        bool(getattr(entry, "free_transform_enabled", False)),
        _vec2_sig(getattr(entry, "free_transform_bottom_left", (0.0, 0.0))),
        _vec2_sig(getattr(entry, "free_transform_bottom_right", (0.0, 0.0))),
        _vec2_sig(getattr(entry, "free_transform_top_left", (0.0, 0.0))),
        _vec2_sig(getattr(entry, "free_transform_top_right", (0.0, 0.0))),
    ))


def _ruby_detail_signature(entry) -> tuple:
    return tuple(
        (
            int(getattr(span, "start", 0)),
            int(getattr(span, "length", 1)),
            str(getattr(span, "ruby_text", "") or ""),
            str(getattr(span, "style", "group") or "group"),
            str(getattr(span, "origin", "manual") or "manual"),
            int(getattr(span, "priority", 0) or 0),
            tuple(
                (
                    int(getattr(segment, "start", 0)),
                    int(getattr(segment, "length", 1)),
                    str(getattr(segment, "ruby_text", "") or ""),
                )
                for segment in getattr(span, "segments", ())
            ),
        )
        for span in getattr(entry, "ruby_spans", ())
    )


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
        # 生成画像のピクセルは .blend に保存されないため pack して永続化する。
        # pack しないと、ファイルを開き直した時に黒い矩形になり、
        # 「アドオン無効でも保存ファイルだけで表示できる」要件も満たせない。
        image.pack()
    except Exception:  # noqa: BLE001
        _logger.exception("text real object: image pixel upload failed")
    return image


def _ensure_material(name: str, image: Optional[bpy.types.Image], *, mask_info=None) -> bpy.types.Material:
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
    # 発光+透過で照明非依存に描く。Principled だと照明 (ワールド背景) に
    # 依存して文字色が沈み、フキダシ・コマ等の発光描画と明るさが揃わない。
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (360, 0)
    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (-60, -140)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (-60, 60)
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (140, 0)
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.location = (-340, 40)
    tex.image = image
    try:
        emission.inputs["Strength"].default_value = 1.0
        nt.links.new(tex.outputs["Color"], emission.inputs["Color"])
        if mask_info is not None:
            from . import material_opacity_mask
            alpha_out = material_opacity_mask.multiply_alpha_by_mask(
                nt, tex.outputs["Alpha"],
                mask_object=getattr(mask_info, "space_object", None),
                mask_image=getattr(mask_info, "image", None),
            )
            if alpha_out is not None:
                nt.links.new(alpha_out, mix.inputs["Fac"])
            else:
                nt.links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
        else:
            nt.links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
        nt.links.new(transparent.outputs["BSDF"], mix.inputs[1])
        nt.links.new(emission.outputs["Emission"], mix.inputs[2])
        nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    except Exception:  # noqa: BLE001
        _logger.exception("text real object: material link failed")
    try:
        mat.update_tag()
    except Exception:  # noqa: BLE001
        pass
    return mat


def _text_mesh_vertex(entry, x_mm: float, y_mm: float) -> tuple[float, float, float]:
    x_mm, y_mm = free_transform.transform_entry_local_point(entry, x_mm, y_mm)
    return mm_to_m(x_mm), mm_to_m(y_mm), 0.0


def _rebuild_mesh(mesh: bpy.types.Mesh, width_mm: float, height_mm: float, pad_mm: float, entry=None) -> None:
    verts = [
        _text_mesh_vertex(entry, -pad_mm, -pad_mm),
        _text_mesh_vertex(entry, width_mm + pad_mm, -pad_mm),
        _text_mesh_vertex(entry, width_mm + pad_mm, height_mm + pad_mm),
        _text_mesh_vertex(entry, -pad_mm, height_mm + pad_mm),
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
        object_preserve.preserve_object(obj, "古いテキスト実体を保持")


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
        object_preserve.preserve_object(obj, "同じテキストの既存実体を保持")


def _find_existing_text_object(full_id: str, text_id: str, obj_name: str) -> Optional[bpy.types.Object]:
    obj = on.find_object_by_bmanga_id(full_id, kind="text")
    if obj is None:
        legacy_obj = on.find_object_by_bmanga_id(text_id, kind="text")
        if legacy_obj is not None and legacy_obj.type == "MESH":
            obj = legacy_obj
        elif legacy_obj is not None:
            object_preserve.preserve_object(legacy_obj, "古いテキスト実体を保持")
    if obj is None:
        obj = bpy.data.objects.get(obj_name)
    if object_preserve.is_preserved(obj):
        return None
    if obj is not None and obj.type != "MESH":
        object_preserve.preserve_object(obj, "古いテキスト実体を保持")
        return None
    return obj


def _can_reuse_rendered_object(obj: Optional[bpy.types.Object], signature: str) -> bool:
    if obj is None or obj.type != "MESH":
        return False
    if str(obj.get(_TEXT_RENDER_SIGNATURE_PROP, "") or "") != signature:
        return False
    mesh = getattr(obj, "data", None)
    if mesh is None or len(getattr(mesh, "polygons", []) or []) == 0:
        return False
    image = _image_for_object(obj)
    if image is None or getattr(image, "size", (0, 0))[0] <= 0:
        return False
    # 生成画像が pack されていない場合、開き直しでピクセルが失われ
    # 黒い矩形になっているので再描画させる (再描画時に pack される)。
    if getattr(image, "source", "") == "GENERATED" and getattr(image, "packed_file", None) is None:
        return False
    return True


def _rotation_offset_mm(width_mm: float, height_mm: float, rotation_deg: float) -> tuple[float, float]:
    """矩形左下 (0, 0) を中心軸周りに rotation_deg 回転した時の変位 (Ox, Oy) を返す.

    _rotated_bottom_left_mm(entry) の中心回転の式を x_mm=y_mm=0 で展開すると

        center = (w/2, h/2)
        dx = 0 - center.x = -w/2  (x_mm に依存しない定数)
        dy = 0 - center.y = -h/2  (y_mm に依存しない定数)
        rotated = center + R(theta) @ (dx, dy)

    となり、一般の x_mm, y_mm についても
        rotated_bl(x, y) = (x, y) + rotated_bl(0, 0)
    が成り立つ (dx, dy が x_mm, y_mm に依存しないため、回転はどの (x_mm, y_mm)
    でも同じ定数オフセットの平行移動として作用する)。つまり
    ``rotated_bl(x, y) - (x, y)`` は x_mm, y_mm に依存しない定数であり、
    この関数はその定数部分だけを計算する。逆変換 (unrotate_bottom_left_mm)
    はこのオフセットを引くだけで得られる。
    """
    if abs(rotation_deg) <= 1.0e-9:
        return 0.0, 0.0
    theta = math.radians(rotation_deg)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    # dx=-w/2, dy=-h/2 を center=(w/2, h/2) に加えた式を展開したもの。
    offset_x = width_mm * 0.5 * (1.0 - cos_t) + height_mm * 0.5 * sin_t
    offset_y = height_mm * 0.5 * (1.0 - cos_t) - width_mm * 0.5 * sin_t
    return offset_x, offset_y


def _rotated_bottom_left_mm(entry) -> tuple[float, float]:
    """矩形左下 (x_mm, y_mm) を、矩形中心を軸に rotation_deg だけ回転した位置へ変換する.

    テキストの実体オブジェクトはメッシュ原点が矩形左下のままなので (要件は
    「選択枠の中心軸で回転」)、位置側でこの補正を行い obj.rotation_euler[2] と
    組み合わせて見かけ上の中心回転を再現する。rotation_deg == 0 のときは
    補正前 (= 従来) の x_mm, y_mm とビット単位で一致し、後方互換を保つ。
    """
    x_mm = float(getattr(entry, "x_mm", 0.0) or 0.0)
    y_mm = float(getattr(entry, "y_mm", 0.0) or 0.0)
    rotation_deg = float(getattr(entry, "rotation_deg", 0.0) or 0.0)
    width_mm = float(getattr(entry, "width_mm", 0.0) or 0.0)
    height_mm = float(getattr(entry, "height_mm", 0.0) or 0.0)
    offset_x, offset_y = _rotation_offset_mm(width_mm, height_mm, rotation_deg)
    return x_mm + offset_x, y_mm + offset_y


def unrotate_bottom_left_mm(
    bl_x_mm: float,
    bl_y_mm: float,
    width_mm: float,
    height_mm: float,
    rotation_deg: float,
) -> tuple[float, float]:
    """_rotated_bottom_left_mm の逆変換.

    「中心軸回転後の左下」座標 (bl_x_mm, bl_y_mm) から、回転前の矩形左下
    (= entry.x_mm, entry.y_mm 相当) を復元する。ユーザーが Blender の
    3D ビューポートで回転済みテキストの実体オブジェクトを直接動かした時、
    obj.location (mm 換算・ページオフセット減算後) はこの「回転後の左下」に
    なっているため、entry.x_mm/y_mm へ書き戻す前にこの関数を通す必要がある
    (utils/empty_layer_object.py の sync_entry_position_from_object 参照)。

    _rotation_offset_mm の導出により rotated_bl(x, y) = (x, y) + offset
    (offset は x_mm, y_mm に依存しない定数) が成り立つため、逆変換は
    単純に offset を引くだけで厳密に得られる (反復計算や近似は不要)。
    rotation_deg == 0 のときは offset が (0, 0) になり、入力をそのまま返す
    (従来の無回転挙動とビット単位で一致)。
    """
    offset_x, offset_y = _rotation_offset_mm(width_mm, height_mm, rotation_deg)
    return bl_x_mm - offset_x, bl_y_mm - offset_y


def _apply_text_object_state(
    scene: bpy.types.Scene,
    page,
    entry,
    obj: bpy.types.Object,
    *,
    full_id: str,
    text_id: str,
    folder_id: str,
) -> None:
    work = getattr(scene, "bmanga_work", None)
    ox_mm, oy_mm = _page_offset_mm(scene, work, page)
    bl_x_mm, bl_y_mm = _rotated_bottom_left_mm(entry)
    # entry.x_mm/y_mm は矩形「左下」だが rotation_deg != 0 のときは obj.location
    # に「中心軸回転後の左下」を書き込むため、entry の値とビット単位では
    # 一致しなくなる。depsgraph_update_post 経由の Blender→entry 書戻し
    # (utils/empty_layer_object.py の sync_entry_position_from_object) は
    # この差分を「ユーザーが3Dビューポートで直接動かした」と誤認して
    # entry.x_mm/y_mm を回転後の値で上書きしてしまう (往復不整合)。
    # los.suppress_sync() で自分自身の書込み中は depsgraph 側の書戻しを
    # 抑止し、この誤検知を防ぐ (balloon/image は x_mm/y_mm 自体が中心座標
    # なのでこの往復不整合が起きず、これまで顕在化していなかった)。
    with los.suppress_sync():
        obj.location.x = mm_to_m(bl_x_mm + ox_mm)
        obj.location.y = mm_to_m(bl_y_mm + oy_mm)
        obj.rotation_euler[2] = math.radians(float(getattr(entry, "rotation_deg", 0.0) or 0.0))

        parent_kind, parent_key, stamp_folder = _resolve_parent_for_entry(entry, page, folder_id)
        los.stamp_layer_object(
            obj,
            kind="text",
            bmanga_id=full_id,
            title=str(getattr(entry, "title", "") or getattr(entry, "body", "") or text_id)[:40],
            z_index=_text_z_index(page, text_id),
            parent_kind=parent_kind,
            parent_key=parent_key,
            folder_id=stamp_folder,
            scene=scene,
            apply_page_offset=False,
        )
        preview_hidden = _preview_hidden(obj)
        obj.hide_viewport = preview_hidden or not bool(getattr(entry, "visible", True))
        obj.hide_render = obj.hide_viewport
        obj.hide_select = False


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

    full_id = text_object_bmanga_id(page, entry)
    obj_name = _object_name(page_id, text_id)
    obj = _find_existing_text_object(full_id, text_id, obj_name)
    signature = _entry_render_signature(entry)
    if _can_reuse_rendered_object(obj, signature):
        _remove_duplicate_text_objects(full_id, text_id, obj)
        _remove_legacy_empty(text_id, keep_obj=obj)
        _apply_text_object_state(
            scene,
            page,
            entry,
            obj,
            full_id=full_id,
            text_id=text_id,
            folder_id=folder_id,
        )
        return obj

    rendered = _render_entry_to_pillow(entry)
    if rendered is None:
        _logger.warning("Pillow が利用できないためテキスト実体を更新できません")
        return None
    pil_image, pad_mm, width_mm, height_mm = rendered
    image = _ensure_image_data(_image_name(page_id, text_id), pil_image)
    mask_info = None
    parent_kind_hint, parent_key_hint, _ = _resolve_parent_for_entry(entry, page, folder_id)
    if parent_kind_hint == "coma" and parent_key_hint and ":" in parent_key_hint:
        try:
            from . import coma_content_mask
            mask_info = coma_content_mask.ensure_viewport_mask_for_parent(
                scene, getattr(scene, "bmanga_work", None), parent_key_hint,
            )
        except Exception:  # noqa: BLE001
            pass
    mat = _ensure_material(_material_name(page_id, text_id), image, mask_info=mask_info)

    mesh = bpy.data.meshes.get(_mesh_name(page_id, text_id))
    if mesh is None:
        mesh = bpy.data.meshes.new(_mesh_name(page_id, text_id))
    _rebuild_mesh(mesh, width_mm, height_mm, pad_mm, entry)
    if not mesh.materials:
        mesh.materials.append(mat)
    elif mesh.materials[0] is not mat:
        mesh.materials[0] = mat

    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        obj.data = mesh
    obj[_TEXT_RENDER_SIGNATURE_PROP] = signature

    _remove_duplicate_text_objects(full_id, text_id, obj)
    _remove_legacy_empty(text_id, keep_obj=obj)

    _apply_text_object_state(
        scene,
        page,
        entry,
        obj,
        full_id=full_id,
        text_id=text_id,
        folder_id=folder_id,
    )
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
    if auto_sync_suspended():
        return False
    scene = bpy.context.scene if bpy.context is not None else None
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
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


def _refresh_existing_text_mesh(scene, entry, page) -> bool:
    page_id = _page_id_for_entry(page, entry)
    text_id = str(getattr(entry, "id", "") or "")
    obj = find_text_object(page_id, text_id)
    if obj is None or getattr(obj, "type", "") != "MESH":
        return ensure_text_real_object(scene=scene, entry=entry, page=page) is not None
    mesh = getattr(obj, "data", None)
    if mesh is None:
        return False
    width_mm, height_mm, pad_mm = _mesh_dimensions_for_entry(entry)
    _rebuild_mesh(mesh, width_mm, height_mm, pad_mm, entry)
    obj[_TEXT_RENDER_SIGNATURE_PROP] = _entry_render_signature(entry)
    return True


def on_text_free_transform_changed(entry) -> bool:
    scene = bpy.context.scene if bpy.context is not None else None
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
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
            if same_pointer or (target_id and candidate_id == target_id):
                return _refresh_existing_text_mesh(scene, candidate, page)
    for candidate in getattr(work, "shared_texts", []) or []:
        candidate_id = str(getattr(candidate, "id", "") or "")
        try:
            same_pointer = bool(target_ptr) and int(candidate.as_pointer()) == target_ptr
        except Exception:  # noqa: BLE001
            same_pointer = False
        if same_pointer or (target_id and candidate_id == target_id):
            return _refresh_existing_text_mesh(scene, candidate, None)
    return False


def remove_text_real_object(page_id: str, text_id: str) -> bool:
    removed = False
    full_id = text_object_bmanga_id_for_values(page_id, text_id)
    for obj in list(bpy.data.objects):
        if object_preserve.is_preserved(obj):
            continue
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
            valid.add(text_object_bmanga_id(page, entry))
            valid_simple.add(str(getattr(entry, "id", "") or ""))
    for entry in getattr(work, "shared_texts", []) if work is not None else []:
        valid.add(text_object_bmanga_id_for_values(OUTSIDE_PAGE_ID, str(getattr(entry, "id", "") or "")))
        valid_simple.add(str(getattr(entry, "id", "") or ""))
    removed = 0
    for obj in list(bpy.data.objects):
        if object_preserve.is_preserved(obj):
            continue
        if obj.get(on.PROP_KIND) != "text":
            continue
        bid = str(obj.get(on.PROP_ID, "") or "")
        if bid in valid:
            continue
        # 旧 Empty 方式は単独 ID なので、実体化後は全て置き換える。
        if bid in valid_simple or obj.name.startswith(TEXT_OBJECT_NAME_PREFIX):
            object_preserve.preserve_object(obj, "作品データにないテキスト実体を保持")
            removed += 1
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
