"""レイヤーとフキダシしっぽのコピー / 貼り付け Operator."""

from __future__ import annotations

from array import array
import json
from pathlib import Path

import bpy
from bpy.types import Operator

from ..core.work import get_work
from ..utils import layer_stack as layer_stack_utils

_LAYER_CLIPBOARD_KEY = "bname_layer_clipboard_json"
_TAIL_CLIPBOARD_KEY = "bname_balloon_tail_clipboard_json"
_COPYABLE_KINDS = {"balloon", "text", "raster", "gp", "effect", "effect_legacy"}


def _active_stack_item(context, *, sync: bool = False):
    if sync:
        stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    else:
        stack = getattr(getattr(context, "scene", None), "bname_layer_stack", None)
    if stack is None:
        return None
    idx = int(getattr(context.scene, "bname_active_layer_stack_index", -1))
    if 0 <= idx < len(stack):
        return stack[idx]
    return None


def _normal_kind(kind: str) -> str:
    return "effect" if kind == "effect_legacy" else str(kind or "")


def _item_uid(item) -> str:
    return layer_stack_utils.stack_item_uid(item) if item is not None else ""


def _find_stack_item_by_uid(context, uid: str):
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None or not uid:
        return -1, None
    for index, item in enumerate(stack):
        if layer_stack_utils.stack_item_uid(item) == uid:
            return index, item
    return -1, None


def _set_clipboard(context, key: str, payload: dict) -> None:
    wm = getattr(context, "window_manager", None)
    if wm is None:
        return
    wm[key] = json.dumps(payload, ensure_ascii=False)


def _get_clipboard(context, key: str) -> dict:
    wm = getattr(context, "window_manager", None)
    if wm is None:
        return {}
    raw = str(wm.get(key, "") or "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def has_layer_clipboard(context) -> bool:
    return _get_clipboard(context, _LAYER_CLIPBOARD_KEY).get("type") == "layer"


def has_tail_clipboard(context) -> bool:
    data = _get_clipboard(context, _TAIL_CLIPBOARD_KEY)
    return data.get("type") == "balloon_tails" and bool(data.get("tails"))


def active_item_copyable(context) -> bool:
    item = _active_stack_item(context)
    return item is not None and _normal_kind(getattr(item, "kind", "")) in _COPYABLE_KINDS


def active_balloon_has_tails(context) -> bool:
    item = _active_stack_item(context)
    if item is None or _normal_kind(getattr(item, "kind", "")) != "balloon":
        return False
    resolved = layer_stack_utils.resolve_stack_item(context, item)
    entry = resolved.get("target") if resolved is not None else None
    return entry is not None and len(getattr(entry, "tails", [])) > 0


def active_balloon_target(context):
    item = _active_stack_item(context)
    if item is None or _normal_kind(getattr(item, "kind", "")) != "balloon":
        return None, None
    resolved = layer_stack_utils.resolve_stack_item(context, item)
    if resolved is None:
        return None, None
    return resolved.get("page"), resolved.get("target")


def _copy_pixels(source, dest) -> bool:
    try:
        width = int(source.size[0])
        height = int(source.size[1])
        if width != int(dest.size[0]) or height != int(dest.size[1]):
            return False
        data = array("f", source.pixels[:])
        if len(data) != width * height * 4:
            return False
        dest.pixels.foreach_set(data)
        dest.update()
        return True
    except Exception:  # noqa: BLE001
        return False


def duplicate_raster_item(context, item) -> bool:
    from ..io import schema
    from . import raster_layer_op

    resolved = layer_stack_utils.resolve_stack_item(context, item)
    src = resolved.get("target") if resolved is not None else None
    coll = getattr(context.scene, "bname_raster_layers", None)
    work = get_work(context)
    if src is None or coll is None or work is None or not getattr(work, "work_dir", ""):
        return False

    src_image = raster_layer_op.ensure_raster_image(context, src, create_missing=False)
    dst = coll.add()
    schema.raster_layer_from_dict(dst, schema.raster_layer_to_dict(src))
    raster_id = raster_layer_op._allocate_raster_id(context.scene, Path(work.work_dir))
    dst.id = raster_id
    dst.title = _unique_title(coll, dst, f"{getattr(src, 'title', '') or 'ラスター'} 複製")
    dst.image_name = raster_layer_op.raster_image_name(raster_id)
    dst.filepath_rel = raster_layer_op.raster_filepath_rel(raster_id)
    context.scene.bname_active_raster_layer_index = len(coll) - 1
    context.scene.bname_active_layer_kind = "raster"

    dst_image = raster_layer_op.ensure_raster_image(context, dst, create_missing=True)
    if src_image is not None and dst_image is not None:
        _copy_pixels(src_image, dst_image)
    raster_layer_op.save_raster_png(context, dst, force=True)
    if raster_layer_op.ensure_raster_plane(context, dst) is None:
        coll.remove(len(coll) - 1)
        return False
    layer_stack_utils.sync_layer_stack_after_data_change(context)
    _select_new_layer(context, "raster", raster_id)
    return True


def _unique_title(coll, dst, base: str) -> str:
    used = {str(getattr(entry, "title", "") or "") for entry in coll if entry is not dst}
    if base not in used:
        return base
    index = 1
    while True:
        candidate = f"{base}.{index:03d}"
        if candidate not in used:
            return candidate
        index += 1


def _select_new_layer(context, kind: str, key: str) -> None:
    uid = layer_stack_utils.target_uid(kind, key)
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None:
        return
    for index, item in enumerate(stack):
        if layer_stack_utils.stack_item_uid(item) == uid:
            layer_stack_utils.select_stack_index(context, index)
            return


def _tail_to_dict(tail) -> dict:
    return {
        "type": str(getattr(tail, "type", "straight") or "straight"),
        "directionDeg": float(getattr(tail, "direction_deg", 270.0)),
        "lengthMm": float(getattr(tail, "length_mm", 6.0)),
        "rootWidthMm": float(getattr(tail, "root_width_mm", 3.0)),
        "tipWidthMm": float(getattr(tail, "tip_width_mm", 0.0)),
        "curveBend": float(getattr(tail, "curve_bend", 0.0)),
    }


def _append_tail_from_dict(entry, data: dict) -> None:
    tail = entry.tails.add()
    tail.type = str(data.get("type", "straight") or "straight")
    tail.direction_deg = float(data.get("directionDeg", 270.0))
    tail.length_mm = float(data.get("lengthMm", 6.0))
    tail.root_width_mm = float(data.get("rootWidthMm", 3.0))
    tail.tip_width_mm = float(data.get("tipWidthMm", 0.0))
    tail.curve_bend = float(data.get("curveBend", 0.0))


class BNAME_OT_layer_clipboard_copy(Operator):
    bl_idname = "bname.layer_clipboard_copy"
    bl_label = "コピー"
    bl_description = "選択中のレイヤーをコピーします"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return active_item_copyable(context)

    def execute(self, context):
        item = _active_stack_item(context, sync=True)
        if item is None:
            return {"CANCELLED"}
        kind = _normal_kind(str(getattr(item, "kind", "") or ""))
        payload = {
            "type": "layer",
            "version": 1,
            "kind": kind,
            "uid": _item_uid(item),
            "key": str(getattr(item, "key", "") or ""),
            "label": str(getattr(item, "label", "") or getattr(item, "name", "") or ""),
        }
        _set_clipboard(context, _LAYER_CLIPBOARD_KEY, payload)
        self.report({"INFO"}, "コピーしました")
        return {"FINISHED"}


class BNAME_OT_layer_clipboard_paste(Operator):
    bl_idname = "bname.layer_clipboard_paste"
    bl_label = "貼り付け"
    bl_description = "コピーしたレイヤーを貼り付けます"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return has_layer_clipboard(context)

    def execute(self, context):
        data = _get_clipboard(context, _LAYER_CLIPBOARD_KEY)
        uid = str(data.get("uid", "") or "")
        index, item = _find_stack_item_by_uid(context, uid)
        if item is None:
            self.report({"WARNING"}, "コピー元のレイヤーが見つかりません")
            return {"CANCELLED"}
        if not layer_stack_utils.select_stack_index(context, index):
            self.report({"WARNING"}, "コピー元のレイヤーを選択できません")
            return {"CANCELLED"}
        kind = _normal_kind(str(getattr(item, "kind", "") or ""))
        if kind == "raster":
            ok = duplicate_raster_item(context, item)
        else:
            result = bpy.ops.bname.layer_stack_duplicate("EXEC_DEFAULT")
            ok = "FINISHED" in result
        if not ok:
            self.report({"WARNING"}, "貼り付けできません")
            return {"CANCELLED"}
        self.report({"INFO"}, "貼り付けました")
        return {"FINISHED"}


class BNAME_OT_balloon_tail_clipboard_copy(Operator):
    bl_idname = "bname.balloon_tail_clipboard_copy"
    bl_label = "しっぽをコピー"
    bl_description = "選択中のフキダシのしっぽをコピーします"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return active_balloon_has_tails(context)

    def execute(self, context):
        _page, entry = active_balloon_target(context)
        if entry is None or len(getattr(entry, "tails", [])) == 0:
            self.report({"WARNING"}, "コピーできるしっぽがありません")
            return {"CANCELLED"}
        payload = {
            "type": "balloon_tails",
            "version": 1,
            "tails": [_tail_to_dict(tail) for tail in entry.tails],
        }
        _set_clipboard(context, _TAIL_CLIPBOARD_KEY, payload)
        self.report({"INFO"}, "しっぽをコピーしました")
        return {"FINISHED"}


class BNAME_OT_balloon_tail_clipboard_paste(Operator):
    bl_idname = "bname.balloon_tail_clipboard_paste"
    bl_label = "しっぽを貼り付け"
    bl_description = "コピーしたしっぽを選択中のフキダシへ追加します"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        _page, entry = active_balloon_target(context)
        return entry is not None and has_tail_clipboard(context)

    def execute(self, context):
        page, entry = active_balloon_target(context)
        data = _get_clipboard(context, _TAIL_CLIPBOARD_KEY)
        tails = data.get("tails", [])
        if entry is None or not isinstance(tails, list) or not tails:
            self.report({"WARNING"}, "貼り付けるしっぽがありません")
            return {"CANCELLED"}
        for tail_data in tails:
            if isinstance(tail_data, dict):
                _append_tail_from_dict(entry, tail_data)
        try:
            from .balloon_tail_op import _sync_after_tail_change

            _sync_after_tail_change(context, page, entry)
        except Exception:  # noqa: BLE001
            layer_stack_utils.sync_layer_stack_after_data_change(context)
        self.report({"INFO"}, "しっぽを貼り付けました")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_layer_clipboard_copy,
    BNAME_OT_layer_clipboard_paste,
    BNAME_OT_balloon_tail_clipboard_copy,
    BNAME_OT_balloon_tail_clipboard_paste,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
