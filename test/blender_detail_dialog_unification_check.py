"""Blender実機用: 詳細設定一元化の共通契約を生成データだけで検証する。

本テストは実装先行の受入契約である。未実装の段階でも最初の一件で
停止せず、3入口、対象固定、幅、OK／キャンセル、独立即時操作を
個別ケースとして報告する。ユーザー作品は開かず、一時作品だけを使う。
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import shutil
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_detail_dialog_unification"
PRESET_NAME = "__detail_dialog_contract_text__"
PRESET_COPY_NAME = "__detail_dialog_contract_text_copy__"

_API_MODULES = (
    "utils.detail_dialog",
    "utils.detail_dialog_state",
    "panels.detail_drawers",
    "panels.detail_drawers.dispatcher",
)


@dataclass
class _Fixture:
    context: Any
    work: Any
    raster_a: Any
    raster_b: Any
    object_a: Any
    object_b: Any
    preset_path: Path


class _OperatorProxy:
    def __init__(self, records: list[tuple], op_id: str):
        object.__setattr__(self, "_records", records)
        object.__setattr__(self, "_op_id", op_id)

    def __setattr__(self, name: str, value: Any) -> None:
        self._records.append(("operator_property", self._op_id, name, value))
        object.__setattr__(self, name, value)


class _RecordingLayout:
    def __init__(self, records: list[tuple] | None = None):
        self.records = records if records is not None else []
        self.enabled = True
        self.active = True
        self.alert = False
        self.scale_x = 1.0
        self.scale_y = 1.0
        self.use_property_split = False
        self.use_property_decorate = True
        self.operator_context = "INVOKE_DEFAULT"

    def _child(self, *_args, **_kwargs):
        return _RecordingLayout(self.records)

    box = _child
    row = _child
    column = _child
    split = _child
    grid_flow = _child
    column_flow = _child

    def separator(self, **_kwargs):
        self.records.append(("separator",))

    def label(self, text: str = "", **_kwargs):
        self.records.append(("label", str(text)))

    def prop(self, _owner, attr: str, **_kwargs):
        self.records.append(("prop", str(attr)))

    def prop_search(self, owner, attr: str, *_args, **kwargs):
        self.prop(owner, attr, **kwargs)

    def operator(self, op_id: str, **_kwargs):
        self.records.append(("operator", str(op_id)))
        return _OperatorProxy(self.records, str(op_id))

    def operator_menu_enum(self, op_id: str, prop: str, **_kwargs):
        self.records.append(("operator_menu_enum", str(op_id), str(prop)))
        return _OperatorProxy(self.records, str(op_id))

    def menu(self, menu_id: str, **_kwargs):
        self.records.append(("menu", str(menu_id)))

    def popover(self, panel: str = "", **_kwargs):
        self.records.append(("popover", str(panel)))

    def context_pointer_set(self, *_args, **_kwargs):
        return None

    def template_list(self, *args, **_kwargs):
        self.records.append(("template_list", str(args[0]) if args else ""))

    def template_curve_mapping(self, *_args, **_kwargs):
        self.records.append(("template_curve_mapping",))

    def __getattr__(self, name: str):
        if not name.startswith("template_"):
            raise AttributeError(name)

        def _template(*_args, **_kwargs):
            self.records.append((name,))

        return _template


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _sub(path: str):
    __import__(f"{MOD_NAME}.{path}")
    return sys.modules[f"{MOD_NAME}.{path}"]


def _find_api(name: str):
    for path in _API_MODULES:
        try:
            module = _sub(path)
        except ModuleNotFoundError:
            continue
        value = getattr(module, name, None)
        if value is not None:
            return value
    raise AssertionError(f"公開契約APIが未実装です: {name}")


def _call_contract(function, values: dict[str, Any]):
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    for parameter in inspect.signature(function).parameters.values():
        if parameter.kind in {parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD}:
            continue
        if parameter.name not in values:
            if parameter.default is parameter.empty:
                raise AssertionError(
                    f"{function.__name__} の必須引数をテスト契約から渡せません: {parameter.name}"
                )
            continue
        if parameter.kind is parameter.POSITIONAL_ONLY:
            args.append(values[parameter.name])
        else:
            kwargs[parameter.name] = values[parameter.name]
    return function(*args, **kwargs)


def _api_values(fixture: _Fixture, **extra) -> dict[str, Any]:
    values = {
        "context": fixture.context,
        "scene": fixture.context.scene,
        "work": fixture.work,
    }
    values.update(extra)
    return values


def _create_fixture(temp_root: Path) -> _Fixture:
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "DetailDialogContract.bmanga"))
    assert "FINISHED" in result, f"一時作品を作成できません: {result}"
    result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
    assert "FINISHED" in result, f"一時ページを開けません: {result}"
    for _ in range(2):
        result = bpy.ops.bmanga.raster_layer_add(
            "EXEC_DEFAULT", dpi=30, bit_depth="gray8", enter_paint=False
        )
        assert "FINISHED" in result, f"テスト用ラスターを作成できません: {result}"
    context = bpy.context
    rasters = context.scene.bmanga_raster_layers
    raster_a, raster_b = rasters[-2], rasters[-1]
    raster_a.title, raster_b.title = "対象A", "対象B"
    raster_a.opacity, raster_b.opacity = 81.0, 42.0
    raster_op = _sub("operators.raster_layer_op")
    object_a = raster_op.ensure_raster_plane(context, raster_a)
    object_b = raster_op.ensure_raster_plane(context, raster_b)
    assert object_a is not None and object_b is not None, "テスト用ラスター実体がありません"
    work = _sub("core.work").get_work(context)
    text_presets = _sub("io.text_presets")
    preset_path = text_presets.save_local_preset(
        None, PRESET_NAME, "詳細設定契約", {"font_size_unit": "q", "font_size_value": 14.0}
    )
    return _Fixture(context, work, raster_a, raster_b, object_a, object_b, preset_path)


def _activate_b(fixture: _Fixture) -> None:
    scene = fixture.context.scene
    for index, entry in enumerate(scene.bmanga_raster_layers):
        if str(entry.id) == str(fixture.raster_b.id):
            scene.bmanga_active_raster_layer_index = index
            break
    for obj in tuple(fixture.context.selected_objects):
        obj.select_set(False)
    fixture.object_b.select_set(True)
    fixture.context.view_layer.objects.active = fixture.object_b


def _stack_item_a(fixture: _Fixture):
    stack_api = _sub("utils.layer_stack")
    stack = stack_api.sync_layer_stack(fixture.context, preserve_active_index=True)
    uid = stack_api.target_uid("raster", str(fixture.raster_a.id))
    for index, item in enumerate(stack or ()):
        if stack_api.stack_item_uid(item) == uid:
            return item, index, uid, stack_api.resolve_stack_item(fixture.context, item)
    raise AssertionError("対象Aのレイヤー一覧行が見つかりません")


def _target_from_stack(fixture: _Fixture):
    item, index, uid, resolved = _stack_item_a(fixture)
    resolver = _find_api("resolve_detail_target_from_stack")
    target_type = _find_api("DetailTarget")

    def target_resolver(key: str):
        if str(key) != uid:
            return None
        return target_type(
            kind="raster",
            stable_id=str(fixture.raster_a.id),
            stack_uid=uid,
            data=fixture.raster_a,
            object_ref=fixture.object_a,
        )

    return _call_contract(
        resolver,
        _api_values(
            fixture,
            item=item,
            stack_item=item,
            index=index,
            stack_index=index,
            uid=uid,
            stack_uid=uid,
            resolved=resolved,
            resolver=target_resolver,
        ),
    )


def _target_from_object(fixture: _Fixture):
    resolver = _find_api("resolve_detail_target_from_object")
    target_type = _find_api("DetailTarget")
    stable_id = str(fixture.raster_a.id)

    def target_resolver(key: str):
        if str(key) != stable_id:
            return None
        return target_type(
            kind="raster",
            stable_id=stable_id,
            stack_uid=_stack_item_a(fixture)[2],
            data=fixture.raster_a,
            object_ref=fixture.object_a,
        )

    return _call_contract(
        resolver,
        _api_values(
            fixture,
            obj=fixture.object_a,
            object=fixture.object_a,
            object_ref=fixture.object_a,
            bmanga_id=str(fixture.raster_a.id),
            stable_id=str(fixture.raster_a.id),
            kind="raster",
            resolver=target_resolver,
        ),
    )


def _target_from_preset(fixture: _Fixture):
    resolver = _find_api("resolve_preset_detail_target")
    text_presets = _sub("io.text_presets")
    preset = text_presets.load_preset_by_name(PRESET_NAME)
    assert preset is not None, "テスト用プリセットが見つかりません"
    scratch = fixture.context.window_manager.bmanga_preset_scratch_text
    text_presets.reset_entry_to_defaults(scratch)
    text_presets.apply_to_entry(scratch, preset.data)
    return _call_contract(
        resolver,
        _api_values(
            fixture,
            preset_type="text",
            kind="text",
            preset_name=PRESET_NAME,
            name=PRESET_NAME,
            data=scratch,
            params=None,
        ),
    )


def _mode(name: str):
    mode_type = _find_api("DetailMode")
    value = getattr(mode_type, name, None)
    assert value is not None, f"DetailMode.{name} がありません"
    return value


def _snapshot(fixture: _Fixture, target, mode):
    return _call_contract(
        _find_api("snapshot_detail_state"),
        _api_values(fixture, target=target, mode=mode),
    )


def _restore(fixture: _Fixture, target, snapshot, mode) -> None:
    _call_contract(
        _find_api("restore_detail_state"),
        _api_values(fixture, target=target, snapshot=snapshot, mode=mode),
    )


def _make_session(fixture: _Fixture, target, snapshot, mode, token: str):
    factory = _find_api("begin_detail_session")
    return _call_contract(
        factory,
        _api_values(
            fixture,
            token=token,
            target=target,
            mode=mode,
            target_validator=lambda _identity: True,
        ),
    )


def _field(data, name: str):
    return data.get(name) if isinstance(data, dict) else getattr(data, name)


def _set_field(data, name: str, value: Any) -> None:
    if isinstance(data, dict):
        data[name] = value
    else:
        setattr(data, name, value)


def _operator_class(path: str, name: str):
    cls = getattr(_sub(path), name, None)
    assert cls is not None, f"入口オペレーターがありません: {name}"
    return cls


def _test_public_api() -> None:
    names = (
        "DetailTarget",
        "DetailMode",
        "DetailLayoutSpec",
        "resolve_detail_target_from_stack",
        "resolve_detail_target_from_object",
        "resolve_preset_detail_target",
        "resolve_detail_layout",
        "snapshot_detail_state",
        "restore_detail_state",
        "apply_preset_to_target",
        "draw_detail_dialog",
    )
    missing = [name for name in names if _api_missing(name)]
    assert not missing, f"共通契約の公開APIが不足しています: {missing}"
    session_names = ("DetailEditSession", "DetailSession")
    assert any(not _api_missing(name) for name in session_names), (
        "ダイアログ単位で対象と開始時状態を保持する編集セッションがありません"
    )


def _api_missing(name: str) -> bool:
    try:
        _find_api(name)
    except AssertionError:
        return True
    return False


def _test_entry_registration() -> None:
    expected = (
        ("operators.layer_stack_detail_op", "BMANGA_OT_layer_stack_detail", "bmanga.layer_stack_detail"),
        ("operators.layer_detail_op", "BMANGA_OT_layer_detail_open", "bmanga.layer_detail_open"),
        ("operators.preset_detail_op", "BMANGA_OT_preset_detail_edit", "bmanga.preset_detail_edit"),
    )
    for path, class_name, op_id in expected:
        cls = _operator_class(path, class_name)
        assert cls.bl_idname == op_id, f"入口IDが変わっています: {cls.bl_idname}"
        assert hasattr(bpy.types, class_name), f"入口がBlenderへ登録されていません: {class_name}"


def _test_entry_contract(path: str, class_name: str, resolver_name: str) -> None:
    cls = _operator_class(path, class_name)
    source = inspect.getsource(sys.modules[cls.__module__])
    missing = [resolver_name] if resolver_name not in source else []
    if not any(name in source for name in ("resolve_detail_layout", "begin_actual_session")):
        missing.append("固定最大幅セッション")
    if not any(name in source for name in ("draw_detail_dialog", "draw_actual_session")):
        missing.append("共通描画")
    assert not missing, f"{cls.bl_idname} が共通契約へ接続されていません: {missing}"


def _assert_target_a(target, fixture: _Fixture, entrance: str) -> None:
    assert target is not None, f"{entrance}から対象を解決できません"
    assert str(getattr(target, "kind", "")) == "raster", f"{entrance}の種別が違います"
    assert str(getattr(target, "stable_id", "")) == str(fixture.raster_a.id), (
        f"{entrance}がアクティブな対象Bへすり替わりました"
    )


def _test_stack_target_fixed(fixture: _Fixture) -> None:
    _activate_b(fixture)
    _assert_target_a(_target_from_stack(fixture), fixture, "レイヤー一覧入口")


def _test_stack_uid_priority_and_detail_icon(fixture: _Fixture) -> None:
    stack_api = _sub("utils.layer_stack")
    resolver = _sub("utils.detail_target_resolver")
    stack_op = _sub("operators.layer_stack_detail_op")
    panel = _sub("panels.gpencil_panel")
    item, index, uid, resolved = _stack_item_a(fixture)
    stack = fixture.context.scene.bmanga_layer_stack
    wrong_index = next(i for i in range(len(stack)) if i != index)

    assert stack_op._resolve_detail_stack_index(stack, wrong_index, uid) == index, (
        "再同期後も行番号を優先し、別レイヤーへ対象がすり替わります"
    )
    assert stack_op._resolve_detail_stack_index(stack, wrong_index, "raster:missing") == -1, (
        "指定UIDが消えた時に、同じ行番号の別レイヤーへフォールバックしました"
    )

    layout = _RecordingLayout()
    panel._draw_type_icon(
        layout,
        index,
        "BRUSH_DATA",
        item=item,
        target=resolved.get("target"),
    )
    assert (
        "operator_property",
        "bmanga.layer_stack_detail",
        "uid",
        uid,
    ) in layout.records, "レイヤー一覧の歯車から固定UIDが渡されていません"

    fixture.context.scene.bmanga_active_layer_stack_index = index
    commands = _sub("ui.context_menu").selection_command_items(fixture.context)
    detail_command = next(command for command in commands if command["label"] == "詳細設定")
    detail_props = detail_command.get("props", {})
    if detail_command["operator"] == "bmanga.layer_stack_detail":
        assert detail_props.get("uid") == uid, (
            "レイヤー一覧の右クリックから固定UIDが渡されていません"
        )
    else:
        assert detail_props.get("bmanga_id") == str(fixture.raster_a.id), (
            "右クリックから固定IDが渡されていません"
        )

    missing_layout = _RecordingLayout()
    panel._draw_type_icon(
        missing_layout,
        index,
        "BRUSH_DATA",
        item=item,
        target=None,
    )
    assert not any(record[:2] == ("operator", "bmanga.layer_stack_detail") for record in missing_layout.records), (
        "実体を解決できない行にも詳細設定ボタンが表示されました"
    )
    assert not resolver.can_open_actual_detail("coma_preview", resolved.get("target")), (
        "仮想行のコマプレビューが詳細設定可能と判定されました"
    )


def _test_object_target_fixed(fixture: _Fixture) -> None:
    _activate_b(fixture)
    _assert_target_a(_target_from_object(fixture), fixture, "右クリック入口")


def _test_preset_target(fixture: _Fixture) -> None:
    _activate_b(fixture)
    first = _target_from_preset(fixture)
    second = _target_from_preset(fixture)
    assert first is not None and getattr(first, "data", None) is not None, "プリセット一時設定がありません"
    assert str(getattr(first, "kind", "")) == "text", "プリセットの通常描画種別が違います"
    assert getattr(first, "object_ref", None) is None, "プリセットが実レイヤーに結び付いています"
    assert getattr(first, "stable_id", None) == getattr(second, "stable_id", None), (
        "同じプリセットの安定IDが再解決で変わりました"
    )


def _test_common_draw_records(fixture: _Fixture) -> None:
    _activate_b(fixture)
    mode = _mode("ACTUAL")
    stack_target = _target_from_stack(fixture)
    object_target = _target_from_object(fixture)
    draw = _find_api("draw_detail_dialog")
    layouts = [_RecordingLayout(), _RecordingLayout()]
    for index, target in enumerate((stack_target, object_target)):
        snapshot = _snapshot(fixture, target, mode)
        session = _make_session(fixture, target, snapshot, mode, f"actual-{index}")
        _call_contract(
            draw,
            _api_values(fixture, layout=layouts[index], session=session, target=target, mode=mode),
        )
    assert layouts[0].records, "共通描画の記録が空です"
    signatures = []
    for layout in layouts:
        signatures.append(
            [
                (*record[:3], "<session>")
                if len(record) == 4
                and record[0] == "operator_property"
                and record[2] == "session_token"
                else record
                for record in layout.records
            ]
        )
    assert signatures[0] == signatures[1], (
        "レイヤー一覧と右クリックで項目または順序が一致しません: "
        f"{signatures[0]!r} != {signatures[1]!r}"
    )


def _synthetic_effect_target(effect_type: str):
    target_type = _find_api("DetailTarget")
    params = SimpleNamespace(effect_type=effect_type)
    return target_type(
        kind="effect",
        stable_id="effect-contract",
        stack_uid="effect:effect-contract",
        data=params,
        object_ref=None,
        params=params,
    )


def _test_fixed_max_width(fixture: _Fixture) -> None:
    resolver = _find_api("resolve_detail_layout")
    mode = _mode("ACTUAL")
    specs = []
    for effect_type in ("focus", "white_outline"):
        target = _synthetic_effect_target(effect_type)
        spec = _call_contract(resolver, _api_values(fixture, target=target, mode=mode))
        assert spec is not None, f"効果線幅を解決できません: {effect_type}"
        assert int(getattr(spec, "column_count", 0)) >= 1, f"列数が不正です: {effect_type}"
        assert float(getattr(spec, "dialog_width", 0.0)) > 0.0, f"幅が不正です: {effect_type}"
        specs.append(spec)
    assert specs[0].dialog_width == specs[1].dialog_width, (
        "線種変更で外枠幅が変わりました。種別ごとの安全な最大幅で固定してください"
    )
    assert max(int(spec.column_count) for spec in specs) >= 2, "効果線が複数列に分割されていません"


def _test_same_actual_target_cannot_open_twice(fixture: _Fixture) -> None:
    runtime = _sub("operators.detail_dialog_runtime")
    detail = _sub("utils.detail_dialog")
    resolver = _sub("utils.detail_target_resolver")
    target_a = resolver.resolve_target_from_object(fixture.context, fixture.object_a)
    session_a = runtime.begin_actual_session(fixture.context, target_a)
    session_b = None
    try:
        duplicate = resolver.resolve_target_from_object(fixture.context, fixture.object_a)
        try:
            runtime.begin_actual_session(fixture.context, duplicate)
        except detail.DetailContractError:
            pass
        else:
            raise AssertionError("同じ実体の詳細設定を2画面で開けました")

        target_b = resolver.resolve_target_from_object(fixture.context, fixture.object_b)
        session_b = runtime.begin_actual_session(fixture.context, target_b)
    finally:
        if session_b is not None:
            runtime.cancel_actual_session(fixture.context, session_b)
        runtime.cancel_actual_session(fixture.context, session_a)


def _test_failed_close_releases_actual_session(fixture: _Fixture) -> None:
    runtime = _sub("operators.detail_dialog_runtime")
    resolver = _sub("utils.detail_target_resolver")
    alive = {"value": True}

    def is_alive(_identity):
        return alive["value"]

    cancel_target = resolver.resolve_target_from_object(fixture.context, fixture.object_a)
    cancel_session = runtime.begin_actual_session(
        fixture.context,
        cancel_target,
        target_validator=is_alive,
    )
    alive["value"] = False
    try:
        runtime.cancel_actual_session(fixture.context, cancel_session)
    except Exception:
        pass
    else:
        raise AssertionError("削除済み対象の復元不能エラーが黙殺されました")
    assert cancel_session.token not in runtime._OPEN_ACTUAL_SESSIONS, (
        "キャンセル失敗後も詳細設定セッションが登録表へ残りました"
    )

    alive["value"] = True
    commit_target = resolver.resolve_target_from_object(fixture.context, fixture.object_a)
    commit_session = runtime.begin_actual_session(
        fixture.context,
        commit_target,
        target_validator=is_alive,
    )
    alive["value"] = False
    try:
        runtime.commit_actual_session(fixture.context, commit_session)
    except Exception:
        pass
    else:
        raise AssertionError("削除済み対象の確定失敗エラーが黙殺されました")
    assert commit_session.token not in runtime._OPEN_ACTUAL_SESSIONS, (
        "確定失敗後も詳細設定セッションが登録表へ残りました"
    )
    alive["value"] = True
    runtime.cancel_actual_session(fixture.context, commit_session)

    reopened = runtime.begin_actual_session(
        fixture.context,
        resolver.resolve_target_from_object(fixture.context, fixture.object_a),
    )
    runtime.cancel_actual_session(fixture.context, reopened)


def _test_object_tool_resume_lifecycle(fixture: _Fixture) -> None:
    """実詳細設定だけがObject Toolを1回再開し、解除時には再開しない。"""

    runtime = _sub("operators.detail_dialog_runtime")
    modal_state = _sub("operators.coma_modal_state")
    object_tool_op = _sub("operators.object_tool_op")
    resolver = _sub("utils.detail_target_resolver")
    relaunches: list[float] = []
    finishes: list[bool] = []
    original_schedule = object_tool_op._schedule_object_tool_relaunch

    class _DummyObjectTool:
        def finish_from_external(self, context, *, keep_selection: bool) -> None:
            finishes.append(bool(keep_selection))
            modal_state.clear_active("object_tool", self, context)

    object_tool_op._schedule_object_tool_relaunch = (
        lambda delay_seconds=0.3: relaunches.append(float(delay_seconds))
    )
    try:
        active = _DummyObjectTool()
        modal_state.set_active("object_tool", active, fixture.context)
        target = resolver.resolve_target_from_object(fixture.context, fixture.object_a)
        session = runtime.begin_actual_session(fixture.context, target)
        assert session.token in runtime._RESUME_OBJECT_TOOL_TOKENS, (
            "Object Toolから開いた実詳細設定が再開対象として記録されません"
        )
        runtime.cancel_actual_session(fixture.context, session)
        assert finishes == [True], "旧Object Toolが選択維持で終了されません"
        assert relaunches == [0.05], "ダイアログ終了時のObject Tool再開が1回ではありません"
        assert session.token not in runtime._RESUME_OBJECT_TOOL_TOKENS, (
            "終了済み詳細設定のObject Tool再開トークンが残りました"
        )

        # Object Toolを使わずに開いた詳細設定は、閉じても勝手に起動しない。
        target = resolver.resolve_target_from_object(fixture.context, fixture.object_a)
        no_tool_session = runtime.begin_actual_session(fixture.context, target)
        runtime.cancel_actual_session(fixture.context, no_tool_session)
        assert relaunches == [0.05], "非起動時にもObject Toolが開始されました"

        # アドオン解除／ファイル切替相当の全セッション清掃では再起動しない。
        cleanup_tool = _DummyObjectTool()
        modal_state.set_active("object_tool", cleanup_tool, fixture.context)
        target = resolver.resolve_target_from_object(fixture.context, fixture.object_a)
        cleanup_session = runtime.begin_actual_session(fixture.context, target)
        failures = runtime.cleanup_all_sessions(fixture.context)
        assert not failures, failures
        assert relaunches == [0.05], "全セッション清掃中にObject Toolが再開されました"
        assert cleanup_session.token not in runtime._RESUME_OBJECT_TOOL_TOKENS
        modal_state.clear_active("object_tool", cleanup_tool, fixture.context)
    finally:
        object_tool_op._schedule_object_tool_relaunch = original_schedule
        modal_state.clear_active("object_tool", context=fixture.context)


def _test_actual_lifecycle(path: str, class_name: str) -> None:
    cls = _operator_class(path, class_name)
    assert "UNDO" in set(cls.bl_options), f"{cls.bl_idname} のOKが1回のUndo単位ではありません"
    assert "cancel" in cls.__dict__, f"{cls.bl_idname} にキャンセル／Esc復元処理がありません"
    assert "execute" in cls.__dict__, f"{cls.bl_idname} にOK確定処理がありません"


def _test_preset_lifecycle() -> None:
    cls = _operator_class("operators.preset_detail_op", "BMANGA_OT_preset_detail_edit")
    assert "UNDO" not in set(cls.bl_options), "プリセットJSON保存をBlender Undo対象にしないでください"
    assert "cancel" in cls.__dict__, "プリセット歯車にJSON非書込みのキャンセル処理がありません"


def _test_cancel_restores_fixed_target(fixture: _Fixture) -> None:
    _activate_b(fixture)
    target = _target_from_object(fixture)
    mode = _mode("ACTUAL")
    before_a = float(fixture.raster_a.opacity)
    before_b = float(fixture.raster_b.opacity)
    snapshot = _snapshot(fixture, target, mode)
    try:
        _set_field(target.data, "opacity", 13.0)
        _restore(fixture, target, snapshot, mode)
        assert abs(float(fixture.raster_a.opacity) - before_a) < 1.0e-6, (
            "キャンセルで対象Aが復元されません"
        )
        assert abs(float(fixture.raster_b.opacity) - before_b) < 1.0e-6, (
            "キャンセルが対象Bを変更しました"
        )
    finally:
        fixture.raster_a.opacity = before_a
        fixture.raster_b.opacity = before_b


def _test_preset_cancel_keeps_json(fixture: _Fixture) -> None:
    target = _target_from_preset(fixture)
    mode = _mode("PRESET")
    before_json = fixture.preset_path.read_bytes()
    before_value = float(_field(target.data, "font_size_value"))
    snapshot = _snapshot(fixture, target, mode)
    try:
        _set_field(target.data, "font_size_value", before_value + 9.0)
        _restore(fixture, target, snapshot, mode)
        assert fixture.preset_path.read_bytes() == before_json, (
            "プリセット歯車のキャンセルがJSONを書き換えました"
        )
        assert abs(float(_field(target.data, "font_size_value")) - before_value) < 1.0e-6, (
            "プリセットの一時設定が開始時へ戻りません"
        )
    finally:
        _set_field(target.data, "font_size_value", before_value)


def _test_bit_depth_uses_fixed_id(fixture: _Fixture) -> None:
    _activate_b(fixture)
    before_a, before_b = fixture.raster_a.bit_depth, fixture.raster_b.bit_depth
    try:
        try:
            result = bpy.ops.bmanga.raster_layer_set_bit_depth(
                "EXEC_DEFAULT", raster_id=str(fixture.raster_a.id), bit_depth="gray1"
            )
        except TypeError as exc:
            raise AssertionError("ラスター階調変更に対象の安定IDがありません") from exc
        assert "FINISHED" in result, f"対象ID付き階調変更が失敗しました: {result}"
        assert fixture.raster_a.bit_depth == "gray1", "階調変更が固定済み対象Aへ反映されません"
        assert fixture.raster_b.bit_depth == before_b, "階調変更がアクティブな対象Bへ誤適用されました"
    finally:
        fixture.raster_a.bit_depth, fixture.raster_b.bit_depth = before_a, before_b


def _test_file_action_survives_cancel(fixture: _Fixture) -> None:
    _activate_b(fixture)
    target = _target_from_object(fixture)
    before_opacity = float(fixture.raster_a.opacity)
    png_path = Path(fixture.work.work_dir) / str(fixture.raster_a.filepath_rel)
    raster_op = _sub("operators.raster_layer_op")
    runtime = _sub("operators.detail_dialog_runtime")
    image = raster_op.ensure_raster_image(fixture.context, fixture.raster_a)
    assert image is not None, "PNG保存対象のラスター画像がありません"
    assert png_path.is_file(), "テスト用ラスターの開始時PNGがありません"
    before_png = png_path.read_bytes()
    pixel = list(image.pixels[:4])
    pixel[0] = 0.75 if float(pixel[0]) < 0.5 else 0.25
    image.pixels[:4] = pixel
    image.update()
    raster_op.mark_raster_dirty(fixture.raster_a)
    session = runtime.begin_actual_session(
        fixture.context,
        target,
        target_validator=lambda identity: identity.stable_id == target.stable_id,
    )
    try:
        _set_field(target.data, "opacity", 17.0)
        result = bpy.ops.bmanga.detail_raster_save_png(
            "EXEC_DEFAULT",
            session_token=session.token,
            target_id=str(fixture.raster_a.id),
            force=True,
        )
        assert "FINISHED" in result and png_path.is_file(), "対象ID付きPNG保存が失敗しました"
        assert png_path.read_bytes() != before_png, "ラスターPNGの変更が保存されていません"
        assert not bool(fixture.raster_a.get("bmanga_raster_dirty", False)), (
            "PNG保存後もラスターが未保存扱いです"
        )
        runtime.cancel_actual_session(fixture.context, session)
        assert abs(float(fixture.raster_a.opacity) - before_opacity) < 1.0e-6, (
            "親ダイアログの通常設定がキャンセルで復元されません"
        )
        assert png_path.is_file(), (
            "親ダイアログのキャンセルが独立したPNG保存を取り消しました"
        )
        assert not bool(fixture.raster_a.get("bmanga_raster_dirty", False)), (
            "親ダイアログのキャンセルで保存済みラスターが未保存へ戻りました"
        )
    finally:
        if str(getattr(session.status, "value", "")) == "open":
            runtime.cancel_actual_session(fixture.context, session)
        fixture.raster_a.opacity = before_opacity


def _test_preset_crud_survives_cancel(fixture: _Fixture) -> None:
    target = _target_from_preset(fixture)
    mode = _mode("PRESET")
    snapshot = _snapshot(fixture, target, mode)
    text_presets = _sub("io.text_presets")
    before_value = float(_field(target.data, "font_size_value"))
    try:
        text_presets.duplicate_preset(PRESET_NAME, PRESET_COPY_NAME)
        _set_field(target.data, "font_size_value", 31.0)
        _restore(fixture, target, snapshot, mode)
        assert abs(float(_field(target.data, "font_size_value")) - before_value) < 1.0e-6, (
            "プリセットの通常編集値がキャンセルで復元されません"
        )
        assert text_presets.load_preset_by_name(PRESET_COPY_NAME) is not None, (
            "親ダイアログのキャンセルが独立即時操作のプリセット複製を取り消しました"
        )
    finally:
        _set_field(target.data, "font_size_value", before_value)


def _cases(fixture: _Fixture) -> list[tuple[str, Callable[[], None]]]:
    return [
        ("公開API", _test_public_api),
        ("3入口の登録", _test_entry_registration),
        (
            "レイヤー一覧入口の共通接続",
            lambda: _test_entry_contract(
                "operators.layer_stack_detail_op",
                "BMANGA_OT_layer_stack_detail",
                "resolve_detail_target_from_stack",
            ),
        ),
        (
            "右クリック入口の共通接続",
            lambda: _test_entry_contract(
                "operators.layer_detail_op",
                "BMANGA_OT_layer_detail_open",
                "resolve_detail_target_from_object",
            ),
        ),
        (
            "プリセット歯車入口の共通接続",
            lambda: _test_entry_contract(
                "operators.preset_detail_op",
                "BMANGA_OT_preset_detail_edit",
                "resolve_preset_detail_target",
            ),
        ),
        ("レイヤー一覧の対象固定", lambda: _test_stack_target_fixed(fixture)),
        ("レイヤー一覧のUID優先と詳細表示可否", lambda: _test_stack_uid_priority_and_detail_icon(fixture)),
        ("右クリックの対象固定", lambda: _test_object_target_fixed(fixture)),
        ("プリセット一時設定の分離", lambda: _test_preset_target(fixture)),
        ("実レイヤー2入口の描画順一致", lambda: _test_common_draw_records(fixture)),
        ("線種変更中の最大幅固定", lambda: _test_fixed_max_width(fixture)),
        ("同一実体の二重起動拒否", lambda: _test_same_actual_target_cannot_open_twice(fixture)),
        ("復元・確定失敗後のセッション解放", lambda: _test_failed_close_releases_actual_session(fixture)),
        ("Object Tool再開と全解除のライフサイクル", lambda: _test_object_tool_resume_lifecycle(fixture)),
        ("レイヤー一覧のOK／キャンセル", lambda: _test_actual_lifecycle("operators.layer_stack_detail_op", "BMANGA_OT_layer_stack_detail")),
        ("右クリックのOK／キャンセル", lambda: _test_actual_lifecycle("operators.layer_detail_op", "BMANGA_OT_layer_detail_open")),
        ("プリセット歯車のOK／キャンセル", _test_preset_lifecycle),
        ("キャンセルの固定対象復元", lambda: _test_cancel_restores_fixed_target(fixture)),
        ("プリセットキャンセルのJSON非書込み", lambda: _test_preset_cancel_keeps_json(fixture)),
        ("独立階調変更の対象ID", lambda: _test_bit_depth_uses_fixed_id(fixture)),
        ("独立ファイル保存のキャンセル境界", lambda: _test_file_action_survives_cancel(fixture)),
        ("独立プリセット管理のキャンセル境界", lambda: _test_preset_crud_survives_cancel(fixture)),
    ]


def _run_cases(fixture: _Fixture) -> None:
    failures: list[str] = []
    for name, case in _cases(fixture):
        try:
            case()
        except Exception as exc:  # noqa: BLE001 - 全契約を一回で報告する
            message = f"{name}: {type(exc).__name__}: {exc}"
            failures.append(message)
            print(f"[FAIL] {message}", flush=True)
        else:
            print(f"[PASS] {name}", flush=True)
    if failures:
        detail = "\n".join(f"- {failure}" for failure in failures)
        raise AssertionError(f"詳細設定一元化契約 {len(failures)} 件が未達です:\n{detail}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_detail_dialog_unification_"))
    old_config = os.environ.get("BMANGA_USER_CONFIG_DIR")
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(temp_root / "config")
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        fixture = _create_fixture(temp_root)
        _run_cases(fixture)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                traceback.print_exc()
        if old_config is None:
            os.environ.pop("BMANGA_USER_CONFIG_DIR", None)
        else:
            os.environ["BMANGA_USER_CONFIG_DIR"] = old_config
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
        print("BMANGA_DETAIL_DIALOG_UNIFICATION_OK", flush=True)
        os._exit(0)
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
