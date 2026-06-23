"""画像レイヤーの実画像平面同期.

画像レイヤーは編集補助だけを 3D ビューポート表示に残し、画像そのものは
透明テクスチャ付き Mesh 平面として Blender データに保持する。
"""

from __future__ import annotations

from contextlib import contextmanager
import math
from pathlib import Path
from typing import Optional

import bpy

from . import layer_object_sync as los
from . import log
from . import object_naming as on
from . import object_preserve
from . import percentage
from .geom import mm_to_m

_logger = log.get_logger(__name__)

IMAGE_OBJECT_NAME_PREFIX = "image_"
IMAGE_MESH_NAME_PREFIX = "image_mesh_"
IMAGE_DATA_NAME_PREFIX = "bmanga_image_layer_"
IMAGE_MATERIAL_NAME_PREFIX = "BManga_Image_"
IMAGE_Z_BASE = 300
_AUTO_SYNC_SUSPEND_DEPTH = 0


@contextmanager
def suspend_auto_sync():
    global _AUTO_SYNC_SUSPEND_DEPTH
    _AUTO_SYNC_SUSPEND_DEPTH += 1
    try:
        yield
    finally:
        _AUTO_SYNC_SUSPEND_DEPTH = max(0, _AUTO_SYNC_SUSPEND_DEPTH - 1)


def auto_sync_suspended() -> bool:
    return _AUTO_SYNC_SUSPEND_DEPTH > 0


def _safe_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(value or ""))


def _object_name(image_id: str) -> str:
    return f"{IMAGE_OBJECT_NAME_PREFIX}{_safe_token(image_id)}"


def _mesh_name(image_id: str) -> str:
    return f"{IMAGE_MESH_NAME_PREFIX}{_safe_token(image_id)}"


def _image_name(image_id: str) -> str:
    return f"{IMAGE_DATA_NAME_PREFIX}{_safe_token(image_id)}"


def _material_name(image_id: str) -> str:
    return f"{IMAGE_MATERIAL_NAME_PREFIX}{_safe_token(image_id)}"


def _resolve_parent_for_entry(entry, page, folder_id: str) -> tuple[str, str, str]:
    parent_kind = str(getattr(entry, "parent_kind", "") or "page")
    parent_key = str(getattr(entry, "parent_key", "") or "")
    entry_folder = folder_id or str(getattr(entry, "folder_key", "") or "")
    if parent_kind in {"none", "outside"}:
        return "outside", "", ""
    if parent_kind == "coma" and parent_key:
        return "coma", parent_key, entry_folder
    if parent_kind == "folder":
        folder_key = entry_folder or parent_key
        if folder_key:
            return "folder", folder_key, folder_key
    return "page", parent_key or str(getattr(page, "id", "") or ""), entry_folder


def _page_by_id(work, page_id: str):
    if work is None or not page_id:
        return None
    for candidate in getattr(work, "pages", []) or []:
        if str(getattr(candidate, "id", "") or "") == page_id:
            return candidate
    return None


def _semantic_parent_key_for_entry(work, entry, fallback_page=None) -> str:
    parent_kind = str(getattr(entry, "parent_kind", "") or "page")
    parent_key = str(getattr(entry, "parent_key", "") or "")
    folder_key = str(getattr(entry, "folder_key", "") or "")
    if parent_kind in {"none", "outside"}:
        return ""
    if parent_kind == "folder":
        folder_key = folder_key or parent_key
        if folder_key:
            try:
                from . import layer_folder
                from .layer_hierarchy import OUTSIDE_STACK_KEY

                semantic = layer_folder.semantic_parent_key_for_folder(work, folder_key)
                return "" if semantic == OUTSIDE_STACK_KEY else semantic
            except Exception:  # noqa: BLE001
                return ""
    if parent_key:
        return parent_key
    if folder_key:
        try:
            from . import layer_folder
            from .layer_hierarchy import OUTSIDE_STACK_KEY

            semantic = layer_folder.semantic_parent_key_for_folder(work, folder_key)
            if semantic != OUTSIDE_STACK_KEY:
                return semantic
        except Exception:  # noqa: BLE001
            pass
    return str(getattr(fallback_page, "id", "") or "")


def page_for_entry(scene, work, entry, fallback_page=None):
    key = _semantic_parent_key_for_entry(work, entry, fallback_page)
    page_id = key.split(":", 1)[0] if key else ""
    page = _page_by_id(work, page_id)
    if (
        page is None
        and str(getattr(entry, "parent_kind", "") or "page") == "page"
        and not str(getattr(entry, "parent_key", "") or "")
    ):
        # 「親種別だけ page で対象ページ未設定」の過渡状態は先頭ページ扱い。
        # この規則を配置 (sync_all_image_real_objects) と書き戻し
        # (sync_entry_position_from_object) の両方が共有しないと、片側だけ
        # オフセット 0 で解釈してページ幅単位の位置ドリフトが起こる
        # (docs/image_layer_xmm_origin_mismatch_investigation_2026-06-12.md)。
        pages = getattr(work, "pages", []) or []
        if len(pages) > 0:
            return pages[0]
    return page


def _page_offset_mm(scene, work, page) -> tuple[float, float]:
    if scene is None or work is None or page is None:
        return 0.0, 0.0
    page_id = str(getattr(page, "id", "") or "")
    page_index = -1
    for i, candidate in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(candidate, "id", "") or "") == page_id:
            page_index = i
            break
    if page_index < 0:
        return 0.0, 0.0
    try:
        from . import page_grid

        return page_grid.page_total_offset_mm(work, scene, page_index)
    except Exception:  # noqa: BLE001
        _logger.exception("image real object: page offset failed")
        return 0.0, 0.0


def entry_page_offset_mm(scene, work, entry, fallback_page=None) -> tuple[float, float]:
    page = page_for_entry(scene, work, entry, fallback_page)
    if page is None:
        return 0.0, 0.0
    return _page_offset_mm(scene, work, page)


def _image_z_index(scene, image_id: str) -> int:
    coll = getattr(scene, "bmanga_image_layers", None) if scene is not None else None
    if coll is not None:
        for i, entry in enumerate(coll):
            if str(getattr(entry, "id", "") or "") == image_id:
                return IMAGE_Z_BASE + (i + 1) * 10
    return IMAGE_Z_BASE


def _needs_pixel_adjustment(entry) -> bool:
    eps = 1e-6
    try:
        opacity = percentage.percent_to_factor(getattr(entry, "opacity", 100.0), 100.0)
    except Exception:  # noqa: BLE001
        opacity = 1.0
    tint = getattr(entry, "tint_color", (1.0, 1.0, 1.0, 1.0))
    try:
        tint_rgba = (
            float(tint[0]) if len(tint) > 0 else 1.0,
            float(tint[1]) if len(tint) > 1 else 1.0,
            float(tint[2]) if len(tint) > 2 else 1.0,
            float(tint[3]) if len(tint) > 3 else 1.0,
        )
    except Exception:  # noqa: BLE001
        tint_rgba = (1.0, 1.0, 1.0, 1.0)
    return (
        abs(float(getattr(entry, "brightness", 0.0) or 0.0)) > eps
        or abs(float(getattr(entry, "contrast", 0.0) or 0.0)) > eps
        or abs(opacity - 1.0) > eps
        or any(abs(channel - 1.0) > eps for channel in tint_rgba)
        or bool(getattr(entry, "binarize_enabled", False))
    )


def _load_adjusted_pillow(entry):
    filepath = str(getattr(entry, "filepath", "") or "")
    if not filepath:
        return None
    abs_path = Path(bpy.path.abspath(filepath))
    if not abs_path.is_file():
        return None
    try:
        from . import python_deps

        python_deps.ensure_bundled_wheels_on_path()
        from PIL import Image  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        image = Image.open(abs_path).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None

    brightness = max(-1.0, min(1.0, float(getattr(entry, "brightness", 0.0) or 0.0)))
    contrast = max(-1.0, min(1.0, float(getattr(entry, "contrast", 0.0) or 0.0)))
    opacity = percentage.percent_to_factor(getattr(entry, "opacity", 100.0), 100.0)
    tint = getattr(entry, "tint_color", (1.0, 1.0, 1.0, 1.0))
    tint_rgba = (
        float(tint[0]) if len(tint) > 0 else 1.0,
        float(tint[1]) if len(tint) > 1 else 1.0,
        float(tint[2]) if len(tint) > 2 else 1.0,
        float(tint[3]) if len(tint) > 3 else 1.0,
    )
    binarize = bool(getattr(entry, "binarize_enabled", False))
    threshold = max(0.0, min(1.0, float(getattr(entry, "binarize_threshold", 0.5) or 0.5)))

    out = []
    contrast_scale = 1.0 + contrast
    for r, g, b, a in image.getdata():
        rf = max(0.0, min(1.0, ((r / 255.0) - 0.5) * contrast_scale + 0.5 + brightness))
        gf = max(0.0, min(1.0, ((g / 255.0) - 0.5) * contrast_scale + 0.5 + brightness))
        bf = max(0.0, min(1.0, ((b / 255.0) - 0.5) * contrast_scale + 0.5 + brightness))
        if binarize:
            lum = rf * 0.299 + gf * 0.587 + bf * 0.114
            rf = gf = bf = 1.0 if lum >= threshold else 0.0
        rf = max(0.0, min(1.0, rf * tint_rgba[0]))
        gf = max(0.0, min(1.0, gf * tint_rgba[1]))
        bf = max(0.0, min(1.0, bf * tint_rgba[2]))
        af = max(0.0, min(1.0, (a / 255.0) * tint_rgba[3] * opacity))
        out.append((
            int(round(rf * 255.0)),
            int(round(gf * 255.0)),
            int(round(bf * 255.0)),
            int(round(af * 255.0)),
        ))
    image.putdata(out)
    return image


def _ensure_image_data_from_pillow(name: str, pil_image) -> Optional[bpy.types.Image]:
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
        # 生成画像のピクセルは .blend に保存されないため pack して永続化する
        # (開き直しで補正済み画像が黒い矩形になるのを防ぐ)。
        image.pack()
    except Exception:  # noqa: BLE001
        _logger.exception("image real object: pixel upload failed")
    return image


def _ensure_image_data(entry, image_id: str) -> Optional[bpy.types.Image]:
    filepath = str(getattr(entry, "filepath", "") or "")
    if not filepath:
        return None
    if _needs_pixel_adjustment(entry):
        pil_image = _load_adjusted_pillow(entry)
        if pil_image is not None:
            return _ensure_image_data_from_pillow(_image_name(image_id), pil_image)
    try:
        image = bpy.data.images.load(bpy.path.abspath(filepath), check_existing=True)
        try:
            image.colorspace_settings.name = "sRGB"
        except Exception:  # noqa: BLE001
            pass
        return image
    except Exception:  # noqa: BLE001
        return None


def _ensure_material(name: str, image: Optional[bpy.types.Image], *, mask_info=None) -> bpy.types.Material:
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    try:
        mat.blend_method = "BLEND"
        mat.show_transparent_back = True
    except Exception:  # noqa: BLE001
        pass
    nt = mat.node_tree
    for node in list(nt.nodes):
        nt.nodes.remove(node)
    # 発光+透過で照明非依存に描く。Principled だと照明 (ワールド背景) に
    # 依存して画像が暗く沈み、用紙・フキダシ等の発光描画と明るさが揃わない。
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
        _logger.exception("image real object: material link failed")
    try:
        mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    except Exception:  # noqa: BLE001
        pass
    try:
        mat.update_tag()
    except Exception:  # noqa: BLE001
        pass
    return mat


def _rebuild_mesh(mesh: bpy.types.Mesh, entry) -> None:
    width_m = mm_to_m(max(0.1, float(getattr(entry, "width_mm", 0.1) or 0.1)))
    height_m = mm_to_m(max(0.1, float(getattr(entry, "height_mm", 0.1) or 0.1)))
    half_w = width_m * 0.5
    half_h = height_m * 0.5
    verts = [
        (-half_w, -half_h, 0.0),
        (half_w, -half_h, 0.0),
        (half_w, half_h, 0.0),
        (-half_w, half_h, 0.0),
    ]
    mesh.clear_geometry()
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    mesh.update()
    uv_layer = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
    u0, u1 = (1.0, 0.0) if bool(getattr(entry, "flip_x", False)) else (0.0, 1.0)
    v0, v1 = (1.0, 0.0) if bool(getattr(entry, "flip_y", False)) else (0.0, 1.0)
    uvs = ((u0, v0), (u1, v0), (u1, v1), (u0, v1))
    for loop_index, uv in zip(mesh.polygons[0].loop_indices, uvs, strict=False):
        uv_layer.data[loop_index].uv = uv


def _remove_object(obj: bpy.types.Object) -> None:
    data = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        _logger.exception("image real object: object removal failed")
        return
    if data is not None and getattr(data, "users", 0) == 0:
        try:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
        except Exception:  # noqa: BLE001
            pass


def ensure_image_real_object(
    *,
    scene: bpy.types.Scene,
    entry,
    page,
    folder_id: str = "",
) -> Optional[bpy.types.Object]:
    if scene is None or entry is None:
        return None
    image_id = str(getattr(entry, "id", "") or "")
    if not image_id:
        return None

    image = _ensure_image_data(entry, image_id)

    mask_info = None
    parent_kind, parent_key, _ = _resolve_parent_for_entry(entry, page, folder_id)
    if parent_kind == "coma" and parent_key and ":" in parent_key:
        try:
            from . import coma_content_mask
            work = getattr(scene, "bmanga_work", None)
            mask_info = coma_content_mask.ensure_viewport_mask_for_parent(
                scene, work, parent_key,
            )
        except Exception:  # noqa: BLE001
            pass

    mat = _ensure_material(_material_name(image_id), image, mask_info=mask_info)

    mesh = bpy.data.meshes.get(_mesh_name(image_id))
    if mesh is None:
        mesh = bpy.data.meshes.new(_mesh_name(image_id))
    _rebuild_mesh(mesh, entry)
    if not mesh.materials:
        mesh.materials.append(mat)
    elif mesh.materials[0] is not mat:
        mesh.materials[0] = mat

    obj_name = _object_name(image_id)
    obj = on.find_object_by_bmanga_id(image_id, kind="image")
    if obj is None:
        obj = bpy.data.objects.get(obj_name)
    if object_preserve.is_preserved(obj):
        obj = None
    if obj is not None and obj.type != "MESH":
        object_preserve.preserve_object(obj, "古い画像実体を保持")
        obj = None
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        obj.data = mesh

    work = getattr(scene, "bmanga_work", None)
    ox_mm, oy_mm = entry_page_offset_mm(scene, work, entry, page)
    width_mm = max(0.1, float(getattr(entry, "width_mm", 0.1) or 0.1))
    height_mm = max(0.1, float(getattr(entry, "height_mm", 0.1) or 0.1))
    obj.location.x = mm_to_m(float(getattr(entry, "x_mm", 0.0) or 0.0) + width_mm * 0.5 + ox_mm)
    obj.location.y = mm_to_m(float(getattr(entry, "y_mm", 0.0) or 0.0) + height_mm * 0.5 + oy_mm)
    obj.rotation_euler[2] = math.radians(float(getattr(entry, "rotation_deg", 0.0) or 0.0))

    parent_kind, parent_key, stamp_folder = _resolve_parent_for_entry(entry, page, folder_id)
    los.stamp_layer_object(
        obj,
        kind="image",
        bmanga_id=image_id,
        title=str(getattr(entry, "title", "") or image_id),
        z_index=_image_z_index(scene, image_id),
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


def find_image_entry(scene, image_id: str):
    coll = getattr(scene, "bmanga_image_layers", None) if scene is not None else None
    if coll is None:
        return None
    for entry in coll:
        if str(getattr(entry, "id", "") or "") == image_id:
            return entry
    return None


def cleanup_orphan_image_objects(scene: bpy.types.Scene) -> int:
    coll = getattr(scene, "bmanga_image_layers", None) if scene is not None else None
    valid = {str(getattr(entry, "id", "") or "") for entry in coll or []}
    removed = 0
    for obj in list(bpy.data.objects):
        if object_preserve.is_preserved(obj):
            continue
        if obj.get(on.PROP_KIND) != "image":
            continue
        bid = str(obj.get(on.PROP_ID, "") or "")
        if bid in valid:
            continue
        object_preserve.preserve_object(obj, "作品データにない画像実体を保持")
        removed += 1
    return removed


def remove_image_real_object(image_id: str) -> bool:
    if not image_id:
        return False
    removed = False
    for obj in list(bpy.data.objects):
        if object_preserve.is_preserved(obj):
            continue
        if obj.get(on.PROP_KIND) != "image":
            continue
        if str(obj.get(on.PROP_ID, "") or "") != image_id:
            continue
        _remove_object(obj)
        removed = True
    return removed


def sync_all_image_real_objects(scene: bpy.types.Scene, work) -> int:
    if scene is None or work is None:
        return 0
    coll = getattr(scene, "bmanga_image_layers", None)
    if coll is None:
        return 0
    count = 0
    for entry in coll:
        # 過渡状態 (parent_kind=page かつ parent_key 空) の先頭ページ扱いは
        # page_for_entry 側へ一元化済み (書き戻しと同じ規則を共有する)
        page = page_for_entry(scene, work, entry)
        if ensure_image_real_object(scene=scene, entry=entry, page=page) is not None:
            count += 1
    cleanup_orphan_image_objects(scene)
    return count


def on_image_entry_changed(entry) -> bool:
    if auto_sync_suspended():
        return False
    scene = bpy.context.scene if bpy.context is not None else None
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if scene is None or work is None or entry is None:
        return False
    image_id = str(getattr(entry, "id", "") or "")
    target_ptr = 0
    try:
        target_ptr = int(entry.as_pointer())
    except Exception:  # noqa: BLE001
        pass
    coll = getattr(scene, "bmanga_image_layers", None) or []
    for candidate in coll:
        same_id = bool(image_id) and str(getattr(candidate, "id", "") or "") == image_id
        try:
            same_ptr = bool(target_ptr) and int(candidate.as_pointer()) == target_ptr
        except Exception:  # noqa: BLE001
            same_ptr = False
        if same_id or same_ptr:
            return sync_all_image_real_objects(scene, work) > 0
    return False
