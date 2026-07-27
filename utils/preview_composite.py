"""書き出しと同じPillow順でページを合成し、GPU用Imageへ転送する."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import time
from typing import Any, Iterable

import bpy
from bpy.app.handlers import persistent

from ..core.work import get_active_page, get_work
from ..io import export_group_masks, export_pipeline, export_stack_order
from . import color_space, layer_stack, log, object_naming as on, page_file_scene

_logger = log.get_logger(__name__)

IMAGE_PREFIX = "BManga_CompositePreview_"
MAX_CACHE_BYTES = 512 * 1024 * 1024
HIGH_DELAY_SECONDS = 0.150
LOW_TIMER_DELAY = 0.01
MAX_LONG_PX = 4096
SANDWICH_KINDS = frozenset({"gp", "effect", "raster"})
_VISUAL_KINDS = frozenset(
    {"balloon", "text", "fill", "image", "image_path", "raster", "effect", "gp"}
)
_OWNER_PROPS = (
    "bmanga_coma_plane_owner_id",
    "bmanga_coma_mask_owner_id",
    "bmanga_coma_border_owner_id",
    "bmanga_coma_white_margin_owner_id",
    "bmanga_balloon_fill_owner_id",
    "bmanga_balloon_fill_mesh_owner_id",
    "bmanga_balloon_line_mesh_owner_id",
    "bmanga_balloon_source_owner_id",
    "bmanga_balloon_clip_mask_owner_id",
    "bmanga_effect_controller_id",
)


@dataclass
class CompositeFrame:
    page_id: str
    dpi: int
    quality: str
    size: tuple[int, int]
    layers: tuple[Any, ...]
    full_pil: Any
    full_image: bpy.types.Image | None
    revision: int = 0
    mode: str = "full"
    anchor_uid: str = ""
    excluded_uids: frozenset[str] = frozenset()
    back_pil: Any | None = None
    active_pil: Any | None = None
    front_pil: Any | None = None
    back_image: bpy.types.Image | None = None
    active_image: bpy.types.Image | None = None
    front_image: bpy.types.Image | None = None
    active_layers: tuple[Any, ...] = ()
    active_z: float = 0.05
    active_offset_mm: tuple[float, float] = (0.0, 0.0)
    byte_size: int = 0
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class _DragRequest:
    anchor_uid: str
    excluded_uids: frozenset[str]
    objects: tuple[bpy.types.Object, ...]
    overlay_only: bool = True
    offset_mm: tuple[float, float] = (0.0, 0.0)


class PreviewCompositeService:
    """dirty通知、LRU、前後分割、GPU転送を一元管理する."""

    def __init__(self, *, max_cache_bytes: int = MAX_CACHE_BYTES) -> None:
        self.max_cache_bytes = max(1, int(max_cache_bytes))
        self._cache: OrderedDict[tuple[str, int], CompositeFrame] = OrderedDict()
        self._dirty_all: set[str] = set()
        self._dirty_order: set[str] = set()
        self._dirty_uids: dict[str, set[str]] = {}
        self._revision: dict[str, int] = {}
        self._latest_key: dict[str, tuple[str, int]] = {}
        self._hidden: dict[int, tuple[bpy.types.Object, bool]] = {}
        self._drag: _DragRequest | None = None
        self._last_dirty_at = 0.0
        self._rendering = False
        self._save_suspended = False
        self._render_count = 0
        self._cache_hits = 0

    @property
    def rendering(self) -> bool:
        return self._rendering

    def enabled(self, scene=None) -> bool:
        scene = scene or getattr(bpy.context, "scene", None)
        return bool(
            scene is not None
            and getattr(scene, "bmanga_composite_preview_enabled", False)
            and page_file_scene.is_page_edit_scene(scene)
        )

    def set_enabled(self, context=None, enabled: bool | None = None) -> None:
        context = context or bpy.context
        scene = getattr(context, "scene", None)
        if scene is None:
            return
        active = self.enabled(scene) if enabled is None else bool(enabled)
        if not active:
            self.restore_objects()
            self._drag = None
            self.tag_redraw(context)
            return
        self.mark_dirty(context=context)

    def mark_dirty(
        self,
        *,
        context=None,
        page_id: str = "",
        layer_uid: str = "",
        order_only: bool = False,
    ) -> None:
        if self._rendering:
            return
        context = context or bpy.context
        scene = getattr(context, "scene", None)
        if scene is None or not self.enabled(scene):
            return
        page_id = str(page_id or page_file_scene.current_page_id(scene) or "")
        if not page_id:
            page = get_active_page(context)
            page_id = str(getattr(page, "id", "") or "") if page is not None else ""
        if not page_id:
            return
        if order_only and page_id not in self._dirty_all:
            self._dirty_order.add(page_id)
        else:
            self._dirty_all.add(page_id)
            self._dirty_order.discard(page_id)
        if layer_uid:
            self._dirty_uids.setdefault(page_id, set()).add(str(layer_uid))
        self._revision[page_id] = self._revision.get(page_id, 0) + 1
        self._last_dirty_at = time.monotonic()
        self._schedule_timers()

    def mark_entry_dirty(self, kind: str, entry, *, context=None) -> None:
        context = context or bpy.context
        scene = getattr(context, "scene", None)
        if entry is None or scene is None:
            return
        entry_id = str(getattr(entry, "id", "") or "")
        page_id = self._entry_page_id(entry)
        uid = layer_stack.target_uid(str(kind or ""), entry_id) if entry_id else ""
        self.mark_dirty(
            context=context,
            page_id=page_id,
            layer_uid=uid,
        )

    def selection_changed(self, context=None) -> None:
        context = context or bpy.context
        if not self.enabled(getattr(context, "scene", None)):
            return
        frame = self.frame_for_page(page_file_scene.current_page_id(context.scene))
        if frame is None:
            self.mark_dirty(context=context, order_only=True)
            return
        self._configure_frame_mode(context, frame)
        self._apply_visibility(context, frame)
        self.tag_redraw(context)

    def render_now(
        self,
        context=None,
        *,
        page=None,
        quality: str = "high",
        force: bool = False,
    ) -> CompositeFrame | None:
        context = context or bpy.context
        scene = getattr(context, "scene", None)
        work = get_work(context)
        page = page or get_active_page(context)
        if (
            scene is None
            or work is None
            or page is None
            or not getattr(work, "loaded", False)
            or (not force and not self.enabled(scene))
        ):
            return None
        page_id = str(getattr(page, "id", "") or "")
        if not page_id or self._rendering:
            return self.frame_for_page(page_id)
        dpi = self._preview_dpi(work, page, scene, quality)
        key = (page_id, dpi)
        cached = self._cache.get(key)
        revision = self._revision.get(page_id, 0)
        dirty = (
            page_id in self._dirty_all
            or (cached is not None and cached.revision != revision)
        )
        order_dirty = page_id in self._dirty_order
        if cached is not None and not dirty and not order_dirty:
            self._cache_hits += 1
            self._touch(key)
            self._latest_key[page_id] = key
            self._configure_frame_mode(context, cached)
            self._ensure_gpu_images(cached)
            self._apply_visibility(context, cached)
            return cached
        self._rendering = True
        try:
            if cached is not None and order_dirty and not dirty:
                layers = export_stack_order.apply_coma_preview_order(
                    work, page, list(cached.layers)
                )
            else:
                options = self._options(dpi)
                layers = export_pipeline.build_page_layers(work, page, options)
                masks = export_pipeline._coma_group_masks(work, page, options)
                layers = export_group_masks.apply_group_masks_to_layers(
                    layers,
                    masks,
                    export_pipeline.Image,
                    export_pipeline.ImageChops,
                )
            size = export_pipeline._page_canvas_size_px(work, page, self._options(dpi))
            full = export_pipeline._flatten_layers(layers, size).convert("RGBA")
            frame = CompositeFrame(
                page_id=page_id,
                dpi=dpi,
                quality=str(quality or "high"),
                size=size,
                layers=tuple(layers),
                full_pil=full,
                full_image=self._upload(page_id, dpi, "full", full),
                revision=revision,
            )
            self._configure_frame_mode(context, frame)
            self._recount_frame_bytes(frame)
            self._replace_cache(key, frame)
            self._latest_key[page_id] = key
            self._dirty_all.discard(page_id)
            self._dirty_order.discard(page_id)
            self._dirty_uids.pop(page_id, None)
            self._render_count += 1
            self._apply_visibility(context, frame)
            self.tag_redraw(context)
            return frame
        except Exception:  # noqa: BLE001
            _logger.exception("2D composite preview build failed: %s", page_id)
            return cached
        finally:
            self._rendering = False

    def frame_for_page(self, page_id: str) -> CompositeFrame | None:
        key = self._latest_key.get(str(page_id or ""))
        frame = self._cache.get(key) if key is not None else None
        if frame is not None:
            self._touch(key)
        return frame

    def ensure_requested(self, context=None) -> None:
        context = context or bpy.context
        if not self.enabled(getattr(context, "scene", None)):
            return
        page_id = page_file_scene.current_page_id(context.scene)
        if self.frame_for_page(page_id) is None:
            self.mark_dirty(context=context, page_id=page_id)

    def begin_drag(
        self,
        context,
        *,
        anchor_uid: str,
        exclude_uids: Iterable[str],
        objects: Iterable[bpy.types.Object],
        overlay_only: bool = True,
    ) -> bool:
        if not self.enabled(getattr(context, "scene", None)):
            return False
        page_id = page_file_scene.current_page_id(context.scene)
        frame = self.frame_for_page(page_id)
        if frame is None:
            self.mark_dirty(context=context, page_id=page_id)
            return False
        self._drag = _DragRequest(
            str(anchor_uid or ""),
            frozenset(str(uid) for uid in exclude_uids if uid),
            tuple(obj for obj in objects if obj is not None),
            bool(overlay_only),
        )
        self._configure_frame_mode(context, frame)
        self._apply_visibility(context, frame)
        self.tag_redraw(context)
        return frame.mode == "split"

    def update_drag(
        self,
        context=None,
        *,
        dx_mm: float = 0.0,
        dy_mm: float = 0.0,
    ) -> None:
        context = context or bpy.context
        if self._drag is not None:
            self._drag.offset_mm = (float(dx_mm), float(dy_mm))
            frame = self.frame_for_page(
                page_file_scene.current_page_id(context.scene)
            )
            if frame is not None:
                frame.active_offset_mm = self._drag.offset_mm
        self.tag_redraw(context)

    def end_drag(self, context=None, *, committed: bool) -> None:
        context = context or bpy.context
        self._drag = None
        if committed:
            self.mark_dirty(context=context)
            return
        frame = self.frame_for_page(page_file_scene.current_page_id(context.scene))
        if frame is not None:
            self._configure_frame_mode(context, frame)
            self._apply_visibility(context, frame)
        self.tag_redraw(context)

    def cache_stats(self) -> dict[str, int]:
        return {
            "entries": len(self._cache),
            "bytes": sum(frame.byte_size for frame in self._cache.values()),
            "renders": self._render_count,
            "hits": self._cache_hits,
            "layer_entries": sum(len(frame.layers) for frame in self._cache.values()),
        }

    def restore_objects(self) -> None:
        for _pointer, (obj, hidden) in list(self._hidden.items()):
            try:
                obj.hide_set(bool(hidden))
            except (ReferenceError, RuntimeError):
                pass
        self._hidden.clear()

    def reset(self, *, remove_images: bool = True) -> None:
        self.restore_objects()
        self._cache.clear()
        self._latest_key.clear()
        self._dirty_all.clear()
        self._dirty_order.clear()
        self._dirty_uids.clear()
        self._revision.clear()
        self._drag = None
        if remove_images:
            self._remove_images()

    def before_save(self) -> None:
        self._save_suspended = True
        self.restore_objects()
        # 生成Imageをblendへ直列化しない。Pillow側キャッシュは保持し、
        # 保存完了後にGPU転送だけをやり直す。
        for frame in self._cache.values():
            self._remove_frame_images(frame)
            frame.full_image = None
            frame.back_image = None
            frame.active_image = None
            frame.front_image = None
            self._recount_frame_bytes(frame)

    def after_save(self) -> None:
        self._save_suspended = False
        context = bpy.context
        frame = self.frame_for_page(page_file_scene.current_page_id(context.scene))
        if frame is not None and self.enabled(context.scene):
            self._ensure_gpu_images(frame)
            self._apply_visibility(context, frame)
            self.tag_redraw(context)

    def _options(self, dpi: int):
        return export_pipeline.ExportOptions(
            color_mode="rgb",
            format="png",
            area="canvas",
            dpi_override=max(1, int(dpi)),
            include_tombo=False,
            include_page_overlay_fills=False,
            prefer_memory_raster=True,
        )

    def _preview_dpi(self, work, page, scene, quality: str) -> int:
        paper_dpi = max(1, int(getattr(work.paper, "dpi", 600) or 600))
        percent = max(
            5.0,
            min(
                200.0,
                float(
                    getattr(
                        scene,
                        "bmanga_page_preview_resolution_percentage",
                        25.0,
                    )
                    or 25.0
                ),
            ),
        )
        high = max(12, int(round(paper_dpi * percent / 100.0)))
        dpi = min(high, 72) if str(quality).lower() == "low" else high
        size = export_pipeline._page_canvas_size_px(work, page, self._options(dpi))
        long_px = max(size)
        if long_px > MAX_LONG_PX:
            dpi = max(12, int(dpi * (MAX_LONG_PX / float(long_px))))
        return dpi

    def _configure_frame_mode(self, context, frame: CompositeFrame) -> None:
        request = self._drag or self._selection_request(context)
        if request is None or not request.anchor_uid:
            self._set_full_mode(frame)
            return
        if (
            frame.mode == "split"
            and frame.anchor_uid == request.anchor_uid
            and frame.excluded_uids == request.excluded_uids
            and frame.back_image is not None
            and frame.front_image is not None
            and (not request.overlay_only or frame.active_image is not None)
        ):
            frame.active_z = self._active_z(request.objects)
            frame.active_offset_mm = request.offset_mm
            return
        back, active, front = export_stack_order.partition_around_stack_uid(
            get_work(context),
            frame.layers,
            request.anchor_uid,
            exclude_uids=request.excluded_uids,
        )
        if not active:
            self._set_full_mode(frame)
            return
        frame.mode = "split"
        frame.anchor_uid = request.anchor_uid
        frame.excluded_uids = request.excluded_uids
        frame.active_layers = tuple(active)
        frame.active_offset_mm = request.offset_mm
        frame.back_pil = export_pipeline._flatten_layers(back, frame.size).convert("RGBA")
        frame.front_pil = export_pipeline._flatten_layers(front, frame.size).convert("RGBA")
        frame.back_image = self._upload(
            frame.page_id, frame.dpi, "back", frame.back_pil
        )
        frame.front_image = self._upload(
            frame.page_id, frame.dpi, "front", frame.front_pil
        )
        if request.overlay_only:
            frame.active_pil = export_pipeline._flatten_layers(
                active,
                frame.size,
            ).convert("RGBA")
            frame.active_image = self._upload(
                frame.page_id,
                frame.dpi,
                "active",
                frame.active_pil,
            )
        else:
            self._remove_image(frame.active_image)
            frame.active_pil = None
            frame.active_image = None
        keep_objects = request.objects
        frame.active_z = self._active_z(keep_objects)
        self._recount_frame_bytes(frame)

    def _set_full_mode(self, frame: CompositeFrame) -> None:
        self._remove_image(frame.back_image)
        self._remove_image(frame.active_image)
        self._remove_image(frame.front_image)
        frame.mode = "full"
        frame.anchor_uid = ""
        frame.excluded_uids = frozenset()
        frame.active_layers = ()
        frame.back_pil = None
        frame.active_pil = None
        frame.front_pil = None
        frame.back_image = None
        frame.active_image = None
        frame.front_image = None
        frame.active_offset_mm = (0.0, 0.0)
        self._recount_frame_bytes(frame)

    def _selection_request(self, context) -> _DragRequest | None:
        stack = getattr(context.scene, "bmanga_layer_stack", None)
        index = int(getattr(context.scene, "bmanga_active_layer_stack_index", -1))
        if stack is None or not (0 <= index < len(stack)):
            return None
        item = stack[index]
        if str(getattr(item, "kind", "") or "") not in SANDWICH_KINDS:
            return None
        uid = layer_stack.stack_item_uid(item)
        resolved = layer_stack.resolve_stack_item(context, item)
        objects = self._objects_for_resolved(resolved)
        return _DragRequest(
            uid,
            frozenset({uid}),
            tuple(objects),
            overlay_only=False,
        )

    def _objects_for_resolved(self, resolved) -> set[bpy.types.Object]:
        if not resolved:
            return set()
        obj = resolved.get("object")
        objects = {obj} if obj is not None else set()
        if obj is not None:
            stable_id = str(obj.get(on.PROP_ID, "") or "")
            for candidate in bpy.data.objects:
                if (
                    candidate.parent is obj
                    or str(candidate.get("bmanga_effect_controller_id", "") or "")
                    == stable_id
                ):
                    objects.add(candidate)
        return self._expand_objects(objects)

    def _apply_visibility(self, context, frame: CompositeFrame) -> None:
        if self._save_suspended or not self.enabled(getattr(context, "scene", None)):
            self.restore_objects()
            return
        keep = set()
        if frame.mode == "split":
            request = self._drag or self._selection_request(context)
            if request is not None and not request.overlay_only:
                keep = self._expand_objects(request.objects)
        for obj in self._visual_objects(getattr(context, "scene", None)):
            pointer = int(obj.as_pointer())
            if pointer not in self._hidden:
                try:
                    self._hidden[pointer] = (obj, bool(obj.hide_get()))
                except (ReferenceError, RuntimeError):
                    continue
            original_hidden = self._hidden[pointer][1]
            try:
                obj.hide_set(original_hidden if obj in keep else True)
            except (ReferenceError, RuntimeError):
                pass

    def _visual_objects(self, scene):
        """現在のシーンにリンクされた作品実体だけを表示制御する."""
        for obj in getattr(scene, "objects", ()) or ():
            kind = str(obj.get(on.PROP_KIND, "") or "")
            if kind in _VISUAL_KINDS:
                yield obj
                continue
            if str(obj.get("bmanga_paper_bg_kind", "") or ""):
                yield obj
                continue
            if any(str(obj.get(prop, "") or "") for prop in _OWNER_PROPS):
                yield obj

    def _expand_objects(self, objects) -> set[bpy.types.Object]:
        result = {obj for obj in objects if obj is not None}
        pending = list(result)
        while pending:
            obj = pending.pop()
            parent = getattr(obj, "parent", None)
            if parent is not None and parent not in result:
                result.add(parent)
                pending.append(parent)
            for child in getattr(obj, "children", ()) or ():
                if child not in result:
                    result.add(child)
                    pending.append(child)
        return result

    def _active_z(self, objects) -> float:
        values = []
        for obj in objects:
            try:
                values.append(float(obj.matrix_world.translation.z))
            except (ReferenceError, AttributeError):
                pass
        return sum(values) / len(values) if values else 0.05

    def _entry_page_id(self, entry) -> str:
        parent = str(getattr(entry, "parent_key", "") or "")
        if parent:
            return parent.split(":", 1)[0]
        work = get_work(bpy.context)
        entry_id = str(getattr(entry, "id", "") or "")
        for page in getattr(work, "pages", ()) if work is not None else ():
            for attr in ("balloons", "texts"):
                if any(
                    str(getattr(candidate, "id", "") or "") == entry_id
                    for candidate in getattr(page, attr, ()) or ()
                ):
                    return str(getattr(page, "id", "") or "")
        return page_file_scene.current_page_id(getattr(bpy.context, "scene", None))

    def _upload(self, page_id: str, dpi: int, role: str, pil_image):
        if pil_image is None:
            return None
        name = f"{IMAGE_PREFIX}{page_id}_{dpi}_{role}"
        image = bpy.data.images.get(name)
        width, height = int(pil_image.width), int(pil_image.height)
        if image is None:
            image = bpy.data.images.new(name, width=width, height=height, alpha=True)
        elif tuple(int(v) for v in image.size[:2]) != (width, height):
            image.scale(width, height)
        try:
            image.colorspace_settings.name = "sRGB"
            image.alpha_mode = "STRAIGHT"
        except Exception:  # noqa: BLE001
            pass
        try:
            import numpy as np

            flipped = pil_image.transpose(export_pipeline.Image.Transpose.FLIP_TOP_BOTTOM)
            pixels = np.asarray(flipped, dtype=np.float32).reshape((-1, 4)) / 255.0
            rgb = pixels[:, :3]
            pixels[:, :3] = np.where(
                rgb <= 0.04045,
                rgb / 12.92,
                np.power((rgb + 0.055) / 1.055, 2.4),
            )
            image.pixels.foreach_set(pixels.reshape(-1))
        except Exception:  # noqa: BLE001
            flipped = pil_image.transpose(export_pipeline.Image.Transpose.FLIP_TOP_BOTTOM)
            image.pixels.foreach_set(
                [
                    (
                        channel / 255.0
                        if channel_index == 3
                        else color_space.srgb_to_linear_value(channel / 255.0)
                    )
                    for pixel in flipped.getdata()
                    for channel_index, channel in enumerate(pixel)
                ]
            )
        image.update()
        return image

    def _ensure_gpu_images(self, frame: CompositeFrame) -> None:
        if frame.full_image is None:
            frame.full_image = self._upload(
                frame.page_id,
                frame.dpi,
                "full",
                frame.full_pil,
            )
        if frame.mode == "split":
            if frame.back_image is None:
                frame.back_image = self._upload(
                    frame.page_id,
                    frame.dpi,
                    "back",
                    frame.back_pil,
                )
            if frame.front_image is None:
                frame.front_image = self._upload(
                    frame.page_id,
                    frame.dpi,
                    "front",
                    frame.front_pil,
                )
            if frame.active_pil is not None and frame.active_image is None:
                frame.active_image = self._upload(
                    frame.page_id,
                    frame.dpi,
                    "active",
                    frame.active_pil,
                )
        self._recount_frame_bytes(frame)

    def _recount_frame_bytes(self, frame: CompositeFrame) -> None:
        images = [layer.image for layer in frame.layers]
        images.extend(
            [frame.full_pil, frame.back_pil, frame.active_pil, frame.front_pil]
        )
        pil_bytes = sum(
            int(image.width) * int(image.height) * 4
            for image in images
            if image is not None
        )
        gpu_bytes = sum(
            int(image.size[0]) * int(image.size[1]) * 4
            for image in (
                frame.full_image,
                frame.back_image,
                frame.active_image,
                frame.front_image,
            )
            if image is not None
        )
        frame.byte_size = pil_bytes + gpu_bytes

    def _replace_cache(self, key, frame: CompositeFrame) -> None:
        self._cache[key] = frame
        self._touch(key)
        self._evict(frame.page_id)

    def _touch(self, key) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)

    def _evict(self, active_page_id: str) -> None:
        while sum(frame.byte_size for frame in self._cache.values()) > self.max_cache_bytes:
            candidate = next(
                (
                    key
                    for key, frame in self._cache.items()
                    if frame.page_id != active_page_id
                ),
                next(iter(self._cache), None),
            )
            if candidate is None:
                break
            frame = self._cache.pop(candidate)
            if self._latest_key.get(frame.page_id) == candidate:
                self._latest_key.pop(frame.page_id, None)
            self._remove_frame_images(frame)

    def _remove_frame_images(self, frame: CompositeFrame) -> None:
        for image in (
            frame.full_image,
            frame.back_image,
            frame.active_image,
            frame.front_image,
        ):
            self._remove_image(image)

    @staticmethod
    def _remove_image(image) -> None:
        if image is None or image.name not in bpy.data.images:
            return
        if int(getattr(image, "users", 0) or 0) == 0:
            bpy.data.images.remove(image)

    def _remove_images(self) -> None:
        for image in list(bpy.data.images):
            if image.name.startswith(IMAGE_PREFIX) and int(image.users) == 0:
                bpy.data.images.remove(image)

    def _schedule_timers(self) -> None:
        try:
            if not bpy.app.timers.is_registered(_low_refresh_timer):
                bpy.app.timers.register(
                    _low_refresh_timer,
                    first_interval=LOW_TIMER_DELAY,
                )
            if not bpy.app.timers.is_registered(_high_refresh_timer):
                bpy.app.timers.register(
                    _high_refresh_timer,
                    first_interval=HIGH_DELAY_SECONDS,
                )
        except Exception:  # noqa: BLE001
            _logger.exception("2D composite preview timer registration failed")

    def _frozen(self) -> bool:
        mode = str(getattr(bpy.context, "mode", "") or "")
        return mode in {"PAINT_GREASE_PENCIL", "PAINT_GPENCIL"}

    def run_low_timer(self):
        if self._frozen():
            return 0.05
        self.render_now(bpy.context, quality="low")
        return None

    def run_high_timer(self):
        if self._frozen():
            return 0.05
        remaining = HIGH_DELAY_SECONDS - (time.monotonic() - self._last_dirty_at)
        if remaining > 0.0:
            return max(0.01, remaining)
        self.render_now(bpy.context, quality="high")
        return None

    @staticmethod
    def tag_redraw(context) -> None:
        screen = getattr(context, "screen", None)
        if screen is None:
            return
        for area in getattr(screen, "areas", ()) or ():
            if getattr(area, "type", "") == "VIEW_3D":
                area.tag_redraw()


SERVICE = PreviewCompositeService()


def get_service() -> PreviewCompositeService:
    return SERVICE


def mark_dirty(**kwargs) -> None:
    SERVICE.mark_dirty(**kwargs)


def mark_entry_dirty(kind: str, entry, *, context=None) -> None:
    SERVICE.mark_entry_dirty(kind, entry, context=context)


def selection_changed(context=None) -> None:
    SERVICE.selection_changed(context)


def _low_refresh_timer():
    return SERVICE.run_low_timer()


def _high_refresh_timer():
    return SERVICE.run_high_timer()


@persistent
def _on_load_post(*_args) -> None:
    SERVICE.reset(remove_images=True)
    if SERVICE.enabled(getattr(bpy.context, "scene", None)):
        SERVICE.mark_dirty(context=bpy.context)


@persistent
def _on_save_pre(*_args) -> None:
    SERVICE.before_save()


@persistent
def _on_save_post(*_args) -> None:
    SERVICE.after_save()


@persistent
def _on_depsgraph_update_post(scene, depsgraph) -> None:
    """GP描画の連続更新をdirty化し、描画終了後の再合成へつなぐ."""
    if not SERVICE.enabled(scene) or SERVICE.rendering:
        return
    objects = tuple(getattr(scene, "objects", ()) or ())
    for update in getattr(depsgraph, "updates", ()) or ():
        updated = getattr(update, "id", None)
        if updated is None:
            continue
        if any(
            getattr(obj, "data", None) == updated
            and str(obj.get(on.PROP_KIND, "") or "") == "gp"
            for obj in objects
        ):
            SERVICE.mark_dirty(context=bpy.context)
            return


def _remove_named_handler(handlers, name: str) -> None:
    for handler in list(handlers):
        if getattr(handler, "__name__", "") == name:
            handlers.remove(handler)


def register() -> None:
    _remove_named_handler(bpy.app.handlers.load_post, _on_load_post.__name__)
    _remove_named_handler(bpy.app.handlers.save_pre, _on_save_pre.__name__)
    _remove_named_handler(bpy.app.handlers.save_post, _on_save_post.__name__)
    _remove_named_handler(
        bpy.app.handlers.depsgraph_update_post,
        _on_depsgraph_update_post.__name__,
    )
    bpy.app.handlers.load_post.append(_on_load_post)
    bpy.app.handlers.save_pre.append(_on_save_pre)
    bpy.app.handlers.save_post.append(_on_save_post)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update_post)


def unregister() -> None:
    for timer in (_low_refresh_timer, _high_refresh_timer):
        try:
            if bpy.app.timers.is_registered(timer):
                bpy.app.timers.unregister(timer)
        except Exception:  # noqa: BLE001
            pass
    _remove_named_handler(bpy.app.handlers.load_post, _on_load_post.__name__)
    _remove_named_handler(bpy.app.handlers.save_pre, _on_save_pre.__name__)
    _remove_named_handler(bpy.app.handlers.save_post, _on_save_post.__name__)
    _remove_named_handler(
        bpy.app.handlers.depsgraph_update_post,
        _on_depsgraph_update_post.__name__,
    )
    SERVICE.reset()


__all__ = [
    "CompositeFrame",
    "PreviewCompositeService",
    "SERVICE",
    "get_service",
    "mark_dirty",
    "mark_entry_dirty",
    "selection_changed",
]
