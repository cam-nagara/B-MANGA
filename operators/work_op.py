"""作品 (.bmanga) の新規作成・オープン・保存・クローズ Operator."""

from __future__ import annotations

from pathlib import Path
import uuid

import bpy
from bpy.props import StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper, ExportHelper

from ..core.mode import MODE_PAGE, MODE_COMA, get_mode, set_mode
from ..core.work import get_work
from ..core.work_info import suppress_page_number_range_update
from ..io import blend_io, page_io, presets, work_io
from ..utils import color_space, detail_popup, log, page_grid, page_range, paths, view_settings

_logger = log.get_logger(__name__)


def _recover_selected_work_before_open(work_dir: Path) -> tuple[bool, str]:
    """work.blendが退避中でも、作品を開く入口から先に復旧できるようにする."""

    try:
        from ..io import native_save_guard

        native_save_guard.recover_pending_native_saves(work_dir)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        _logger.exception("work_open: pending native save recovery failed")
        return False, str(exc)


def _canonical_mainfile_role(
    work_dir: Path,
    current_mainfile: Path | None = None,
) -> tuple[str, str, str]:
    """Domain登録済みの正規mainfileだけを作品保存対象として分類する."""

    current = (
        blend_io.current_mainfile_path()
        if current_mainfile is None
        else Path(current_mainfile)
    )
    if current is None:
        return "unknown", "", ""
    from ..utils import page_file_scene

    return page_file_scene.canonical_role_from_path(current, work_dir)


def _apply_phase1_defaults(work) -> None:
    """新規作品のワンショット既定値セット.

    原稿上の表示の初期値:
      - 作品名 ON / 左下 (bottom-left)
      - 話数 OFF / 上中央 (top-center)
      - サブタイトル OFF / 右上 (top-right)
      - 作者名 ON / 右下 (bottom-right) — 値は OS のユーザー名で初期化
      - ページ番号 ON / 下中央 (bottom-center)
    """
    info = work.work_info
    info.display_work_name.enabled = True
    info.display_work_name.position = "bottom-left"
    info.display_episode.enabled = False
    info.display_episode.position = "top-center"
    info.display_subtitle.enabled = False
    info.display_subtitle.position = "top-right"
    info.display_author.enabled = True
    info.display_author.position = "bottom-right"
    info.display_page_number.enabled = True
    info.display_page_number.position = "bottom-center"
    # 前作品の値が残っている場合に備え、ページ番号レンジは 1, 1 に強制リセット。
    # update callback を抑止することで ``ensure_pages_for_number_range`` が
    # 中間状態 (start=1, end=旧値) で発火するのを防ぐ。
    with suppress_page_number_range_update():
        info.page_number_start = 1
        if hasattr(info, "page_number_end"):
            info.page_number_end = 1
    if hasattr(work, "coma_blend_template_path"):
        work.coma_blend_template_path = ""
    # 作者名が未入力なら OS のユーザー名で初期化 (上書きはしない)
    if not info.author:
        try:
            import getpass
            info.author = getpass.getuser()
        except Exception:  # noqa: BLE001
            pass
    # 既定プリセット適用 (見つからなくても既定値は PropertyGroup に入っている)
    presets.load_default_preset_for_work(work)
    # セーフライン外塗りは新規作品ごとに既定値へ戻す。
    # PropertyGroup は同一 scene 内で前回値を保持するため、ここで明示的に初期化しないと
    # 「前の作品で変えた不透明度」が新規作品へ漏れる。プリセット適用後に置き直して、
    # 今後プリセット側が拡張されても新規作品の既定を固定する。
    work.safe_area_overlay.enabled = True
    work.safe_area_overlay.opacity = 30.0
    work.safe_area_overlay.color = (0.0, 0.0, 0.0)
    work.safe_area_overlay.bleed_outer_enabled = True
    work.safe_area_overlay.bleed_outer_opacity = 100.0
    work.safe_area_overlay.bleed_outer_color = color_space.srgb_to_linear_rgb(
        (0x40 / 255.0, 0x40 / 255.0, 0x40 / 255.0)
    )


def _cleanup_default_scene_objects() -> None:
    """Blender のデフォルトシーンに含まれる Cube / Light / Camera を削除.

    B-MANGA の新規作品ではネームキャンバスを真正面から見るため、3D の既定
    ライトやカメラは不要。ユーザーが作ったオブジェクトと名前衝突しないよう、
    Blender 既定の "Cube" / "Light" / "Camera" という正確な名前のみを対象とする。
    """
    default_names = ("Cube", "Light", "Camera")
    for name in default_names:
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            _logger.warning("failed to remove default object: %s", name)
    # 孤児化したデータブロック (Mesh/Light/Camera 本体) も掃除
    for mesh in tuple(bpy.data.meshes):
        if mesh.name == "Cube" and mesh.users == 0:
            try:
                bpy.data.meshes.remove(mesh)
            except Exception:  # noqa: BLE001
                pass
    for light_data in tuple(bpy.data.lights):
        if light_data.name == "Light" and light_data.users == 0:
            try:
                bpy.data.lights.remove(light_data)
            except Exception:  # noqa: BLE001
                pass
    for cam_data in tuple(bpy.data.cameras):
        if cam_data.name == "Camera" and cam_data.users == 0:
            try:
                bpy.data.cameras.remove(cam_data)
            except Exception:  # noqa: BLE001
                pass


def _disable_work_viewport_overlays(context, *, schedule: bool = False) -> None:
    """ページ一覧ファイル用に Blender 標準オーバーレイをオフへ揃える."""
    try:
        from ..ui import overlay as _overlay

        _overlay.set_viewport_overlays_enabled(context, enabled=False)
        if schedule:
            _overlay.schedule_viewport_overlays_enabled(enabled=False)
    except Exception:  # noqa: BLE001
        _logger.exception("work viewport overlay setup failed")


def _schedule_layer_stack_sync(context, *, schedule: bool = True) -> None:
    try:
        from ..utils import layer_stack as _layer_stack

        _layer_stack.sync_layer_stack(context)
        if schedule:
            _layer_stack.schedule_layer_stack_sync()
    except Exception:  # noqa: BLE001
        _logger.exception("work layer stack sync failed")


def _initialize_new_work_domain(
    context,
    work_dir: Path,
    marker: Path,
    token: str,
):
    from ..utils import new_work_transaction

    on_committed = new_work_transaction.committed_artifact_recorder(
        marker,
        token,
    )
    work = get_work(context)
    if work is None:
        raise RuntimeError("シーンにB-MANGAデータが見つかりません")
    new_work_transaction.create_directories(
        marker,
        token,
        new_work_transaction.skeleton_directories(work_dir),
    )
    work_io.create_bmanga_skeleton(work_dir)
    _apply_phase1_defaults(work)
    work.work_dir = str(work_dir.resolve(strict=True))
    work.loaded = True
    work.work_info.work_name = work_dir.stem
    view_settings.apply_preferences_to_work_defaults(work, context)
    view_settings.apply_work_to_scene(context.scene, work)
    work_io.save_work_json(
        work_dir,
        work,
        on_committed=on_committed,
    )
    page_io.save_pages_json(
        work_dir,
        work,
        on_committed=on_committed,
    )
    entry = page_io.register_new_page(work)
    page_dir = paths.page_dir(work_dir, str(entry.id))
    new_work_transaction.create_directories(
        marker,
        token,
        (
            page_dir,
            paths.page_assets_dir(work_dir, str(entry.id)),
            paths.page_comas_dir(work_dir, str(entry.id)),
        ),
    )
    page_io.ensure_page_dir(work_dir, entry)
    from .coma_op import create_basic_frame_coma

    create_basic_frame_coma(
        work,
        entry,
        work_dir,
        on_committed=on_committed,
    )
    page_io.save_pages_json(
        work_dir,
        work,
        on_committed=on_committed,
    )
    page_range.sync_end_number_to_page_count(work)
    return work


def _prepare_new_work_scene(
    context,
    work,
    work_dir: Path,
    marker: Path,
    token: str,
) -> None:
    """空のhomefile上だけで新作品のSceneとwork.blendを確定する。"""

    _cleanup_default_scene_objects()
    page_grid.apply_page_collection_transforms(context, work)
    set_mode(MODE_PAGE, context)
    scene = context.scene
    scene.bmanga_current_page_id = ""
    scene.bmanga_current_coma_id = ""
    scene.bmanga_current_coma_page_id = ""
    scene.bmanga_overview_mode = True
    if hasattr(scene, "bmanga_active_layer_kind"):
        scene.bmanga_active_layer_kind = "page"
    from ..utils import display_settings, geometry_nodes_bridge
    from . import preset_op, raster_layer_op

    display_settings.apply_standard_color_management(scene)
    preset_op.sync_paper_preset_selector(context)
    preset_op.sync_border_preset_selector(context)
    _schedule_layer_stack_sync(context, schedule=False)
    raster_layer_op.ensure_all_raster_runtime(context)
    _disable_work_viewport_overlays(context)
    geometry_nodes_bridge.ensure_effect_line_node_group_for_work(context)
    from ..utils import page_file_scene, page_preview_object

    page_file_scene.purge_work_list_runtime_data(scene)
    page_preview_object.sync_page_previews(context, work)
    page_file_scene.purge_work_list_runtime_data(scene)
    page_file_scene.clear_work_list_page_details(scene)
    from ..utils import handlers
    from ..utils import new_work_transaction

    with handlers.suppress_work_metadata_save():
        if not blend_io.save_work_blend(
            work_dir,
            on_committed=new_work_transaction.committed_artifact_recorder(
                marker,
                token,
            ),
        ):
            raise RuntimeError("作品一覧ファイルを保存できませんでした")


def _open_new_work_target(
    work_dir: Path,
    marker: Path,
    token: str,
) -> bool:
    if not blend_io.read_homefile():
        return False
    context = bpy.context
    work = _initialize_new_work_domain(
        context,
        work_dir,
        marker,
        token,
    )
    _prepare_new_work_scene(
        context,
        work,
        work_dir,
        marker,
        token,
    )
    return True


def _unmanaged_session_is_dirty(context) -> bool:
    work = get_work(context)
    unmanaged = (
        work is None
        or not bool(getattr(work, "loaded", False))
        or not str(getattr(work, "work_dir", "") or "")
    )
    return unmanaged and bool(getattr(bpy.data, "is_dirty", False))


def _require_clean_unmanaged_session(
    context,
    *,
    next_step: str,
) -> None:
    if _unmanaged_session_is_dirty(context):
        raise RuntimeError(
            "現在のBlenderファイルに未保存の変更があります。"
            f"先に保存してから{next_step}"
        )


def _checkpoint_before_new_work(context) -> bool:
    from ..utils import lifecycle_checkpoint, lifecycle_coordinator

    work = get_work(context)
    if (
        work is None
        or not bool(getattr(work, "loaded", False))
        or not str(getattr(work, "work_dir", "") or "")
    ):
        _require_clean_unmanaged_session(
            context,
            next_step="新規作品を作成してください",
        )
        return True
    source = lifecycle_coordinator.current_target(context)
    if source.role not in {"work", "page", "coma"} or not source.filepath:
        raise RuntimeError(
            "現在の作品が正規ファイルではないため、安全に保存できません"
        )
    return lifecycle_checkpoint.checkpoint_succeeded(
        context,
        reason="create another work",
        force_native=True,
    )


def _finish_new_work_view(context) -> None:
    from ..ui import overlay as _overlay

    _overlay.reset_viewport_background_to_theme(context)
    _overlay.apply_bmanga_shading_mode(context)
    _disable_work_viewport_overlays(context, schedule=True)
    if getattr(context, "window", None) is not None:
        bpy.ops.bmanga.view_fit_all("INVOKE_DEFAULT")
    from . import object_tool_op

    object_tool_op.schedule_object_tool_relaunch_after_file_open()


class BMANGA_OT_work_new(Operator, ExportHelper):
    """新規作品を作成 (.bmanga ディレクトリを生成).

    既存の同名ディレクトリがあれば作成を中止する (安全のため上書き禁止)。
    """

    bl_idname = "bmanga.work_new"
    bl_label = "新規作品を作成"
    bl_options = {"REGISTER"}

    filename_ext = paths.BMANGA_DIR_SUFFIX
    filter_glob: StringProperty(default="*.bmanga", options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        try:
            _require_clean_unmanaged_session(
                context,
                next_step="新規作品を作成してください",
            )
        except RuntimeError as exc:
            self.report(
                {"ERROR"},
                str(exc),
            )
            return {"CANCELLED"}
        selected = Path(self.filepath)
        work_dir = paths.ensure_bmanga_suffix(selected)
        if work_dir.exists():
            self.report({"ERROR"}, f"既に存在します: {work_dir.name}")
            return {"CANCELLED"}
        token = uuid.uuid4().hex
        try:
            from ..utils import new_work_transaction

            marker = new_work_transaction.claim_directory(
                work_dir,
                token,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("work_new directory claim failed")
            self.report({"ERROR"}, f"保存先を作成できませんでした: {exc}")
            return {"CANCELLED"}

        from ..utils import lifecycle_coordinator

        target = lifecycle_coordinator.target_for_path(
            paths.work_blend_path(work_dir),
            work_root=work_dir,
            context=context,
        )
        outcome = lifecycle_coordinator.run_transition(
            context,
            target,
            checkpoint=lambda: _checkpoint_before_new_work(context),
            open_target=lambda: _open_new_work_target(
                work_dir,
                marker,
                token,
            ),
        )
        if not outcome.succeeded:
            try:
                from ..io import domain_runtime

                domain_runtime.forget_repository(work_dir)
                unknown = new_work_transaction.cleanup_failed_work(
                    work_dir,
                    marker,
                    token,
                )
                if unknown:
                    self.report(
                        {"WARNING"},
                        "保存先に別のファイルが追加されたため、"
                        "そのファイルとフォルダーを残しました",
                    )
            except Exception:  # noqa: BLE001
                _logger.exception("work_new partial directory cleanup failed")
            self.report({"ERROR"}, f"作成失敗: {outcome.error}")
            return {"CANCELLED"}
        try:
            new_work_transaction.release_marker(marker, token)
        except Exception:  # noqa: BLE001
            _logger.exception("work_new ownership marker cleanup failed")
        try:
            _finish_new_work_view(bpy.context)
        except Exception:  # noqa: BLE001
            _logger.exception("work_new final view setup failed")

        self.report({"INFO"}, f"作品を作成: {work_dir.name} (page p0001 を初期化)")
        return {"FINISHED"}


class BMANGA_OT_work_open(Operator, ImportHelper):
    """既存の .bmanga 作品フォルダを開く."""

    bl_idname = "bmanga.work_open"
    bl_label = "作品を開く"
    bl_options = {"REGISTER"}

    filename_ext = paths.BMANGA_DIR_SUFFIX

    def invoke(self, context, _event):
        # .bmangaフォルダー内のwork.blendを直接選べるよう、拡張子フィルターを
        # 指定せずファイルブラウザーを開く。現在のblend名も選択欄へ流用しない。
        self.filepath = ""
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        selected = Path(self.filepath)
        work_dir = selected if selected.suffix == paths.BMANGA_DIR_SUFFIX else selected.parent
        if not work_dir.is_dir() or work_dir.suffix != paths.BMANGA_DIR_SUFFIX:
            self.report({"ERROR"}, f".bmanga フォルダを指定してください: {work_dir}")
            return {"CANCELLED"}
        try:
            _require_clean_unmanaged_session(
                context,
                next_step="作品を開いてください",
            )
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        target_path = paths.work_blend_path(work_dir)

        from ..utils import lifecycle_checkpoint, lifecycle_coordinator

        def _prepare() -> bool:
            recovered, recovery_error = _recover_selected_work_before_open(
                work_dir
            )
            if not recovered:
                raise RuntimeError(
                    "中断された保存を復旧できませんでした: "
                    f"{recovery_error}"
                )
            return True

        def _checkpoint() -> bool:
            current_work = get_work(context)
            if (
                current_work is None
                or not bool(getattr(current_work, "loaded", False))
                or not str(getattr(current_work, "work_dir", "") or "")
            ):
                _require_clean_unmanaged_session(
                    context,
                    next_step="作品を開いてください",
                )
                return True
            return lifecycle_checkpoint.checkpoint_succeeded(
                context,
                reason="open another work",
                force_native=True,
            )

        def _open_target() -> bool:
            try:
                from . import raster_layer_op

                raster_layer_op.purge_all_raster_runtime(context.scene)
            except Exception:  # noqa: BLE001
                _logger.exception("work_open: old raster runtime purge failed")
            return bool(blend_io.open_work_blend(work_dir))

        target = lifecycle_coordinator.target_for_path(
            target_path,
            work_root=work_dir,
            context=context,
        )
        outcome = lifecycle_coordinator.run_transition(
            context,
            target,
            prepare=_prepare,
            checkpoint=_checkpoint,
            open_target=_open_target,
        )
        if not outcome.succeeded:
            if str(getattr(outcome.failed_phase, "value", "")) == "SAVING_SOURCE":
                message = (
                    "現在の作品を保存できないため、切り替えませんでした: "
                    f"{outcome.error}"
                )
            else:
                message = f"作品を開けませんでした: {outcome.error}"
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        try:
            from ..ui import overlay as _overlay

            _overlay.reset_viewport_background_to_theme(bpy.context)
            _overlay.apply_bmanga_shading_mode(bpy.context)
            _disable_work_viewport_overlays(bpy.context, schedule=True)
        except Exception:  # noqa: BLE001
            _logger.exception("work_open: shading/background setup failed")

        self.report({"INFO"}, f"作品を開きました: {work_dir.name}")
        return {"FINISHED"}


class BMANGA_OT_work_make_coma_file(Operator):
    """現在開いている .blend を、親作品を持たない単独のコマファイルにする.

    ページ一覧ファイルでもコマファイルでもない .blend を開いたときに、
    この .blend を「単独コマファイル」として扱えるようにする。作品
    (.bmanga) には属さないため、ページ一覧側のコマ一覧には現れない。
    """

    bl_idname = "bmanga.work_make_coma_file"
    bl_label = "コマファイル化"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        # 作品 (ページ一覧) が開かれているときは対象外。未認識の .blend
        # を開いた状態 (= 作品が開かれていない) でのみ実行できる。
        return not (work and work.loaded)

    def invoke(self, context, event):
        return detail_popup.invoke_confirm(
            context,
            event,
            self,
            title="コマファイル化",
            message=(
                "この .blend を単独のコマファイルにします。"
                "作品 (ページ一覧) には属しません。"
            ),
            confirm_text="コマファイル化",
        )

    def execute(self, context):
        scene = context.scene
        if scene is None:
            self.report({"ERROR"}, "シーンが見つかりません")
            return {"CANCELLED"}

        from ..core import mode as _mode

        inferred = _mode._infer_mode_from_filepath(scene)
        if inferred is not None:
            inferred_mode = inferred[0]
            if inferred_mode == MODE_PAGE:
                self.report(
                    {"ERROR"},
                    "ページ一覧ファイルはコマファイル化できません",
                )
                return {"CANCELLED"}
            if inferred_mode == MODE_COMA:
                self.report({"INFO"}, "既にコマファイルです")
                return {"CANCELLED"}

        try:
            from ..utils import coma_scene, coma_camera, display_settings

            coma_scene.prepare_coma_blend_scene(context, purge_orphans=False)

            set_mode(MODE_COMA, context)
            scene.bmanga_current_coma_id = ""
            scene.bmanga_current_coma_page_id = ""
            if hasattr(scene, "bmanga_overview_mode"):
                scene.bmanga_overview_mode = False
            if hasattr(scene, "bmanga_active_layer_kind"):
                scene.bmanga_active_layer_kind = "coma"

            display_settings.apply_standard_color_management(scene)
            coma_camera.ensure_coma_camera_scene(
                context,
                work=None,
                generate_references=False,
            )

            from ..ui import overlay as _overlay

            _overlay.reset_viewport_background_to_theme(context)
            _overlay.apply_bmanga_shading_mode(context)
            coma_camera.schedule_coma_view_camera()
            try:
                from ..ui import sidebar as _sidebar

                _sidebar.schedule_open_bmanga_sidebar()
            except Exception:  # noqa: BLE001
                _logger.exception("work_make_coma_file: sidebar open failed")
        except Exception as exc:  # noqa: BLE001
            _logger.exception("work_make_coma_file failed")
            self.report({"ERROR"}, f"コマファイル化に失敗しました: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, "単独のコマファイルにしました")
        return {"FINISHED"}


class BMANGA_OT_work_save(Operator):
    """現在の作品データを保存 (work.json / pages.json + 現在の mainfile .blend)."""

    bl_idname = "bmanga.work_save"
    bl_label = "作品を保存"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work and work.loaded and work.work_dir)

    def execute(self, context):
        work = get_work(context)
        work_dir = Path(work.work_dir)
        if not work_dir.is_dir():
            self.report({"ERROR"}, f"作品ディレクトリが見つかりません: {work_dir}")
            return {"CANCELLED"}
        file_role, file_page_id, file_coma_id = _canonical_mainfile_role(work_dir)
        if file_role == "unknown":
            self.report(
                {"ERROR"},
                (
                    "正規の作品ファイルではないコピーからは保存できません。"
                    "元の作品ファイルを開き直してください"
                ),
            )
            return {"CANCELLED"}
        try:
            mode = get_mode(context)
            if mode == MODE_COMA and bool(getattr(work, "auto_render_coma_thumb_on_return", True)):
                try:
                    from ..utils import coma_thumb_output

                    if not coma_thumb_output.render_thumb_png(context):
                        self.report({"WARNING"}, "コマ画像の更新に失敗しました")
                except Exception:  # noqa: BLE001
                    _logger.exception("work_save: coma thumb refresh failed")
                    self.report({"WARNING"}, "コマ画像の更新に失敗しました")
            if mode != MODE_COMA:
                _disable_work_viewport_overlays(context)
            try:
                from ..utils import page_file_scene, page_preview_object

                if file_role == "page" and paths.is_valid_page_id(file_page_id):
                    page_index = page_file_scene.find_page_index(work, file_page_id)
                    if 0 <= page_index < len(work.pages):
                        for variant in (
                            page_preview_object.PREVIEW_RENDER_VARIANT_DETAIL,
                            page_preview_object.PREVIEW_RENDER_VARIANT_WORK,
                        ):
                            page_preview_object.ensure_preview_png(
                                work,
                                work.pages[page_index],
                                page_index,
                                current=True,
                                scene=context.scene,
                                force=True,
                                variant=variant,
                            )
                elif file_role == "work":
                    page_file_scene.purge_work_list_runtime_data(context.scene)
                    page_preview_object.sync_page_previews(context, work, force=True)
                    page_file_scene.purge_work_list_runtime_data(context.scene)
            except Exception:  # noqa: BLE001
                _logger.exception("work_save: page preview refresh failed")

            from ..utils import lifecycle_checkpoint

            checkpoint = lifecycle_checkpoint.checkpoint_current(
                context,
                reason="explicit work save",
                force_native=True,
            )
            saved_blend = checkpoint.succeeded
            saved_path = checkpoint.filepath
            if not saved_blend:
                raise RuntimeError(checkpoint.error or "作品を保存できませんでした")
        except Exception as exc:  # noqa: BLE001
            _logger.exception("work_save failed")
            self.report({"ERROR"}, f"保存失敗: {exc}")
            return {"CANCELLED"}
        if saved_blend:
            self.report({"INFO"}, f"作品を保存: {Path(saved_path).name}")
        else:
            self.report({"ERROR"}, "作品を保存できませんでした")
            return {"CANCELLED"}
        return {"FINISHED"}


class BMANGA_OT_work_close(Operator):
    """作品をcheckpointして閉じ、新しい空のBlenderファイルへ戻る。"""

    bl_idname = "bmanga.work_close"
    bl_label = "作品を閉じる"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work and work.loaded)

    def execute(self, context):
        from ..bmanga_core.lifecycle import LifecycleTarget
        from ..utils import lifecycle_checkpoint, lifecycle_coordinator

        outcome = lifecycle_coordinator.run_transition(
            context,
            LifecycleTarget(role="home"),
            checkpoint=lambda: lifecycle_checkpoint.checkpoint_succeeded(
                context,
                reason="close work",
            ),
            open_target=lambda: bool(blend_io.read_homefile()),
        )
        if not outcome.succeeded:
            self.report({"ERROR"}, f"作品を閉じられませんでした: {outcome.error}")
            return {"CANCELLED"}
        self.report({"INFO"}, "作品を閉じました")
        return {"FINISHED"}


_CLASSES = (
    BMANGA_OT_work_new,
    BMANGA_OT_work_open,
    BMANGA_OT_work_make_coma_file,
    BMANGA_OT_work_save,
    BMANGA_OT_work_close,
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
