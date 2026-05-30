"""bpy.app.handlers ハンドラ.

``load_post``: .blend ファイル open 後に、B-Name 作品フォルダ配下の
.blend であれば work.json / pages.json を再読み込みして Scene プロパティを
同期する。また、開かれた .blend のパスから active_page_index と
bname_current_coma_id を自動推定する。

これにより、ページ切替 (page.blend 差替) 時に JSON メタが正しく維持され、
古い .blend 内に残っていた Scene プロパティが上書きされる。
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.app.handlers import persistent

from . import log, paths

_logger = log.get_logger(__name__)

_current_file_sync_generation = 0
_saving_work_metadata = False


def _find_work_root(blend_path: Path) -> Path | None:
    """blend パスから上位に辿って .bname ディレクトリを探す (最大 6 階層)."""
    p = blend_path.parent
    for _ in range(6):
        if p.suffix == paths.BNAME_DIR_SUFFIX:
            return p
        if p.parent == p:
            break
        p = p.parent
    return None


def _sync_active_from_blend_path(
    scene, work, work_dir: Path, blend_path: Path
) -> None:
    """開かれた blend のパスから mode / active_page_index / coma_id を推定.

    - ``<work>.bname/work.blend`` → overview モード (MODE_PAGE)
    - ``<work>.bname/pNNNN/cNN/cNN.blend`` → コマ編集モード
      (MODE_COMA + active_page_index を該当ページに、coma_id を設定)
    - それ以外のパス (旧 page.blend 等) は何もしない
    """
    try:
        rel = blend_path.resolve().relative_to(work_dir.resolve())
    except ValueError:
        return
    try:
        from ..core.mode import MODE_PAGE, MODE_COMA, set_mode
    except Exception:  # noqa: BLE001
        return
    parts = rel.parts

    # work.blend 直下 → overview モード
    if len(parts) == 1 and parts[0] == paths.WORK_BLEND_NAME:
        scene.bname_current_coma_id = ""
        scene.bname_current_coma_page_id = ""
        try:
            scene.bname_overview_mode = True
        except Exception:  # noqa: BLE001
            pass
        if hasattr(scene, "bname_active_layer_kind"):
            scene.bname_active_layer_kind = "page"
        set_mode(MODE_PAGE, bpy.context)
        return

    # pNNNN/cNN/cNN.blend → コマ編集モード
    if (
        len(parts) == 3
        and paths.is_valid_page_id(parts[0])
        and paths.is_valid_coma_id(parts[1])
        and parts[2] == f"{parts[1]}.blend"
    ):
        page_id = parts[0]
        coma_id = parts[1]
        for i, pg in enumerate(work.pages):
            if pg.id == page_id:
                work.active_page_index = i
                break
        scene.bname_current_coma_id = coma_id
        scene.bname_current_coma_page_id = page_id
        if hasattr(scene, "bname_active_layer_kind"):
            scene.bname_active_layer_kind = "coma"
        set_mode(MODE_COMA, bpy.context)
        _disable_bname_shortcuts_for_coma_blend()
        return

    # それ以外 (未知のパス) は overview 扱いのまま触らない


def _disable_bname_shortcuts_for_coma_blend() -> None:
    """コマ用blendファイルではB-Name専用キーと起動中操作を残さない."""
    try:
        from ..keymap import keymap

        keymap.force_shortcuts_disabled()
    except Exception:  # noqa: BLE001
        _logger.exception("load_post: disable B-Name shortcuts for coma blend failed")


def _active_view_layer_name(scene) -> str:
    try:
        window = getattr(bpy.context, "window", None)
        if window is not None and getattr(window, "scene", None) is scene:
            view_layer = getattr(window, "view_layer", None)
            if view_layer is not None:
                return str(getattr(view_layer, "name", "") or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        view_layer = getattr(bpy.context, "view_layer", None)
        return str(getattr(view_layer, "name", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def _restore_coma_user_view_layer(scene, layer_name: str = "") -> None:
    try:
        from . import coma_mask_object

        coma_mask_object.restore_preferred_user_view_layer(scene, layer_name)
    except Exception:  # noqa: BLE001
        _logger.exception("restore coma view layer failed")


def _reload_all_pages_panels(work, work_dir: Path) -> None:
    """全ページの ``comas`` を各 ``page.json`` から再ロードして Scene に反映.

    pages.json は全ページのリストだけを持ち、comas は各ページの page.json
    にしか無いため、load_post で pages.json を読み込んだ後に各 page.json
    を個別に再ロードしないと、他ページの comas が現在の .blend に
    キャッシュされた古いものに固定されてしまう。

    load_page_json は内部で ``page_entry.comas.clear()`` → 再構築 するので
    上書き安全。
    """
    from ..io import page_io  # 遅延 import

    for page_entry in work.pages:
        if not page_entry.id:
            continue
        try:
            page_io.load_page_json(work_dir, page_entry)
        except Exception:  # noqa: BLE001
            # 個別 page.json の欠損や不整合はスキップ
            _logger.warning(
                "load_post: failed to load page.json for %s", page_entry.id,
                exc_info=True,
            )


def sync_scene_work_from_disk(context, work_dir: Path):
    """現在 scene の ``bname_work`` を disk 上の work/pages/page JSON に同期."""
    from ..core.work import get_work
    from ..io import page_io, work_io

    work = get_work(context)
    if work is None:
        return None
    work_io.load_work_json(work_dir, work)
    page_io.load_pages_json(work_dir, work)
    _reload_all_pages_panels(work, work_dir)
    work.work_dir = str(Path(work_dir).resolve())
    work.loaded = True
    return work


def save_scene_work_to_disk(context, *, reason: str = "") -> bool:
    """現在 scene の B-Name JSON メタデータを disk へ保存する.

    通常の .blend 保存フックからも呼ぶため、ここでは .blend 保存は行わない。
    """
    global _saving_work_metadata
    if _saving_work_metadata:
        return False
    try:
        from ..core.work import get_work
        from ..io import page_io, work_io
        from . import page_range
    except Exception:  # noqa: BLE001
        return False

    work = get_work(context)
    if (
        work is None
        or not getattr(work, "loaded", False)
        or not getattr(work, "work_dir", "")
    ):
        return False
    work_dir = Path(str(getattr(work, "work_dir", "") or ""))
    if not work_dir.is_dir():
        return False

    _saving_work_metadata = True
    try:
        page_range.update_page_range_visibility(work)
        try:
            from ..operators import raster_layer_op

            raster_layer_op.save_dirty_raster_layers(context)
        except Exception:  # noqa: BLE001
            _logger.exception("raster dirty save failed")
        work_io.save_work_json(work_dir, work)
        page_io.save_pages_json(work_dir, work)
        for page in getattr(work, "pages", []):
            if not getattr(page, "id", ""):
                continue
            page_io.save_page_json(work_dir, page)
        _logger.info("B-Name metadata saved%s", f" ({reason})" if reason else "")
        # Phase 1: 保存契機で Outliner mirror を最新化する。page/coma 追加削除
        # 直後に save_scene_work_to_disk が呼ばれるため、ここでミラーを更新
        # しておけば各 op に侵襲しない。冪等で安全。
        try:
            from . import layer_object_sync as _los

            scene = getattr(context, "scene", None)
            if scene is not None:
                _los.mirror_work_to_outliner(scene, work)
        except Exception:  # noqa: BLE001
            _logger.exception("save_scene_work_to_disk: mirror refresh failed")
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("B-Name metadata save failed%s", f" ({reason})" if reason else "")
        return False
    finally:
        _saving_work_metadata = False


def _reconcile_gpencil_collections(context, work) -> None:
    """master GP とページ Collection × pages の整合をとる (新仕様).

    - 作品全体で **唯一の** master GP オブジェクトを ensure (旧 page GP は残置)
    - 旧仕様の紙メッシュ (page_NNNN_paper) は削除し、用紙は overlay で描画
    - 全ページ Collection の grid offset を apply

    旧バージョンの page_NNNN_sketch GP オブジェクトはここでは触らない
    (ユーザーのデータを残置)。新規描画は master GP に行う。
    """
    from . import gpencil as gp_utils
    from . import page_grid

    scene = getattr(context, "scene", None) if context else None
    if scene is None:
        scene = bpy.context.scene
    if scene is None or work is None:
        return

    try:
        gp_utils.remove_all_page_papers()
    except Exception:  # noqa: BLE001
        _logger.exception("load_post: remove page paper meshes failed")

    # master GP は作品で 1 つだけ
    try:
        gp_utils.ensure_master_gpencil(scene)
    except Exception:  # noqa: BLE001
        _logger.exception("load_post: ensure_master_gpencil failed")

    try:
        from . import layer_stack as layer_stack_utils

        if layer_stack_utils.get_effect_gp_object() is not None:
            layer_stack_utils.ensure_effect_gp_object(scene)
    except Exception:  # noqa: BLE001
        _logger.exception("load_post: ensure_effect_gp_object failed")

    try:
        page_grid.apply_page_collection_transforms(context, work)
    except Exception:  # noqa: BLE001
        _logger.exception("load_post: apply_page_collection_transforms failed")
    try:
        from ..operators import raster_layer_op

        raster_layer_op.ensure_all_raster_runtime(context)
    except Exception:  # noqa: BLE001
        _logger.exception("load_post: raster runtime sync failed")


@persistent
def _bname_on_load_post(filepath_arg) -> None:  # signature: (str,) in Blender handlers
    """.blend ロード直後に B-Name 作品のメタ情報を再同期."""
    try:
        # ファイル切替前のツール modal が残っているとイベントを奪ったままになる
        # (例: 枠線ツール起動中にページ一覧へ戻ると、マウスホイールドラッグや N
        # キーが効かなくなる)。 ロードされた scene は新しいので、 旧 modal の
        # 参照は無効化済み。 ここでは外部終了フラグだけ立てて、 各 modal の
        # 次回 event で自然終了させる。
        try:
            from ..operators import coma_modal_state as _modal_state
            _modal_state.mark_all_externally_finished()
        except Exception:  # noqa: BLE001
            _logger.exception("load_post: mark_all_externally_finished failed")
        # 遅延 import: サブシステムの初期化順を回避
        scene = bpy.context.scene
        if scene is None:
            return
        blend_path = Path(bpy.data.filepath)
        if str(blend_path) == "" or not blend_path.is_file():
            return
        work_dir = _find_work_root(blend_path)
        if work_dir is None:
            return
        work = sync_scene_work_from_disk(bpy.context, work_dir)
        if work is None:
            return
        try:
            work.work_dir = str(work_dir.resolve())
            work.loaded = True
        except Exception:  # noqa: BLE001
            _logger.exception("load_post: failed to sync work/pages json")
            return
        _sync_active_from_blend_path(scene, work, work_dir, blend_path)
        from . import display_settings

        # 色管理の標準化はページ一覧 (work.blend) のみに適用する。
        # コマ用blendファイルはユーザーの 3D 作業領域であり、ここで
        # 毎回 Standard へ戻すと、ユーザーが設定したビュー変換/露出/
        # ルックが開く/閉じるたびに失われる (保存直前にも走るため
        # 保存値ごと初期化されていた)。work.blend かどうかは下の
        # 分岐で判定するため、ここでは一律適用しない。
        try:
            from ..operators import preset_op

            preset_op.sync_paper_preset_selector(bpy.context)
            preset_op.sync_border_preset_selector(bpy.context)
        except Exception:  # noqa: BLE001
            _logger.exception("load_post: preset selector sync failed")
        try:
            from . import layer_stack as _layer_stack

            _layer_stack.sync_layer_stack(bpy.context)
            _layer_stack.schedule_layer_stack_sync()
        except Exception:  # noqa: BLE001
            _logger.exception("load_post: layer stack sync failed")
        # コマ blend (cNN/cNN.blend) では Outliner mirror を即時実行しない。
        # prepare_coma_blend_scene が後段で scene 構造を組み直すため、その前に
        # mirror が走ると不要な B-Name root が cNN scene に作られる。
        is_coma_blend = False
        try:
            rel = blend_path.resolve().relative_to(work_dir.resolve())
            is_coma_blend = (
                len(rel.parts) == 3
                and paths.is_valid_page_id(rel.parts[0])
                and paths.is_valid_coma_id(rel.parts[1])
                and rel.parts[2] == f"{rel.parts[1]}.blend"
            )
        except ValueError:
            pass
        if not is_coma_blend:
            try:
                from . import layer_object_sync as _los

                _los.mirror_work_to_outliner(scene, work)
            except Exception:  # noqa: BLE001
                _logger.exception("load_post: outliner mirror failed")
        # work.blend / cNN.blend ごとに Scene の整合を補正する。
        try:
            rel = blend_path.resolve().relative_to(work_dir.resolve())
            if len(rel.parts) == 1 and rel.parts[0] == paths.WORK_BLEND_NAME:
                _reconcile_gpencil_collections(bpy.context, work)
                # ページ一覧は常にフラットな印刷物の見た目 (Standard)。
                display_settings.apply_standard_color_management(scene)
                try:
                    from ..ui import overlay as _overlay

                    _overlay.reset_viewport_background_to_theme(bpy.context)
                    _overlay.apply_bname_shading_mode(bpy.context)
                    _overlay.set_viewport_overlays_enabled(bpy.context, enabled=False)
                    _overlay.schedule_viewport_overlays_enabled(enabled=False)
                except Exception:  # noqa: BLE001
                    _logger.exception(
                        "load_post: shading/background reset failed"
                    )
            elif (
                len(rel.parts) == 3
                and paths.is_valid_page_id(rel.parts[0])
                and paths.is_valid_coma_id(rel.parts[1])
                and rel.parts[2] == f"{rel.parts[1]}.blend"
            ):
                from . import coma_scene
                from . import coma_camera
                from ..ui import overlay as _overlay

                active_view_layer_name = _active_view_layer_name(scene)
                coma_scene.prepare_coma_blend_scene(bpy.context)
                # コマ用blendファイルの色管理はユーザーに委ねる
                # (ここで Standard に戻さない)。
                coma_camera.ensure_coma_camera_scene(
                    bpy.context,
                    work=work,
                    generate_references=True,
                )
                try:
                    from . import coma_thumb_output

                    coma_thumb_output.ensure_thumb_output_node(scene)
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: thumb output setup failed")
                try:
                    from . import coma_mask_object

                    coma_mask_object.ensure_coma_mask_mesh(
                        scene, work, str(rel.parts[0]), str(rel.parts[1])
                    )
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: coma mask mesh sync failed")
                _overlay.reset_viewport_background_to_theme(bpy.context)
                _overlay.apply_bname_shading_mode(bpy.context)
                coma_camera.schedule_coma_view_camera()
                try:
                    from ..ui import sidebar as _sidebar

                    _sidebar.schedule_open_bname_sidebar()
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: B-Name sidebar open failed")
                _restore_coma_user_view_layer(scene, active_view_layer_name)
        except ValueError:
            pass
        _logger.info("B-Name: load_post synced for %s", blend_path)
    except Exception:  # noqa: BLE001
        _logger.exception("B-Name load_post handler failed")


@persistent
def _bname_on_save_pre(filepath_arg) -> None:  # signature: (str,) in Blender handlers
    """通常の .blend 保存前に B-Name の JSON メタデータも同期する."""
    try:
        try:
            from . import coma_camera

            coma_camera.capture_camera_runtime_settings(bpy.context)
        except Exception:  # noqa: BLE001
            _logger.exception("B-Name coma camera save_pre sync failed")
        try:
            blend_path = Path(bpy.data.filepath)
            work_dir = _find_work_root(blend_path) if str(blend_path) else None
            if work_dir is not None:
                rel = blend_path.resolve().relative_to(work_dir.resolve())
                if (
                    len(rel.parts) == 3
                    and paths.is_valid_page_id(rel.parts[0])
                    and paths.is_valid_coma_id(rel.parts[1])
                    and rel.parts[2] == f"{rel.parts[1]}.blend"
                ):
                    active_view_layer_name = _active_view_layer_name(bpy.context.scene)
                    from . import coma_thumb_output

                    coma_thumb_output.ensure_thumb_output_node(bpy.context.scene)
                    try:
                        from . import coma_mask_object
                        from ..core.work import get_work as _get_work

                        coma_mask_object.ensure_coma_mask_mesh(
                            bpy.context.scene,
                            _get_work(bpy.context),
                            str(rel.parts[0]),
                            str(rel.parts[1]),
                        )
                    except Exception:  # noqa: BLE001
                        _logger.exception("save_pre: coma mask mesh sync failed")
                    _restore_coma_user_view_layer(
                        bpy.context.scene,
                        active_view_layer_name,
                    )
        except Exception:  # noqa: BLE001
            _logger.exception("B-Name thumb output save_pre sync failed")
        try:
            from ..operators import raster_layer_op

            raster_layer_op.save_dirty_raster_layers(bpy.context)
        except Exception:  # noqa: BLE001
            _logger.exception("B-Name raster save_pre failed")
        save_scene_work_to_disk(bpy.context, reason="save_pre")
    except Exception:  # noqa: BLE001
        _logger.exception("B-Name save_pre handler failed")


def _remove_named_handler(handler_list, name: str) -> None:
    for h in list(handler_list):
        if getattr(h, "__name__", "") == name:
            try:
                handler_list.remove(h)
            except ValueError:
                pass


def register() -> None:
    """ハンドラを重複なく登録."""
    # 既存の同名ハンドラを除去 (reload 対策)
    _remove_named_handler(bpy.app.handlers.load_post, _bname_on_load_post.__name__)
    _remove_named_handler(bpy.app.handlers.save_pre, _bname_on_save_pre.__name__)
    bpy.app.handlers.load_post.append(_bname_on_load_post)
    bpy.app.handlers.save_pre.append(_bname_on_save_pre)
    _logger.debug("handlers registered")


def schedule_current_file_sync(retries: int = 3, interval: float = 0.15) -> None:
    """アドオン再読込時に、現在開いている B-Name .blend を load_post 相当に同期する."""
    global _current_file_sync_generation
    _current_file_sync_generation += 1
    generation = _current_file_sync_generation
    state = {"left": max(1, int(retries))}

    def _tick():
        if generation != _current_file_sync_generation:
            return None
        try:
            _bname_on_load_post(str(getattr(bpy.data, "filepath", "") or ""))
        except Exception:  # noqa: BLE001
            _logger.exception("scheduled current file sync failed")
        state["left"] -= 1
        return interval if state["left"] > 0 else None

    try:
        bpy.app.timers.register(_tick, first_interval=interval)
    except Exception:  # noqa: BLE001
        _logger.exception("schedule current file sync failed")


def unregister() -> None:
    global _current_file_sync_generation
    _current_file_sync_generation += 1
    _remove_named_handler(bpy.app.handlers.load_post, _bname_on_load_post.__name__)
    _remove_named_handler(bpy.app.handlers.save_pre, _bname_on_save_pre.__name__)
    _logger.debug("handlers unregistered")
