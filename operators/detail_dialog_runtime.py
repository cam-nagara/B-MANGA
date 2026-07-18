"""3つの詳細設定入口で共有するBlender側の編集ライフサイクル。"""

from __future__ import annotations

from ..utils import detail_dialog, detail_dialog_state, detail_state_adapters, log
from ..utils import detail_target_resolver


_OPEN_ACTUAL_SESSIONS = {}
_OPEN_ACTUAL_SCENE_KEYS = {}
_OPEN_PRESET_SESSIONS = {}
_PREPARING_EFFECT_TARGET_IDS: set[str] = set()
_RESUME_OBJECT_TOOL_TOKENS: set[str] = set()
_logger = log.get_logger(__name__)

_PRESET_SELECTOR_BY_TYPE = {
    "border": "bmanga_border_preset_selector",
    "balloon": "bmanga_balloon_tool_preset_selector",
    "text": "bmanga_text_tool_preset_selector",
    "effect_line": "bmanga_effect_line_tool_preset_selector",
    "fill": "bmanga_fill_tool_preset_selector",
    "gradient": "bmanga_gradient_tool_preset_selector",
    "image_path": "bmanga_image_path_tool_preset_selector",
}


def available_dialog_width(context) -> int | None:
    window = getattr(context, "window", None)
    try:
        width = int(getattr(window, "width", 0) or 0)
    except (TypeError, ValueError):
        return None
    return width if width > 0 else None


def prepare_actual_target(context, target) -> None:
    """対象固有の一時設定と編集用グラフをinvoke時に一度だけ準備する。"""

    if target.kind == "text":
        from ..core.text_entry import prime_writing_mode_tracking

        prime_writing_mode_tracking(target.data)
    _prepare_layer_object_state(target)
    if target.kind == "effect":
        _load_effect_target(context, target)
    params = _curve_params(target)
    if params is not None:
        _ensure_curve_nodes(params)
        _request_curve_sync(params)


def _scene_session_key(context) -> tuple[str, int]:
    """Window／WindowManagerが違っても同じSceneを同一視する。"""

    scene = getattr(context, "scene", None)
    if scene is None:
        return ("missing", 0)
    try:
        pointer = int(scene.as_pointer())
    except (AttributeError, ReferenceError, TypeError, ValueError):
        pointer = 0
    return ("rna", pointer) if pointer else ("python", id(scene))


def _discard_actual_session(session) -> None:
    token = str(getattr(session, "token", "") or "")
    _OPEN_ACTUAL_SESSIONS.pop(token, None)
    _OPEN_ACTUAL_SCENE_KEYS.pop(token, None)


def _reject_conflicting_actual_session(context, target) -> None:
    """同じSceneの同一実体と、共有値を持つ効果線の二重編集を拒否する。"""

    scene_key = _scene_session_key(context)
    stale_sessions = []
    conflict_reason = ""
    for token, session in tuple(_OPEN_ACTUAL_SESSIONS.items()):
        if _OPEN_ACTUAL_SCENE_KEYS.get(token) != scene_key:
            continue
        active = session.status in {
            detail_dialog.DetailSessionStatus.OPEN,
            detail_dialog.DetailSessionStatus.RESTORE_FAILED,
        }
        if not active:
            stale_sessions.append(session)
            continue
        same_target = (
            session.target.kind == target.kind
            and session.target.stable_id == target.stable_id
        )
        if same_target:
            conflict_reason = "same_target"
            break
        if session.target.kind == target.kind == "effect":
            # 効果線はScene共有の編集値を使うため、異なる対象同士も同時に開けない。
            conflict_reason = "shared_effect"
            break
    for session in stale_sessions:
        _discard_actual_session(session)
    if conflict_reason == "same_target":
        raise detail_dialog.DetailContractError(
            "同じレイヤーの詳細設定は同時に2つ開けません。"
            "先に開いている詳細設定を閉じてください"
        )
    if conflict_reason == "shared_effect":
        raise detail_dialog.DetailContractError(
            "同じ作品画面では、効果線の詳細設定を同時に2つ開けません。"
            "先に開いている詳細設定を閉じてください"
        )


def begin_actual_session(context, target, *, target_validator=None):
    # 別Window／WindowManagerからの同一実体の2画面目を、対象値へ触る前に拒否する。
    _reject_conflicting_actual_session(context, target)
    # 効果線の共有編集設定読込や編集用カーブ準備も、キャンセル時には
    # ダイアログを開く直前へ戻す必要があるため、準備より先に退避する。
    opening_snapshot = detail_dialog_state.snapshot_detail_state(
        target,
        registry=detail_state_adapters.ACTUAL_DETAIL_STATE_REGISTRY,
    )
    if target.kind == "effect":
        _PREPARING_EFFECT_TARGET_IDS.add(target.stable_id)
    try:
        prepare_actual_target(context, target)
        session = detail_dialog_state.begin_detail_session(
            target,
            detail_dialog.DetailMode.ACTUAL,
            registry=detail_state_adapters.ACTUAL_DETAIL_STATE_REGISTRY,
            target_validator=(
                target_validator
                if target_validator is not None
                else detail_target_resolver.make_target_liveness_validator(context)
            ),
            opening_snapshot=opening_snapshot,
            available_width=available_dialog_width(context),
            preset_type=_preset_type_for_target(target),
            preset_selection=initial_preset_selection_for_target(context, target),
        )
        mark_preset_settings_saved(
            context,
            session.token,
            target,
            session.preset_type,
            session=session,
        )
    except Exception as prepare_error:
        _release_curve_sync(target)
        try:
            detail_dialog_state.restore_detail_state(target, opening_snapshot)
            _resync_restored_target(context, target)
        except Exception as rollback_error:
            raise RuntimeError(
                f"詳細設定の開始失敗後に開始時状態を復元できませんでした: {rollback_error}"
            ) from prepare_error
        raise
    finally:
        if target.kind == "effect":
            _PREPARING_EFFECT_TARGET_IDS.discard(target.stable_id)
    _OPEN_ACTUAL_SESSIONS[session.token] = session
    _OPEN_ACTUAL_SCENE_KEYS[session.token] = _scene_session_key(context)
    if _object_tool_active_for_dialog(context):
        _RESUME_OBJECT_TOOL_TOKENS.add(session.token)
    return session


def _object_tool_active_for_dialog(_context) -> bool:
    """詳細設定を開く時点でオブジェクトツールが動いているか記録する。"""

    from . import coma_modal_state

    return coma_modal_state.get_active("object_tool") is not None


def _resume_object_tool_after_dialog(context, session) -> None:
    token = str(getattr(session, "token", "") or "")
    if token not in _RESUME_OBJECT_TOOL_TOKENS:
        return
    _RESUME_OBJECT_TOOL_TOKENS.discard(token)
    from . import coma_modal_state, object_tool_op

    # props dialog をまたいだ古いmodal handlerはBlender側だけ失効する場合が
    # あるため、Python側の参照も含めて必ず終了し、新しいhandlerへ交換する。
    operator = coma_modal_state.get_active("object_tool")
    try:
        if operator is not None:
            operator.finish_from_external(context, keep_selection=True)
    except Exception:  # noqa: BLE001
        _logger.exception("detail dialog: failed to retire the previous object tool")
        coma_modal_state.clear_active("object_tool", operator, context)
    finally:
        object_tool_op._schedule_object_tool_relaunch(delay_seconds=0.05)


def initial_preset_selection_for_target(context, target) -> str | None:
    """現在の画面選択を読み、グローバル値を変えずセッションへ複製する。"""

    preset_type = _preset_type_for_target(target)
    if preset_type is None:
        return None
    if preset_type == "border":
        border = getattr(target.data, "border", None)
        applied = str(getattr(border, "preset_name", "") or "").strip()
        if applied:
            return applied
    if preset_type == "balloon":
        # フキダシだけは実レイヤーが適用中のカスタム形状名を保持する。
        # 右クリック対象とツール側の選択が違っても、別対象の選択を持ち込まない。
        if hasattr(target.data, "custom_preset_name"):
            applied = str(
                getattr(target.data, "custom_preset_name", "") or ""
            ).strip()
            return applied or None
    wm = getattr(context, "window_manager", None)
    selector = _PRESET_SELECTOR_BY_TYPE[preset_type]
    selected = str(getattr(wm, selector, "") or "").strip() if wm is not None else ""
    if selected in {"", "NONE", "DEFAULT"}:
        return None
    if preset_type == "balloon":
        return selected.split(":", 1)[1] if selected.startswith("custom:") else None
    return selected


def _preset_type_for_target(target) -> str | None:
    if target.kind == "coma":
        return "border"
    if target.kind in {"balloon", "text", "effect", "image_path"}:
        return {
            "balloon": "balloon",
            "text": "text",
            "effect": "effect_line",
            "image_path": "image_path",
        }[target.kind]
    if target.kind != "fill":
        return None
    fill_type = str(getattr(target.data, "fill_type", "solid") or "solid")
    # 実レイヤーはダイアログ開始時のnamespaceではなく、画面で現在選ばれている
    # タイプを正とする。gradientからsolidへ変えた後も旧カテゴリへ保存しない。
    return "gradient" if fill_type == "gradient" else "fill"


def _sync_preset_guard_state(target) -> None:
    """グラフUI上の編集値も、切替前の変更検知へ取り込む。"""

    if target.kind == "coma":
        _sync_coma_curve(target.data)
    params = _curve_params(target)
    if params is not None:
        _sync_curve_nodes(params)


def preset_switch_requires_confirmation(
    context,
    session_token: str,
    target,
    preset_type: str,
) -> bool:
    """最後の適用／保存後に、プリセット保存対象値が変わったかを返す。"""

    session = _OPEN_ACTUAL_SESSIONS.get(str(session_token or ""))
    if session is None:
        raise detail_dialog.DetailTargetNotFoundError(str(target.stable_id or ""))
    if session.target.kind != target.kind or session.target.stable_id != target.stable_id:
        raise detail_dialog.DetailTargetNotFoundError(str(target.stable_id or ""))
    from ..utils import detail_preset_change_guard

    _sync_preset_guard_state(target)
    current = detail_preset_change_guard.capture_preset_settings(target, preset_type)
    baseline = session.preset_baseline
    return baseline is not None and detail_preset_change_guard.preset_settings_changed(
        baseline,
        current,
    )


def mark_preset_settings_saved(
    context,
    session_token: str,
    target,
    preset_type: str | None,
    *,
    session=None,
) -> bool:
    """ダイアログ開始・プリセット適用・現在値保存後の比較基準を更新する。"""

    active = session or _OPEN_ACTUAL_SESSIONS.get(str(session_token or ""))
    if active is None:
        return False
    if active.target.kind != target.kind or active.target.stable_id != target.stable_id:
        return False
    kind = str(preset_type or "").strip()
    if not kind:
        active.set_preset_baseline(None)
        return False
    from ..utils import detail_preset_change_guard

    _sync_preset_guard_state(target)
    active.set_preset_baseline(
        detail_preset_change_guard.capture_preset_settings(target, kind)
    )
    return True


def sync_actual_session(context, session, *, commit_graphs: bool = False) -> None:
    """詳細設定ダイアログの ``check()`` から毎回呼ばれる同期処理。

    ``commit_graphs=False`` (既定) では、線幅グラフ (線幅グラフ・白線幅
    グラフ・黒線幅グラフ) の編集内容をパラメータへ確定しない。グラフの
    ドラッグ操作のたびに重いメッシュ再生成が走るのを避けるため、確定は
    「適用」ボタン (``bmanga.effect_profile_graph_apply``) か、
    ``commit_graphs=True`` で呼ばれる ``commit_actual_session`` (OK確定)
    だけが行う。数値スライダー等グラフ以外の設定はこの関数を通じて
    従来どおり毎回反映される。
    """

    session.validate_target()
    target = session.target
    if target.kind == "coma":
        _sync_coma_curve(target.data)
    params = _curve_params(target)
    if params is not None:
        _ensure_curve_nodes(params)
        _request_curve_sync(params)
        if commit_graphs:
            _sync_curve_nodes(params)
    elif target.kind == "balloon":
        # 線種をグラフ非対応へ変えた後は、旧線種の短周期同期を残さない。
        _release_curve_sync(target)
    if target.kind == "effect":
        _write_effect_target(context, target)
    _sync_layer_object_state(target)
    columns = detail_dialog.current_column_count_for_target(target, session.mode)
    if columns != session.layout.column_count:
        session.set_current_columns(columns)
    _redraw(context)


def commit_actual_session(context, session) -> None:
    try:
        # OK確定時は、「適用」ボタンを押し忘れたグラフ編集も併せて確定する。
        sync_actual_session(context, session, commit_graphs=True)
        detail_dialog_state.commit_detail_session(session)
    finally:
        # 対象削除・移動による生存確認失敗でも、以後の詳細設定を塞がない。
        _discard_actual_session(session)
        _release_curve_sync(session.target)
        _resume_object_tool_after_dialog(context, session)


def cancel_actual_session(context, session) -> None:
    try:
        _release_curve_sync(session.target)
        failure = None
        for _attempt in range(2):
            try:
                detail_dialog_state.cancel_detail_session(session)
                failure = None
                break
            except Exception as exc:  # 1回目の部分復元失敗後に同じ対象へ再試行する
                failure = exc
        if failure is not None:
            raise failure
        target = session.target
        if target.kind == "coma":
            _restore_coma_curve(target.data)
        params = _curve_params(target)
        if params is not None:
            _restore_curve_nodes(params)
        _resync_restored_target(context, target)
        _redraw(context)
    finally:
        # 復元不能のエラーは上位へ伝えつつ、登録だけは必ず解放する。
        _discard_actual_session(session)
        _resume_object_tool_after_dialog(context, session)


def rollback_failed_actual_session(context, session) -> None:
    """最終同期の途中失敗を開始時snapshotまで戻し、開いたまま残さない。"""

    cancel_actual_session(context, session)


def abort_opening_actual_session(context, session) -> None:
    """ダイアログ生成失敗時に開始時状態へ戻し、セッションを必ず破棄する。"""

    try:
        cancel_actual_session(context, session)
    finally:
        _discard_actual_session(session)


def record_preset_selection(
    session_token: str,
    target,
    preset_name: str | None,
    *,
    preset_type: str | None = None,
) -> bool:
    session = _OPEN_ACTUAL_SESSIONS.get(str(session_token or ""))
    if session is None:
        return False
    if session.target.kind != target.kind or session.target.stable_id != target.stable_id:
        return False
    if preset_type is None:
        session.set_preset_selection(preset_name)
    else:
        session.set_preset_context(preset_type, preset_name)
    return True


def record_preset_selection_for_identity(
    session_token: str,
    target_kind: str,
    target_id: str,
    preset_name: str | None,
    *,
    preset_type: str | None = None,
) -> bool:
    session = _matching_open_session(session_token, target_kind, target_id)
    if session is None:
        return False
    if preset_type is None:
        session.set_preset_selection(preset_name)
    else:
        session.set_preset_context(preset_type, preset_name)
    return True


def reconcile_preset_reference_after_management(
    session_token: str,
    target_kind: str,
    target_id: str,
    preset_type: str,
    old_name: str,
    new_name: str | None,
    *,
    balloon_outline_json: str = "",
) -> bool:
    """改名／削除を固定対象の保存参照と親Cancel基準へ即時確定する。"""

    session = _matching_open_session(session_token, target_kind, target_id)
    if session is None or session.mode is not detail_dialog.DetailMode.ACTUAL:
        raise detail_dialog.DetailTargetNotFoundError(str(target_id or ""))
    binding = _preset_reference_binding(session.target, str(preset_type or ""))
    if binding is None:
        # テキスト・効果線・塗り・パターンカーブは値をコピーするだけで、
        # レイヤー側にプリセット名を保存しない。
        return False
    subject, attribute = binding
    old_value = str(old_name or "").strip()
    replacement = str(new_name or "").strip()
    if not old_value:
        raise detail_dialog.DetailContractError("managed preset name is required")
    snapshot = session.opening_snapshot
    updated_snapshot, baseline_changed = (
        detail_dialog_state.transform_detail_snapshot_fragment(
            snapshot,
            "preset_reference",
            lambda payload: detail_state_adapters.replace_preset_reference_snapshot(
                payload,
                old_value,
                replacement,
                balloon_outline_json=str(balloon_outline_json or ""),
            ),
        )
    )
    outline = str(balloon_outline_json or "")
    current_changed = _replace_live_preset_reference(
        session.target,
        subject,
        attribute,
        old_value,
        replacement,
        balloon_outline_json=outline,
    )
    if baseline_changed:
        session.replace_opening_snapshot(updated_snapshot)
    return current_changed or baseline_changed


def _preset_reference_binding(target, preset_type: str):
    if preset_type == "border" and target.kind == "coma":
        border = getattr(target.data, "border", None)
        return (border, "preset_name") if border is not None else None
    if preset_type == "balloon" and target.kind == "balloon":
        return target.data, "custom_preset_name"
    return None


def _replace_live_preset_reference(
    target,
    subject,
    attribute: str,
    expected: str,
    replacement: str,
    *,
    balloon_outline_json: str = "",
) -> bool:
    if subject is None or str(getattr(subject, attribute, "") or "").strip() != expected:
        return False
    if target.kind == "balloon":
        from ..utils import balloon_curve_object

        # 改名・削除は形状そのものを変えない。保存参照だけを確定し、
        # 現在の実体と輪郭キャッシュはそのまま維持する。
        with balloon_curve_object.suspend_auto_sync():
            if not replacement and balloon_outline_json:
                target.data.custom_outline_json = balloon_outline_json
            setattr(subject, attribute, replacement)
    else:
        setattr(subject, attribute, replacement)
    return True


def detail_action_session_is_open(
    session_token: str,
    target_kind: str,
    target_id: str,
) -> bool:
    return _matching_open_session(session_token, target_kind, target_id) is not None


def detail_action_target(session_token: str, target_kind: str, target_id: str):
    session = _matching_open_session(session_token, target_kind, target_id)
    return session.target if session is not None else None


def detail_action_is_allowed(
    session_token: str,
    action_id: str,
    target_kind: str,
    target_id: str,
) -> bool:
    session = _matching_open_session(session_token, target_kind, target_id)
    if session is None:
        return False
    try:
        spec = detail_dialog.get_detail_action_spec(action_id)
    except detail_dialog.DetailContractError:
        return False
    return spec.boundary is not detail_dialog.DetailActionBoundary.EXCLUDED


def execute_transactional_detail_action(
    context,
    session_token: str,
    action_id: str,
    target_kind: str,
    target_id: str,
    action,
):
    """通常の子操作を直前状態つきで実行し、失敗時に表示実体も戻す。"""

    session = _matching_open_session(session_token, target_kind, target_id)
    if session is None:
        raise detail_dialog.DetailTargetNotFoundError(str(target_id or ""))
    spec = detail_dialog.get_detail_action_spec(action_id)
    if spec.boundary is not detail_dialog.DetailActionBoundary.TRANSACTIONAL:
        raise detail_dialog.DetailContractError("detail action is not transactional")
    try:
        return detail_dialog_state.execute_detail_action(session, spec, action)
    except Exception as action_error:
        if (
            session.status is detail_dialog.DetailSessionStatus.OPEN
            and session.mode is detail_dialog.DetailMode.ACTUAL
        ):
            try:
                _resync_action_rollback(context, session.target)
            except Exception as resync_error:
                raise resync_error from action_error
        raise


def execute_independent_detail_action(
    session_token: str,
    action_id: str,
    target_kind: str,
    target_id: str,
    action,
    *,
    confirmed: bool = False,
):
    """親のキャンセル対象と重ならない独立操作だけを実行する。"""

    session = _matching_open_session(session_token, target_kind, target_id)
    if session is None:
        raise detail_dialog.DetailTargetNotFoundError(str(target_id or ""))
    spec = detail_dialog.get_detail_action_spec(action_id)
    if spec.boundary is not detail_dialog.DetailActionBoundary.INDEPENDENT_IMMEDIATE:
        raise detail_dialog.DetailContractError("detail action is not independent")
    return detail_dialog_state.execute_detail_action(
        session,
        spec,
        action,
        confirmed=confirmed,
    )


def record_detail_action(
    session_token: str,
    action_id: str,
    target_kind: str,
    target_id: str,
    result=None,
) -> bool:
    session = _matching_open_session(session_token, target_kind, target_id)
    if session is None:
        return False
    session.record_action(detail_dialog.get_detail_action_spec(action_id), result)
    return True


def register_preset_session(session) -> None:
    """プリセット本文内の固定対象操作から参照できる間だけ登録する。"""

    if session.mode is not detail_dialog.DetailMode.PRESET:
        raise detail_dialog.DetailContractError("preset session mode is required")
    session.require_open()
    session.validate_target()
    token = str(session.token or "")
    existing = _OPEN_PRESET_SESSIONS.get(token)
    if existing is not None and existing is not session:
        raise detail_dialog.DetailContractError("preset session token is already registered")
    _OPEN_PRESET_SESSIONS[token] = session


def ensure_preset_type_available(preset_type: str) -> None:
    """WindowManager共有scratchを使う同種プリセットの二重編集を拒否する。"""

    requested = str(preset_type or "").strip()
    stale_tokens = []
    for token, session in tuple(_OPEN_PRESET_SESSIONS.items()):
        active = session.status in {
            detail_dialog.DetailSessionStatus.OPEN,
            detail_dialog.DetailSessionStatus.RESTORE_FAILED,
        }
        if not active:
            stale_tokens.append(token)
            continue
        namespace = str(getattr(session.target, "namespace", "") or "")
        if namespace == requested:
            raise detail_dialog.DetailContractError(
                "同じ種類のプリセット詳細設定は同時に2つ開けません。"
                "先に開いている詳細設定を閉じてください"
            )
    for token in stale_tokens:
        _OPEN_PRESET_SESSIONS.pop(token, None)


def unregister_preset_session(session) -> None:
    if session is None:
        return
    token = str(getattr(session, "token", "") or "")
    if _OPEN_PRESET_SESSIONS.get(token) is session:
        _OPEN_PRESET_SESSIONS.pop(token, None)


def _resync_action_rollback(context, target) -> None:
    if target.kind == "coma":
        _restore_coma_curve(target.data)
    params = _curve_params(target)
    if params is not None:
        _restore_curve_nodes(params)
    _resync_restored_target(context, target)
    _sync_layer_object_state(target)
    _redraw(context)


def _matching_open_session(session_token: str, target_kind: str, target_id: str):
    token = str(session_token or "")
    session = _OPEN_ACTUAL_SESSIONS.get(token) or _OPEN_PRESET_SESSIONS.get(token)
    if session is None:
        return None
    target = session.target
    if target.kind != str(target_kind or "") or target.stable_id != str(target_id or ""):
        return None
    try:
        session.require_open()
        session.validate_target()
    except Exception:
        return None
    return session


def preset_session_is_open(session_token: str, target) -> bool:
    session = _OPEN_ACTUAL_SESSIONS.get(str(session_token or ""))
    if session is None:
        return False
    if session.target.kind != target.kind or session.target.stable_id != target.stable_id:
        return False
    try:
        session.require_open()
        session.validate_target()
    except Exception:  # 閉じた画面／削除済み対象は「開いていない」として扱う
        return False
    return True


def effect_target_has_open_actual_session(obj, layer) -> bool:
    """詳細設定が固定している効果線だけを、リンク伝播の対象外と判定する。"""

    from ..utils import layer_object_model

    stable_id = layer_object_model.stable_id(obj)
    if stable_id in _PREPARING_EFFECT_TARGET_IDS:
        return True
    for session in tuple(_OPEN_ACTUAL_SESSIONS.values()):
        target = getattr(session, "target", None)
        if target is None or target.kind != "effect":
            continue
        # bpy RNAラッパーは同じ実体でもPythonの ``is`` が一致しない場合がある。
        # 1管理Object＝1効果線の永続IDを比較し、固定対象を取り違えない。
        if target.stable_id != stable_id:
            continue
        try:
            session.require_open()
            session.validate_target()
        except Exception:  # staleな別画面は固定対象の所有者に数えない
            continue
        return True
    return False


def effect_selection_is_allowed(context, obj) -> bool:
    """効果線詳細の固定対象以外へScene共有値を切り替えない。"""

    from ..utils import layer_object_model

    stable_id = layer_object_model.stable_id(obj)
    if not stable_id:
        return True
    scene_key = _scene_session_key(context)
    stale_sessions = []
    for token, session in tuple(_OPEN_ACTUAL_SESSIONS.items()):
        if _OPEN_ACTUAL_SCENE_KEYS.get(token) != scene_key:
            continue
        active = session.status in {
            detail_dialog.DetailSessionStatus.OPEN,
            detail_dialog.DetailSessionStatus.RESTORE_FAILED,
        }
        if not active:
            stale_sessions.append(session)
            continue
        target = getattr(session, "target", None)
        if target is not None and target.kind == "effect":
            return target.stable_id == stable_id
    for session in stale_sessions:
        _discard_actual_session(session)
    return True


def draw_actual_session(layout, context, session, *, preset_list_owner=None) -> bool:
    from ..panels.detail_drawers import draw_detail_dialog

    return draw_detail_dialog(
        layout,
        context,
        session,
        detail_dialog.DetailMode.ACTUAL,
        preset_list_owner=preset_list_owner,
    )


def _load_effect_target(context, target) -> None:
    from . import effect_line_op
    from ..utils import layer_object_model

    scene = getattr(context, "scene", None)
    stable_id = layer_object_model.stable_id(target.object_ref)
    if scene is None or not stable_id:
        raise RuntimeError("効果線の固定対象を設定できません")
    if hasattr(scene, "bmanga_active_layer_kind"):
        scene.bmanga_active_layer_kind = "effect"
    if hasattr(scene, "bmanga_active_effect_layer_name"):
        scene.bmanga_active_effect_layer_name = stable_id

    effect_line_op._load_layer_params_to_scene(
        context,
        target.object_ref,
        target.data,
    )


def _prepare_layer_object_state(target) -> None:
    if target.kind not in {"gp", "effect"}:
        return
    from ..utils import layer_object_model

    layer_object_model.initialize_user_state(target.object_ref)


def _sync_layer_object_state(target) -> None:
    if target.kind not in {"gp", "effect"}:
        return
    from ..utils import layer_object_model, object_naming

    obj = target.object_ref
    if obj is None:
        return
    layer_object_model.set_user_visible(
        obj, bool(obj.get(layer_object_model.PROP_USER_VISIBLE, True))
    )
    layer_object_model.set_user_locked(
        obj, bool(obj.get(layer_object_model.PROP_USER_LOCKED, False))
    )
    title = str(obj.get(object_naming.PROP_TITLE, "") or "").strip()
    if title:
        layer_object_model.set_display_title(obj, title)


def _resync_restored_target(context, target) -> None:
    if target.kind == "image":
        from ..utils import image_real_object

        image_real_object.on_image_entry_changed(target.data)
    elif target.kind == "image_path":
        from ..utils import image_path_object

        image_path_object.on_image_path_entry_changed(target.data)
    elif target.kind == "raster":
        from . import raster_layer_op

        raster_layer_op.sync_raster_runtime_display(context, target.data)
    elif target.kind == "fill":
        from ..utils import fill_real_object

        fill_real_object.on_fill_entry_changed(target.data)
    elif target.kind in {"balloon", "balloon_tail"}:
        from ..utils import balloon_curve_object

        balloon_curve_object.on_balloon_entry_changed(target.data)
    elif target.kind == "text":
        from ..utils import text_real_object

        text_real_object.on_text_entry_changed(target.data)


def _write_effect_target(context, target) -> None:
    from . import effect_line_op

    bounds = effect_line_op.effect_layer_bounds(target.object_ref, target.data)
    if bounds is None:
        return
    effect_line_op._write_effect_strokes(
        context,
        target.object_ref,
        target.data,
        bounds,
        params_override=target.params,
        propagate_link=False,
    )


def _curve_params(target):
    if target.kind == "effect":
        return target.params
    if target.kind == "image_path":
        return target.data
    if target.kind != "balloon":
        return None
    try:
        from ..utils import balloon_shapes

        style = balloon_shapes.normalize_line_style(
            str(getattr(target.data, "line_style", "") or "")
        )
    except Exception:  # 対象が破棄済みなら曲線UI準備を行わず上位の生存確認へ委ねる
        return None
    return target.data if style in {"uni_flash", "white_outline"} else None


def active_curve_params_for_scene(context, *, profile_key: str = "main"):
    """「適用」ボタンが確定すべきパラメータを、現在開いている詳細設定から探す。

    同じSceneで開いている詳細設定 (効果線・フキダシ・画像パス) のうち、
    線幅グラフを持つ対象があればそのパラメータを返す。``profile_key`` が
    "white"/"black" の場合、白抜き線関連の属性を持たない対象 (画像パス等)
    は候補から除外する。開いている詳細設定が無い、または対象が
    ``profile_key`` のグラフを持たない場合は ``None``。
    """

    from ..utils import effect_inout_curve

    scene_key = _scene_session_key(context)
    fields, _node_name, _source_prop, _label = effect_inout_curve.profile_spec_for_key(
        profile_key
    )
    for token, session in tuple(_OPEN_ACTUAL_SESSIONS.items()):
        if _OPEN_ACTUAL_SCENE_KEYS.get(token) != scene_key:
            continue
        if session.status not in {
            detail_dialog.DetailSessionStatus.OPEN,
            detail_dialog.DetailSessionStatus.RESTORE_FAILED,
        }:
            continue
        try:
            session.validate_target()
        except Exception:  # noqa: BLE001
            continue
        params = _curve_params(session.target)
        if params is None:
            continue
        if fields is not None and not all(hasattr(params, attr) for attr in fields.values()):
            continue
        return params
    return None


def _ensure_curve_nodes(params) -> None:
    from ..utils import balloon_shapes, effect_inout_curve

    effect_inout_curve.ensure_ui_nodes(params)
    effect_inout_curve.ensure_profile_node(params)
    effect_type = str(getattr(params, "effect_type", "") or "")
    line_style = balloon_shapes.normalize_line_style(
        str(getattr(params, "line_style", "") or "")
    )
    if effect_type == "white_outline" or line_style == "white_outline":
        _ensure_white_profile_nodes(params, effect_inout_curve)


def _ensure_white_profile_nodes(params, curve) -> None:
    for fields, node_name, source_prop, label in (
        (curve.WHITE_PROFILE_FIELDS, curve.WHITE_PROFILE_NODE_NAME, curve.WHITE_PROFILE_SOURCE_PROP, "白線幅グラフ"),
        (curve.BLACK_PROFILE_FIELDS, curve.BLACK_PROFILE_NODE_NAME, curve.BLACK_PROFILE_SOURCE_PROP, "黒線幅グラフ"),
    ):
        curve.ensure_profile_node(
            params,
            fields=fields,
            node_name=node_name,
            source_prop=source_prop,
            label=label,
        )


def _request_curve_sync(params) -> None:
    from ..utils import balloon_shapes, effect_inout_curve

    effect_inout_curve.request_live_profile_sync(params)
    effect_type = str(getattr(params, "effect_type", "") or "")
    line_style = balloon_shapes.normalize_line_style(
        str(getattr(params, "line_style", "") or "")
    )
    if effect_type != "white_outline" and line_style != "white_outline":
        return
    for fields, node_name, source_prop in (
        (
            effect_inout_curve.WHITE_PROFILE_FIELDS,
            effect_inout_curve.WHITE_PROFILE_NODE_NAME,
            effect_inout_curve.WHITE_PROFILE_SOURCE_PROP,
        ),
        (
            effect_inout_curve.BLACK_PROFILE_FIELDS,
            effect_inout_curve.BLACK_PROFILE_NODE_NAME,
            effect_inout_curve.BLACK_PROFILE_SOURCE_PROP,
        ),
    ):
        effect_inout_curve.request_live_profile_sync(
            params,
            fields=fields,
            node_name=node_name,
            source_prop=source_prop,
        )


def _release_curve_sync(target) -> None:
    from ..utils import effect_inout_curve

    if target.kind == "effect":
        subject = target.params
    elif target.kind in {"balloon", "image_path"}:
        subject = target.data
    else:
        subject = None
    effect_inout_curve.release_live_profile_sync(subject)


def _sync_curve_nodes(params) -> bool:
    from ..utils import effect_inout_curve

    changed = bool(effect_inout_curve.sync_ui_nodes_to_params(params))
    changed |= bool(effect_inout_curve.sync_profile_node_bidirectional(params))
    changed |= bool(effect_inout_curve.sync_active_profile_nodes_to_params(params))
    return changed


def _restore_curve_nodes(params) -> None:
    from ..utils import effect_inout_curve

    effect_inout_curve.restore_ui_nodes_from_params(params)


def _sync_coma_curve(coma) -> None:
    from ..utils import coma_blur_curve

    coma_blur_curve.sync_active_coma_curve_to_border(coma)
    border = getattr(coma, "border", None)
    if str(getattr(border, "style", "solid") or "solid") == "brush":
        coma_blur_curve.ensure_ui_curve_node(border)


def _restore_coma_curve(coma) -> None:
    from ..utils import coma_blur_curve

    coma_blur_curve.restore_ui_curve_from_border(getattr(coma, "border", None))


def _redraw(context) -> None:
    from ..utils import layer_stack

    layer_stack.sync_layer_stack_after_data_change(context)
    layer_stack.tag_view3d_redraw(context)


def cleanup_all_sessions(context=None) -> tuple[Exception, ...]:
    """登録解除時に復元を試み、成否にかかわらず全ロックを解放する。"""

    actual_sessions = tuple(_OPEN_ACTUAL_SESSIONS.values())
    preset_sessions = tuple(_OPEN_PRESET_SESSIONS.values())
    # 復元中の更新コールバックから古いセッションを再発見させない。
    _OPEN_ACTUAL_SESSIONS.clear()
    _OPEN_ACTUAL_SCENE_KEYS.clear()
    _OPEN_PRESET_SESSIONS.clear()
    _PREPARING_EFFECT_TARGET_IDS.clear()
    # unregister中は詳細設定を閉じてもツールを再起動しない。
    _RESUME_OBJECT_TOOL_TOKENS.clear()
    if context is None:
        try:
            import bpy

            context = bpy.context
        except (ImportError, AttributeError):
            context = None
    failures: list[Exception] = []
    for session in actual_sessions:
        try:
            if session.status in {
                detail_dialog.DetailSessionStatus.OPEN,
                detail_dialog.DetailSessionStatus.RESTORE_FAILED,
            }:
                if context is None:
                    detail_dialog_state.cancel_detail_session(session)
                else:
                    cancel_actual_session(context, session)
        except Exception as exc:  # 登録解除は後続クラスの解除を必ず続ける
            failures.append(exc)
        finally:
            try:
                _release_curve_sync(session.target)
            except Exception as exc:
                failures.append(exc)
    for session in preset_sessions:
        try:
            if session.status in {
                detail_dialog.DetailSessionStatus.OPEN,
                detail_dialog.DetailSessionStatus.RESTORE_FAILED,
            }:
                detail_dialog_state.cancel_detail_session(session)
        except Exception as exc:
            failures.append(exc)
        finally:
            try:
                _release_curve_sync(session.target)
            except Exception as exc:
                failures.append(exc)
    # cancel処理が将来変更されても、登録解除後にロックだけ残らない最終保証。
    _OPEN_ACTUAL_SESSIONS.clear()
    _OPEN_ACTUAL_SCENE_KEYS.clear()
    _OPEN_PRESET_SESSIONS.clear()
    _PREPARING_EFFECT_TARGET_IDS.clear()
    return tuple(failures)


__all__ = [
    "abort_opening_actual_session",
    "active_curve_params_for_scene",
    "available_dialog_width",
    "begin_actual_session",
    "cancel_actual_session",
    "commit_actual_session",
    "cleanup_all_sessions",
    "detail_action_is_allowed",
    "detail_action_session_is_open",
    "detail_action_target",
    "draw_actual_session",
    "effect_selection_is_allowed",
    "execute_independent_detail_action",
    "execute_transactional_detail_action",
    "initial_preset_selection_for_target",
    "mark_preset_settings_saved",
    "preset_switch_requires_confirmation",
    "ensure_preset_type_available",
    "prepare_actual_target",
    "preset_session_is_open",
    "record_preset_selection",
    "record_preset_selection_for_identity",
    "reconcile_preset_reference_after_management",
    "record_detail_action",
    "register_preset_session",
    "rollback_failed_actual_session",
    "sync_actual_session",
    "unregister_preset_session",
]
