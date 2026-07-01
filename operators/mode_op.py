"""overview 編集モード / コマ編集モードの切替 Operator.

モード切替時の .blend 入出力:
- **enter_coma_mode**: 現在の work.blend を save → cNN.blend を open
  (cNN.blend が未作成なら、空 scene から新規生成)
- **exit_coma_mode**: 現在の cNN.blend を save → work.blend を open
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator

from ..core.mode import MODE_PAGE, MODE_COMA, get_mode, set_mode
from ..core.work import get_active_page, get_work
from ..io import blend_io, page_io, work_io
from ..utils import geom, log, paths
from . import coma_modal_state

_logger = log.get_logger(__name__)
_PENDING_ENTER_COMA: tuple[int, int, bool] | None = None


def _suspend_keymap_visibility_updates(seconds: float = 4.0) -> None:
    try:
        from ..keymap import keymap as _keymap

        _keymap.suspend_visibility_updates(seconds, reason="blend switch")
    except Exception:  # noqa: BLE001
        pass


def _save_current_work_metadata(work, page) -> None:
    """mainfile 切替前に JSON 側へ現在の用紙/ページ状態を反映する."""
    if work is None or not getattr(work, "work_dir", ""):
        return
    work_dir = Path(work.work_dir)
    try:
        from ..utils import view_settings

        view_settings.copy_scene_to_work(bpy.context.scene, work)
    except Exception:  # noqa: BLE001
        pass
    work_io.save_work_json(work_dir, work)
    page_io.save_pages_json(work_dir, work)
    if page is not None:
        page_io.save_page_json(work_dir, page)


def _find_page_by_id(work, page_id: str):
    for pg in getattr(work, "pages", []) or []:
        if str(getattr(pg, "id", "") or "") == page_id:
            return pg
    return None


def _auto_render_thumb_before_return(context, work) -> None:
    if work is None or not bool(getattr(work, "auto_render_coma_thumb_on_return", True)):
        return
    try:
        from ..utils import coma_thumb_output

        coma_thumb_output.render_thumb_png(context, skip_if_recent_seconds=2.0)
    except Exception:  # noqa: BLE001
        _logger.exception("exit_coma_mode: thumb auto render failed")


def _resolve_coma_at_event(context, event) -> tuple[int, int] | None:
    """``event.mouse_x/y`` の位置から (page_index, coma_index) を逆引き.

    VIEW_3D エリアに乗っていない場合は None。overview モードなら全ページを
    走査、OFF なら active ページのみ。Z 順最大 (最前面) のヒットを返す。
    """
    work = get_work(context)
    if work is None or not work.loaded:
        return None
    from bpy_extras.view3d_utils import region_2d_to_location_3d

    # coma_picker ヘルパを遅延 import (operators→utils の循環依存回避)
    from . import coma_picker

    screen = getattr(context, "screen", None)
    if screen is None:
        return None
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type != "WINDOW":
                continue
            if not (
                region.x <= event.mouse_x < region.x + region.width
                and region.y <= event.mouse_y < region.y + region.height
            ):
                continue
            space = area.spaces.active
            rv3d = getattr(space, "region_3d", None)
            if rv3d is None:
                continue
            mx = event.mouse_x - region.x
            my = event.mouse_y - region.y
            loc = region_2d_to_location_3d(region, rv3d, (mx, my), (0.0, 0.0, 0.0))
            if loc is None:
                continue
            x_mm = geom.m_to_mm(loc.x)
            y_mm = geom.m_to_mm(loc.y)
            return coma_picker.find_coma_at_world_mm(work, x_mm, y_mm)
    return None


def _resolve_page_preview_at_event(context, event) -> int | None:
    """Return a page index when the event hits a page edit preview image."""
    work = get_work(context)
    if work is None or not work.loaded:
        return None
    from bpy_extras.view3d_utils import region_2d_to_location_3d

    from ..utils import page_preview_object

    screen = getattr(context, "screen", None)
    if screen is None:
        return None
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type != "WINDOW":
                continue
            if not (
                region.x <= event.mouse_x < region.x + region.width
                and region.y <= event.mouse_y < region.y + region.height
            ):
                continue
            space = area.spaces.active
            rv3d = getattr(space, "region_3d", None)
            if rv3d is None:
                continue
            mx = event.mouse_x - region.x
            my = event.mouse_y - region.y
            loc = region_2d_to_location_3d(region, rv3d, (mx, my), (0.0, 0.0, 0.0))
            if loc is None:
                continue
            return page_preview_object.page_index_at_world_mm(
                context.scene,
                work,
                geom.m_to_mm(loc.x),
                geom.m_to_mm(loc.y),
            )
    return None


def _resolve_page_at_event(context, event) -> int | None:
    """Return a page index when the event hits a page in the overview."""
    work = get_work(context)
    if work is None or not work.loaded:
        return None
    from . import coma_picker

    return coma_picker.find_page_at_event(context, event)


def page_file_index_from_viewport_event(context, event) -> int | None:
    """Return the page file index that should open for a viewport double click."""
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return None
    from ..utils import page_file_scene

    role, current_page_id, _coma_id = page_file_scene.current_role(context)
    if role == page_file_scene.ROLE_PAGE:
        page_hit = _resolve_page_preview_at_event(context, event)
        if page_hit is None or not (0 <= page_hit < len(work.pages)):
            return None
        hit_page_id = str(getattr(work.pages[page_hit], "id", "") or "")
        if hit_page_id and hit_page_id != current_page_id:
            return int(page_hit)
        return None
    if (
        role == page_file_scene.ROLE_WORK
        and bool(getattr(context.scene, "bmanga_overview_mode", False))
    ):
        page_hit = _resolve_page_at_event(context, event)
        if page_hit is not None and 0 <= page_hit < len(work.pages):
            return int(page_hit)
    return None


_PENDING_OPEN_PAGE: int | None = None


def schedule_open_page_file(page_index: int) -> bool:
    """Open a page file after the current viewport event has finished."""
    global _PENDING_OPEN_PAGE
    try:
        index = int(page_index)
    except Exception:
        return False
    if index < 0:
        return False
    _PENDING_OPEN_PAGE = index
    _suspend_keymap_visibility_updates()
    if not bpy.app.timers.is_registered(_run_deferred_open_page_file):
        bpy.app.timers.register(_run_deferred_open_page_file, first_interval=0.12)
    return True


def _run_deferred_open_page_file():
    global _PENDING_OPEN_PAGE
    page_index = _PENDING_OPEN_PAGE
    _PENDING_OPEN_PAGE = None
    if page_index is None:
        return None
    try:
        context = bpy.context
        work = context.scene.bmanga_work
        if not getattr(work, "loaded", False):
            return None
        if get_mode(context) != MODE_PAGE:
            return None
        if int(page_index) < 0 or int(page_index) >= len(work.pages):
            return None
        work.active_page_index = int(page_index)
        bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=int(page_index))
    except Exception:
        _logger.exception("deferred page file open failed")
    return None


def schedule_enter_coma_mode(
    page_index: int,
    coma_index: int,
    *,
    prompt_template_if_missing: bool = False,
) -> bool:
    """ダブルクリックイベント終了後にコマ編集モードへ入る."""
    global _PENDING_ENTER_COMA
    try:
        page_index = int(page_index)
        coma_index = int(coma_index)
    except (TypeError, ValueError):
        return False
    if page_index < 0 or coma_index < 0:
        return False
    _PENDING_ENTER_COMA = (
        page_index,
        coma_index,
        bool(prompt_template_if_missing),
    )
    _suspend_keymap_visibility_updates()
    if not bpy.app.timers.is_registered(_run_deferred_enter_coma_mode):
        bpy.app.timers.register(_run_deferred_enter_coma_mode, first_interval=0.15)
    return True


def _run_deferred_enter_coma_mode() -> None:
    global _PENDING_ENTER_COMA
    pending = _PENDING_ENTER_COMA
    _PENDING_ENTER_COMA = None
    if pending is None:
        return None
    page_index, coma_index, prompt_template_if_missing = pending
    context = bpy.context
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return None
    if get_mode(context) != MODE_PAGE:
        return None
    if not (0 <= page_index < len(work.pages)):
        return None
    page = work.pages[page_index]
    if not (0 <= coma_index < len(page.comas)):
        return None
    work.active_page_index = page_index
    page.active_coma_index = coma_index
    try:
        bpy.ops.bmanga.enter_coma_mode(
            "EXEC_DEFAULT",
            prompt_template_if_missing=prompt_template_if_missing,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("deferred enter_coma_mode failed")
    return None


def _clear_deferred_enter_coma_mode() -> None:
    global _PENDING_ENTER_COMA, _PENDING_OPEN_PAGE
    _PENDING_ENTER_COMA = None
    _PENDING_OPEN_PAGE = None
    try:
        if bpy.app.timers.is_registered(_run_deferred_open_page_file):
            bpy.app.timers.unregister(_run_deferred_open_page_file)
    except Exception:
        pass
    if bpy.app.timers.is_registered(_run_deferred_enter_coma_mode):
        try:
            bpy.app.timers.unregister(_run_deferred_enter_coma_mode)
        except ValueError:
            pass


class BMANGA_OT_enter_coma_mode(Operator):
    """選択中 or マウス直下のコマの 3D シーンに入る (コマ編集モード).

    work.blend を保存し、cNN.blend を開く。未作成なら空の scene から
    cNN.blend を初期化する。

    invoke(event) ではマウス直下のコマを優先的に逆引きして active を更新。
    execute のみの場合は現在の active をそのまま使う。
    """

    bl_idname = "bmanga.enter_coma_mode"
    bl_label = "コマ編集モードへ"

    filepath: StringProperty(  # type: ignore[valid-type]
        name="コマ用blendファイル",
        subtype="FILE_PATH",
        default="",
    )
    filter_glob: StringProperty(  # type: ignore[valid-type]
        default="*.blend",
        options={"HIDDEN"},
    )
    # 既定で False: コマ用blendファイル未作成でもテンプレート選択窓を
    # 出さず、共通テンプレート/空のコマから確実に開く。ファイル選択窓は
    # ウィンドウ/モーダル無しの呼び出し文脈では機能せず「コマが開かない」
    # 原因になっていた (ダブルクリック/各ボタン共通)。
    prompt_template_if_missing: BoolProperty(  # type: ignore[valid-type]
        default=False,
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return (
            work is not None
            and work.loaded
            and bool(work.work_dir)
            and get_mode(context) == MODE_PAGE
        )

    def invoke(self, context, event):
        # Blender が Object モード以外 (例: GP 描画モード PAINT_GREASE_PENCIL,
        # Edit モード等) のときはダブルクリックを譲る (描画ストロークなどに干渉しない)。
        cur_mode = getattr(context, "mode", "")
        if cur_mode != "OBJECT":
            print(f"[B-MANGA][OP] enter_coma_mode: skip (context.mode={cur_mode!r})")
            return {"PASS_THROUGH"}
        print(f"[B-MANGA][OP] enter_coma_mode.invoke event.type={event.type} value={event.value}"
              f" poll_ok={self.__class__.poll(context)}")
        # ダブルクリックからの起動: マウス直下のコマへ active をフォーカス
        hit = _resolve_coma_at_event(context, event)
        if hit is None:
            # ダブルクリック時のみ、未ヒットなら現在の active panel に
            # フォールバックせず何もしない。UI ボタンからの EXEC_DEFAULT は
            # 従来どおり active panel を対象に execute へ入る。
            return {"PASS_THROUGH"}

        work = get_work(context)
        page_idx, coma_idx = hit
        if work is None or not (0 <= page_idx < len(work.pages)):
            return {"PASS_THROUGH"}
        page = work.pages[page_idx]
        if not (0 <= coma_idx < len(page.comas)):
            return {"PASS_THROUGH"}

        work.active_page_index = page_idx
        page.active_coma_index = coma_idx
        # ダブルクリックは確実にコマを開く。未作成コマでテンプレート選択
        # ダイアログ (fileselect_add) を出すと「ダブルクリックしたのに
        # コマが開かずファイル選択窓が出る」状態になるため、プロンプトを
        # 抑止し既存 cNN.blend / 解決済みテンプレート / 空シーンから開く
        # (オブジェクトツール経路と同じ挙動に揃える)。
        self.prompt_template_if_missing = False
        if schedule_enter_coma_mode(
            page_idx,
            coma_idx,
            prompt_template_if_missing=False,
        ):
            return {"FINISHED"}
        return {"CANCELLED"}

    def execute(self, context):
        coma_modal_state.finish_all(context)
        work = get_work(context)
        page = get_active_page(context)
        if (
            work is None
            or page is None
            or not (0 <= page.active_coma_index < len(page.comas))
        ):
            self.report({"WARNING"}, "編集対象のコマが選択されていません")
            return {"CANCELLED"}

        try:
            from ..utils import data_name_organizer

            organize_result = data_name_organizer.organize_data_names(context)
            if organize_result.changed:
                self.report({"INFO"}, organize_result.summary)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("enter_coma_mode: data name organize failed")
            self.report({"ERROR"}, f"実データ名の整理に失敗しました: {exc}")
            return {"CANCELLED"}

        work = get_work(context)
        page = get_active_page(context)
        if (
            work is None
            or page is None
            or not (0 <= page.active_coma_index < len(page.comas))
        ):
            self.report({"WARNING"}, "編集対象のコマが選択されていません")
            return {"CANCELLED"}
        entry = page.comas[page.active_coma_index]
        # BMangaComaEntry は ``id`` と ``coma_id`` の 2 フィールドを持つ。
        # 旧コード/移行データでは coma_id が空のまま id だけ設定される
        # ケースがあるため、両方を fallback として参照する。
        stem = entry.coma_id or entry.id
        if not paths.is_valid_coma_id(stem):
            self.report({"ERROR"}, f"不正なコマ stem: {stem}")
            return {"CANCELLED"}
        # entry.coma_id が空だったら id をミラー (次回以降の整合のため)
        if not entry.coma_id and entry.id:
            try:
                entry.coma_id = entry.id
            except Exception:  # noqa: BLE001
                pass
        page_id = page.id
        work_dir = Path(work.work_dir)
        _suspend_keymap_visibility_updates()

        try:
            if self._should_prompt_coma_template(context, work_dir, page_id, stem, entry):
                self._prime_template_dialog_path(work, work_dir)
                context.window_manager.fileselect_add(self)
                return {"RUNNING_MODAL"}
            if not blend_io.coma_blend_exists(work_dir, page_id, stem) and self.filepath:
                selected_template = self._selected_template_path()
                if selected_template is None:
                    return {"CANCELLED"}
                entry.coma_blend_template_path = str(selected_template)
                if hasattr(entry, "coma_blend_template_needs_apply"):
                    entry.coma_blend_template_needs_apply = False
                page_io.save_page_json(work_dir, page)

            # work.blend を開く前後の load_post は work.json を正として再同期する。
            # 用紙色や開始ページを UI で変えた直後に cNN.blend へ入っても
            # 古い JSON で巻き戻らないよう、mainfile 切替前に必ず保存する。
            _save_current_work_metadata(work, page)

            # 1) 現在の mainfile が B-MANGA の編集ファイルなら上書き保存
            cur = blend_io.current_mainfile_path()
            expected_work = paths.work_blend_path(work_dir).resolve()
            expected_page = paths.page_blend_path(work_dir, page_id).resolve()
            if cur is not None and cur == expected_work:
                blend_io.save_current_as(expected_work)
            elif cur is not None and cur == expected_page:
                blend_io.save_current_as(expected_page)

            # 2) cNN.blend を開く。未作成なら現シーンを新規保存して遷移。
            if blend_io.coma_blend_exists(work_dir, page_id, stem):
                changed_template = self._pending_coma_template_path(work, work_dir, page, entry)
                if changed_template is False:
                    return {"CANCELLED"}
                if changed_template is not None:
                    from ..utils import coma_scene

                    copied = coma_scene.copy_template_into_coma(
                        changed_template,
                        work_dir,
                        page_id,
                        stem,
                    )
                    if copied is None:
                        self.report({"ERROR"}, "コマ用blendファイルをこのコマへコピーできませんでした")
                        return {"CANCELLED"}
                    if hasattr(entry, "coma_blend_template_needs_apply"):
                        entry.coma_blend_template_needs_apply = False
                    page_io.save_page_json(work_dir, page)
                _suspend_keymap_visibility_updates()
                ok = blend_io.open_coma_blend(work_dir, page_id, stem)
                _suspend_keymap_visibility_updates()
                if not ok:
                    self.report({"ERROR"}, "cNN.blend を開けませんでした")
                    return {"CANCELLED"}
            else:
                from ..utils import coma_scene

                template_path, template_error = coma_scene.resolve_coma_blend_template_path(
                    work, work_dir, entry
                )
                if template_error:
                    self.report({"ERROR"}, template_error)
                    return {"CANCELLED"}
                if template_path is not None and hasattr(entry, "coma_blend_template_needs_apply"):
                    entry.coma_blend_template_needs_apply = False
                    page_io.save_page_json(work_dir, page)
                if template_path is None:
                    _suspend_keymap_visibility_updates()
                if template_path is None and not blend_io.read_homefile():
                    self.report({"ERROR"}, "cNN.blend の初期化に失敗")
                    return {"CANCELLED"}
                _suspend_keymap_visibility_updates()
                ok = coma_scene.bootstrap_new_coma_blend(
                    bpy.context,
                    work_dir,
                    page_id,
                    stem,
                    template_path=template_path,
                )
                if not ok:
                    self.report({"ERROR"}, "cNN.blend の新規作成に失敗")
                    try:
                        _suspend_keymap_visibility_updates()
                        if blend_io.page_blend_exists(work_dir, page_id):
                            blend_io.open_page_blend(work_dir, page_id)
                        else:
                            blend_io.open_work_blend(work_dir)
                        _suspend_keymap_visibility_updates()
                    except Exception:  # noqa: BLE001
                        _logger.exception("enter_coma_mode: failed to restore work.blend")
                    return {"CANCELLED"}
                try:
                    from ..utils import coma_camera

                    coma_camera.ensure_coma_camera_scene(
                        bpy.context,
                        page_id=page_id,
                        coma_id=stem,
                        generate_references=False,
                    )
                except Exception:  # noqa: BLE001
                    _logger.exception("enter_coma_mode: initial panel camera setup failed")
                ok = blend_io.save_coma_blend(work_dir, page_id, stem)
                if not ok:
                    self.report({"ERROR"}, "cNN.blend の新規保存に失敗")
                    try:
                        _suspend_keymap_visibility_updates()
                        if blend_io.page_blend_exists(work_dir, page_id):
                            blend_io.open_page_blend(work_dir, page_id)
                        else:
                            blend_io.open_work_blend(work_dir)
                        _suspend_keymap_visibility_updates()
                    except Exception:  # noqa: BLE001
                        _logger.exception("enter_coma_mode: failed to restore work.blend")
                    return {"CANCELLED"}
                # save_as_mainfile 直後は load_post が走らないので、mode/stem/page_id と
                # viewport 状態は明示的に current scene に反映する。
                try:
                    from ..ui import overlay as _overlay

                    set_mode(MODE_COMA, bpy.context)
                    bpy.context.scene.bmanga_current_coma_id = stem
                    bpy.context.scene.bmanga_current_coma_page_id = page_id
                    if hasattr(bpy.context.scene, "bmanga_active_layer_kind"):
                        bpy.context.scene.bmanga_active_layer_kind = "coma"
                    _overlay.reset_viewport_background_to_theme(bpy.context)
                    _overlay.apply_bmanga_shading_mode(bpy.context)
                except Exception:  # noqa: BLE001
                    _logger.exception("enter_coma_mode: initial panel scene finalize failed")
        except Exception as exc:  # noqa: BLE001
            _logger.exception("enter_coma_mode failed")
            self.report({"ERROR"}, f"コマ編集モード遷移失敗: {exc}")
            return {"CANCELLED"}

        # load_post ハンドラがモード/stem を同期するが、念のため明示的にも設定
        ctx = bpy.context
        set_mode(MODE_COMA, ctx)
        ctx.scene.bmanga_current_coma_id = stem
        ctx.scene.bmanga_current_coma_page_id = page_id
        if hasattr(ctx.scene, "bmanga_active_layer_kind"):
            ctx.scene.bmanga_active_layer_kind = "coma"
        active_view_layer_name = str(
            getattr(getattr(ctx, "view_layer", None), "name", "") or ""
        )
        try:
            from ..utils import coma_camera

            coma_camera.ensure_coma_camera_scene(
                ctx,
                page_id=page_id,
                coma_id=stem,
                generate_references=False,
            )
        except Exception:  # noqa: BLE001
            _logger.exception("enter_coma_mode: final panel camera setup failed")
        try:
            from ..utils import page_preview_object

            page_preview_object.schedule_sync_page_previews()
        except Exception:  # noqa: BLE001
            _logger.exception("enter_coma_mode: page preview setup failed")
        try:
            from ..utils import coma_mask_object

            coma_mask_object.restore_preferred_user_view_layer(
                ctx.scene,
                active_view_layer_name,
            )
        except Exception:  # noqa: BLE001
            _logger.exception("enter_coma_mode: restore view layer failed")
        try:
            from ..ui import sidebar as _sidebar

            _sidebar.schedule_open_bmanga_sidebar()
        except Exception:  # noqa: BLE001
            _logger.exception("enter_coma_mode: B-MANGA sidebar open failed")
        self.report({"INFO"}, f"コマ編集モード: {stem}")
        return {"FINISHED"}

    def _should_prompt_coma_template(self, context, work_dir: Path, page_id: str, stem: str, entry) -> bool:
        if bpy.app.background:
            return False
        if not bool(getattr(self, "prompt_template_if_missing", False)):
            return False
        if str(getattr(self, "filepath", "") or "").strip():
            return False
        if blend_io.coma_blend_exists(work_dir, page_id, stem):
            return False
        return not str(getattr(entry, "coma_blend_template_path", "") or "").strip()

    def _prime_template_dialog_path(self, work, work_dir: Path) -> None:
        if str(getattr(self, "filepath", "") or "").strip():
            return
        try:
            from ..utils import coma_scene

            fallback, _error = coma_scene.resolve_coma_blend_template_path(work, work_dir, None)
        except Exception:  # noqa: BLE001
            fallback = None
        if fallback is not None:
            self.filepath = str(fallback)

    def _selected_template_path(self) -> Path | None:
        raw = str(getattr(self, "filepath", "") or "").strip()
        if not raw:
            self.report({"WARNING"}, "コマ用blendファイルが選択されていません")
            return None
        path = Path(raw)
        if path.suffix.lower() != ".blend":
            self.report({"ERROR"}, "コマ用blendファイルは .blend を指定してください")
            return None
        if not path.is_file():
            self.report({"ERROR"}, f"コマ用blendファイルが見つかりません: {path}")
            return None
        return path.resolve()

    def _pending_coma_template_path(self, work, work_dir: Path, page, entry):
        if not bool(getattr(entry, "coma_blend_template_needs_apply", False)):
            return None
        raw = str(getattr(entry, "coma_blend_template_path", "") or "").strip()
        if not raw:
            try:
                entry.coma_blend_template_needs_apply = False
            except Exception:  # noqa: BLE001
                pass
            page_io.save_page_json(work_dir, page)
            return None
        from ..utils import coma_scene

        template_path, template_error = coma_scene.resolve_coma_blend_template_path(
            work,
            work_dir,
            entry,
        )
        if template_error:
            self.report({"ERROR"}, template_error)
            return False
        if template_path is None:
            return None
        return template_path


class BMANGA_OT_enter_coma_mode_from_viewport(Operator):
    """3D ビューのダブルクリックからコマ用 blend ファイルを開く."""

    bl_idname = "bmanga.enter_coma_mode_from_viewport"
    bl_label = "コマ用blendファイルを開く"

    @classmethod
    def poll(cls, context):
        return BMANGA_OT_enter_coma_mode.poll(context)

    def invoke(self, context, event):
        cur_mode = getattr(context, "mode", "")
        if cur_mode != "OBJECT":
            return {"PASS_THROUGH"}
        work = get_work(context)
        if work is None:
            return {"PASS_THROUGH"}
        try:
            page_hit = page_file_index_from_viewport_event(context, event)
            if page_hit is not None:
                if schedule_open_page_file(int(page_hit)):
                    return {"FINISHED"}
                return {"CANCELLED"}
        except Exception:  # noqa: BLE001
            _logger.exception("enter_coma_mode_from_viewport: page switch failed")
            return {"CANCELLED"}
        hit = _resolve_coma_at_event(context, event)
        if hit is None:
            return {"PASS_THROUGH"}
        page_idx, coma_idx = hit
        if work is None or not (0 <= page_idx < len(work.pages)):
            return {"PASS_THROUGH"}
        page = work.pages[page_idx]
        if not (0 <= coma_idx < len(page.comas)):
            return {"PASS_THROUGH"}
        work.active_page_index = page_idx
        page.active_coma_index = coma_idx
        if schedule_enter_coma_mode(
            page_idx,
            coma_idx,
            prompt_template_if_missing=False,
        ):
            return {"FINISHED"}
        return {"CANCELLED"}

    def execute(self, context):
        work = get_work(context)
        page = get_active_page(context)
        if (
            work is None
            or page is None
            or not (0 <= page.active_coma_index < len(page.comas))
        ):
            return {"CANCELLED"}
        if schedule_enter_coma_mode(
            int(work.active_page_index),
            int(page.active_coma_index),
            prompt_template_if_missing=False,
        ):
            return {"FINISHED"}
        return {"CANCELLED"}


class BMANGA_OT_exit_coma_mode(Operator):
    """コマ編集モードを抜けて overview モード (work.blend) へ戻る.

    cNN.blend を保存し、work.blend を開く。
    """

    bl_idname = "bmanga.exit_coma_mode"
    bl_label = "紙面編集モードへ戻る"

    @classmethod
    def poll(cls, context):
        return get_mode(context) == MODE_COMA

    def execute(self, context):
        coma_modal_state.finish_all(context)
        try:
            from ..utils import coma_camera

            coma_camera.capture_camera_runtime_settings(context)
        except Exception:  # noqa: BLE001
            _logger.exception("exit_coma_mode: camera runtime sync failed")
        # 1) 現在の cNN.blend を保存 → work.blend を開く
        work = get_work(context)
        stem = getattr(context.scene, "bmanga_current_coma_id", "")
        if (
            work is not None
            and work.loaded
            and paths.is_valid_coma_id(stem)
        ):
            work_dir = Path(work.work_dir)
            try:
                page_id = getattr(context.scene, "bmanga_current_coma_page_id", "")
                if not paths.is_valid_page_id(page_id):
                    self.report({"ERROR"}, "編集中コマの page_id が失われています")
                    return {"CANCELLED"}
                cur = blend_io.current_mainfile_path()
                expected_panel = paths.coma_blend_path(work_dir, page_id, stem).resolve()
                _auto_render_thumb_before_return(context, work)
                if cur is not None and cur == expected_panel:
                    blend_io.save_current_as(expected_panel)
                # メタデータを JSON へ書き出してから戻り先 blend を開く。
                # enter_coma_mode と同様、load_post で古い JSON に巻き戻るのを防ぐ。
                page = _find_page_by_id(work, page_id)
                _save_current_work_metadata(work, page)
                # ページ用blendがあればページ編集へ戻る。無い場合だけページ一覧へ戻る。
                if blend_io.page_blend_exists(work_dir, page_id):
                    _suspend_keymap_visibility_updates()
                    blend_io.open_page_blend(work_dir, page_id)
                    _suspend_keymap_visibility_updates()
                elif blend_io.work_blend_exists(work_dir):
                    _suspend_keymap_visibility_updates()
                    blend_io.open_work_blend(work_dir)
                    _suspend_keymap_visibility_updates()
                else:
                    _logger.error(
                        "exit_coma_mode: page/work blend not found at %s / %s",
                        paths.page_blend_path(work_dir, page_id),
                        paths.work_blend_path(work_dir),
                    )
                    self.report(
                        {"ERROR"},
                        "戻り先のblendファイルが見つかりません. 作品フォルダの整合性を確認してください",
                    )
                    return {"CANCELLED"}
            except Exception as exc:  # noqa: BLE001
                _logger.exception("exit_coma_mode blend switch failed")
                self.report({"ERROR"}, f"work.blend 切替失敗: {exc}")
                return {"CANCELLED"}

        ctx = bpy.context
        set_mode(MODE_PAGE, ctx)
        ctx.scene.bmanga_current_coma_id = ""
        ctx.scene.bmanga_current_coma_page_id = ""
        self.report({"INFO"}, "紙面編集モード")
        return {"FINISHED"}


def _current_blend_is_coma_blend() -> tuple[Path | None, str, str]:
    """開いている mainfile が ``pNNNN/cNN/cNN.blend`` 形式かを判定.

    Returns ``(work_dir, page_id, coma_id)`` を返す。マッチしなければ
    ``(None, "", "")``。``bmanga_mode`` / ``bmanga_current_coma_id`` 等の
    Scene プロパティが load_post 失敗で同期されていないケースの救済用。
    """
    fp = bpy.data.filepath
    if not fp:
        return None, "", ""
    try:
        path = Path(fp).resolve()
    except OSError:
        return None, "", ""
    parts = path.parts
    if len(parts) < 4:
        return None, "", ""
    page_id, coma_id, fname = parts[-3], parts[-2], parts[-1]
    if not (
        paths.is_valid_page_id(page_id)
        and paths.is_valid_coma_id(coma_id)
        and fname == f"{coma_id}.blend"
    ):
        return None, "", ""
    work_dir = path.parents[2]
    return work_dir, page_id, coma_id


class BMANGA_OT_exit_coma_mode_safe(Operator):
    """コマ編集を終了してページに戻る."""

    bl_idname = "bmanga.exit_coma_mode_safe"
    bl_label = "ページに戻る"

    @classmethod
    def poll(cls, context):
        if get_mode(context) == MODE_COMA:
            return True
        work_dir, _page_id, _coma_id = _current_blend_is_coma_blend()
        return work_dir is not None

    def execute(self, context):
        # 1) 通常パス: ``exit_coma_mode`` の poll が通るならそれに委譲
        try:
            if BMANGA_OT_exit_coma_mode.poll(context):
                return bpy.ops.bmanga.exit_coma_mode("EXEC_DEFAULT")
        except Exception:  # noqa: BLE001
            _logger.exception("exit_coma_mode_safe: 通常パス失敗")

        # 2) フォールバック: パスから work_dir を逆引きし、work.blend を開く
        work_dir, page_id, coma_id = _current_blend_is_coma_blend()
        if work_dir is None:
            self.report({"ERROR"}, "コマファイル (cNN.blend) ではありません")
            return {"CANCELLED"}
        try:
            try:
                from ..utils import coma_camera

                coma_camera.capture_camera_runtime_settings(context)
            except Exception:  # noqa: BLE001
                _logger.exception("exit_coma_mode_safe: camera runtime sync failed")
            # 念のため現在の cNN.blend に save (上書き保存)
            try:
                cur = blend_io.current_mainfile_path()
                expected_panel = paths.coma_blend_path(work_dir, page_id, coma_id).resolve()
                _auto_render_thumb_before_return(context, get_work(context))
                if cur is not None and cur == expected_panel:
                    blend_io.save_current_as(expected_panel)
            except Exception:  # noqa: BLE001
                _logger.exception("exit_coma_mode_safe: cNN.blend 保存失敗 (続行)")

            if blend_io.page_blend_exists(work_dir, page_id):
                target_opened = blend_io.open_page_blend(work_dir, page_id)
            elif blend_io.work_blend_exists(work_dir):
                target_opened = blend_io.open_work_blend(work_dir)
            else:
                self.report(
                    {"ERROR"},
                    f"戻り先のblendファイルが見つかりません: {paths.work_blend_path(work_dir)}",
                )
                return {"CANCELLED"}
            if not target_opened:
                self.report({"ERROR"}, "戻り先のblendファイルを開けませんでした")
                return {"CANCELLED"}
            # load_post が走るので mode/state は自動で同期される。
            # 念のため現在 scene にも反映 (load_post 前に UI 更新が走る場合)。
            try:
                ctx = bpy.context
                set_mode(MODE_PAGE, ctx)
                ctx.scene.bmanga_current_coma_id = ""
                ctx.scene.bmanga_current_coma_page_id = ""
            except Exception:  # noqa: BLE001
                pass
            self.report({"INFO"}, "戻りました")
            return {"FINISHED"}
        except Exception as exc:  # noqa: BLE001
            _logger.exception("exit_coma_mode_safe: work.blend 切替失敗")
            self.report({"ERROR"}, f"work.blend 切替失敗: {exc}")
            return {"CANCELLED"}


_CLASSES = (
    BMANGA_OT_enter_coma_mode,
    BMANGA_OT_enter_coma_mode_from_viewport,
    BMANGA_OT_exit_coma_mode,
    BMANGA_OT_exit_coma_mode_safe,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    _clear_deferred_enter_coma_mode()
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
