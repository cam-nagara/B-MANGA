"""旧作品の手描き・効果線を個別レイヤー構造へ安全に移行する画面。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import secrets

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from ..core.work import get_work
from ..io import project_content_migration
from ..utils import layer_uid, log

_logger = log.get_logger(__name__)
_PROMPT_PENDING = False
_RECOVERY_RELOAD_PENDING = False
_RESULT_RELOAD_DEPTH = 0
_WORKER_TOKEN_ENV = "BMANGA_DETAIL_MIGRATION_WORKER_TOKEN"
_WORKER_CLAIM_ENV = "BMANGA_DETAIL_MIGRATION_WORKER_CLAIM"

_PHASE_LABELS = {
    "preparing": "準備",
    "backup": "元データの退避",
    "convert": "ページ変換",
    "install": "ページ入替え",
    "validate_installed": "入替え後の検証",
    "marking": "版情報の更新",
    "committed": "入替え完了",
    "restart_validation": "再読込検証",
    "migration": "作品データ更新",
    "rollback": "元データへの復旧",
}


@dataclass(frozen=True, slots=True)
class _OpenBlendState:
    filepath: Path | None
    page_id: str = ""
    is_work_blend: bool = False
    is_dirty: bool = False

    @property
    def needs_disk_reload(self) -> bool:
        return bool(self.filepath is not None and (self.page_id or self.is_work_blend))


class _MigrationProgressDisplay:
    """同期移行中もステータスバーへ段階・ページ・復旧結果を出す。"""

    def __init__(self, operator, context, page_count: int) -> None:
        self.operator = operator
        self.context = context
        self.page_count = max(1, int(page_count))
        self.maximum = self.page_count * 5 + 2
        self.position = 0
        self.active_page_id = ""
        self.failed_page_id = ""
        self.rollback_status = ""
        self.last_message = ""
        self._started = False

    def begin(self) -> None:
        self.context.window_manager.progress_begin(0, self.maximum)
        self._started = True
        self._set_status("作品データ更新を開始しています")

    def end(self) -> None:
        if self._started:
            self.context.window_manager.progress_end()
            self._started = False
        self._set_status(None)

    def __call__(self, progress: project_content_migration.MigrationProgress) -> None:
        if progress.event == "page_started":
            self.active_page_id = progress.page_id
        elif progress.event == "page_completed":
            self.active_page_id = ""
        elif progress.event == "failed":
            self.failed_page_id = progress.page_id or self.active_page_id
        if progress.rollback_status:
            self.rollback_status = progress.rollback_status
        self.last_message = progress.message
        self.position = max(self.position, self._position_for(progress))
        self.context.window_manager.progress_update(min(self.maximum, self.position))
        label = _PHASE_LABELS.get(progress.phase, progress.phase)
        text = progress.message or label
        self._set_status(text)
        if progress.event in {
            "phase_started",
            "failed",
            "completed",
            "interrupted",
            "not_needed",
        }:
            level = {"ERROR"} if progress.event == "failed" else {"INFO"}
            self.operator.report(level, f"{label}: {text}")

    def _position_for(self, progress) -> int:
        offsets = {
            "backup": 0,
            "convert": self.page_count,
            "install": self.page_count * 2,
            "validate_installed": self.page_count * 3,
            "marking": self.page_count * 4,
            "committed": self.page_count * 4 + 1,
            "restart_validation": self.page_count * 4 + 2,
        }
        base = offsets.get(progress.phase, self.position)
        if progress.event == "page_completed":
            return base + max(0, int(progress.index))
        if progress.event == "completed" and progress.phase == "restart_validation":
            return self.maximum
        return base

    def _set_status(self, text: str | None) -> None:
        workspace = getattr(self.context, "workspace", None)
        if workspace is None or not hasattr(workspace, "status_text_set"):
            return
        try:
            workspace.status_text_set(text)
        except Exception:  # ステータスバーを持たないbackground等でも移行本体は継続する
            _logger.debug("detail migration status text is unavailable")


def migration_worker_owns_runtime() -> bool:
    """明示トークンを持つ変換・検証ワーカー内だけで真を返す。"""

    inherited = str(os.environ.get(_WORKER_TOKEN_ENV, "") or "")
    claimed = str(os.environ.get(_WORKER_CLAIM_ENV, "") or "")
    return bool(
        inherited
        and claimed
        and secrets.compare_digest(inherited, claimed)
    )


def migration_result_reload_in_progress() -> bool:
    """移行結果を安全に再読込している間だけ真を返す。"""

    return _RESULT_RELOAD_DEPTH > 0


def _current_filepath() -> Path | None:
    raw = str(getattr(bpy.data, "filepath", "") or "").strip()
    return Path(raw).resolve() if raw else None


def _capture_open_blend_state(work_dir: Path, plan=None) -> _OpenBlendState:
    filepath = _current_filepath()
    page_id = ""
    if filepath is not None:
        if plan is not None:
            for page in plan.pages:
                if filepath == page.source_path.resolve():
                    page_id = page.page_id
                    break
        elif (
            filepath.name.lower() == "page.blend"
            and filepath.parent.parent == work_dir.resolve()
            and filepath.parent.name.startswith("p")
        ):
            page_id = filepath.parent.name
    work_blend = (work_dir / "work.blend").resolve()
    return _OpenBlendState(
        filepath=filepath,
        page_id=page_id,
        is_work_blend=bool(filepath is not None and filepath == work_blend),
        is_dirty=bool(getattr(bpy.data, "is_dirty", False)),
    )


def _require_saved_open_file(state: _OpenBlendState) -> None:
    if state.is_dirty:
        raise project_content_migration.PreflightBlocked(
            "現在開いているBlenderファイルに未保存の変更があります。"
            "先に保存してから作品データ更新をやり直してください"
        )


def _reload_blend_from_disk(path: Path, *, suppress_prompt: bool = True) -> None:
    global _RESULT_RELOAD_DEPTH
    if not path.is_file():
        raise FileNotFoundError(f"再読込するBlenderファイルがありません: {path}")
    if suppress_prompt:
        _RESULT_RELOAD_DEPTH += 1
    try:
        result = bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
        if "FINISHED" not in result:
            raise RuntimeError(f"Blenderファイルを再読込できませんでした: {path}")
    finally:
        if suppress_prompt:
            _RESULT_RELOAD_DEPTH = max(0, _RESULT_RELOAD_DEPTH - 1)


def _reload_or_sync_open_state(state: _OpenBlendState, work_dir: Path) -> None:
    if state.needs_disk_reload and state.filepath is not None:
        _reload_blend_from_disk(state.filepath)
        return
    _reload_work_version(bpy.context, work_dir)


def _popup_result(context, *, title: str, icon: str, lines: tuple[str, ...]) -> None:
    if bpy.app.background:
        return

    def _draw(menu, _context):
        for line in lines:
            menu.layout.label(text=str(line))

    context.window_manager.popup_menu(_draw, title=title, icon=icon)


def _schedule_recovered_file_reload(path: Path) -> bool:
    """load_post内の復旧後、次のtimerで元ファイルをディスクから読み直す。"""

    global _RECOVERY_RELOAD_PENDING
    if _RECOVERY_RELOAD_PENDING:
        return False
    _RECOVERY_RELOAD_PENDING = True

    def _reload():
        global _RECOVERY_RELOAD_PENDING
        _RECOVERY_RELOAD_PENDING = False
        try:
            # 復旧済み旧版を読み直した後は、通常の確認画面を改めて予約する。
            _reload_blend_from_disk(path, suppress_prompt=False)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("recovered migration file reload failed")
            _popup_result(
                bpy.context,
                title="復旧後の再読込に失敗しました",
                icon="ERROR",
                lines=(
                    "現在のファイルを保存せず、Blenderを閉じて開き直してください",
                    str(exc),
                ),
            )
        return None

    bpy.app.timers.register(_reload, first_interval=0.05)
    return True


def _format_bytes(value: int) -> str:
    amount = float(max(0, int(value)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024.0 or unit == "TB":
            return f"{amount:.1f} {unit}"
        amount /= 1024.0
    return f"{amount:.1f} TB"


def work_requires_detail_migration(work) -> bool:
    if work is None:
        return False
    work_dir = str(getattr(work, "work_dir", "") or "").strip()
    if not work_dir:
        return False
    version = int(getattr(work, "detail_data_version", 0) or 0)
    # 旧版だけでなく、このアドオンより新しい版も通常編集へ通さない。
    # 後者は事前確認で「新しい作品データ」として書込み前停止される。
    if version != layer_uid.CURRENT_DETAIL_DATA_VERSION:
        return True
    try:
        from ..utils import page_file_scene

        role, _page_id, _coma_id = page_file_scene.current_role(bpy.context)
        if (
            role == page_file_scene.ROLE_PAGE
            and layer_uid.scene_detail_data_version(bpy.context.scene)
            != layer_uid.CURRENT_DETAIL_DATA_VERSION
        ):
            return True
    except Exception:
        # ページ用blendか判定できない状態を版確認済みとして通さない。
        return True
    try:
        return bool(project_content_migration.find_incomplete_journals(work_dir))
    except Exception:  # 壊れた移行記録も新経路を有効にせず、安全側で停止する
        return True


def enforce_detail_migration_gate(work) -> bool:
    """旧版または復旧待ちの作品を通常Operatorのpollから外す。"""

    required = work_requires_detail_migration(work)
    if required and work is not None:
        work.loaded = False
    return required


def _migration_callbacks():
    from ..io import detail_data_blender_migration

    return detail_data_blender_migration.callbacks()


def _recover_incomplete(work_dir: Path) -> int:
    recovered = 0
    for journal in project_content_migration.find_incomplete_journals(work_dir):
        project_content_migration.recover_transaction(
            journal,
            force=True,
            expected_work_dir=work_dir,
        )
        recovered += 1
    return recovered


def _reload_work_version(context, work_dir: Path) -> None:
    from ..io import work_io

    work = get_work(context)
    if work is None:
        return
    work_io.load_work_json(work_dir, work)
    work.work_dir = str(work_dir)
    work.loaded = True
    enforce_detail_migration_gate(work)


def _recover_interrupted_migration(context, work_dir: Path) -> int:
    """確認済みだった中断処理だけを退避時点へ戻し、作品版を再読込する。"""

    recovered = _recover_incomplete(work_dir)
    if recovered:
        _reload_work_version(context, work_dir)
    return recovered


class BMANGA_OT_detail_data_migrate(Operator):
    """全ページを退避してから個別レイヤー構造へ一括移行する。"""

    bl_idname = "bmanga.detail_data_migrate"
    bl_label = "作品データを安全に更新"
    bl_options = {"REGISTER"}

    work_dir: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]

    def _resolved_work_dir(self, context) -> Path | None:
        raw = str(self.work_dir or "").strip()
        if not raw:
            work = get_work(context)
            raw = str(getattr(work, "work_dir", "") or "")
        path = Path(raw).resolve() if raw else None
        return path if path is not None and path.is_dir() else None

    def _build_plan(self, context):
        work_dir = self._resolved_work_dir(context)
        if work_dir is None:
            raise FileNotFoundError("作品フォルダーが見つかりません")
        inspector, _converter, _validator = _migration_callbacks()
        return project_content_migration.build_migration_plan(
            work_dir, inspector=inspector
        )

    def invoke(self, context, _event):
        try:
            work_dir = self._resolved_work_dir(context)
            if work_dir is None:
                raise FileNotFoundError("作品フォルダーが見つかりません")
            before_recovery = _capture_open_blend_state(work_dir)
            _require_saved_open_file(before_recovery)
            recovered = _recover_interrupted_migration(context, work_dir)
            if recovered and before_recovery.needs_disk_reload:
                # 復旧でディスク上の現在ページ／一覧が戻ったため、古いメモリを
                # 残したまま同じinvokeを続けず再読込後の新しい画面へ引き継ぐ。
                _reload_blend_from_disk(
                    before_recovery.filepath,
                    suppress_prompt=False,
                )
                return {"CANCELLED"}
            self._plan = self._build_plan(context)
            self._open_state = _capture_open_blend_state(work_dir, self._plan)
            _require_saved_open_file(self._open_state)
            enforce_detail_migration_gate(get_work(context))
        except Exception as exc:  # noqa: BLE001
            _logger.exception("detail data migration preflight failed")
            self.report({"ERROR"}, f"作品データの事前確認に失敗しました: {exc}")
            _popup_result(
                bpy.context,
                title="作品データ更新を開始できません",
                icon="ERROR",
                lines=(str(exc),),
            )
            return {"CANCELLED"}
        if self._plan.already_current:
            self.report({"INFO"}, "作品データは更新済みです")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=620)

    def cancel(self, context) -> None:
        """確認を閉じても旧構造の通常編集を再開させない。"""

        enforce_detail_migration_gate(get_work(context))
        self.report(
            {"INFO"},
            "作品データを更新するまで通常の編集機能は停止したままです",
        )

    def draw(self, _context):
        layout = self.layout
        plan = getattr(self, "_plan", None)
        if plan is None:
            layout.label(text="事前確認結果がありません", icon="ERROR")
            return
        layout.label(text="この作品の全ページを新しいレイヤー形式へ更新します", icon="INFO")
        layout.label(text="元のページは別フォルダーへ退避し、失敗時は全ページを元へ戻します")
        layout.label(text="開始後は完了または自動復旧まで画面を閉じられません", icon="LOCKED")
        open_state = getattr(self, "_open_state", None)
        if open_state is not None and open_state.needs_disk_reload:
            layout.label(text="現在のページ／一覧は処理後にディスクから自動再読込します", icon="FILE_REFRESH")
        box = layout.box()
        box.label(text=f"対象ページ: {plan.page_count}ページ")
        box.label(text=f"退避と作業に必要な容量: {_format_bytes(plan.required_bytes)}")
        box.label(text=f"現在の空き容量: {_format_bytes(plan.available_bytes)}")
        backup = box.column()
        backup.label(text="元データの退避先:")
        backup.label(text=str(plan.backup_dir), icon="FILE_FOLDER")
        incomplete = project_content_migration.find_incomplete_journals(plan.work_dir)
        if incomplete:
            box.label(text="前回中断分を先に元へ戻してから再実行します", icon="RECOVER_LAST")
        if not plan.capacity_ok:
            layout.label(text="空き容量が不足しているため実行できません", icon="ERROR")
        for issue in plan.issues[:8]:
            label = f"{issue.page_id}: {issue.message}" if issue.page_id else issue.message
            layout.label(text=label, icon="ERROR")
        if len(plan.issues) > 8:
            layout.label(text=f"ほか {len(plan.issues) - 8}件の問題があります", icon="ERROR")

    def execute(self, context):
        plan = getattr(self, "_plan", None)
        if plan is None or plan.issues or not plan.capacity_ok:
            self.report({"ERROR"}, "事前確認の問題を解消するまで更新できません")
            return {"CANCELLED"}
        open_state = _capture_open_blend_state(plan.work_dir, plan)
        try:
            _require_saved_open_file(open_state)
        except project_content_migration.PreflightBlocked as exc:
            message = str(exc)
            self.report({"ERROR"}, message)
            _popup_result(
                context,
                title="先にBlenderファイルを保存してください",
                icon="ERROR",
                lines=(message,),
            )
            return {"CANCELLED"}

        display = _MigrationProgressDisplay(self, context, plan.page_count)
        result = None
        failure = None
        display.begin()
        try:
            _inspector, converter, validator = _migration_callbacks()
            # 確認画面に表示した計画そのものを実行する。確認後のファイル変更は
            # execute_migration側のハッシュ検証で書込み前に拒否される。
            result = project_content_migration.execute_migration(
                plan,
                confirmed=True,
                converter=converter,
                validator=validator,
                auto_rollback_on_error=True,
                progress_callback=display,
            )
            if result.journal_path is not None:
                project_content_migration.verify_after_restart(
                    result.journal_path,
                    validator=validator,
                    rollback_on_error=True,
                    progress_callback=display,
                    expected_work_dir=plan.work_dir,
                )
        except Exception as exc:  # noqa: BLE001
            failure = exc
            _logger.exception("detail data migration failed")
        finally:
            display.end()

        if failure is not None:
            return self._finish_failure(plan, open_state, display, failure)

        try:
            _reload_or_sync_open_state(open_state, plan.work_dir)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("detail migration result reload failed")
            message = (
                "作品データの更新は完了しましたが、現在のBlenderファイルを"
                "再読込できませんでした。保存せずBlenderを閉じ、同じファイルを開き直してください"
            )
            self.report({"ERROR"}, f"{message}: {exc}")
            _popup_result(
                bpy.context,
                title="更新後の再読込に失敗しました",
                icon="ERROR",
                lines=(message, f"退避先: {plan.backup_dir}"),
            )
            return {"CANCELLED"}

        assert result is not None
        message = f"作品データを更新しました（{result.page_count}ページ、再読込検証済み）"
        self.report({"INFO"}, message)
        _popup_result(
            bpy.context,
            title="作品データの更新が完了しました",
            icon="CHECKMARK",
            lines=(
                message,
                "現在のページ／一覧も更新後の内容へ再読込しました",
                f"元データの退避先: {result.backup_dir}",
            ),
        )
        return {"FINISHED"}

    def _finish_failure(self, plan, open_state, display, failure):
        rollback = (
            failure.rollback
            if isinstance(failure, project_content_migration.MigrationExecutionError)
            else None
        )
        rollback_status = (
            str(getattr(rollback, "status", "") or "")
            or display.rollback_status
        )
        failed_page = display.failed_page_id
        if rollback_status == "rolled_back":
            try:
                _reload_or_sync_open_state(open_state, plan.work_dir)
            except Exception as reload_exc:  # noqa: BLE001
                _logger.exception("detail migration rollback reload failed")
                rollback_status = "reload_failed"
                failure = RuntimeError(f"{failure}; 元ファイル再読込失敗: {reload_exc}")
        elif isinstance(failure, project_content_migration.PreflightBlocked):
            rollback_status = "not_needed"

        page_line = f"失敗ページ: {failed_page}" if failed_page else "失敗ページ: ページ入替え前または特定不能"
        if rollback_status == "rolled_back":
            summary = "更新に失敗しましたが、全ページを元の状態へ戻して再読込しました"
            rollback_line = "ロールバック結果: 完了"
        elif rollback_status == "not_needed":
            summary = "確認後の変更を検出し、書込み開始前に停止しました"
            rollback_line = "ロールバック結果: 書込み前のため不要"
        else:
            summary = "更新または自動復旧に失敗しました。現在のファイルを保存しないでください"
            rollback_line = f"ロールバック結果: {rollback_status or '確認不能'}"
        message = f"{summary}: {failure}"
        self.report({"ERROR"}, message)
        _popup_result(
            bpy.context,
            title="作品データの更新に失敗しました",
            icon="ERROR",
            lines=(
                summary,
                page_line,
                rollback_line,
                f"退避先: {plan.backup_dir}",
                str(failure),
            ),
        )
        return {"CANCELLED"}


def schedule_migration_prompt(context) -> bool:
    """中断分を復旧し、旧作品なら書込み前の確認画面だけを予約する。"""

    global _PROMPT_PENDING
    # 変換・installed検証・再起動相当検証の子Blenderは、親トランザクション
    # が指定ファイルを所有している。ここで未完了ジャーナルを自動復旧すると
    # 検証中の正常な入替えを自分で巻き戻すため、復旧も画面予約も行わない。
    if migration_worker_owns_runtime() or migration_result_reload_in_progress():
        return False
    work = get_work(context)
    work_dir = str(getattr(work, "work_dir", "") or "")
    if not work_dir:
        return False
    resolved_work_dir = Path(work_dir).resolve()
    open_state = _capture_open_blend_state(resolved_work_dir)
    try:
        _require_saved_open_file(open_state)
    except project_content_migration.PreflightBlocked as exc:
        # 自動復旧は現在ファイルを再読込する。未保存の標準Blender編集を
        # 黙って破棄しないよう、保存されるまで復旧も画面予約も行わない。
        enforce_detail_migration_gate(work)
        _logger.warning("detail migration recovery waits for saved file: %s", exc)
        if not bpy.app.background:
            def _draw_unsaved(menu, _context):
                menu.layout.label(text="未保存の変更があるため自動復旧を停止しました", icon="ERROR")
                menu.layout.label(text="先にBlenderファイルを保存してから、作品を開き直してください")

            context.window_manager.popup_menu(
                _draw_unsaved,
                title="作品データの復旧を保留しました",
                icon="ERROR",
            )
        return False
    try:
        recovered = _recover_interrupted_migration(context, resolved_work_dir)
    except Exception:  # noqa: BLE001
        _logger.exception("interrupted detail data migration recovery failed")
        if not bpy.app.background:
            def _draw_error(menu, _context):
                menu.layout.label(text="前回中断した作品データ更新を元へ戻せません", icon="ERROR")
                menu.layout.label(text="ページ編集を続けず、退避データを確認してください")

            context.window_manager.popup_menu(
                _draw_error,
                title="作品データの復旧に失敗しました",
                icon="ERROR",
            )
        return False
    if recovered and open_state.needs_disk_reload and open_state.filepath is not None:
        _schedule_recovered_file_reload(open_state.filepath)
        return False
    work = get_work(context)
    required = enforce_detail_migration_gate(work)
    if not required or bpy.app.background or _PROMPT_PENDING:
        return False
    _PROMPT_PENDING = True

    def _open_prompt():
        global _PROMPT_PENDING
        _PROMPT_PENDING = False
        current = get_work(bpy.context)
        if not work_requires_detail_migration(current):
            return None
        try:
            bpy.ops.bmanga.detail_data_migrate("INVOKE_DEFAULT", work_dir=work_dir)
        except Exception:  # noqa: BLE001
            _logger.exception("detail data migration dialog could not open")
        return None

    bpy.app.timers.register(_open_prompt, first_interval=0.25)
    return True


_CLASSES = (BMANGA_OT_detail_data_migrate,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    global _PROMPT_PENDING, _RECOVERY_RELOAD_PENDING, _RESULT_RELOAD_DEPTH
    _PROMPT_PENDING = False
    _RECOVERY_RELOAD_PENDING = False
    _RESULT_RELOAD_DEPTH = 0
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass  # 未登録クラスの解除はBlender再読込時に起こり得る
