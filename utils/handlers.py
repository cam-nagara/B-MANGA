"""bpy.app.handlers ハンドラ.

``load_post``: .blend ファイル open 後に、B-MANGA 作品フォルダ配下の
.blend であれば work.json / pages.json を再読み込みして Scene プロパティを
同期する。また、開かれた .blend のパスから active_page_index と
bmanga_current_coma_id を自動推定する。

これにより、ページ切替 (page.blend 差替) 時に JSON メタが正しく維持され、
古い .blend 内に残っていた Scene プロパティが上書きされる。
"""

from __future__ import annotations

import functools
from pathlib import Path

import bpy
from bpy.app.handlers import persistent

from . import log, paths

_logger = log.get_logger(__name__)

_current_file_sync_generation = 0
_saving_work_metadata = False
_native_save_token = None
_native_save_reload_generation = 0
# 保存トランザクションの一時退避と再読込タイマーが重なる競合窓を吸収する
# ためのリトライ回数・間隔。上限到達後は作品ファイルへフォールバックする。
_NATIVE_SAVE_RELOAD_MAX_ATTEMPTS = 10
_NATIVE_SAVE_RELOAD_RETRY_INTERVAL = 0.2
_NATIVE_SAVE_RELOAD_FIRST_INTERVAL = 0.15


def _suspend_keymap_for_native_reload(*, disable_now: bool) -> None:
    """保存復旧のmainfile切替とBlenderのキーマップ更新を重ねない."""
    try:
        from ..keymap import keymap as _keymap

        _keymap.suspend_visibility_updates(
            6.0,
            reason="native save recovery",
            disable_now=disable_now,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("native save recovery keymap suspension failed")


def _native_save_memory_version(scene, work) -> int:
    """現在開いているファイル自身が保持する詳細データ版を返す."""

    from . import layer_uid, page_file_scene

    role, _page_id, _coma_id = page_file_scene.current_role(bpy.context)
    if role == page_file_scene.ROLE_PAGE:
        return layer_uid.scene_detail_data_version(scene)
    return layer_uid.detail_data_version_for_save(work)


def _show_native_save_notice(*, title: str, lines: tuple[str, ...]) -> None:
    if bpy.app.background:
        return

    def _draw(menu, _context):
        for line in lines:
            menu.layout.label(text=str(line))

    try:
        bpy.context.window_manager.popup_menu(_draw, title=title, icon="ERROR")
    except Exception:  # noqa: BLE001
        _logger.exception("native save notice failed")


def _reload_fallback_target(path: Path) -> Path | None:
    """再読込対象が見つからない時に代わりに開く作品ファイルを返す.

    復旧済みページファイルはトランザクションの一時退避と競合して直後の
    数百msだけ消えていることがある (リトライで通常はここへ来ない)。
    リトライ上限を超えてもまだ無い場合だけ、行き止まりを避けて作品ファイル
    (ページ一覧) へ逃がす。work.blend 自身の再読込失敗はフォールバック先が
    無いため、従来どおり行き止まりダイアログになる。
    """
    work_root = _find_work_root(path)
    if work_root is None:
        return None
    candidate = work_root / paths.WORK_BLEND_NAME
    if not candidate.is_file():
        return None
    try:
        if candidate.resolve(strict=False) == path.resolve(strict=False):
            return None
    except OSError:
        return None
    return candidate


def _open_native_reload_target(path: Path) -> None:
    _suspend_keymap_for_native_reload(disable_now=False)
    try:
        result = bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
        if "FINISHED" not in result:
            raise RuntimeError("最新の作品データを再読込できませんでした")
    finally:
        _suspend_keymap_for_native_reload(disable_now=False)


def _reload_missing_target(path: Path, state: dict, *, last_error: str = "") -> float | None:
    """再読込対象が未出現・一時読込不能の間の待機処理."""

    state["attempts"] += 1
    if state["attempts"] < _NATIVE_SAVE_RELOAD_MAX_ATTEMPTS:
        return _NATIVE_SAVE_RELOAD_RETRY_INTERVAL
    fallback = _reload_fallback_target(path)
    if fallback is not None:
        try:
            _open_native_reload_target(fallback)
            _show_native_save_notice(
                title="作品ファイルを開き直しました",
                lines=(
                    "最新のページファイルが見つからないため、作品ファイル（ページ一覧）を開き直しました。",
                    "ページはページ一覧から開き直すと再構築されます。",
                ),
            )
            return None
        except Exception:  # noqa: BLE001
            _logger.exception("native save recovery fallback reload failed")
    _show_native_save_notice(
        title="再読込に失敗しました",
        lines=(
            "この画面では保存せず、Blenderを閉じて作品を開き直してください。",
            last_error or "最新のページファイルが見つかりませんでした。",
        ),
    )
    return None


def _native_save_reload_tick(path: Path, generation: int, state: dict) -> float | None:
    """予約された再読込を1回分実行する (クロージャでなくモジュール関数化してテスト可能に)."""

    if generation != _native_save_reload_generation:
        return None
    origin = str(state.get("origin", "") or "")
    current = str(getattr(bpy.data, "filepath", "") or "")
    if origin and current:
        try:
            if Path(current).resolve(strict=False) != Path(origin).resolve(strict=False):
                return None
        except OSError:
            return None
    if not path.is_file():
        return _reload_missing_target(path, state)
    try:
        _open_native_reload_target(path)
    except Exception as exc:  # noqa: BLE001
        _logger.exception("native save recovery reload failed")
        return _reload_missing_target(path, state, last_error=str(exc))
    return None


def _schedule_native_save_reload(path: Path, *, notice: bool = True) -> None:
    """旧画面の保存結果を戻した後、復旧済みファイルを安全に再読込する."""

    global _native_save_reload_generation
    _native_save_reload_generation += 1
    generation = _native_save_reload_generation
    if notice:
        _show_native_save_notice(
            title="最新の作品データを保護しました",
            lines=(
                "古い画面からの保存を取り消しました。",
                "最新の作品データを再読込します。",
            ),
        )

    # 保存/選択イベントの同じ処理単位ではキーマップを書き換えず、先に停止を
    # 確定してから次のイベントループでmainfileを開く。
    _suspend_keymap_for_native_reload(disable_now=True)
    state = {
        "attempts": 0,
        "origin": str(getattr(bpy.data, "filepath", "") or ""),
    }
    bpy.app.timers.register(
        functools.partial(_native_save_reload_tick, path, generation, state),
        first_interval=_NATIVE_SAVE_RELOAD_FIRST_INTERVAL,
    )


def _begin_native_save_guard(filepath_arg=None) -> bool | None:
    """ネイティブ保存を保護する。

    旧画面なら ``False``、B-MANGA作品の現行保存なら ``True``、通常の
    Blenderファイルなど保護対象外なら ``None`` を返す。
    """

    global _native_save_token
    from ..core.work import get_work
    from ..io import project_content_native_save_guard

    force_current_restore = False
    if _native_save_token is not None:
        # 例外的に前回のsave_postが届かなかった場合も、ロックを残さない。
        previous_source = _native_save_token.source
        previous = project_content_native_save_guard.finish_native_save(
            _native_save_token
        )
        _native_save_token = None
        if previous.reload_required:
            _schedule_native_save_reload(previous_source)
            # save_preのreturn/例外では今回のBlender本体保存は止まらない。
            # 復旧済み内容のreload前に始まった今回分も、改めて退避して戻す。
            force_current_restore = True
    scene = getattr(bpy.context, "scene", None)
    work = get_work(bpy.context)
    # save_as_mainfile中は bpy.data.filepath が切替前の元ファイルを指す場合が
    # ある。handler引数は今回Blenderが実際に書く保存先なので、こちらを優先
    # し、通常Ctrl+Sなど引数が空の環境だけ現在ファイルへフォールバックする。
    filepath = ""
    if isinstance(filepath_arg, (str, bytes, Path)):
        try:
            filepath = str(filepath_arg.decode() if isinstance(filepath_arg, bytes) else filepath_arg)
        except (UnicodeDecodeError, OSError):
            filepath = ""
    filepath = filepath.strip() or str(getattr(bpy.data, "filepath", "") or "")
    if scene is None or work is None or not filepath:
        return None
    work_dir_text = str(getattr(work, "work_dir", "") or "").strip()
    if not work_dir_text:
        return None
    work_dir = Path(work_dir_text).resolve(strict=False)
    try:
        Path(filepath).resolve(strict=False).relative_to(work_dir)
    except ValueError:
        # アドオン登録中でも、作品外の通常blend保存へ作品用のJSON/PNG
        # トランザクションを持ち込まない。
        return None
    if work_dir.suffix != paths.BMANGA_DIR_SUFFIX or not work_dir.is_dir():
        return None
    memory_version = _native_save_memory_version(scene, work)
    if work_dir_text:
        try:
            from ..io import project_content_migration

            if project_content_migration.find_incomplete_journals(work_dir_text):
                # 未完了トランザクション中のpageを通常保存でunknown hashへ
                # 変えない。旧版0同士の通常保存はこの分岐に入らず許可する。
                memory_version = -1
        except Exception:  # 壊れた記録や列挙失敗も保存側へ通さない
            memory_version = -1
    _native_save_token = project_content_native_save_guard.begin_native_save(
        filepath,
        memory_version,
    )
    if force_current_restore:
        project_content_native_save_guard.force_native_save_restore(
            _native_save_token,
            reason="前回保存の復旧後、再読込前に保存が始まりました",
        )
    return not bool(
        _native_save_token is not None and _native_save_token.requires_restore
    )


def _mark_native_save_metadata_result(succeeded: bool, *, error: str = "") -> None:
    from ..io import project_content_native_save_guard

    project_content_native_save_guard.mark_native_save_metadata_result(
        _native_save_token,
        succeeded,
        error=error,
    )


def _finish_native_save_guard(*, native_save_succeeded: bool = True):
    """保存ガードを解放し、復旧の要否と対象ファイルを返す."""

    global _native_save_token
    from ..io import project_content_native_save_guard

    token = _native_save_token
    _native_save_token = None
    if token is None:
        return project_content_native_save_guard.finish_native_save(
            None,
            native_save_succeeded=native_save_succeeded,
        ), None
    source = token.source
    result = project_content_native_save_guard.finish_native_save(
        token,
        native_save_succeeded=native_save_succeeded,
    )
    return result, source


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


def _loaded_page_json_paths(work, work_dir: Path) -> list[Path]:
    page_paths = []
    for page in getattr(work, "pages", []) or []:
        page_id = str(getattr(page, "id", "") or "")
        if page_id and bool(getattr(page, "detail_loaded", False)):
            page_paths.append(paths.page_meta_path(work_dir, page_id))
    return page_paths


def _raster_sidecar_paths(scene, work_dir: Path) -> list[Path]:
    raster_paths = []
    for entry in getattr(scene, "bmanga_raster_layers", []) or []:
        raster_id = str(getattr(entry, "id", "") or "")
        relative = str(getattr(entry, "filepath_rel", "") or "")
        if not relative and raster_id:
            relative = f"{paths.RASTER_DIR_NAME}/{raster_id}.png"
        if not relative:
            continue
        candidate = (work_dir / relative).resolve(strict=False)
        try:
            candidate.relative_to(work_dir.resolve(strict=True))
        except ValueError as exc:
            raise RuntimeError("ラスター画像の保存先が作品フォルダー外です") from exc
        raster_paths.append(candidate)
    return raster_paths


def _native_sidecar_paths(work, work_dir: Path) -> tuple[Path, ...]:
    from ..operators import raster_layer_op

    return tuple(
        [work_dir / "work.json", work_dir / "pages.json"]
        + _loaded_page_json_paths(work, work_dir)
        + list(raster_layer_op.dirty_raster_paths(bpy.context))
    )


def _capture_native_save_baseline(work, work_dir: Path, blend_path: Path) -> None:
    """読込済み範囲のsidecarと現在blendを同一画面競合の基準にする."""

    from ..io import project_content_save_baseline

    page_paths = _loaded_page_json_paths(work, work_dir)
    raster_paths = _raster_sidecar_paths(getattr(bpy.context, "scene", None), work_dir)
    project_content_save_baseline.capture_loaded_baseline(
        work_dir,
        blend_path,
        page_json_paths=page_paths,
        content_paths=raster_paths,
    )


def _prepare_native_save_sidecars() -> None:
    from ..core.work import get_work
    from ..io import project_content_native_save_guard

    work = get_work(bpy.context)
    work_dir = Path(str(getattr(work, "work_dir", "") or ""))
    if work is None or not work_dir.is_dir():
        raise RuntimeError("作品情報の保存先がありません")
    project_content_native_save_guard.prepare_native_save_sidecars(
        _native_save_token,
        _native_sidecar_paths(work, work_dir),
    )


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


def _restore_expected_basic_frame_coma(work, work_dir: Path, page_id: str) -> None:
    if work is None or not page_id:
        return
    page = next(
        (entry for entry in getattr(work, "pages", []) or [] if str(getattr(entry, "id", "") or "") == page_id),
        None,
    )
    if page is None or len(getattr(page, "comas", []) or []) > 0:
        return
    if int(getattr(page, "coma_count", 0) or 0) <= 0:
        return
    try:
        from ..operators.coma_op import create_basic_frame_coma
        from ..io import page_io

        create_basic_frame_coma(work, page, work_dir)
        page_io.save_pages_json(work_dir, work)
    except Exception:  # noqa: BLE001
        _logger.exception("restore expected basic frame coma failed: %s", page_id)


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

        # 手描きObjectの描画点はローカル座標のまま保ち、現在のページ配置を記録する
        page_grid.reconcile_gp_strokes_with_page_offset(context, work)
    except Exception:  # noqa: BLE001
        _logger.exception("gp page-offset reconcile failed")
    return work


def save_scene_work_to_disk(
    context,
    *,
    reason: str = "",
    strict_rasters: bool = False,
) -> bool:
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
        try:
            from . import object_state_sync

            scene = getattr(context, "scene", None)
            if scene is not None:
                for obj in bpy.data.objects:
                    object_state_sync.sync_from_blender_object(scene, obj)
        except Exception:  # noqa: BLE001
            _logger.exception("object state save writeback failed")
        page_range.update_page_range_visibility(work)
        try:
            from . import view_settings

            view_settings.copy_scene_to_work(getattr(context, "scene", None), work)
        except Exception:  # noqa: BLE001
            _logger.exception("view settings save failed")
        try:
            from ..operators import raster_layer_op

            raster_layer_op.save_dirty_raster_layers(
                context,
                strict=strict_rasters,
            )
        except Exception:  # noqa: BLE001
            _logger.exception("raster dirty save failed")
            if strict_rasters:
                raise
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


def save_legacy_scene_sidecars(context, *, reason: str = "") -> bool:
    """旧版0同士のCtrl+S用。新形式同期をせず既存sidecarだけ厳格保存する."""

    global _saving_work_metadata
    if _saving_work_metadata:
        return False
    try:
        from ..core.work import get_work
        from ..io import page_io, work_io
        from ..operators import raster_layer_op

        work = get_work(context)
        work_dir = Path(str(getattr(work, "work_dir", "") or ""))
        if (
            work is None
            or not work_dir.is_dir()
        ):
            return False
        _saving_work_metadata = True
        raster_layer_op.save_dirty_raster_layers(context, strict=True)
        work_io.save_work_json(work_dir, work)
        page_io.save_pages_json(work_dir, work)
        for page in getattr(work, "pages", []) or []:
            if (
                str(getattr(page, "id", "") or "")
                and bool(getattr(page, "detail_loaded", True))
            ):
                page_io.save_page_json(work_dir, page)
        _logger.info("legacy sidecars saved%s", f" ({reason})" if reason else "")
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("legacy sidecar save failed%s", f" ({reason})" if reason else "")
        return False
    finally:
        _saving_work_metadata = False


def _hide_legacy_overlay_objects() -> None:
    _PREFIXES = (
        "page_paper_guide_",
        "page_safe_area_fill_",
        "page_bleed_outer_fill_",
        "work_info_text_",
        "page_preview_",
    )
    for obj in bpy.data.objects:
        name = obj.name
        if any(name.startswith(p) for p in _PREFIXES):
            try:
                obj.hide_viewport = True
            except Exception:  # noqa: BLE001
                pass


def _reconcile_gpencil_collections(context, work, *, include_page_content: bool = True) -> None:
    """個別管理ObjectとページCollectionの配置を再整合する。"""
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

    try:
        page_grid.apply_page_collection_transforms(context, work)
    except Exception:  # noqa: BLE001
        _logger.exception("load_post: apply_page_collection_transforms failed")
    try:
        _hide_legacy_overlay_objects()
    except Exception:  # noqa: BLE001
        _logger.exception("load_post: hide legacy overlay objects failed")
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
        # ページ変換・検証ワーカーは親トランザクションの所有下で対象blendを
        # 開く。通常load_postの自動復旧を走らせると、その親処理を自己rollback
        # するため、明示トークンを持つワーカーでは通常同期ごと抑止する。
        from ..operators import detail_data_migration_op

        if detail_data_migration_op.migration_worker_owns_runtime():
            _logger.info("load_post: detail migration worker owns opened blend")
            return
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
        try:
            from ..io import project_content_native_save_guard

            restored_paths = project_content_native_save_guard.recover_pending_native_saves(
                work_dir
            )
        except Exception:  # noqa: BLE001
            _logger.exception("load_post: native save recovery failed")
            try:
                scene.bmanga_work.loaded = False
            except Exception:  # noqa: BLE001
                pass
            _show_native_save_notice(
                title="作品データの復旧に失敗しました",
                lines=(
                    "この画面では保存せず、Blenderを閉じて作品を開き直してください。",
                ),
            )
            return
        if blend_path.resolve() in {path.resolve() for path in restored_paths}:
            # 異常終了前の旧画面が書いたファイルを元へ戻したため、メモリ上の
            # 旧内容を通常同期へ流さず、復旧済みファイルから読み直す。
            _schedule_native_save_reload(blend_path, notice=True)
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
            _capture_native_save_baseline(work, work_dir, blend_path)
        except Exception:  # noqa: BLE001
            _logger.exception("load_post: save baseline capture failed")
            try:
                work.loaded = False
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            from ..operators import detail_data_migration_op

            if detail_data_migration_op.work_requires_detail_migration(work):
                # 旧構造を読み込んだまま通常Operatorを使うと、新経路で上書き
                # され得る。確認画面を閉じてもpollが通らない状態を維持する。
                detail_data_migration_op.enforce_detail_migration_gate(work)
                detail_data_migration_op.schedule_migration_prompt(bpy.context)
                _logger.info("load_post: waiting for detail data migration confirmation")
                return
        except Exception:  # noqa: BLE001
            _logger.exception("load_post: detail data migration gate failed")
            return
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
                _restore_expected_basic_frame_coma(work, work_dir, str(rel.parts[0]))
                _reconcile_gpencil_collections(bpy.context, work, include_page_content=True)
                try:
                    from . import balloon_curve_object

                    balloon_curve_object.prewarm_balloon_resources()
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: balloon resource preparation failed")
                try:
                    from . import page_file_scene

                    page_file_scene.purge_other_page_data(scene, str(rel.parts[0]))
                    page_file_scene.resync_page_runtime_objects(scene, work, str(rel.parts[0]))
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: purge other page data failed")
                try:
                    from . import layer_stack as _layer_stack

                    _layer_stack.sync_layer_stack_after_data_change(bpy.context)
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: page layer order refresh failed")
                try:
                    from . import page_preview_object

                    page_preview_object.highlight_preview_page(scene, work, None)
                    page_preview_object.sync_page_previews(bpy.context, work, force=True)
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
        # 移行ワーカーが保存するのは作品外の一時 page.blend。コピー元sceneに
        # 残る work_dir で通常保存を走らせると、全件検証前の元作品へ
        # work/page/rasterを書き戻すため、ワーカーでは全保存副作用を止める。
        from ..operators import detail_data_migration_op

        if detail_data_migration_op.migration_worker_owns_runtime():
            _logger.info("save_pre: detail migration worker owns staged blend")
            return
        guard_started = _begin_native_save_guard(filepath_arg)
        if guard_started is None:
            return
        if not guard_started:
            # Blenderはsave_pre例外を無視して本体保存を続行する。既存ファイルは
            # 退避済みなので、B-MANGA側のJSONや画像を旧画面から書き戻さない。
            _logger.warning("save_pre: stale detail data save will be restored")
            return
        try:
            # 最初のJSON/PNG書込みより前に全対象と元blendを一括退避する。
            _prepare_native_save_sidecars()
        except Exception:  # noqa: BLE001
            _logger.exception("save_pre: sidecar transaction prepare failed")
            try:
                _mark_native_save_metadata_result(
                    False,
                    error="作品情報の保存準備が完了しませんでした",
                )
            except Exception:  # noqa: BLE001
                _logger.exception("save_pre: sidecar prepare recovery arm failed")
            return
        try:
            from ..core.work import get_work as _get_work

            if detail_data_migration_op.work_requires_detail_migration(
                _get_work(bpy.context)
            ):
                # 旧版0同士でも未保存のJSON/PNGを失わせない。新形式のmirror等は
                # 走らせず、既存sidecarだけをstrict保存してから本体保存を許可。
                metadata_saved = save_legacy_scene_sidecars(
                    bpy.context,
                    reason="legacy_save_pre",
                )
                _mark_native_save_metadata_result(
                    metadata_saved,
                    error="旧形式の作品情報またはラスター画像を保存できませんでした",
                )
                if not metadata_saved:
                    _logger.warning("save_pre: failed legacy sidecars will restore blend")
                else:
                    _logger.info("save_pre: legacy detail data sidecars saved")
                return
        except Exception:  # 判定不能時もB-MANGA側の書込みだけを止める
            _logger.exception("save_pre: detail migration state check failed")
            return
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
        metadata_saved = save_scene_work_to_disk(
            bpy.context,
            reason="save_pre",
            strict_rasters=True,
        )
        _mark_native_save_metadata_result(
            metadata_saved,
            error="作品情報またはラスター画像を保存できませんでした",
        )
        if not metadata_saved:
            _logger.warning("save_pre: metadata failure will restore blend")
    except Exception:  # noqa: BLE001
        _logger.exception("B-MANGA save_pre handler failed")
        try:
            _mark_native_save_metadata_result(
                False,
                error="保存前処理が完了しませんでした",
            )
        except Exception:  # noqa: BLE001
            _logger.exception("save_pre: failed to arm recovery after handler error")


@persistent
def _bmanga_on_save_post(filepath_arg) -> None:  # signature: (str,) in Blender handlers
    """保存後にページ一覧用の軽量表示を戻す."""
    try:
        from ..operators import detail_data_migration_op

        if detail_data_migration_op.migration_worker_owns_runtime():
            return
        save_result, source = _finish_native_save_guard(
            native_save_succeeded=True,
        )
        if save_result.reload_required and source is not None:
            _schedule_native_save_reload(source)
            return
        try:
            from ..core.work import get_work as _get_work

            if detail_data_migration_op.work_requires_detail_migration(
                _get_work(bpy.context)
            ):
                return
        except Exception:  # noqa: BLE001
            _logger.exception("save_post: detail migration state check failed")
            return
        try:
            from . import cross_page_transfer

            cross_page_transfer.commit_staged_imports_after_save(
                bpy.context,
                blend_path=source or str(getattr(bpy.data, "filepath", "") or ""),
                metadata_saved=save_result.metadata_saved,
                native_save_succeeded=(
                    save_result.native_save_succeeded and not save_result.restored
                ),
            )
        except Exception:  # noqa: BLE001
            _logger.exception("save_post: staged imports commit failed")
        from . import page_content_visibility

        page_content_visibility.schedule_apply(bpy.context)
    except Exception:  # noqa: BLE001
        _logger.exception("B-MANGA save_post handler failed")


@persistent
def _bmanga_on_save_post_fail(*_args) -> None:
    """保存失敗時も作品ロックを解放し、退避した最新ファイルを戻す."""

    try:
        from ..operators import detail_data_migration_op

        if detail_data_migration_op.migration_worker_owns_runtime():
            return
        save_result, source = _finish_native_save_guard(
            native_save_succeeded=False,
        )
        if save_result.reload_required and source is not None:
            _schedule_native_save_reload(source)
    except Exception:  # noqa: BLE001
        _logger.exception("B-MANGA save_post_fail handler failed")
        _show_native_save_notice(
            title="保存後の復旧に失敗しました",
            lines=(
                "この画面では保存せず、Blenderを閉じて作品を開き直してください。",
            ),
        )


def _remove_named_handler(handler_list, name: str) -> None:
    for h in list(handler_list):
        if getattr(h, "__name__", "") == name:
            try:
                handler_list.remove(h)
            except ValueError:
                pass


@persistent
def _bmanga_on_undo_pre(*_args) -> None:
    """Undo/Redo が RNA を差し替える前に監視とモーダル参照を止める."""
    try:
        from ..operators import coma_modal_state as _modal_state
        from . import history_runtime

        count = _modal_state.mark_all_externally_finished()
        history_runtime.begin_restore(relaunch_object_tool=count > 0)
        if count > 0:
            _logger.debug("undo_pre: marked %d modals as finished", count)
    except Exception:  # noqa: BLE001
        _logger.exception("undo_pre: restore guard failed")


@persistent
def _bmanga_on_undo_post(*_args) -> None:
    """Undo/Redo 後の次イベントループで B-MANGA 実体を再同期する."""
    try:
        from ..operators import coma_modal_state as _modal_state
        from . import history_runtime

        count = _modal_state.mark_all_externally_finished()
        if not history_runtime.is_restoring():
            history_runtime.begin_restore(relaunch_object_tool=count > 0)
        elif count > 0:
            history_runtime.request_object_tool_relaunch()
        history_runtime.schedule_reconcile()
    except Exception:  # noqa: BLE001
        _logger.exception("undo_post: deferred reconcile failed")


def register() -> None:
    """ハンドラを重複なく登録."""
    # 既存の同名ハンドラを除去 (reload 対策)
    _remove_named_handler(bpy.app.handlers.load_post, _bmanga_on_load_post.__name__)
    _remove_named_handler(bpy.app.handlers.save_pre, _bmanga_on_save_pre.__name__)
    _remove_named_handler(bpy.app.handlers.save_post, _bmanga_on_save_post.__name__)
    save_post_fail = getattr(bpy.app.handlers, "save_post_fail", None)
    if save_post_fail is not None:
        _remove_named_handler(save_post_fail, _bmanga_on_save_post_fail.__name__)
    _remove_named_handler(bpy.app.handlers.undo_pre, _bmanga_on_undo_pre.__name__)
    _remove_named_handler(bpy.app.handlers.redo_pre, _bmanga_on_undo_pre.__name__)
    _remove_named_handler(bpy.app.handlers.undo_post, _bmanga_on_undo_post.__name__)
    _remove_named_handler(bpy.app.handlers.redo_post, _bmanga_on_undo_post.__name__)
    bpy.app.handlers.load_post.append(_bmanga_on_load_post)
    bpy.app.handlers.save_pre.append(_bmanga_on_save_pre)
    bpy.app.handlers.save_post.append(_bmanga_on_save_post)
    if save_post_fail is not None:
        save_post_fail.append(_bmanga_on_save_post_fail)
    bpy.app.handlers.undo_pre.append(_bmanga_on_undo_pre)
    bpy.app.handlers.redo_pre.append(_bmanga_on_undo_pre)
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
    global _current_file_sync_generation, _native_save_reload_generation
    _current_file_sync_generation += 1
    _native_save_reload_generation += 1
    try:
        _finish_native_save_guard()
    except Exception:  # noqa: BLE001
        _logger.exception("native save guard release during unregister failed")
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
    save_post_fail = getattr(bpy.app.handlers, "save_post_fail", None)
    if save_post_fail is not None:
        _remove_named_handler(save_post_fail, _bmanga_on_save_post_fail.__name__)
    _remove_named_handler(bpy.app.handlers.undo_pre, _bmanga_on_undo_pre.__name__)
    _remove_named_handler(bpy.app.handlers.redo_pre, _bmanga_on_undo_pre.__name__)
    _remove_named_handler(bpy.app.handlers.undo_post, _bmanga_on_undo_post.__name__)
    _remove_named_handler(bpy.app.handlers.redo_post, _bmanga_on_undo_post.__name__)
    _logger.debug("handlers unregistered")
