"""2D合成プレビューの手動更新・診断入口."""

from __future__ import annotations

import bpy

from ..core.work import get_work
from ..utils import page_file_scene


class BMANGA_OT_preview_composite_refresh(bpy.types.Operator):
    bl_idname = "bmanga.preview_composite_refresh"
    bl_label = "2D合成表示を再生成"
    bl_description = "現在ページの2D合成キャッシュを破棄して高解像度で作り直します"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(
            work is not None
            and getattr(work, "loaded", False)
            and page_file_scene.is_page_edit_scene(getattr(context, "scene", None))
        )

    def execute(self, context):
        from ..utils import preview_composite

        service = preview_composite.get_service()
        service.mark_dirty(context=context)
        frame = service.render_now(context, quality="high", force=True)
        if frame is None:
            self.report({"ERROR"}, "2D合成表示を生成できませんでした")
            return {"CANCELLED"}
        stats = service.cache_stats()
        mebibytes = float(stats["bytes"]) / (1024.0 * 1024.0)
        self.report(
            {"INFO"},
            f"2D合成表示を更新しました（{frame.size[0]}×{frame.size[1]}、{mebibytes:.1f} MiB）",
        )
        return {"FINISHED"}


_CLASSES = (BMANGA_OT_preview_composite_refresh,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
