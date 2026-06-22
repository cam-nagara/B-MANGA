"""bpy.app.handlers ハンドラ.

``load_post``: .blend ファイル open 後に、B-MANGA 作品フォルダ配下の
.blend であれば work.json / pages.json を再読み込みして Scene プロパティを
同期する。また、開かれた .blend のパスから active_page_index と
bmanga_current_coma_id を自動推定する。

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
    """blend パスから上位に辿って .bmanga ディレクトリを探す (最大 6 階層)."""
    p = blend_path.parent
    for _ in range(6):
        if p.suffix == paths.BMANGA_DIR_SUFFIX:
            return p
        if p.parent == p:
            break
        p = p.parent
    return None


def _sync_active_from_blend_path(
    scene, work, work_dir: Path, blend_path: Path
) -> None:
    """開かれた blend のパスから mode / active_page_index / coma_id を推定.

    - ``<work>.bmanga/work.blend`` → overview モード (MODE_PAGE)
    - ``<work>.bmanga/pNNNN/page.blend`` → ページ編集モード
    - ``<work>.bmanga/pNNNN/cNN/cNN.blend`` → コマ編集モード
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
        scene.bmanga_current_coma_id = ""
        scene.bmanga_current_coma_page_id = ""
        scene.bmanga_current_page_id = ""
        try:
            scene.bmanga_overview_mode = True
        except Exception:  # noqa: BLE001
            pass
        if hasattr(scene, "bmanga_active_layer_kind"):
            scene.bmanga_active_layer_kind = "page"
        set_mode(MODE_PAGE, bpy.context)
        return

    # pNNNN/page.blend → ページ編集モード
    if (
        len(parts) == 2
        and paths.is_valid_page_id(parts[0])
        and parts[1] == paths.PAGE_BLEND_NAME
    ):
        page_id = parts[0]
        for i, pg in enumerate(work.pages):
            if pg.id == page_id:
                work.active_page_index = i
                break
        scene.bmanga_current_page_id = page_id
        scene.bmanga_current_coma_id = ""
        scene.bmanga_current_coma_page_id = ""
        try:
            scene.bmanga_overview_mode = True
        except Exception:  # noqa: BLE001
            pass
        if hasattr(scene, "bmanga_active_layer_kind"):
            scene.bmanga_active_layer_kind = "page"
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
        scene.bmanga_current_coma_id = coma_id
        scene.bmanga_current_coma_page_id = page_id
        scene.bmanga_current_page_id = page_id
        if hasattr(scene, "bmanga_active_layer_kind"):
            scene.bmanga_active_layer_kind = "coma"
        set_mode(MODE_COMA, bpy.context)
        _disable_bmanga_shortcuts_for_coma_blend()
        return

    # それ以外 (未知のパス) は overview 扱いのまま触らない


def _disable_bmanga_shortcuts_for_coma_blend() -> None:
    """コマ用blendファイルではB-MANGA専用キーと起動中操作を残さない."""
    try:
        from ..keymap import keymap

        keymap.force_shortcuts_disabled()
    except Exception:  # noqa: BLE001
        _logger.exception("load_post: disable B-MANGA shortcuts for coma blend failed")


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


def _page_detail_filter() -> set[str] | None:
    """このファイルで詳細 (コマ・フキダシ・テキスト) を持つページ ID を返す.

    - 作品ファイル: 空集合 (ページ一覧だけを扱うため詳細は持たない)
    - ページ用 blend / コマ用 blend: 自分が属するページのみ
      (フキダシ番号は採番カウンター、出力・見開き・リンク先はその場読み込みで
      他ページ詳細への依存を断っている)
    - 判定不能な旧ファイル: None (= 全ページ読み込み)
    """
    try:
        from . import page_file_scene

        role, page_id, _coma_id = page_file_scene.current_role(bpy.context)
        if role == page_file_scene.ROLE_WORK:
            return set()
        if role in {page_file_scene.ROLE_PAGE, page_file_scene.ROLE_COMA} and page_id:
            return {page_id}
    except Exception:  # noqa: BLE001
        _logger.exception("page detail filter resolve failed")
    return None


def _reload_all_pages_panels(work, work_dir: Path) -> None:
    """各ページの詳細を page.json から再ロードして Scene に反映.

    pages.json は全ページのリストだけを持ち、comas は各ページの page.json
    にしか無いため、load_post で pages.json を読み込んだ後に各 page.json
    を個別に再ロードしないと、他ページの comas が現在の .blend に
    キャッシュされた古いものに固定されてしまう。

    ファイルの役割に応じて読み込む対象を絞り、対象外のページは詳細を
    メモリから破棄する (作品ファイルのスリム化)。load_page_json は内部で
    ``page_entry.comas.clear()`` → 再構築 するので上書き安全。
    """
    from ..io import page_io  # 遅延 import
    from . import page_detail

    detail_filter = _page_detail_filter()
    for page_entry in work.pages:
        if not page_entry.id:
            continue
        if detail_filter is not None and page_entry.id not in detail_filter:
            page_detail.clear_page_detail(page_entry)
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
    """現在 scene の ``bmanga_work`` を disk 上の work/pages/page JSON に同期."""
    from ..core.work import get_work
    from ..io import page_io, work_io
    from . import view_settings

    work = get_work(context)
    if work is None:
        return None
    work_io.load_work_json(work_dir, work)
    page_io.load_pages_json(work_dir, work)
    _reload_all_pages_panels(work, work_dir)
    work.work_dir = str(Path(work_dir).resolve())
    work.loaded = True
    view_settings.apply_work_to_scene(getattr(context, "scene", None), work)
    try:
        from . import page_grid

        # 閉じている間に一覧側で並べ替え・配置変更があった場合、
        # 下書き (マスター GP) のストロークを新しいページ位置へ追従させる
        page_grid.reconcile_gp_strokes_with_page_offset(context, work)
    except Exception:  # noqa: BLE001
        _logger.exception("gp page-offset reconcile failed")
    return work


def save_scene_work_to_disk(context, *, reason: str = "") -> bool:
    """現在 scene の B-MANGA JSON メタデータを disk へ保存する.

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
            from . import view_settings

            view_settings.copy_scene_to_work(getattr(context, "scene", None), work)
        except Exception:  # noqa: BLE001
            _logger.exception("view settings save failed")
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
            # 詳細未読込のページは page.json を書かない (作品ファイルなどで
            # 空のコマ・フキダシ・テキストによる上書きを防ぐ)
            if not bool(getattr(page, "detail_loaded", True)):
                continue
            page_io.save_page_json(work_dir, page)
        _logger.info("B-MANGA metadata saved%s", f" ({reason})" if reason else "")
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
        try:
            from . import page_grid

            # 読込時の下書き位置補正の基準として、自ページの現在配置を記録
            page_grid.record_gp_page_offset(context, work)
        except Exception:  # noqa: BLE001
            _logger.exception("gp page-offset record failed")
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("B-MANGA metadata save failed%s", f" ({reason})" if reason else "")
        return False
    finally:
        _saving_work_metadata = False


def _reconcile_gpencil_collections(context, work, *, include_page_content: bool = True) -> None:
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

    # master GP はページ編集側だけで用意する。ページ一覧では中身を載せない。
    if include_page_content:
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
    if include_page_content:
        try:
            from ..operators import raster_layer_op

            raster_layer_op.ensure_all_raster_runtime(context)
        except Exception:  # noqa: BLE001
            _logger.exception("load_post: raster runtime sync failed")
        try:
            from . import page_content_visibility

            page_content_visibility.apply_page_content_visibility(context, work)
        except Exception:  # noqa: BLE001
            _logger.exception("load_post: page content visibility sync failed")


@persistent
def _bmanga_on_load_post(filepath_arg) -> None:  # signature: (str,) in Blender handlers
    """.blend ロード直後に B-MANGA 作品のメタ情報を再同期."""
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
        try:
            from . import page_content_visibility

            if page_content_visibility.is_work_blend_scene(scene):
                page_content_visibility.apply_page_content_visibility(bpy.context, work)
        except Exception:  # noqa: BLE001
            _logger.exception("load_post: initial page content visibility sync failed")
        from . import display_settings

        # 色管理の標準化はページ一覧 (work.blend) のみに適用する。
        # コマ用blendファイルはユーザーの 3D 作業領域であり、ここで
        # 毎回 Standard へ戻すと、ユーザーが設定したビュー変換/露出/
        # ルックが開く/閉じるたびに失われる (保存直前にも走るため
        # 保存値ごと初期化されていた)。work.blend かどうかは下の
        # 分岐で判定するため、ここでは一律適用しない。
        try:
            from ..operators import balloon_tail_detail_op, preset_op

            preset_op.sync_paper_preset_selector(bpy.context)
            preset_op.sync_border_preset_selector(bpy.context)
            preset_op.restore_tool_preset_selectors(bpy.context)
            balloon_tail_detail_op.restore_tail_preset_selector(bpy.context)
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
        # mirror が走ると不要な B-MANGA root が cNN scene に作られる。
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
                _reconcile_gpencil_collections(bpy.context, work, include_page_content=False)
                # ページ一覧は常にフラットな印刷物の見た目 (Standard)。
                display_settings.apply_standard_color_management(scene)
                try:
                    from ..ui import overlay as _overlay

                    _overlay.reset_viewport_background_to_theme(bpy.context)
                    _overlay.apply_bmanga_shading_mode(bpy.context)
                    _overlay.set_viewport_overlays_enabled(bpy.context, enabled=False)
                    _overlay.schedule_viewport_overlays_enabled(enabled=False)
                except Exception:  # noqa: BLE001
                    _logger.exception(
                        "load_post: shading/background reset failed"
                    )
                try:
                    from . import geometry_nodes_bridge

                    geometry_nodes_bridge.schedule_effect_line_node_group_for_work(
                        bpy.context
                    )
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: effect line display preparation failed")
                try:
                    from ..ui import sidebar as _sidebar

                    _sidebar.schedule_open_bmanga_sidebar()
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: B-MANGA sidebar open failed")
            elif (
                len(rel.parts) == 2
                and paths.is_valid_page_id(rel.parts[0])
                and rel.parts[1] == paths.PAGE_BLEND_NAME
            ):
                _reconcile_gpencil_collections(bpy.context, work, include_page_content=True)
                try:
                    from . import balloon_curve_object

                    balloon_curve_object.prewarm_balloon_resources()
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: balloon resource preparation failed")
                try:
                    from . import page_file_scene

                    page_file_scene.purge_other_page_data(scene, str(rel.parts[0]))
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: purge other page data failed")
                try:
                    from . import page_preview_object

                    page_preview_object.highlight_preview_page(scene, work, None)
                    page_preview_object.sync_page_previews(bpy.context, work)
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: page preview setup failed")
                display_settings.apply_standard_color_management(scene)
                try:
                    from ..ui import overlay as _overlay

                    _overlay.reset_viewport_background_to_theme(bpy.context)
                    _overlay.apply_bmanga_shading_mode(bpy.context)
                    _overlay.set_viewport_overlays_enabled(bpy.context, enabled=False)
                    _overlay.schedule_viewport_overlays_enabled(enabled=False)
                except Exception:  # noqa: BLE001
                    _logger.exception(
                        "load_post: page blend shading/background reset failed"
                    )
                try:
                    from ..ui import sidebar as _sidebar

                    _sidebar.schedule_open_bmanga_sidebar()
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: B-MANGA sidebar open failed")
                try:
                    from . import cross_page_transfer

                    n = cross_page_transfer.process_staged_imports(bpy.context)
                    if n > 0:
                        _logger.info("load_post: imported %d staged effects", n)
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: staged import processing failed")
                try:
                    from ..operators import view_op

                    view_op.schedule_fit_active_page()
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: page fit scheduling failed")
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
                    generate_references=False,
                )
                try:
                    from . import page_preview_object

                    page_preview_object.schedule_sync_page_previews()
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: coma page preview setup failed")
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
                _overlay.apply_bmanga_shading_mode(bpy.context)
                coma_camera.schedule_coma_view_camera()
                try:
                    from ..ui import sidebar as _sidebar

                    _sidebar.schedule_open_bmanga_sidebar()
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: B-MANGA sidebar open failed")
                _restore_coma_user_view_layer(scene, active_view_layer_name)
        except ValueError:
            pass
        _logger.info("B-MANGA: load_post synced for %s", blend_path)
    except Exception:  # noqa: BLE001
        _logger.exception("B-MANGA load_post handler failed")


@persistent
def _bmanga_on_save_pre(filepath_arg) -> None:  # signature: (str,) in Blender handlers
    """通常の .blend 保存前に B-MANGA の JSON メタデータも同期する."""
    try:
        try:
            from ..core.work import get_work as _get_work
            from . import page_content_visibility

            page_content_visibility.restore_all_virtual_hidden(
                bpy.context,
                _get_work(bpy.context),
            )
        except Exception:  # noqa: BLE001
            _logger.exception("B-MANGA page content visibility restore failed")
        try:
            from . import coma_camera

            coma_camera.capture_camera_runtime_settings(bpy.context)
        except Exception:  # noqa: BLE001
            _logger.exception("B-MANGA coma camera save_pre sync failed")
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
            _logger.exception("B-MANGA thumb output save_pre sync failed")
        try:
            from ..operators import raster_layer_op

            raster_layer_op.save_dirty_raster_layers(bpy.context)
        except Exception:  # noqa: BLE001
            _logger.exception("B-MANGA raster save_pre failed")
        save_scene_work_to_disk(bpy.context, reason="save_pre")
    except Exception:  # noqa: BLE001
        _logger.exception("B-MANGA save_pre handler failed")


@persistent
def _bmanga_on_save_post(filepath_arg) -> None:  # signature: (str,) in Blender handlers
    """保存後にページ一覧用の軽量表示を戻す."""
    try:
        from . import page_content_visibility

        page_content_visibility.schedule_apply(bpy.context)
    except Exception:  # noqa: BLE001
        _logger.exception("B-MANGA page content visibility reapply failed")


def _remove_named_handler(handler_list, name: str) -> None:
    for h in list(handler_list):
        if getattr(h, "__name__", "") == name:
            try:
                handler_list.remove(h)
            except ValueError:
                pass


@persistent
def _bmanga_on_undo_post(*_args) -> None:
    """undo/redo 後にモーダルツールの参照を無効化し、再起動する."""
    try:
        from ..operators import coma_modal_state as _modal_state
        count = _modal_state.mark_all_externally_finished()
        if count > 0:
            _logger.debug("undo_post: marked %d modals as finished", count)
            from ..operators.object_tool_op import _schedule_object_tool_relaunch
            _schedule_object_tool_relaunch(delay_seconds=0.1)
    except Exception:  # noqa: BLE001
        _logger.exception("undo_post: mark_all_externally_finished failed")


def register() -> None:
    """ハンドラを重複なく登録."""
    # 既存の同名ハンドラを除去 (reload 対策)
    _remove_named_handler(bpy.app.handlers.load_post, _bmanga_on_load_post.__name__)
    _remove_named_handler(bpy.app.handlers.save_pre, _bmanga_on_save_pre.__name__)
    _remove_named_handler(bpy.app.handlers.save_post, _bmanga_on_save_post.__name__)
    _remove_named_handler(bpy.app.handlers.undo_post, _bmanga_on_undo_post.__name__)
    _remove_named_handler(bpy.app.handlers.redo_post, _bmanga_on_undo_post.__name__)
    bpy.app.handlers.load_post.append(_bmanga_on_load_post)
    bpy.app.handlers.save_pre.append(_bmanga_on_save_pre)
    bpy.app.handlers.save_post.append(_bmanga_on_save_post)
    bpy.app.handlers.undo_post.append(_bmanga_on_undo_post)
    bpy.app.handlers.redo_post.append(_bmanga_on_undo_post)
    _logger.debug("handlers registered")


def schedule_current_file_sync(retries: int = 3, interval: float = 0.15) -> None:
    """アドオン再読込時に、現在開いている B-MANGA .blend を load_post 相当に同期する."""
    global _current_file_sync_generation
    _current_file_sync_generation += 1
    generation = _current_file_sync_generation
    state = {"left": max(1, int(retries))}

    def _tick():
        if generation != _current_file_sync_generation:
            return None
        try:
            _bmanga_on_load_post(str(getattr(bpy.data, "filepath", "") or ""))
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
    try:
        from ..core.work import get_work as _get_work
        from . import page_content_visibility

        page_content_visibility.restore_all_virtual_hidden(
            bpy.context,
            _get_work(bpy.context),
        )
    except Exception:  # noqa: BLE001
        pass
    _remove_named_handler(bpy.app.handlers.load_post, _bmanga_on_load_post.__name__)
    _remove_named_handler(bpy.app.handlers.save_pre, _bmanga_on_save_pre.__name__)
    _remove_named_handler(bpy.app.handlers.save_post, _bmanga_on_save_post.__name__)
    _remove_named_handler(bpy.app.handlers.undo_post, _bmanga_on_undo_post.__name__)
    _remove_named_handler(bpy.app.handlers.redo_post, _bmanga_on_undo_post.__name__)
    _logger.debug("handlers unregistered")
