"""bpy.app.handlers ハンドラ.

``load_post``: .blend ファイル open 後に、B-MANGA 作品フォルダ配下の
.blend であれば project.json / page.json を再読み込みして Scene プロパティを
同期する。また、開かれた .blend のパスから active_page_index と
bmanga_current_coma_id を自動推定する。

これにより、ページ切替 (page.blend 差替) 時に JSON メタが正しく維持され、
古い .blend 内に残っていた Scene プロパティが上書きされる。
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import zlib
from array import array

import bpy
from bpy.app.handlers import persistent

from . import log, paths

_logger = log.get_logger(__name__)

_saving_work_metadata = False
_suppress_work_metadata_save_depth = 0
_native_save_token = None
_trusted_native_save_targets: list[str] = []
# 保存トランザクションの一時退避と再読込タイマーが重なる競合窓を吸収する
# ためのリトライ回数・間隔。上限到達後は作品ファイルへフォールバックする。
_NATIVE_SAVE_RELOAD_MAX_ATTEMPTS = 10
_NATIVE_SAVE_RELOAD_RETRY_INTERVAL = 0.2
_NATIVE_SAVE_RELOAD_FIRST_INTERVAL = 0.15
_TRANSFER_ORPHAN_RECOVERY_TASK = "layer_transfer_orphan_recovery"
_TRANSFER_ORPHAN_RECOVERY_FIRST_INTERVAL = 0.25


@contextmanager
def suppress_work_metadata_save():
    """明示checkpoint済みのnative初回保存ではsave_pre再保存を止める。"""

    global _suppress_work_metadata_save_depth
    _suppress_work_metadata_save_depth += 1
    try:
        yield
    finally:
        _suppress_work_metadata_save_depth = max(
            0, _suppress_work_metadata_save_depth - 1
        )


def _native_path_key(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve(strict=False)))


@contextmanager
def trusted_native_save_target(path: str | Path):
    """B-MANGA内部が意図して別の正規mainfileを生成する保存だけを許可する."""

    key = _native_path_key(path)
    _trusted_native_save_targets.append(key)
    try:
        yield
    finally:
        if _trusted_native_save_targets and _trusted_native_save_targets[-1] == key:
            _trusted_native_save_targets.pop()
        else:
            # 例外的な入れ子順序の崩れでも、許可が後続操作へ漏れないようにする。
            for index in range(len(_trusted_native_save_targets) - 1, -1, -1):
                if _trusted_native_save_targets[index] == key:
                    del _trusted_native_save_targets[index]
                    break


def _native_save_target_is_trusted(path: str | Path) -> bool:
    return _native_path_key(path) in _trusted_native_save_targets


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


def _mark_transfer_recovery_failed(scene) -> None:
    try:
        scene.bmanga_work.loaded = False
    except Exception:  # noqa: BLE001
        _logger.exception("load_post: transfer recovery fail-closed failed")
    _show_native_save_notice(
        title="ページ間移動の復旧に失敗しました",
        lines=(
            "この画面では保存せず、Blenderを閉じて作品を開き直してください。",
        ),
    )


def _schedule_transfer_orphan_recovery(work_dir: Path) -> None:
    """通常openを止めず、journal未作成のprepared stageだけ後で清掃する."""

    from . import lifecycle_scheduler, layer_transfer_group

    expected_root = Path(work_dir).resolve()

    def _recover() -> None:
        try:
            restored_paths = layer_transfer_group.recover_interrupted_transfers(
                expected_root
            )
        except Exception:  # noqa: BLE001
            _logger.exception(
                "deferred page transfer recovery failed: %s",
                expected_root,
            )
            current_path = Path(str(getattr(bpy.data, "filepath", "") or ""))
            current_root = _find_work_root(current_path)
            if current_root is not None and current_root.resolve() == expected_root:
                scene = getattr(bpy.context, "scene", None)
                if scene is not None:
                    _mark_transfer_recovery_failed(scene)
            return None
        current_path = Path(str(getattr(bpy.data, "filepath", "") or ""))
        if (
            current_path.is_file()
            and current_path.resolve() in {
                path.resolve() for path in restored_paths
            }
        ):
            _schedule_native_save_reload(current_path, notice=True)
        return None

    lifecycle_scheduler.schedule(
        _TRANSFER_ORPHAN_RECOVERY_TASK,
        _recover,
        first_interval=_TRANSFER_ORPHAN_RECOVERY_FIRST_INTERVAL,
    )


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


def _native_save_reload_tick(path: Path, state: dict) -> float | None:
    """予約された再読込を1回分実行する (クロージャでなくモジュール関数化してテスト可能に)."""

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
    from . import lifecycle_scheduler

    lifecycle_scheduler.schedule(
        "native_save_reload",
        lambda: _native_save_reload_tick(path, state),
        first_interval=_NATIVE_SAVE_RELOAD_FIRST_INTERVAL,
    )


def _begin_native_save_guard(filepath_arg=None) -> bool | None:
    """ネイティブ保存を保護する。

    旧画面なら ``False``、B-MANGA作品の現行保存なら ``True``、通常の
    Blenderファイルなど保護対象外なら ``None`` を返す。
    """

    global _native_save_token
    from ..core.work import get_work
    from ..io import native_save_guard

    force_current_restore = False
    if _native_save_token is not None:
        # 例外的に前回のsave_postが届かなかった場合も、ロックを残さない。
        previous_token = _native_save_token
        previous_source = (
            previous_token.reload_path or previous_token.source
        )
        previous = native_save_guard.finish_native_save(
            previous_token
        )
        _native_save_token = None
        _finalize_pending_native_checkpoint(
            previous_token,
            committed=bool(
                previous.metadata_saved
                and previous.native_save_succeeded
                and not previous.restored
            ),
        )
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
        from . import page_file_scene

        target_path = Path(filepath).resolve(strict=False)
        role, _page_id, _coma_id = page_file_scene.canonical_role_from_path(
            target_path,
            work_dir,
            require_exists=False,
        )
    except (OSError, RuntimeError, ValueError):
        role = "unknown"
    if role == "unknown":
        # 作品外または作品内の非正規別名コピーへ、正本用JSON/PNG
        # トランザクションを持ち込まない。
        return None
    if work_dir.suffix != paths.BMANGA_DIR_SUFFIX or not work_dir.is_dir():
        return None
    current_text = str(getattr(bpy.data, "filepath", "") or "").strip()
    current_path = (
        Path(current_text).resolve(strict=False)
        if current_text
        else None
    )
    same_mainfile = bool(
        current_path is not None
        and _native_path_key(current_path) == _native_path_key(target_path)
    )
    cross_mainfile_save = (
        not same_mainfile
        and not _native_save_target_is_trusted(target_path)
    )
    _native_save_token = native_save_guard.begin_native_save(
        target_path,
    )
    if _native_save_token is not None:
        from ..io import native_save_outcome

        native_save_outcome.clear(_native_save_token.source)
    if cross_mainfile_save:
        native_save_guard.force_native_save_restore(
            _native_save_token,
            reason=(
                "別の正規作品ファイルへの「名前を付けて保存」はできません。"
                "B-MANGAの画面操作で対象を開いてから保存してください"
            ),
            reload_path=(
                current_path
                if current_path is not None and current_path.is_file()
                else None
            ),
        )
        return False
    if force_current_restore:
        native_save_guard.force_native_save_restore(
            _native_save_token,
            reason="前回保存の復旧後、再読込前に保存が始まりました",
        )
    return not bool(
        _native_save_token is not None and _native_save_token.requires_restore
    )


def _mark_native_save_metadata_result(succeeded: bool, *, error: str = "") -> None:
    from ..io import native_save_guard

    if succeeded is not True and _native_save_token is not None:
        _capture_pending_raster_snapshots(_native_save_token.work_dir)
    native_save_guard.mark_native_save_metadata_result(
        _native_save_token,
        succeeded,
        error=error,
    )


def _finish_native_save_guard(*, native_save_succeeded: bool = True):
    """保存ガードを解放し、復旧の要否と対象ファイルを返す."""

    global _native_save_token
    from ..io import native_save_guard

    token = _native_save_token
    _native_save_token = None
    if token is None:
        return native_save_guard.finish_native_save(
            None,
            native_save_succeeded=native_save_succeeded,
        ), None
    source = token.source
    reload_source = token.reload_path or source
    if (
        token.requires_restore
        or not native_save_succeeded
        or (
            token.sidecar_token is not None
            and token.metadata_saved is not True
        )
    ):
        _capture_pending_raster_snapshots(token.work_dir)
    try:
        result = native_save_guard.finish_native_save(
            token,
            native_save_succeeded=native_save_succeeded,
        )
    except BaseException:
        _finalize_pending_native_checkpoint(token, committed=False)
        raise
    _finalize_pending_native_checkpoint(
        token,
        committed=bool(
            result.metadata_saved
            and result.native_save_succeeded
            and not result.restored
        ),
    )
    from ..io import native_save_outcome

    native_save_outcome.record(source, result)
    return result, reload_source


def _redirty_raster_ids(raster_ids: tuple[str, ...]) -> None:
    if not raster_ids:
        return
    from ..operators import raster_layer_op

    wanted = set(raster_ids)
    scene = getattr(bpy.context, "scene", None)
    for entry in getattr(scene, "bmanga_raster_layers", ()) or ():
        if str(getattr(entry, "id", "") or "") in wanted:
            raster_layer_op.mark_raster_dirty(entry)


def _capture_pending_raster_snapshots(work_dir: Path) -> None:
    from ..io import native_checkpoint_runtime
    from ..operators import raster_layer_op

    pending = native_checkpoint_runtime.pending_for(work_dir)
    if pending is None:
        return
    missing = tuple(
        raster_id
        for raster_id in pending.raster_ids
        if raster_id not in pending.raster_snapshots
    )
    if not missing:
        return
    try:
        snapshots = raster_layer_op.capture_raster_pixel_snapshots(
            bpy.context,
            missing,
            snapshot_dir=pending.snapshot_dir,
        )
    except Exception:  # noqa: BLE001
        # payloadを作れないImageは触らない。旧ディスクPNGで上書きすると、
        # 保存できなかった未保存画素まで失うため、復旧対象へ追加しない。
        _logger.exception("native rollback raster capture failed")
        return
    native_checkpoint_runtime.preserve_raster_snapshots(
        work_dir,
        snapshots,
    )


def _restore_pending_raster_pixels(
    pending,
    *,
    strict: bool = False,
) -> tuple[str, ...]:
    if not pending.raster_snapshots:
        return ()
    from ..operators import raster_layer_op

    scene = getattr(bpy.context, "scene", None)
    entries = {
        str(getattr(entry, "id", "") or ""): entry
        for entry in getattr(scene, "bmanga_raster_layers", ()) or ()
    }
    restored: list[str] = []
    failures: list[str] = []
    for raster_id, snapshot in pending.raster_snapshots.items():
        entry = entries.get(raster_id)
        if entry is None:
            failures.append(raster_id)
            continue
        try:
            image = raster_layer_op.ensure_raster_image(
                bpy.context,
                entry,
                create_missing=False,
            )
            if image is None:
                raise RuntimeError("raster image is not loaded")
            _validate_raster_snapshot_before_restore(
                snapshot,
                entry=entry,
                image=image,
            )
            expected = (
                int(snapshot.width)
                * int(snapshot.height)
                * int(snapshot.channels)
            )
            if tuple(image.size) != (snapshot.width, snapshot.height):
                image.scale(snapshot.width, snapshot.height)
            _restore_raster_snapshot_stream(image, snapshot, expected)
            image.update()
            restored.append(raster_id)
        except Exception:  # noqa: BLE001
            failures.append(raster_id)
            _logger.exception(
                "native rollback raster pixels restore failed: %s",
                raster_id,
            )
    if strict and failures:
        raise RuntimeError(
            "未保存ラスター画素を復旧できませんでした: "
            + ", ".join(sorted(failures))
        )
    return tuple(restored)


def _validate_raster_snapshot_before_restore(
    snapshot,
    *,
    entry,
    image,
) -> None:
    """寸法・容量・圧縮hashを検証してからImageの再確保を許可する。"""

    import hashlib

    from ..io import native_checkpoint_runtime
    from ..operators import raster_layer_op

    width = int(snapshot.width)
    height = int(snapshot.height)
    channels = int(snapshot.channels)
    raw_bytes = int(snapshot.raw_byte_count)
    if (
        width <= 0
        or height <= 0
        or width > native_checkpoint_runtime.MAX_RASTER_SNAPSHOT_DIMENSION
        or height > native_checkpoint_runtime.MAX_RASTER_SNAPSHOT_DIMENSION
        or channels <= 0
        or channels > native_checkpoint_runtime.MAX_RASTER_SNAPSHOT_CHANNELS
        or raw_bytes <= 0
        or raw_bytes > native_checkpoint_runtime.MAX_RASTER_SNAPSHOT_RAW_BYTES
        or raw_bytes != width * height * channels * 4
    ):
        raise RuntimeError("raster rollback snapshot dimensions are invalid")
    work = getattr(getattr(bpy.context, "scene", None), "bmanga_work", None)
    dpi = max(1, min(1200, int(getattr(entry, "dpi", 300) or 300)))
    paper_size = (
        raster_layer_op._raster_size_px(work, dpi)
        if work is not None
        else (0, 0)
    )
    current_size = tuple(int(value) for value in image.size)
    if (width, height) not in {tuple(paper_size), current_size}:
        raise RuntimeError("raster rollback snapshot size is not owned by layer")
    source = Path(snapshot.compressed_path)
    if not source.is_file():
        raise RuntimeError("raster rollback snapshot is missing")
    compressed_size = source.stat().st_size
    if compressed_size <= 0 or compressed_size > raw_bytes + 1024 * 1024:
        raise RuntimeError("raster rollback compressed size is invalid")
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != str(snapshot.compressed_sha256):
        raise RuntimeError("raster rollback snapshot digest is invalid")


def _restore_raster_snapshot_stream(image, snapshot, expected: int) -> None:
    """圧縮snapshotを固定サイズで展開し、Imageへ範囲代入する。"""

    import hashlib

    source = Path(snapshot.compressed_path)
    if not source.is_file():
        raise RuntimeError("raster rollback snapshot is missing")
    digest = hashlib.sha256()
    decompressor = zlib.decompressobj()
    carry = bytearray()
    value_offset = 0
    raw_byte_count = 0

    def _apply_raw(raw: bytes) -> None:
        nonlocal carry, value_offset, raw_byte_count
        if not raw:
            return
        raw_byte_count += len(raw)
        carry.extend(raw)
        aligned = len(carry) - (len(carry) % 4)
        if aligned <= 0:
            return
        pixels = array("f")
        pixels.frombytes(carry[:aligned])
        del carry[:aligned]
        stop = value_offset + len(pixels)
        if stop > expected:
            raise RuntimeError("raster rollback pixel buffer is oversized")
        image.pixels[value_offset:stop] = pixels
        value_offset = stop

    with source.open("rb") as handle:
        while compressed := handle.read(1024 * 1024):
            digest.update(compressed)
            pending = compressed
            while pending:
                raw = decompressor.decompress(
                    pending,
                    _RASTER_RESTORE_CHUNK_BYTES,
                )
                pending = decompressor.unconsumed_tail
                _apply_raw(raw)
                if not pending and raw:
                    while True:
                        buffered = decompressor.decompress(
                            b"",
                            _RASTER_RESTORE_CHUNK_BYTES,
                        )
                        if not buffered:
                            break
                        _apply_raw(buffered)
        _apply_raw(decompressor.flush())
    if (
        not decompressor.eof
        or carry
        or value_offset != expected
        or raw_byte_count != int(snapshot.raw_byte_count)
        or digest.hexdigest() != str(snapshot.compressed_sha256)
    ):
        raise RuntimeError("raster rollback pixel snapshot is invalid")


_RASTER_RESTORE_CHUNK_BYTES = 1024 * 1024


def _finalize_pending_native_checkpoint(token, *, committed: bool) -> None:
    """Native成否と同時にだけDomain/Rasterのclean状態を確定する。"""

    from ..io import (
        domain_runtime,
        native_checkpoint_runtime,
    )

    pending = native_checkpoint_runtime.take(token.work_dir)
    if pending is None:
        return
    try:
        if committed:
            store = domain_runtime.store_for(pending.work_dir)
            store.mark_checkpointed(
                project=pending.project_changed,
                page_uids=tuple(pending.page_uids),
            )
            native_checkpoint_runtime.cleanup_recovered_snapshot_transactions(
                pending.work_dir,
                token.source,
            )
            return
        _restore_pending_raster_pixels(pending)
        _redirty_raster_ids(pending.raster_ids)
        repository = domain_runtime.repository_for(pending.work_dir)
        repository.accept_recovered_files(pending.repository_paths)
    finally:
        native_checkpoint_runtime.cleanup_snapshot_transaction(
            pending.work_dir,
            pending.snapshot_dir,
        )


def _recover_crashed_raster_snapshots(
    work_dir: Path,
    blend_path: Path,
) -> tuple[str, ...]:
    """前回保存中断時の未保存画素を現在Imageへ戻し、dirtyを維持する。"""

    from ..io import native_checkpoint_runtime

    restored: list[str] = []
    for transaction in (
        native_checkpoint_runtime.recoverable_snapshot_transactions(
            work_dir,
            blend_path,
        )
    ):
        pending = native_checkpoint_runtime.PendingNativeCheckpoint(
            transaction.work_dir,
            (),
            tuple(transaction.raster_snapshots),
            raster_snapshots=transaction.raster_snapshots,
            snapshot_dir=transaction.snapshot_dir,
        )
        restored_ids = _restore_pending_raster_pixels(
            pending,
            strict=True,
        )
        if set(restored_ids) != set(transaction.raster_snapshots):
            raise RuntimeError(
                "未保存ラスターsnapshotを全件復旧できませんでした"
            )
        _redirty_raster_ids(tuple(transaction.raster_snapshots))
        native_checkpoint_runtime.mark_recoverable_snapshot_hydrated(
            transaction
        )
        restored.extend(restored_ids)
    return tuple(restored)


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


def _deactivate_noncanonical_work_copy(
    scene,
    blend_path: Path,
    *,
    work_dir: Path | None = None,
    require_domain: bool = True,
    notice: bool,
) -> bool:
    """作品内外を問わず非正規名のBlendをB-MANGAの正本にしない."""

    work = getattr(scene, "bmanga_work", None)
    if work is None or not bool(getattr(work, "loaded", False)):
        return False
    work_dir_text = str(
        work_dir
        if work_dir is not None
        else getattr(work, "work_dir", "")
        or ""
    ).strip()
    if not work_dir_text:
        return False
    try:
        from . import page_file_scene

        if require_domain:
            role, _page_id, _coma_id = page_file_scene.canonical_role_from_path(
                Path(blend_path),
                Path(work_dir_text),
            )
        else:
            role, _page_uid, _coma_uid = page_file_scene.role_from_parts(
                page_file_scene.relative_parts(
                    Path(blend_path),
                    Path(work_dir_text),
                )
            )
        if role != page_file_scene.ROLE_UNKNOWN:
            return False
    except (OSError, RuntimeError, ValueError):
        pass
    try:
        work.loaded = False
        if notice:
            _show_native_save_notice(
                title="正規の作品ファイルではないコピーです",
                lines=(
                    "このコピーはB-MANGA作品の正本ではありません。",
                    "編集を続けるには元の作品ファイルを開き直してください。",
                ),
            )
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("failed to deactivate noncanonical work copy")
        return False


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
        [paths.project_meta_path(work_dir)]
        + _loaded_page_json_paths(work, work_dir)
        + list(raster_layer_op.dirty_raster_paths(bpy.context))
    )


def _capture_native_save_baseline(work, work_dir: Path, blend_path: Path) -> None:
    """読込済み範囲のsidecarと現在blendを同一画面競合の基準にする."""

    from ..io import save_baseline

    page_paths = _loaded_page_json_paths(work, work_dir)
    content_paths = _raster_sidecar_paths(
        getattr(bpy.context, "scene", None), work_dir
    )
    save_baseline.capture_loaded_baseline(
        work_dir,
        blend_path,
        page_json_paths=page_paths,
        content_paths=content_paths,
    )


def _prepare_native_save_sidecars() -> None:
    from ..core.work import get_work
    from ..io import native_checkpoint_runtime, native_save_guard
    from ..operators import raster_layer_op

    work = get_work(bpy.context)
    work_dir = Path(str(getattr(work, "work_dir", "") or ""))
    if work is None or not work_dir.is_dir():
        raise RuntimeError("作品情報の保存先がありません")
    sidecar_paths = _native_sidecar_paths(work, work_dir)
    native_save_guard.prepare_native_save_sidecars(
        _native_save_token,
        sidecar_paths,
    )
    raster_ids = raster_layer_op.dirty_raster_ids(bpy.context)
    # 最初の本番PNG書込みより前に全dirty Imageを符号化する。複数枚の
    # 途中で1枚だけ保存失敗しても、未書込み側を旧PNGで上書きしない。
    transaction_id = str(
        getattr(_native_save_token, "transaction_id", "") or ""
    )
    snapshot_dir = native_checkpoint_runtime.create_snapshot_transaction(
        work_dir,
        transaction_id,
        source_path=getattr(_native_save_token, "source", None),
    )
    try:
        raster_snapshots = raster_layer_op.capture_raster_pixel_snapshots(
            bpy.context,
            raster_ids,
            snapshot_dir=snapshot_dir,
        )
        native_checkpoint_runtime.seal_snapshot_transaction(
            snapshot_dir,
            raster_snapshots,
        )
        native_checkpoint_runtime.begin(
            work_dir,
            repository_paths=(
                path
                for path in sidecar_paths
                if path.name in {"project.json", "page.json"}
            ),
            raster_ids=raster_ids,
            snapshot_dir=snapshot_dir,
        )
        native_checkpoint_runtime.preserve_raster_snapshots(
            work_dir,
            raster_snapshots,
        )
    except BaseException:
        native_checkpoint_runtime.cleanup_snapshot_transaction(
            work_dir,
            snapshot_dir,
        )
        raise


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
        from ..core.mode import MODE_PAGE, MODE_COMA, set_mode
        from . import page_file_scene
    except Exception:  # noqa: BLE001
        return
    role, page_id, coma_id = page_file_scene.role_from_path(
        blend_path,
        work_dir,
    )

    # work.blend 直下 → overview モード
    if role == page_file_scene.ROLE_WORK:
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

    if role == page_file_scene.ROLE_PAGE:
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

    if role == page_file_scene.ROLE_COMA:
        for i, pg in enumerate(work.pages):
            if pg.id == page_id:
                work.active_page_index = i
                for coma_index, coma in enumerate(pg.comas):
                    entry_id = str(
                        getattr(coma, "coma_id", "")
                        or getattr(coma, "id", "")
                        or ""
                    )
                    if entry_id == coma_id:
                        pg.active_coma_index = coma_index
                        break
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


def _reload_all_pages_panels(
    work,
    work_dir: Path,
    *,
    detail_filter: set[str] | None,
) -> None:
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

    for page_entry in work.pages:
        if not page_entry.id:
            continue
        if detail_filter is not None and page_entry.id not in detail_filter:
            page_detail.clear_page_detail(page_entry)
            continue
        try:
            page_io.load_page_json(
                work_dir,
                page_entry,
                allow_missing=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"required Domain page failed to load: {page_entry.id}"
            ) from exc


def _filter_embedded_page_details(work) -> None:
    """blend内キャッシュを現在のファイル役割で許されるページだけに絞る."""

    from . import page_detail

    detail_filter = _page_detail_filter()
    if detail_filter is None:
        return
    for page_entry in getattr(work, "pages", []) or []:
        page_id = str(getattr(page_entry, "id", "") or "")
        if page_id not in detail_filter:
            page_detail.clear_page_detail(page_entry)
        else:
            # detail_loadedはPhase 4から非保存cache。正規のpage/coma blendで
            # sidecar hashが一致した場合だけ、埋込済み詳細を現在世代として扱う。
            page_entry.detail_loaded = True


def _bind_embedded_domain_identifiers(
    work,
    project_document,
    page_documents,
) -> None:
    """Repositoryと一致済みの埋込cacheへ最新UID/revisionだけを束縛する."""

    from ..io import domain_projection

    domain_projection.bind_project_document(work, project_document)
    pages_by_display_id = {
        str(getattr(page, "id", "") or ""): page
        for page in getattr(work, "pages", ())
    }
    display_ids_by_uid = {
        summary.uid: summary.display_id
        for summary in project_document.pages
    }
    for document in page_documents:
        page = pages_by_display_id.get(
            display_ids_by_uid.get(document.page_uid, "")
        )
        if page is not None:
            domain_projection.bind_page_document(page, document)


def sync_scene_work_from_disk(context, work_dir: Path):
    """現在 scene の ``bmanga_work`` を disk 上の work/pages/page JSON に同期."""
    from ..core.work import get_work
    from ..io import page_io, work_io
    from . import view_settings

    work = get_work(context)
    if work is None:
        return None
    work.loaded = False
    # project.json は作品設定とページ順の両方を持つ。旧 work.json /
    # pages.json 時代の二段読込を残すと同じ Domain を二度 UI 投影し、
    # 二度目は work.loaded=True のため用紙設定 callback が全ページの
    # runtime を項目ごとに再生成する。project.json は一度だけ投影する。
    work_io.load_work_json(work_dir, work)
    # project投影だけでは作品全体の読込は完了していない。対象page.jsonの
    # 厳格読込が全件通るまで、保存可能状態へ戻さない。
    work.loaded = False
    from . import page_file_scene

    blend_path = Path(str(getattr(bpy.data, "filepath", "") or ""))
    role, page_id, _coma_id = page_file_scene.role_from_path(
        blend_path,
        work_dir,
    )
    if role == page_file_scene.ROLE_WORK:
        detail_filter: set[str] | None = set()
    elif role in {page_file_scene.ROLE_PAGE, page_file_scene.ROLE_COMA} and page_id:
        detail_filter = {page_id}
    else:
        detail_filter = None
    _reload_all_pages_panels(
        work,
        work_dir,
        detail_filter=detail_filter,
    )
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
    refresh_runtime: bool = True,
) -> bool:
    """現在 scene の B-MANGA JSON メタデータを disk へ保存する.

    通常の .blend 保存フックからも呼ぶため、ここでは .blend 保存は行わない。
    """
    global _saving_work_metadata
    from . import history_runtime

    if history_runtime.is_restoring() or history_runtime.is_blocked():
        _logger.error(
            "B-MANGA metadata save blocked during history reconciliation"
        )
        return False
    if _saving_work_metadata:
        return False
    if _suppress_work_metadata_save_depth:
        # 呼び出し元が直前に明示 checkpoint 済みであるため、save_pre 側では
        # 「保存不要だが成功」として扱う。False は native save の安全復旧を
        # 発動し、正常に書いた初回 work.blend まで巻き戻してしまう。
        return True
    try:
        from ..core.work import get_work
        from ..io import page_io
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
            from . import outliner_change_collector

            outliner_change_collector.flush(
                getattr(context, "scene", None),
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Blenderオブジェクトの変更をDomainへ取り込めませんでした"
            ) from exc
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
        from . import page_file_scene

        role, current_page_id, _coma_id = page_file_scene.current_role(context)
        page = None
        if role == page_file_scene.ROLE_PAGE and current_page_id:
            page = next(
                (
                    entry
                    for entry in getattr(work, "pages", ())
                    if str(getattr(entry, "id", "") or "") == current_page_id
                ),
                None,
            )
            if page is None or not bool(getattr(page, "detail_loaded", False)):
                raise RuntimeError("編集中ページのDomain投影を保存できません")
        page_io.save_work_projection(
            work_dir,
            work,
            page_entry=page,
        )
        try:
            from . import sidecar_load_cache

            sidecar_load_cache.record(
                getattr(context, "scene", None),
                work_dir=work_dir,
            )
        except Exception:  # noqa: BLE001
            _logger.exception("sidecar load signature record failed")
        if page is not None and current_page_id:
            page_file_scene.record_page_runtime_signature(
                getattr(context, "scene", None),
                work,
                current_page_id,
            )
        _logger.info("B-MANGA metadata saved%s", f" ({reason})" if reason else "")
        # Phase 1: 保存契機で Outliner mirror を最新化する。page/coma 追加削除
        # 直後に save_scene_work_to_disk が呼ばれるため、ここでミラーを更新
        # しておけば各 op に侵襲しない。冪等で安全。
        if refresh_runtime:
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


def _hide_legacy_overlay_objects(scene) -> None:
    _PREFIXES = (
        "page_paper_guide_",
        "page_safe_area_fill_",
        "page_bleed_outer_fill_",
        "work_info_text_",
        "page_preview_",
    )
    for obj in getattr(scene, "objects", ()) or ():
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
        _hide_legacy_overlay_objects(scene)
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


_LEGACY_BLEND_METHOD_MATERIAL_PREFIXES = (
    "BManga_Fill_",
    "BManga_Effect_Display_Line_",
    "BManga_Effect_Display_Fill_",
    "BManga_Effect_Display_Underlay_",
)


def _refresh_legacy_material_render_methods() -> None:
    """旧バージョンで保存された fill/effect マテリアルの surface_render_method
    を現行の修正 (DITHERED) へ揃える。

    mat.blend_method = "BLEND" は副作用で surface_render_method を
    "BLENDED" (描画順依存・深度無視) にしてしまう。過去バージョンで保存
    された .blend にはこの副作用のままのマテリアルが残っており、
    layer_object_sync.mirror_work_to_outliner の高速パスは既存実体が
    揃っていれば再生成をスキップするため、ファイルを開いただけでは
    修正が再適用されない。ここでファイル読込のたびに既存マテリアルを
    直接スイープして揃える (対象は少数のため負荷は無視できる)。
    """
    try:
        for mat in bpy.data.materials:
            name = str(getattr(mat, "name", "") or "")
            if not name.startswith(_LEGACY_BLEND_METHOD_MATERIAL_PREFIXES):
                continue
            try:
                if str(getattr(mat, "surface_render_method", "")) == "BLENDED":
                    mat.surface_render_method = "DITHERED"
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        _logger.exception("load_post: legacy material render method refresh failed")


def _hydrate_current_file(filepath_arg) -> bool:
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
            _deactivate_noncanonical_work_copy(
                scene,
                blend_path,
                notice=True,
            )
            return
        if _deactivate_noncanonical_work_copy(
            scene,
            blend_path,
            work_dir=work_dir,
            require_domain=False,
            notice=True,
        ):
            return
        try:
            from ..io import native_save_guard

            restored_paths = native_save_guard.recover_pending_native_saves(
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
        try:
            from . import layer_transfer_group

            if layer_transfer_group.has_transfer_recovery_journal(work_dir):
                transfer_restored = (
                    layer_transfer_group.recover_interrupted_transfers(work_dir)
                )
            else:
                # journalを伴う中断はopen前に同期復旧する。journal作成前に
                # 残り得るprepared stageは確定対象ではないため、通常openの
                # 全ページ走査を避け、load完了後の一回限りタスクで清掃する。
                transfer_restored = ()
                _schedule_transfer_orphan_recovery(work_dir)
        except Exception:  # noqa: BLE001
            _logger.exception("load_post: page transfer recovery failed")
            _mark_transfer_recovery_failed(scene)
            return
        if blend_path.resolve() in {
            path.resolve() for path in transfer_restored
        }:
            _schedule_native_save_reload(blend_path, notice=True)
            return
        try:
            from . import sidecar_load_cache

            embedded_work = getattr(scene, "bmanga_work", None)
            use_embedded = (
                embedded_work is not None
                and sidecar_load_cache.current(
                    scene,
                    work_dir,
                    blend_path,
                )
            )
        except Exception:  # noqa: BLE001
            _logger.exception("load_post: sidecar cache check failed")
            use_embedded = False
            embedded_work = None
        if use_embedded:
            # 埋込PropertyGroupは高速なUI投影cacheに過ぎない。Repositoryを
            # 必ず厳格読込して観測hashとDomain Storeを先に確立し、別画面更新を
            # 未観測のまま保存できないようにする。
            from ..io import (
                coma_move_recovery,
                domain_runtime,
                native_tree_transaction,
            )
            from . import page_file_scene

            repository = domain_runtime.repository_for(work_dir)
            file_role, file_page_id, _file_coma_id = (
                page_file_scene.role_from_path(blend_path, work_dir)
            )
            try:
                repository.recover()
                native_tree_transaction.recover_pending_native_transactions(
                    work_dir,
                    repository=repository,
                )
                coma_move_recovery.recover_interrupted_coma_moves(
                    work_dir,
                    repository=repository,
                )
                project_document = repository.load_project()
                repository.assert_project_page_files(project_document)
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "load_post: embedded Domain recovery failed"
                )
                try:
                    embedded_work.loaded = False
                except Exception:  # noqa: BLE001
                    pass
                _show_native_save_notice(
                    title="作品データの復旧に失敗しました",
                    lines=(
                        "この画面では保存せず、Blenderを閉じて"
                        "作品を開き直してください。",
                    ),
                )
                return
            try:
                page_documents = []
                if file_role in {
                    page_file_scene.ROLE_PAGE,
                    page_file_scene.ROLE_COMA,
                }:
                    summary = next(
                        (
                            value
                            for value in project_document.pages
                            if value.display_id == file_page_id
                        ),
                        None,
                    )
                    if summary is None:
                        raise KeyError(
                            f"page is not listed in Domain: {file_page_id}"
                        )
                    page_path = repository.page_path(summary.uid)
                    if not page_path.is_file():
                        raise FileNotFoundError(
                            f"required Domain page is missing: {page_path}"
                        )
                    page_documents.append(repository.load_page(summary.uid))
                domain_runtime.install_store(
                    work_dir,
                    project_document,
                    tuple(page_documents),
                )
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "load_post: embedded Domain page failed to load"
                )
                embedded_work.loaded = False
                _show_native_save_notice(
                    title="作品データを読み込めません",
                    lines=(
                        "旧形式または破損した作品データです。",
                        "この画面では保存せず、作品を開き直してください。",
                    ),
                )
                return
            work = embedded_work
            work.work_dir = str(work_dir.resolve())
            work.loaded = True
            _bind_embedded_domain_identifiers(
                work,
                project_document,
                page_documents,
            )
            _filter_embedded_page_details(work)
            _logger.debug("load_post: unchanged sidecars reused from blend")
        else:
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
        _refresh_legacy_material_render_methods()
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
        from . import page_file_scene

        file_role, file_page_id, file_coma_id = page_file_scene.role_from_path(
            blend_path,
            work_dir,
        )
        is_coma_blend = file_role == page_file_scene.ROLE_COMA
        if not is_coma_blend:
            try:
                from . import layer_object_sync as _los

                _los.mirror_work_to_outliner(
                    scene,
                    work,
                    # work一覧は直後のpage-grid整合でGPU previewを同期する。
                    # ここでも同期すると全55ページの署名検査を二重実行する。
                    sync_work_previews=(
                        file_role != page_file_scene.ROLE_WORK
                    ),
                )
            except Exception:  # noqa: BLE001
                _logger.exception("load_post: outliner mirror failed")
        # work.blend / cNN.blend ごとに Scene の整合を補正する。
        try:
            if file_role == page_file_scene.ROLE_WORK:
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
                try:
                    from ..operators import object_tool_op as _object_tool_op

                    _object_tool_op.schedule_object_tool_relaunch_after_file_open()
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: object tool relaunch scheduling failed")
            elif file_role == page_file_scene.ROLE_PAGE:
                _reconcile_gpencil_collections(bpy.context, work, include_page_content=True)
                try:
                    from . import balloon_curve_object

                    balloon_curve_object.prewarm_balloon_resources()
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: balloon resource preparation failed")
                try:
                    page_id = file_page_id
                    page_file_scene.purge_other_page_data(scene, page_id)
                    if not page_file_scene.page_runtime_objects_current(
                        scene,
                        work,
                        page_id,
                    ):
                        page_file_scene.resync_page_runtime_objects(
                            scene,
                            work,
                            page_id,
                        )
                    from . import paper_guide_object

                    paper_guide_object.apply_existing_paper_guide_visibility(
                        scene,
                        work,
                        page_ids={page_id},
                    )
                    screen = getattr(bpy.context, "screen", None)
                    for area in (screen.areas if screen is not None else ()):
                        if area.type == "VIEW_3D":
                            area.tag_redraw()
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: purge other page data failed")
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
                except cross_page_transfer.StagedImportRollbackError:
                    _logger.exception(
                        "load_post: staged import rollback failed; "
                        "the work remains fail-closed"
                    )
                    raise
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: staged import processing failed")
                try:
                    from ..operators import view_op

                    view_op.schedule_fit_active_page()
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: page fit scheduling failed")
                try:
                    from ..operators import object_tool_op as _object_tool_op

                    _object_tool_op.schedule_object_tool_relaunch_after_file_open()
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: object tool relaunch scheduling failed")
            elif file_role == page_file_scene.ROLE_COMA:
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
                    from . import coma_thumb_output

                    coma_thumb_output.ensure_thumb_output_node(scene)
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: thumb output setup failed")
                try:
                    from . import coma_mask_object

                    coma_mask_object.ensure_coma_mask_mesh(
                        scene, work, file_page_id, file_coma_id
                    )
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: coma mask mesh sync failed")
                _overlay.reset_viewport_background_to_theme(bpy.context)
                _overlay.apply_bmanga_shading_mode(bpy.context)
                try:
                    from ..ui import sidebar as _sidebar

                    _sidebar.schedule_open_bmanga_sidebar()
                except Exception:  # noqa: BLE001
                    _logger.exception("load_post: B-MANGA sidebar open failed")
                _restore_coma_user_view_layer(scene, active_view_layer_name)
        except (KeyError, ValueError):
            _logger.exception("load_post: file role reconciliation failed")
        try:
            restored_rasters = _recover_crashed_raster_snapshots(
                work_dir,
                blend_path,
            )
        except Exception:  # noqa: BLE001
            _logger.exception(
                "load_post: interrupted raster save recovery failed"
            )
            work.loaded = False
            _show_native_save_notice(
                title="未保存ラスターの復旧に失敗しました",
                lines=(
                    "この画面では保存せず、Blenderを閉じて"
                    "作品を開き直してください。",
                ),
            )
            return False
        if restored_rasters:
            _show_native_save_notice(
                title="未保存ラスターを復旧しました",
                lines=(
                    "前回の保存中断直前のラスターを戻しました。",
                    "内容を確認して、もう一度保存してください。",
                ),
            )
        _logger.debug("B-MANGA: load_post synced for %s", blend_path)
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("B-MANGA load_post handler failed")
        try:
            failed_work = getattr(
                getattr(bpy.context, "scene", None),
                "bmanga_work",
                None,
            )
            if failed_work is not None:
                failed_work.loaded = False
        except Exception:  # noqa: BLE001
            _logger.exception("load_post: failed to mark work unusable")
        _show_native_save_notice(
            title="作品データを読み込めません",
            lines=(
                "旧形式、欠損、または破損した作品データです。",
                "この画面では保存せず、作品を開き直してください。",
            ),
        )
        return False


def _hydrate_with_path_cache(filepath_arg) -> bool:
    from . import page_file_scene

    with paths.path_resolution_cache(), page_file_scene.role_resolution_cache():
        return _hydrate_current_file(filepath_arg)


@persistent
def _bmanga_on_load_post(filepath_arg) -> None:  # signature: (str,) in Blender handlers
    """型付きload EventをCoordinatorへ渡す唯一のload handler。"""

    from ..bmanga_core.lifecycle import LifecycleEvent, LifecycleEventKind
    from . import lifecycle_coordinator, page_file_scene

    filepath = str(getattr(bpy.data, "filepath", "") or "")
    try:
        with paths.path_resolution_cache(), page_file_scene.role_resolution_cache():
            lifecycle_coordinator.handle_handler_event(
                LifecycleEvent(
                    LifecycleEventKind.LOAD,
                    filepath,
                    {"handler_arg": str(filepath_arg or "")},
                ),
                primary=lambda: _hydrate_current_file(filepath_arg),
            )
    except Exception:  # noqa: BLE001
        _logger.exception("B-MANGA lifecycle load handler failed")


@persistent
def _bmanga_on_save_pre(filepath_arg) -> None:  # signature: (str,) in Blender handlers
    """型付きsave EventをCoordinatorへ渡す唯一のsave_pre handler。"""

    from ..bmanga_core.lifecycle import LifecycleEvent, LifecycleEventKind
    from . import lifecycle_coordinator

    try:
        lifecycle_coordinator.handle_handler_event(
            LifecycleEvent(
                LifecycleEventKind.SAVE_PRE,
                str(getattr(bpy.data, "filepath", "") or ""),
                {"handler_arg": str(filepath_arg or "")},
            ),
            primary=lambda: _save_pre_impl(filepath_arg),
        )
    except Exception:  # noqa: BLE001
        _logger.exception("B-MANGA lifecycle save_pre handler failed")


def _save_pre_impl(filepath_arg) -> None:
    """通常の .blend 保存前に B-MANGA の JSON メタデータも同期する."""
    try:
        guard_started = _begin_native_save_guard(filepath_arg)
        if guard_started is None:
            return
        from . import lifecycle_coordinator
        from ..bmanga_core.lifecycle import LifecycleState

        from . import history_runtime

        history_blocked = (
            history_runtime.is_restoring()
            or history_runtime.is_blocked()
        )
        if (
            lifecycle_coordinator.MACHINE.state is LifecycleState.ROLLING_BACK
            or history_blocked
        ):
            from ..io import native_save_guard

            native_save_guard.force_native_save_restore(
                _native_save_token,
                reason=(
                    "作品の復元処理が完了していないため、"
                    "この画面からは保存できません"
                ),
            )
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
                from . import page_file_scene

                role, page_id, coma_id = page_file_scene.role_from_path(
                    blend_path,
                    work_dir,
                )
                if role == page_file_scene.ROLE_COMA:
                    active_view_layer_name = _active_view_layer_name(bpy.context.scene)
                    from . import coma_thumb_output

                    coma_thumb_output.ensure_thumb_output_node(bpy.context.scene)
                    try:
                        from . import coma_mask_object
                        from ..core.work import get_work as _get_work

                        coma_mask_object.ensure_coma_mask_mesh(
                            bpy.context.scene,
                            _get_work(bpy.context),
                            page_id,
                            coma_id,
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
            # 保存はDomain確定とNative Snapshotの準備だけを行う。
            # Outliner派生表示の全再構築はload/明示変更時へ限定する。
            refresh_runtime=False,
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
    """型付きsave EventをCoordinatorへ渡す唯一のsave_post handler。"""

    from ..bmanga_core.lifecycle import LifecycleEvent, LifecycleEventKind
    from . import lifecycle_coordinator

    try:
        lifecycle_coordinator.handle_handler_event(
            LifecycleEvent(
                LifecycleEventKind.SAVE_POST,
                str(getattr(bpy.data, "filepath", "") or ""),
                {"handler_arg": str(filepath_arg or "")},
            ),
            primary=lambda: _save_post_impl(filepath_arg),
        )
    except Exception:  # noqa: BLE001
        _logger.exception("B-MANGA lifecycle save_post handler failed")


def _save_post_impl(filepath_arg) -> None:
    """保存後にページ一覧用の軽量表示を戻す."""
    try:
        save_result, source = _finish_native_save_guard(
            native_save_succeeded=True,
        )
        current_path = Path(str(getattr(bpy.data, "filepath", "") or ""))
        if str(current_path) and _deactivate_noncanonical_work_copy(
            getattr(bpy.context, "scene", None),
            current_path,
            notice=True,
        ):
            return
        if save_result.reload_required and source is not None:
            _schedule_native_save_reload(source)
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
def _bmanga_on_save_post_fail(*args) -> None:
    """型付きsave EventをCoordinatorへ渡す唯一のsave_fail handler。"""

    from ..bmanga_core.lifecycle import LifecycleEvent, LifecycleEventKind
    from . import lifecycle_coordinator

    try:
        lifecycle_coordinator.handle_handler_event(
            LifecycleEvent(
                LifecycleEventKind.SAVE_FAIL,
                str(getattr(bpy.data, "filepath", "") or ""),
            ),
            primary=lambda: _save_post_fail_impl(*args),
        )
    except Exception:  # noqa: BLE001
        _logger.exception("B-MANGA lifecycle save_fail handler failed")


def _save_post_fail_impl(*_args) -> None:
    """保存失敗時も作品ロックを解放し、退避した最新ファイルを戻す."""

    try:
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
    _bmanga_on_history_pre(is_redo=False)


@persistent
def _bmanga_on_redo_pre(*_args) -> None:
    _bmanga_on_history_pre(is_redo=True)


def _bmanga_on_history_pre(*, is_redo: bool) -> None:
    from ..bmanga_core.lifecycle import LifecycleEvent, LifecycleEventKind
    from . import lifecycle_coordinator

    try:
        lifecycle_coordinator.handle_handler_event(
            LifecycleEvent(
                (
                    LifecycleEventKind.REDO_PRE
                    if is_redo
                    else LifecycleEventKind.UNDO_PRE
                ),
                str(getattr(bpy.data, "filepath", "") or ""),
            ),
            primary=_history_pre_impl,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("undo_pre: Coordinator dispatch failed")


def _history_pre_impl() -> None:
    try:
        from ..operators import coma_modal_state as _modal_state
        from . import history_runtime, layer_transfer_history

        count = _modal_state.mark_all_externally_finished()
        layer_transfer_history.begin_restore(bpy.context)
        history_runtime.begin_restore(relaunch_object_tool=count > 0)
        if count > 0:
            _logger.debug("undo_pre: marked %d modals as finished", count)
    except Exception:  # noqa: BLE001
        _logger.exception("undo_pre: restore guard failed")


@persistent
def _bmanga_on_undo_post(*_args) -> None:
    """Undo/Redo 後の次イベントループで B-MANGA 実体を再同期する."""
    _bmanga_on_history_post(is_redo=False)


@persistent
def _bmanga_on_redo_post(*_args) -> None:
    _bmanga_on_history_post(is_redo=True)


def _bmanga_on_history_post(*, is_redo: bool) -> None:
    from ..bmanga_core.lifecycle import LifecycleEvent, LifecycleEventKind
    from . import lifecycle_coordinator

    try:
        lifecycle_coordinator.handle_handler_event(
            LifecycleEvent(
                (
                    LifecycleEventKind.REDO_POST
                    if is_redo
                    else LifecycleEventKind.UNDO_POST
                ),
                str(getattr(bpy.data, "filepath", "") or ""),
            ),
            primary=_history_post_impl,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("undo_post: Coordinator dispatch failed")


def _history_post_impl() -> None:
    try:
        from ..operators import coma_modal_state as _modal_state
        from . import history_runtime

        count = _modal_state.mark_all_externally_finished()
        if not history_runtime.is_restoring():
            history_runtime.begin_restore(relaunch_object_tool=count > 0)
        elif count > 0:
            history_runtime.request_object_tool_relaunch()
        history_runtime.schedule_reconcile()
        from . import preview_composite

        preview_composite.mark_dirty(context=bpy.context)
        from . import file_transition_runtime

        file_transition_runtime.mark_scene_dirty()
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
    _remove_named_handler(bpy.app.handlers.redo_pre, _bmanga_on_redo_pre.__name__)
    _remove_named_handler(bpy.app.handlers.undo_post, _bmanga_on_undo_post.__name__)
    _remove_named_handler(bpy.app.handlers.redo_post, _bmanga_on_undo_post.__name__)
    _remove_named_handler(bpy.app.handlers.redo_post, _bmanga_on_redo_post.__name__)
    bpy.app.handlers.load_post.append(_bmanga_on_load_post)
    bpy.app.handlers.save_pre.append(_bmanga_on_save_pre)
    bpy.app.handlers.save_post.append(_bmanga_on_save_post)
    if save_post_fail is not None:
        save_post_fail.append(_bmanga_on_save_post_fail)
    bpy.app.handlers.undo_pre.append(_bmanga_on_undo_pre)
    bpy.app.handlers.redo_pre.append(_bmanga_on_redo_pre)
    bpy.app.handlers.undo_post.append(_bmanga_on_undo_post)
    bpy.app.handlers.redo_post.append(_bmanga_on_redo_post)
    _logger.debug("handlers registered")


def schedule_current_file_sync(retries: int = 3, interval: float = 0.15) -> None:
    """アドオン再読込時に、現在開いている B-MANGA .blend を load_post 相当に同期する."""
    state = {"left": max(1, int(retries))}

    def _tick():
        try:
            # これは実ファイルloadではなくアドオン再登録後の再hydrate。
            # load Eventを偽装するとScheduler自身をinvalidateし、残りの
            # retryまで旧世代として消してしまう。
            from . import lifecycle_coordinator

            filepath = str(getattr(bpy.data, "filepath", "") or "")
            lifecycle_coordinator.finalize_load_hydration(
                _hydrate_with_path_cache(filepath)
            )
        except Exception:  # noqa: BLE001
            _logger.exception("scheduled current file sync failed")
        state["left"] -= 1
        return interval if state["left"] > 0 else None

    try:
        from . import lifecycle_scheduler

        lifecycle_scheduler.schedule(
            "current_file_sync",
            _tick,
            first_interval=interval,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("schedule current file sync failed")


def unregister() -> None:
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
    _remove_named_handler(bpy.app.handlers.redo_pre, _bmanga_on_redo_pre.__name__)
    _remove_named_handler(bpy.app.handlers.undo_post, _bmanga_on_undo_post.__name__)
    _remove_named_handler(bpy.app.handlers.redo_post, _bmanga_on_undo_post.__name__)
    _remove_named_handler(bpy.app.handlers.redo_post, _bmanga_on_redo_post.__name__)
    _logger.debug("handlers unregistered")
