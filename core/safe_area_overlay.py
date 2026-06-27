"""セーフライン外側表示の PropertyGroup.

表示実体は用紙ガイドオブジェクト側で生成する。
ここではデータモデルと既定値のみ保持する。

仕様:
- セーフライン外 / 裁ち落とし枠外の塗りは全コマより上に配置し、
  ビュー表示の「最前面」で見えるようにする
- 塗り色と不透明度は、実体オブジェクトのビュー表示カラーで指定する
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty

from ..utils import log, viewport_colors

_logger = log.get_logger(__name__)

_DEFAULT_OPACITY = 30.0
_DEFAULT_BLEED_OUTER_OPACITY = 100.0
_DEFAULT_BLEED_OUTER_COLOR = viewport_colors.BLENDER_BACKGROUND_DEFAULT_LINEAR


def _on_safe_area_changed(_self, context) -> None:
    try:
        from ..utils import page_preview_object

        page_preview_object.schedule_sync_page_previews(force=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..utils import page_file_scene, paper_guide_object

        scene = getattr(context, "scene", None) if context is not None else None
        work = getattr(scene, "bmanga_work", None) if scene is not None else None
        if scene is not None and work is not None and bool(getattr(work, "loaded", False)):
            page_ids = None
            if page_file_scene.is_page_edit_scene(scene):
                page_id = page_file_scene.current_page_id(scene)
                if page_id:
                    page_ids = {page_id}
            paper_guide_object.regenerate_all_paper_guides(
                scene,
                page_file_scene.work_for_pages(work, page_ids),
            )
            paper_guide_object.apply_view_constant_thickness()
    except Exception:  # noqa: BLE001
        _logger.exception("safe area runtime object sync failed")
    screen = getattr(context, "screen", None) if context is not None else None
    if screen is not None:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


class BMangaSafeAreaOverlay(bpy.types.PropertyGroup):
    """セーフライン外側を実体オブジェクトのビュー表示カラーで塗る設定."""

    enabled: BoolProperty(  # type: ignore[valid-type]
        name="セーフライン",
        description="セーフライン外を暗く表示 (書き出しには含まれない)",
        default=True,
        update=_on_safe_area_changed,
    )
    opacity: FloatProperty(  # type: ignore[valid-type]
        name="不透明度",
        description="セーフライン外の塗りの不透明度",
        default=_DEFAULT_OPACITY,
        min=0.0,
        max=100.0,
        subtype="PERCENTAGE",
        update=_on_safe_area_changed,
    )
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="色",
        description="セーフライン外の塗り色",
        subtype="COLOR",
        size=3,
        default=(0.0, 0.0, 0.0),
        min=0.0,
        max=1.0,
        update=_on_safe_area_changed,
    )
    bleed_outer_enabled: BoolProperty(  # type: ignore[valid-type]
        name="裁ち落とし枠外",
        description="裁ち落とし枠の外側を塗ります",
        default=True,
        update=_on_safe_area_changed,
    )
    bleed_outer_opacity: FloatProperty(  # type: ignore[valid-type]
        name="不透明度",
        description="裁ち落とし枠外の塗りの不透明度",
        default=_DEFAULT_BLEED_OUTER_OPACITY,
        min=0.0,
        max=100.0,
        subtype="PERCENTAGE",
        update=_on_safe_area_changed,
    )
    bleed_outer_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="色",
        description="裁ち落とし枠外の塗り色",
        subtype="COLOR",
        size=3,
        default=_DEFAULT_BLEED_OUTER_COLOR,
        min=0.0,
        max=1.0,
        update=_on_safe_area_changed,
    )


_CLASSES = (BMangaSafeAreaOverlay,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("safe_area_overlay registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
