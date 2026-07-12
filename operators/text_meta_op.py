"""テキストのメタ情報編集ダイアログ."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..utils import layer_stack as layer_stack_utils
from ..utils import object_naming as on


def _resolve_text_entry(context):
    scene = getattr(context, "scene", None)
    if scene is None:
        return None, None
    stack = getattr(scene, "bmanga_layer_stack", None)
    idx = int(getattr(scene, "bmanga_active_layer_stack_index", -1))
    item = stack[idx] if stack is not None and 0 <= idx < len(stack) else None
    resolved = layer_stack_utils.resolve_stack_item(context, item) if item is not None else None
    if resolved is not None and resolved.get("kind") == "text" and resolved.get("target") is not None:
        return resolved.get("page"), resolved.get("target")

    obj = getattr(context, "active_object", None)
    if obj is not None and on.is_managed(obj) and on.get_kind(obj) == "text":
        try:
            from ..utils import text_real_object

            return text_real_object.find_text_entry(scene, on.get_bmanga_id(obj))
        except Exception:  # noqa: BLE001
            return None, None

    work = getattr(scene, "bmanga_work", None)
    if work is None:
        return None, None
    idx = int(getattr(work, "active_page_index", -1))
    if 0 <= idx < len(work.pages):
        page = work.pages[idx]
        text_idx = int(getattr(page, "active_text_index", -1))
        if 0 <= text_idx < len(page.texts):
            return page, page.texts[text_idx]
    return None, None


class BMANGA_OT_text_meta_dialog(Operator):
    """テキストの話者などメタ情報を編集する."""

    bl_idname = "bmanga.text_meta_dialog"
    bl_label = "テキストメタ情報"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        _page, entry = _resolve_text_entry(context)
        return entry is not None

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=260)

    def draw(self, context):
        layout = self.layout
        page, entry = _resolve_text_entry(context)
        if entry is None:
            layout.label(text="テキストが選択されていません", icon="INFO")
            return
        box = layout.box()
        box.label(text="テキスト", icon="FONT_DATA")
        box.prop(entry, "body", text="本文")
        box.prop(entry, "visible", text="表示")
        box = layout.box()
        box.label(text="メタ情報", icon="INFO")
        box.prop(entry, "speaker_name", text="話者")
        box.prop(entry, "parent_balloon_id", text="親フキダシ")
        owner = "ページ外" if page is None else str(getattr(page, "title", "") or getattr(page, "id", "") or "ページ")
        box.label(text=f"所属: {owner}")

    def execute(self, context):
        _page, entry = _resolve_text_entry(context)
        if entry is not None:
            try:
                from ..utils import text_real_object

                text_real_object.on_text_entry_changed(entry)
            except Exception:  # noqa: BLE001
                pass
        return {"FINISHED"}


_CLASSES = (BMANGA_OT_text_meta_dialog,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
