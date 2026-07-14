"""詳細設定の公開契約だけを使う Blender 実機テスト補助。"""

from __future__ import annotations

import importlib
from types import SimpleNamespace


def _sub(module_name: str, path: str):
    return importlib.import_module(f"{module_name}.{path}")


def resolve_actual_target(module_name: str, context, value, kind: str = ""):
    """実Objectまたは安定ID付きデータから固定対象を解決する。"""

    resolver = _sub(module_name, "utils.detail_target_resolver")
    if hasattr(value, "get") and str(value.get("bmanga_kind", "") or ""):
        return resolver.resolve_target_from_object(context, value)
    stable_id = str(getattr(value, "id", "") or value or "")
    if not stable_id or not kind:
        raise AssertionError("公開詳細対象の種別と安定IDが必要です")
    return resolver.resolve_target_from_object(context, stable_id, kind=kind)


def open_actual_session(module_name: str, context, value, kind: str = ""):
    target = resolve_actual_target(module_name, context, value, kind)
    runtime = _sub(module_name, "operators.detail_dialog_runtime")
    return runtime.begin_actual_session(context, target)


def draw_actual_detail(module_name: str, layout, context, value, kind: str = ""):
    """共通描画を呼び、確定済みセッションと固定幅を返す。"""

    runtime = _sub(module_name, "operators.detail_dialog_runtime")
    session = open_actual_session(module_name, context, value, kind)
    runtime.draw_actual_session(layout, context, session)
    runtime.commit_actual_session(context, session)
    return session


def sync_actual_session(module_name: str, context, session) -> None:
    _sub(module_name, "operators.detail_dialog_runtime").sync_actual_session(context, session)


def close_actual_session(module_name: str, context, session) -> None:
    _sub(module_name, "operators.detail_dialog_runtime").commit_actual_session(context, session)


def draw_preset_detail(
    module_name: str,
    layout,
    context,
    preset_type: str,
    data,
    *,
    preset_name: str = "公開契約テスト",
    params=None,
):
    """プリセット対象を固定し、保存可能サブセットを共通描画する。"""

    contract = _sub(module_name, "utils.detail_dialog")
    state = _sub(module_name, "utils.detail_dialog_state")
    adapters = _sub(module_name, "utils.detail_state_adapters")
    drawers = _sub(module_name, "panels.detail_drawers")
    target = contract.resolve_preset_detail_target(
        preset_type,
        preset_name,
        data,
        params=params,
    )
    session = state.begin_detail_session(
        target,
        contract.DetailMode.PRESET,
        registry=adapters.ACTUAL_DETAIL_STATE_REGISTRY,
        target_validator=lambda identity: identity.stable_id == target.stable_id,
    )
    drawers.draw_detail_dialog(layout, context, session, contract.DetailMode.PRESET)
    state.commit_detail_session(session)
    return session


def draw_all_actual_entry_points(
    module_name: str,
    context,
    session,
    layout_factory,
):
    """共通描画・右クリック・レイヤー一覧の公開draw記録を返す。"""

    contract = _sub(module_name, "utils.detail_dialog")
    drawers = _sub(module_name, "panels.detail_drawers")
    right_click = _sub(module_name, "operators.layer_detail_op")
    layer_stack_detail = _sub(module_name, "operators.layer_stack_detail_op")

    common_layout = layout_factory()
    drawers.draw_detail_dialog(common_layout, context, session, contract.DetailMode.ACTUAL)

    right_layout = layout_factory()
    right_click.BMANGA_OT_layer_detail_open.draw(
        SimpleNamespace(layout=right_layout, _detail_session=session),
        context,
    )

    stack_layout = layout_factory()
    layer_stack_detail.BMANGA_OT_layer_stack_detail.draw(
        SimpleNamespace(layout=stack_layout, _detail_session=session),
        context,
    )
    return common_layout, right_layout, stack_layout
