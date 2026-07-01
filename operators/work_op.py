"""作品 (.bmanga) の新規作成・オープン・保存・クローズ Operator."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper, ExportHelper

from ..core.mode import MODE_PAGE, MODE_COMA, get_mode, set_mode
from ..core.work import get_work
from ..core.work_info import suppress_page_number_range_update
from ..io import blend_io, page_io, presets, work_io
from ..utils import gpencil as gp_utils
from ..utils import color_space, log, page_grid, page_range, paths, view_settings

_logger = log.get_logger(__name__)


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
        work = get_work(context)
        if work is None:
            self.report({"ERROR"}, "シーンに B-MANGA データが見つかりません")
            return {"CANCELLED"}

        selected = Path(self.filepath)
        work_dir = paths.ensure_bmanga_suffix(selected)
        if work_dir.exists():
            self.report({"ERROR"}, f"既に存在します: {work_dir.name}")
            return {"CANCELLED"}

        # 既存の作品データをリセットしてから新規作成
        work.pages.clear()
        for attr in ("shared_balloons", "shared_texts", "shared_comas", "layer_folders"):
            coll = getattr(work, attr, None)
            if coll is not None:
                coll.clear()
        raster_layers = getattr(context.scene, "bmanga_raster_layers", None)
        if raster_layers is not None:
            try:
                from . import raster_layer_op

                raster_layer_op.purge_all_raster_runtime(context.scene)
            except Exception:  # noqa: BLE001
                _logger.exception("work_new: old raster runtime purge failed")
            raster_layers.clear()
        if hasattr(context.scene, "bmanga_active_raster_layer_index"):
            context.scene.bmanga_active_raster_layer_index = -1
        # 前作品の page_pNNNN Collection / GP を掃除 (orphan 防止)
        try:
            gp_utils.remove_all_page_gpencils()
        except Exception:  # noqa: BLE001
            _logger.exception("work_new: orphan page collection cleanup failed")
        work.active_page_index = -1
        work.loaded = False

        try:
            work_io.create_bmanga_skeleton(work_dir)
            _apply_phase1_defaults(work)
            work.work_dir = str(work_dir.resolve())
            work.loaded = True
            work.work_info.work_name = work_dir.stem
            view_settings.apply_preferences_to_work_defaults(work, context)
            view_settings.apply_work_to_scene(context.scene, work)
            work_io.save_work_json(work_dir, work)
            page_io.save_pages_json(work_dir, work)

            # 最初のページ p0001 を自動生成し、ページ一覧ファイルとして保存する。
            # 各ページのフキダシ・テキスト等は、ページ用blendファイルで扱う。
            entry = page_io.register_new_page(work)
            page_io.ensure_page_dir(work_dir, entry.id)
            from .coma_op import create_basic_frame_coma

            create_basic_frame_coma(work, entry, work_dir)
            page_io.save_pages_json(work_dir, work)
            # 初期ページが 1 個できた状態で end を実ページ数に揃える
            # (= start + len(work.pages) - 1)。バグ #2 対策: 仮に
            # `_apply_phase1_defaults` の reset が何らかの理由で適用
            # 失敗していた場合でも、ここで最終状態が 1, max(1, len(pages))
            # に確定する。
            page_range.sync_end_number_to_page_count(work)

            # デフォルトシーンの Cube/Light/Camera を削除してから保存
            # (ネームキャンバスに余計な 3D オブジェクトが載らないようにする)
            _cleanup_default_scene_objects()

            # ページ一覧ファイルでは各ページの実体を持たず、プレビュー画像だけを表示する。
            gp_initial_obj = None
            page_grid.apply_page_collection_transforms(context, work)

            # overview 編集モード既定。保存前にモード/stem を確実にセット。
            set_mode(MODE_PAGE, context)
            context.scene.bmanga_current_page_id = ""
            context.scene.bmanga_current_coma_id = ""
            context.scene.bmanga_current_coma_page_id = ""
            context.scene.bmanga_overview_mode = True
            if hasattr(context.scene, "bmanga_active_layer_kind"):
                context.scene.bmanga_active_layer_kind = "page"
            try:
                from ..utils import display_settings

                display_settings.apply_standard_color_management(context.scene)
            except Exception:  # noqa: BLE001
                _logger.exception("work_new: color management setup failed")
            try:
                from . import preset_op

                preset_op.sync_paper_preset_selector(context)
                preset_op.sync_border_preset_selector(context)
            except Exception:  # noqa: BLE001
                _logger.exception("work_new: preset selector sync failed")

            _schedule_layer_stack_sync(context, schedule=False)
            try:
                from . import raster_layer_op

                raster_layer_op.ensure_all_raster_runtime(context)
            except Exception:  # noqa: BLE001
                _logger.exception("work_new: raster runtime setup failed")
            _disable_work_viewport_overlays(context)
            try:
                from ..utils import geometry_nodes_bridge

                geometry_nodes_bridge.ensure_effect_line_node_group_for_work(context)
            except Exception:  # noqa: BLE001
                _logger.exception("work_new: effect line display preparation failed")
            try:
                from ..utils import page_file_scene, page_preview_object

                page_file_scene.purge_work_list_runtime_data(context.scene)
                page_preview_object.sync_page_previews(context, work)
                page_file_scene.purge_work_list_runtime_data(context.scene)
            except Exception:  # noqa: BLE001
                _logger.exception("work_new: page preview setup failed")
            blend_io.save_work_blend(work_dir)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("work_new failed")
            work.loaded = False
            self.report({"ERROR"}, f"作成失敗: {exc}")
            return {"CANCELLED"}

        # --- 作成直後の UX 整備 ---
        # 0) 旧バージョンで白く書き換えられた可能性のあるビューポート背景を
        #    テーマ既定 (灰色) に戻す + Solid+Flat 照明に切替 (B-MANGA の標準表示)
        try:
            from ..ui import overlay as _overlay

            _overlay.reset_viewport_background_to_theme(context)
            _overlay.apply_bmanga_shading_mode(context)
            _disable_work_viewport_overlays(context, schedule=True)
        except Exception:  # noqa: BLE001
            _logger.exception("work_new: shading/background setup failed")

        # 1) ビューポートを全ページフィット (overview モードを維持したままキャンバス可視化)
        try:
            bpy.ops.bmanga.view_fit_all("INVOKE_DEFAULT")
        except Exception:  # noqa: BLE001
            _logger.exception("work_new: view_fit_all failed")

        # 2) 初期ページ GP を view_layer の active に設定し、ユーザーがモード切替
        #    (Draw / Edit) すればすぐ描画に入れる状態にする。モード遷移自体は
        #    ユーザーの意図を尊重して自動化しない (Phase 2 設計方針)。
        try:
            view_layer = context.view_layer
            if view_layer is not None and gp_initial_obj is not None:
                for o in list(context.selected_objects):
                    if o is not gp_initial_obj:
                        try:
                            o.select_set(False)
                        except Exception:  # noqa: BLE001
                            pass
                view_layer.objects.active = gp_initial_obj
                try:
                    gp_initial_obj.select_set(True)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            _logger.exception("work_new: set active GP failed")

        self.report({"INFO"}, f"作品を作成: {work_dir.name} (page p0001 を初期化)")
        return {"FINISHED"}


class BMANGA_OT_work_open(Operator, ImportHelper):
    """既存の .bmanga 作品フォルダを開く."""

    bl_idname = "bmanga.work_open"
    bl_label = "作品を開く"
    bl_options = {"REGISTER"}

    filename_ext = paths.BMANGA_DIR_SUFFIX
    filter_glob: StringProperty(default="*.bmanga", options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        work = get_work(context)
        if work is None:
            self.report({"ERROR"}, "シーンに B-MANGA データが見つかりません")
            return {"CANCELLED"}

        old_work_dir = Path(work.work_dir) if work.loaded and work.work_dir else None
        if old_work_dir is not None and old_work_dir.is_dir():
            try:
                from ..utils import handlers, page_file_scene

                handlers.save_scene_work_to_disk(context, reason="work_open")
                role, page_id, coma_id = page_file_scene.current_role(context)
                if role == page_file_scene.ROLE_WORK:
                    blend_io.save_work_blend(old_work_dir)
                elif role == page_file_scene.ROLE_PAGE and paths.is_valid_page_id(page_id):
                    blend_io.save_page_blend(old_work_dir, page_id)
                elif role == page_file_scene.ROLE_COMA and paths.is_valid_page_id(page_id) and paths.is_valid_coma_id(coma_id):
                    blend_io.save_coma_blend(old_work_dir, page_id, coma_id)
            except Exception:  # noqa: BLE001
                _logger.exception("work_open: save current file failed")

        selected = Path(self.filepath)
        # ファイルを選ばれても親ディレクトリを作品ルートとして解釈
        work_dir = selected if selected.suffix == paths.BMANGA_DIR_SUFFIX else selected.parent
        if not work_dir.is_dir() or work_dir.suffix != paths.BMANGA_DIR_SUFFIX:
            self.report({"ERROR"}, f".bmanga フォルダを指定してください: {work_dir}")
            return {"CANCELLED"}

        try:
            try:
                from . import raster_layer_op

                raster_layer_op.purge_all_raster_runtime(context.scene)
            except Exception:  # noqa: BLE001
                _logger.exception("work_open: old raster runtime purge failed")
            work_io.load_work_json(work_dir, work)
            page_io.load_pages_json(work_dir, work)
            work.work_dir = str(work_dir.resolve())
            work.loaded = True
            set_mode(MODE_PAGE, context)
            context.scene.bmanga_current_page_id = ""
            context.scene.bmanga_current_coma_id = ""
            context.scene.bmanga_current_coma_page_id = ""
            context.scene.bmanga_overview_mode = True
            if hasattr(context.scene, "bmanga_active_layer_kind"):
                context.scene.bmanga_active_layer_kind = "page"
            try:
                from ..utils import display_settings

                display_settings.apply_standard_color_management(context.scene)
            except Exception:  # noqa: BLE001
                _logger.exception("work_open: color management setup failed")
            try:
                from . import preset_op

                preset_op.sync_paper_preset_selector(context)
                preset_op.sync_border_preset_selector(context)
            except Exception:  # noqa: BLE001
                _logger.exception("work_open: preset selector sync failed")
            try:
                from . import raster_layer_op

                raster_layer_op.ensure_all_raster_runtime(context)
            except Exception:  # noqa: BLE001
                _logger.exception("work_open: raster runtime setup failed")
            _schedule_layer_stack_sync(context)
        except FileNotFoundError as exc:
            _logger.exception("work_open: missing file")
            work.loaded = False
            self.report({"ERROR"}, f"ファイルが見つかりません: {exc}")
            return {"CANCELLED"}
        except Exception as exc:  # noqa: BLE001
            _logger.exception("work_open failed")
            work.loaded = False
            self.report({"ERROR"}, f"読み込み失敗: {exc}")
            return {"CANCELLED"}

        # work.blend を自動オープン (なければ JSON のみ読み込んだ状態)
        if blend_io.work_blend_exists(work_dir):
            blend_io.open_work_blend(work_dir)
            # load_post ハンドラが JSON 再同期と mode/stem の再設定を担う

        # 背景をテーマ既定に戻す + Solid+Flat 照明に切替
        try:
            from ..ui import overlay as _overlay

            _overlay.reset_viewport_background_to_theme(context)
            _overlay.apply_bmanga_shading_mode(context)
            _disable_work_viewport_overlays(context, schedule=True)
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
        return context.window_manager.invoke_confirm(
            self,
            event,
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
        try:
            # 1) JSON メタ保存
            from ..utils import handlers as _handlers

            if not _handlers.save_scene_work_to_disk(context, reason="work_save"):
                self.report({"ERROR"}, "作品メタデータの保存に失敗しました")
                return {"CANCELLED"}
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
                from ..utils import page_file_scene

                file_role, file_page_id, _file_coma_id = page_file_scene.current_role(context)
            except Exception:  # noqa: BLE001
                file_role, file_page_id = "", ""
            try:
                from ..utils import page_file_scene, page_preview_object

                if file_role == "page" and paths.is_valid_page_id(file_page_id):
                    page_index = page_file_scene.find_page_index(work, file_page_id)
                    if 0 <= page_index < len(work.pages):
                        page_preview_object.ensure_preview_png(
                            work,
                            work.pages[page_index],
                            page_index,
                            current=True,
                            scene=context.scene,
                            force=True,
                        )
                elif file_role == "work":
                    page_file_scene.purge_work_list_runtime_data(context.scene)
                    page_preview_object.sync_page_previews(context, work, force=True)
                    page_file_scene.purge_work_list_runtime_data(context.scene)
            except Exception:  # noqa: BLE001
                _logger.exception("work_save: page preview refresh failed")

            # 2) .blend 保存. ユーザーが File > Save As で work_dir 外に保存
            #    していた場合は、そのパスを尊重して save_mainfile する (B-MANGA の
            #    期待パスへ強制リロケートしない)。work_dir 内 or 未保存なら
            #    overview モードなら work.blend、コマ編集モードなら cNN.blend
            #    を期待パスとして save_as_mainfile する。
            cur = blend_io.current_mainfile_path()
            work_dir_resolved = work_dir.resolve()
            in_work_dir = False
            if cur is not None:
                try:
                    cur.relative_to(work_dir_resolved)
                    in_work_dir = True
                except ValueError:
                    in_work_dir = False

            saved_blend = False
            saved_path = ""
            if cur is not None and not in_work_dir:
                # work_dir 外 → ユーザーの Save As パスをそのまま尊重
                try:
                    bpy.ops.wm.save_mainfile(compress=True)
                    saved_blend = True
                    saved_path = str(cur)
                except Exception as exc:  # noqa: BLE001
                    _logger.exception("save_mainfile (external path) failed")
                    saved_blend = False
            else:
                # work_dir 内 or 未保存 → B-MANGA 期待パスへ save_as
                if mode == MODE_COMA:
                    stem = getattr(context.scene, "bmanga_current_coma_id", "")
                    page_id = getattr(context.scene, "bmanga_current_coma_page_id", "")
                    if paths.is_valid_coma_id(stem) and paths.is_valid_page_id(page_id):
                        saved_blend = blend_io.save_coma_blend(
                            work_dir, page_id, stem
                        )
                        if saved_blend:
                            saved_path = str(
                                paths.coma_blend_path(work_dir, page_id, stem)
                            )
                elif file_role == "page" and paths.is_valid_page_id(file_page_id):
                    saved_blend = blend_io.save_page_blend(work_dir, file_page_id)
                    if saved_blend:
                        saved_path = str(paths.page_blend_path(work_dir, file_page_id))
                else:
                    saved_blend = blend_io.save_work_blend(work_dir)
                    if saved_blend:
                        saved_path = str(paths.work_blend_path(work_dir))
        except Exception as exc:  # noqa: BLE001
            _logger.exception("work_save failed")
            self.report({"ERROR"}, f"保存失敗: {exc}")
            return {"CANCELLED"}
        if saved_blend:
            self.report({"INFO"}, f"作品を保存: {Path(saved_path).name}")
        else:
            self.report({"WARNING"}, "JSON は保存、.blend 保存はスキップ")
        return {"FINISHED"}


class BMANGA_OT_work_close(Operator):
    """作品を閉じる (データをメモリから解放、ディスクは触らない)."""

    bl_idname = "bmanga.work_close"
    bl_label = "作品を閉じる"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work and work.loaded)

    def execute(self, context):
        work = get_work(context)
        try:
            from . import raster_layer_op

            raster_layer_op.purge_all_raster_runtime(context.scene)
        except Exception:  # noqa: BLE001
            _logger.exception("work_close: raster runtime purge failed")
        raster_layers = getattr(context.scene, "bmanga_raster_layers", None)
        if raster_layers is not None:
            raster_layers.clear()
        if hasattr(context.scene, "bmanga_active_raster_layer_index"):
            context.scene.bmanga_active_raster_layer_index = -1
        try:
            gp_utils.remove_all_page_gpencils()
        except Exception:  # noqa: BLE001
            _logger.exception("work_close: page collection cleanup failed")
        work.pages.clear()
        for attr in ("shared_balloons", "shared_texts", "shared_comas", "layer_folders"):
            coll = getattr(work, attr, None)
            if coll is not None:
                coll.clear()
        work.active_page_index = -1
        work.loaded = False
        work.work_dir = ""
        set_mode(MODE_PAGE, context)
        context.scene.bmanga_current_page_id = ""
        context.scene.bmanga_current_coma_id = ""
        context.scene.bmanga_current_coma_page_id = ""
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
