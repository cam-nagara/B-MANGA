"""ページ用blendファイルのオープン/復帰 Operator."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import IntProperty
from bpy.types import Operator

from ..core.mode import MODE_PAGE, get_mode, set_mode
from ..core.work import get_work
from ..io import blend_io, page_io
from ..utils import log, page_file_scene, page_grid, paths
from . import coma_modal_state

_logger = log.get_logger(__name__)


def _cleanup_default_scene_objects() -> None:
    for name in ("Cube", "Light", "Camera"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:  # noqa: BLE001
                pass


def _save_current_blend_if_bname(context, work_dir: Path) -> None:
    role, page_id, coma_id = page_file_scene.current_role(context)
    if role == page_file_scene.ROLE_WORK:
        blend_io.save_work_blend(work_dir)
    elif role == page_file_scene.ROLE_PAGE and paths.is_valid_page_id(page_id):
        blend_io.save_page_blend(work_dir, page_id)
    elif role == page_file_scene.ROLE_COMA and paths.is_valid_page_id(page_id) and paths.is_valid_coma_id(coma_id):
        blend_io.save_coma_blend(work_dir, page_id, coma_id)


def _save_metadata(context, *, reason: str) -> bool:
    try:
        from ..utils import handlers

        return handlers.save_scene_work_to_disk(context, reason=reason)
    except Exception:  # noqa: BLE001
        _logger.exception("%s: metadata save failed", reason)
        return False


def _load_work_metadata_into_current_scene(work_dir: Path):
    from ..utils import handlers

    return handlers.sync_scene_work_from_disk(bpy.context, work_dir)


def _finalize_page_scene(context, work, page_id: str) -> bool:
    if work is None:
        return False
    if not page_file_scene.set_page_edit_state(context, page_id):
        return False
    try:
        from ..utils import display_settings

        display_settings.apply_standard_color_management(context.scene)
    except Exception:  # noqa: BLE001
        _logger.exception("page file: color management setup failed")
    try:
        from ..utils import layer_object_sync

        layer_object_sync.mirror_work_to_outliner(context.scene, work)
    except Exception:  # noqa: BLE001
        _logger.exception("page file: outliner mirror failed")
    try:
        page_grid.apply_page_collection_transforms(context, work)
    except Exception:  # noqa: BLE001
        _logger.exception("page file: page transform setup failed")
    try:
        page_file_scene.purge_other_page_data(context.scene, page_id)
    except Exception:  # noqa: BLE001
        _logger.exception("page file: purge other page data failed")
    try:
        from ..utils import page_preview_object

        page_preview_object.sync_page_previews(context, work)
    except Exception:  # noqa: BLE001
        _logger.exception("page file: page preview setup failed")
    try:
        from . import raster_layer_op

        raster_layer_op.ensure_all_raster_runtime(context)
    except Exception:  # noqa: BLE001
        _logger.exception("page file: raster runtime setup failed")
    try:
        from ..utils import layer_stack

        layer_stack.sync_layer_stack(context)
        layer_stack.schedule_layer_stack_sync()
    except Exception:  # noqa: BLE001
        _logger.exception("page file: layer stack setup failed")
    try:
        from ..ui import overlay as _overlay

        _overlay.reset_viewport_background_to_theme(context)
        _overlay.apply_bname_shading_mode(context)
        _overlay.set_viewport_overlays_enabled(context, enabled=False)
        _overlay.schedule_viewport_overlays_enabled(enabled=False)
    except Exception:  # noqa: BLE001
        _logger.exception("page file: viewport setup failed")
    return True


def _create_page_blend(work_dir: Path, page_id: str) -> bool:
    if not blend_io.read_homefile():
        return False
    context = bpy.context
    _cleanup_default_scene_objects()
    work = _load_work_metadata_into_current_scene(work_dir)
    if work is None:
        return False
    if not _finalize_page_scene(context, work, page_id):
        return False
    return blend_io.save_page_blend(work_dir, page_id)


class BNAME_OT_open_page_file(Operator):
    """選択ページのページ用blendファイルを開く."""

    bl_idname = "bname.open_page_file"
    bl_label = "ページを開く"
    bl_options = {"REGISTER"}

    index: IntProperty(default=-1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(
            work
            and work.loaded
            and bool(work.work_dir)
            and get_mode(context) == MODE_PAGE
        )

    def execute(self, context):
        coma_modal_state.finish_all(context)
        work = get_work(context)
        if work is None or not work.loaded or not work.work_dir:
            self.report({"ERROR"}, "作品が開かれていません")
            return {"CANCELLED"}
        if 0 <= int(self.index) < len(work.pages):
            work.active_page_index = int(self.index)
        if not (0 <= work.active_page_index < len(work.pages)):
            self.report({"ERROR"}, "ページが選択されていません")
            return {"CANCELLED"}
        page = work.pages[work.active_page_index]
        page_id = str(getattr(page, "id", "") or "")
        if not paths.is_valid_page_id(page_id):
            self.report({"ERROR"}, "ページを開けません")
            return {"CANCELLED"}
        work_dir = Path(work.work_dir)
        try:
            page_io.ensure_page_dir(work_dir, page_id)
            if not _save_metadata(context, reason="open_page_file"):
                self.report({"ERROR"}, "作品情報の保存に失敗しました")
                return {"CANCELLED"}
            _save_current_blend_if_bname(context, work_dir)
            if blend_io.page_blend_exists(work_dir, page_id):
                if not blend_io.open_page_blend(work_dir, page_id):
                    self.report({"ERROR"}, "ページを開けませんでした")
                    return {"CANCELLED"}
            else:
                if not _create_page_blend(work_dir, page_id):
                    self.report({"ERROR"}, "ページ用blendファイルの作成に失敗しました")
                    try:
                        blend_io.open_work_blend(work_dir)
                    except Exception:  # noqa: BLE001
                        pass
                    return {"CANCELLED"}
        except Exception as exc:  # noqa: BLE001
            _logger.exception("open_page_file failed")
            self.report({"ERROR"}, f"ページを開けませんでした: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, "ページを開きました")
        return {"FINISHED"}


class BNAME_OT_exit_page_file(Operator):
    """ページ用blendファイルを保存してページ一覧へ戻る."""

    bl_idname = "bname.exit_page_file"
    bl_label = "ページ一覧に戻る"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        role, _page_id, _coma_id = page_file_scene.current_role(context)
        return bool(work and work.loaded and role == page_file_scene.ROLE_PAGE)

    def execute(self, context):
        coma_modal_state.finish_all(context)
        work = get_work(context)
        if work is None or not work.loaded or not work.work_dir:
            return {"CANCELLED"}
        work_dir = Path(work.work_dir)
        role, page_id, _coma_id = page_file_scene.current_role(context)
        if role != page_file_scene.ROLE_PAGE or not paths.is_valid_page_id(page_id):
            self.report({"ERROR"}, "ページ用blendファイルではありません")
            return {"CANCELLED"}
        try:
            if not _save_metadata(context, reason="exit_page_file"):
                self.report({"ERROR"}, "作品情報の保存に失敗しました")
                return {"CANCELLED"}
            blend_io.save_page_blend(work_dir, page_id)
            if not blend_io.work_blend_exists(work_dir):
                self.report({"ERROR"}, "ページ一覧ファイルが見つかりません")
                return {"CANCELLED"}
            if not blend_io.open_work_blend(work_dir):
                self.report({"ERROR"}, "ページ一覧へ戻れませんでした")
                return {"CANCELLED"}
        except Exception as exc:  # noqa: BLE001
            _logger.exception("exit_page_file failed")
            self.report({"ERROR"}, f"ページ一覧へ戻れませんでした: {exc}")
            return {"CANCELLED"}
        ctx = bpy.context
        set_mode(MODE_PAGE, ctx)
        page_file_scene.set_work_list_state(ctx)
        self.report({"INFO"}, "ページ一覧に戻りました")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_open_page_file,
    BNAME_OT_exit_page_file,
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
