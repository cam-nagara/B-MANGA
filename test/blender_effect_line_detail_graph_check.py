"""Blender実機用: 効果線詳細設定の列分割と線幅グラフ連動を確認。"""

from __future__ import annotations

import importlib.util
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "test"))

from detail_dialog_public_test_support import (  # noqa: E402
    draw_all_actual_entry_points,
    open_actual_session,
    sync_actual_session,
)
MOD_NAME = "bmanga_dev_effect_line_detail_graph"


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


class _Layout:
    def __init__(
        self,
        props=None,
        labels=None,
        ops=None,
        grid_columns=None,
        props_by_column=None,
        column_name: str = "root",
    ):
        self.props = [] if props is None else props
        self.labels = [] if labels is None else labels
        self.ops = [] if ops is None else ops
        self.grid_columns = [] if grid_columns is None else grid_columns
        self.props_by_column = {} if props_by_column is None else props_by_column
        self.column_name = column_name
        self.enabled = True
        self.active = True

    def _child(self, column_name: str | None = None):
        return _Layout(
            self.props,
            self.labels,
            self.ops,
            self.grid_columns,
            self.props_by_column,
            self.column_name if column_name is None else column_name,
        )

    def box(self):
        return self._child()

    def row(self, align: bool = False):
        return self._child()

    def column(self, align: bool = False):
        return self._child()

    def split(self, factor: float = 0.5, align: bool = False):
        return self._child()

    def grid_flow(self, **kwargs):
        self.grid_columns.append(int(kwargs.get("columns", 0) or 0))
        return _GridLayout(
            self.props,
            self.labels,
            self.ops,
            self.grid_columns,
            self.props_by_column,
        )

    def separator(self, **_kwargs):
        return None

    def label(self, text: str = "", **_kwargs):
        self.labels.append(str(text))
        return None

    def prop(self, _owner, attr: str, **_kwargs):
        name = str(attr)
        self.props.append(name)
        self.props_by_column.setdefault(self.column_name, []).append(name)
        return None

    def prop_search(self, _owner, attr: str, *_args, **_kwargs):
        return self.prop(_owner, attr, **_kwargs)

    def operator(self, op_id: str, **_kwargs):
        self.ops.append(str(op_id))
        return _Op(str(op_id))

    def operator_menu_enum(self, op_id: str, _prop: str, **_kwargs):
        self.ops.append(str(op_id))
        return _Op(str(op_id))

    def template_curve_mapping(self, *_args, **_kwargs):
        self.labels.append("線幅グラフ")
        return None

    def template_list(self, *_args, **_kwargs):
        return None


class _GridLayout(_Layout):
    def __init__(self, props, labels, ops, grid_columns, props_by_column):
        super().__init__(
            props,
            labels,
            ops,
            grid_columns,
            props_by_column,
            "grid",
        )
        self._next_column_index = 0

    def column(self, align: bool = False):
        name = f"col{self._next_column_index}"
        self._next_column_index += 1
        return self._child(name)


class _Op:
    def __init__(self, op_id: str):
        self.op_id = op_id


def _assert_close(actual: float, expected: float, message: str) -> None:
    assert math.isclose(float(actual), float(expected), abs_tol=1.0e-4), (
        f"{message}: actual={actual!r}, expected={expected!r}"
    )


def _assert_point(points, x: float, y: float, message: str) -> None:
    assert any(
        math.isclose(float(px), x, abs_tol=1.0e-4)
        and math.isclose(float(py), y, abs_tol=1.0e-4)
        for px, py in points
    ), f"{message}: points={points!r}"


def _collect_radii(strokes) -> list[float]:
    values: list[float] = []
    for stroke in strokes:
        for radius in getattr(stroke, "radii", None) or ():
            values.append(float(radius))
    return values


def _set_params_silently(scene, effect_line_op, callback) -> None:
    effect_line_op._set_scene_params_syncing(scene, True)
    try:
        callback(scene.bmanga_effect_line_params)
    finally:
        effect_line_op._set_scene_params_syncing(scene, False)


def _create_test_effect(context, scene, page, effect_line_op, page_stack_key):
    def initial_values(p):
        p.effect_type = "focus"
        p.spacing_mode = "angle"
        p.spacing_angle_deg = 30.0
        p.max_line_count = 12
        p.brush_size_mm = 2.0
        p.inout_apply_brush_size = True
        p.inout_apply_opacity = True
        p.in_percent = 100.0
        p.out_percent = 100.0
        p.in_start_percent = 50.0
        p.out_start_percent = 50.0

    _set_params_silently(scene, effect_line_op, initial_values)
    return effect_line_op._create_effect_layer(
        context,
        (40.0, 55.0, 85.0, 65.0),
        parent_key=page_stack_key(page),
    )


def _draw_session(context, session, layout=None):
    target_layout = layout or _Layout()
    _sub("operators.detail_dialog_runtime").draw_actual_session(
        target_layout, context, session
    )
    return target_layout


def _draw_fingerprint(effect_inout_curve, state_adapters, params):
    material = bpy.data.materials.get(effect_inout_curve.MATERIAL_NAME)
    node_tree = getattr(material, "node_tree", None) if material is not None else None
    curve_nodes = []
    if node_tree is not None:
        for node in sorted(node_tree.nodes, key=lambda item: item.name):
            if node.bl_idname == "ShaderNodeFloatCurve":
                curve_nodes.append(
                    (node.name, effect_inout_curve.read_node_points(node))
                )
    source_props = tuple(
        (name, str(material.get(name, "") or ""))
        for name in (
            effect_inout_curve.IN_SOURCE_PROP,
            effect_inout_curve.OUT_SOURCE_PROP,
            effect_inout_curve.PROFILE_SOURCE_PROP,
            effect_inout_curve.WHITE_PROFILE_SOURCE_PROP,
            effect_inout_curve.BLACK_PROFILE_SOURCE_PROP,
        )
        if material is not None
    )
    requests = tuple(
        sorted(
            (
                name,
                tuple(sorted(request[1].items())),
                request[2],
            )
            for name, request in effect_inout_curve._LIVE_PROFILE_REQUESTS.items()
        )
    )
    timer_registered = bpy.app.timers.is_registered(
        effect_inout_curve._live_profile_sync_tick
    )
    return (
        state_adapters.snapshot_rna_state(params),
        tuple(sorted((obj.name, obj.type) for obj in bpy.data.objects)),
        tuple(sorted(material.name for material in bpy.data.materials)),
        tuple(curve_nodes),
        source_props,
        requests,
        bool(effect_inout_curve._LIVE_PROFILE_RUNNING),
        bool(timer_registered),
    )


def _assert_repeated_draw_is_read_only(
    context,
    session,
    params,
    effect_inout_curve,
    state_adapters,
    label: str,
) -> None:
    before = _draw_fingerprint(effect_inout_curve, state_adapters, params)
    for _index in range(3):
        _draw_session(context, session, _Layout())
    after = _draw_fingerprint(effect_inout_curve, state_adapters, params)
    assert after == before, f"{label}は再描画だけで設定またはBlenderデータを変更しました"


def _assert_balloon_and_image_path_draw_are_read_only(
    context,
    page,
    effect_inout_curve,
    state_adapters,
) -> None:
    contract = _sub("utils.detail_dialog")
    runtime = _sub("operators.detail_dialog_runtime")

    balloon = page.balloons.add()
    balloon.id = "detail_draw_balloon"
    balloon.title = "描画無副作用"
    balloon.shape = "ellipse"
    balloon.line_style = "uni_flash"
    balloon.in_percent = 33.0
    balloon.out_percent = 19.0
    balloon.in_start_percent = 47.0
    balloon.out_start_percent = 21.0
    balloon_opening = (
        float(balloon.in_percent),
        float(balloon.out_percent),
        float(balloon.in_start_percent),
        float(balloon.out_start_percent),
    )
    balloon_target = contract.DetailTarget(
        kind="balloon",
        stable_id=f"{page.id}:{balloon.id}",
        stack_uid=None,
        data=balloon,
        object_ref=None,
        params={"page": page, "page_id": str(page.id)},
    )
    balloon_session = runtime.begin_actual_session(
        context,
        balloon_target,
        target_validator=lambda identity: identity.stable_id == balloon_target.stable_id,
    )
    try:
        balloon_node = effect_inout_curve.get_profile_node()
        assert balloon_node is not None, "フキダシ開始時に線幅グラフが準備されていません"
        balloon_points = effect_inout_curve.read_node_points(balloon_node)
        _assert_point(balloon_points, 0.0, 0.19, "フキダシの抜きが開始時グラフへ反映されていません")
        _assert_point(balloon_points, 0.21, 1.0, "フキダシの内端側位置が開始時グラフへ反映されていません")
        _assert_point(balloon_points, 0.53, 1.0, "フキダシの外端側位置が開始時グラフへ反映されていません")
        _assert_point(balloon_points, 1.0, 0.33, "フキダシの入りが開始時グラフへ反映されていません")
        _assert_repeated_draw_is_read_only(
            context,
            balloon_session,
            balloon,
            effect_inout_curve,
            state_adapters,
            "フキダシ詳細設定",
        )
        effect_inout_curve._apply_points_to_node(
            balloon_node,
            ((0.0, 0.2), (0.35, 1.0), (0.65, 1.0), (1.0, 0.4)),
        )
        # check() 相当の同期だけでは、線幅グラフのドラッグ内容を確定しない
        # (重いメッシュ再生成を毎回走らせないための新契約)。
        runtime.sync_actual_session(context, balloon_session)
        assert (
            float(balloon.in_percent),
            float(balloon.out_percent),
            float(balloon.in_start_percent),
            float(balloon.out_start_percent),
        ) == balloon_opening, (
            "グラフのドラッグだけ (check()相当) でパラメータが確定されました"
        )
        # 「適用」ボタン相当の確定を呼んで初めてパラメータへ反映される。
        effect_inout_curve.commit_profile_node_to_params(balloon)
        _assert_close(balloon.in_percent, 40.0, "「適用」相当のグラフ変更が入りへ確定されていません")
        _assert_close(balloon.out_percent, 20.0, "「適用」相当のグラフ変更が抜きへ確定されていません")
        _assert_close(balloon.in_start_percent, 35.0, "「適用」相当のグラフ変更が外端側位置へ確定されていません")
        _assert_close(balloon.out_start_percent, 35.0, "「適用」相当のグラフ変更が内端側位置へ確定されていません")
    finally:
        runtime.cancel_actual_session(context, balloon_session)
    assert (
        float(balloon.in_percent),
        float(balloon.out_percent),
        float(balloon.in_start_percent),
        float(balloon.out_start_percent),
    ) == balloon_opening, "フキダシのキャンセルで開始時グラフ値へ戻りませんでした"

    image_path = context.scene.bmanga_image_path_layers.add()
    image_path.id = "detail_draw_image_path"
    image_path.title = "描画無副作用"
    image_path.in_percent = 61.0
    image_path.out_percent = 27.0
    image_path.in_start_percent = 42.0
    image_path.out_start_percent = 18.0
    image_opening = (
        float(image_path.in_percent),
        float(image_path.out_percent),
        float(image_path.in_start_percent),
        float(image_path.out_start_percent),
    )
    image_target = contract.DetailTarget(
        kind="image_path",
        stable_id=str(image_path.id),
        stack_uid=None,
        data=image_path,
        object_ref=None,
        params=image_path,
    )
    image_session = runtime.begin_actual_session(
        context,
        image_target,
        target_validator=lambda identity: identity.stable_id == image_target.stable_id,
    )
    try:
        image_node = effect_inout_curve.get_profile_node()
        assert image_node is not None, "パターンカーブ開始時に線幅グラフが準備されていません"
        image_points = effect_inout_curve.read_node_points(image_node)
        _assert_point(image_points, 0.0, 0.27, "パターンカーブの抜きが開始時グラフへ反映されていません")
        _assert_point(image_points, 0.18, 1.0, "パターンカーブの内端側位置が開始時グラフへ反映されていません")
        _assert_point(image_points, 0.58, 1.0, "パターンカーブの外端側位置が開始時グラフへ反映されていません")
        _assert_point(image_points, 1.0, 0.61, "パターンカーブの入りが開始時グラフへ反映されていません")
        _assert_repeated_draw_is_read_only(
            context,
            image_session,
            image_path,
            effect_inout_curve,
            state_adapters,
            "パターンカーブ詳細設定",
        )
        effect_inout_curve._apply_points_to_node(
            image_node,
            ((0.0, 0.15), (0.3, 1.0), (0.55, 1.0), (1.0, 0.45)),
        )
        # check() 相当の同期だけでは確定しない (新契約)。
        runtime.sync_actual_session(context, image_session)
        assert (
            float(image_path.in_percent),
            float(image_path.out_percent),
            float(image_path.in_start_percent),
            float(image_path.out_start_percent),
        ) == image_opening, (
            "グラフのドラッグだけ (check()相当) でパラメータが確定されました"
        )
        effect_inout_curve.commit_profile_node_to_params(image_path)
        _assert_close(image_path.in_percent, 45.0, "「適用」相当のグラフ変更が入りへ確定されていません")
        _assert_close(image_path.out_percent, 15.0, "「適用」相当のグラフ変更が抜きへ確定されていません")
        _assert_close(image_path.in_start_percent, 45.0, "「適用」相当のグラフ変更が入り側位置へ確定されていません")
        _assert_close(image_path.out_start_percent, 30.0, "「適用」相当のグラフ変更が抜き側位置へ確定されていません")
    finally:
        runtime.cancel_actual_session(context, image_session)
    assert (
        float(image_path.in_percent),
        float(image_path.out_percent),
        float(image_path.in_start_percent),
        float(image_path.out_start_percent),
    ) == image_opening, "パターンカーブのキャンセルで開始時グラフ値へ戻りませんでした"


def _assert_detail_layout(effect_line_op, context, scene, session) -> None:
    """v0.6.557以降の3列契約: 列1=サイドバー(種類含む)、列2=外端形状・内端形状・

    線・まとまり・入り抜き・色、列3(一番右)=パス。段階的な列の間引きは
    行わず、種類 (集中線/白抜き線) によらず常に3列とも使う。
    """

    layout = _Layout()
    _draw_session(context, session, layout)
    assert 3 in layout.grid_columns, f"集中線の詳細設定が3列で描画されていません: {layout.grid_columns}"
    assert scene.bmanga_active_layer_kind == "effect", "効果線詳細設定の編集対象が選択されていません"
    assert scene.bmanga_active_effect_layer_name, "効果線詳細設定の対象レイヤー名が設定されていません"
    assert "effect_type" in layout.props_by_column.get("col0", ()), "種類がサイドバー(1列目)にありません"
    for prop_name in ("start_shape", "end_shape", "brush_size_mm", "in_percent", "line_color"):
        assert prop_name in layout.props_by_column.get("col1", ()), (
            f"外端形状・内端形状・線・入り抜き・色が2列目にまとまっていません: {prop_name}"
        )
    for prop_name in ("line_image_source", "base_path_enabled"):
        assert prop_name in layout.props_by_column.get("col2", ()), (
            f"パス設定が3列目(一番右)にありません: {prop_name}"
        )

    def white_outline_values(p):
        p.effect_type = "white_outline"

    _set_params_silently(scene, effect_line_op, white_outline_values)
    sync_actual_session(MOD_NAME, context, session)
    layout = _Layout()
    _draw_session(context, session, layout)
    assert "effect_type" in layout.props_by_column.get("col0", ()), (
        "白抜き線でも種類がサイドバー(1列目)にありません"
    )
    for prop_name in (
        "white_outline_count",
        "white_outline_white_ratio_percent",
        "white_outline_black_ratio_percent",
        "white_outline_length_percent",
        "white_outline_white_brush_mm",
        "white_outline_black_direction",
        "white_outline_black_in_percent",
        "white_outline_black_out_percent",
        "line_color",
    ):
        assert prop_name in layout.props_by_column.get("col1", ()), (
            f"白抜き線の設定 (線・まとまり・入り抜き・色相当) が2列目にまとまっていません: {prop_name}"
        )
    for prop_name in (
        "white_outline_black_inout_range_mode",
        "white_outline_black_in_range_percent",
        "white_outline_black_out_range_percent",
        "white_outline_black_in_range_mm",
        "white_outline_black_out_range_mm",
    ):
        assert prop_name not in layout.props, (
            f"線幅グラフに統合した範囲設定が表示されています: {prop_name}"
        )
    assert layout.labels.count("線幅グラフ") >= 3, "主線・黒線・白線の線幅グラフが揃っていません"
    for prop_name in ("line_image_source", "base_path_enabled"):
        assert prop_name in layout.props_by_column.get("col2", ()), (
            f"白抜き線でもパス設定が3列目(一番右)にありません: {prop_name}"
        )


def _assert_all_entry_layouts_match(
    effect_line_op,
    context,
    scene,
    session,
) -> None:
    def focus_values(p):
        p.effect_type = "focus"

    _set_params_silently(scene, effect_line_op, focus_values)
    sync_actual_session(MOD_NAME, context, session)
    layouts = draw_all_actual_entry_points(MOD_NAME, context, session, _Layout)
    assert layouts[0].props == layouts[1].props == layouts[2].props, (
        "共通描画・右クリック・レイヤー一覧で効果線の表示項目が一致しません"
    )
    assert all(3 in layout.grid_columns for layout in layouts), (
        "いずれかの入口で集中線の3列表示が使われていません"
    )
    assert all(layout.props_by_column == layouts[0].props_by_column for layout in layouts[1:]), (
        "入口ごとに等幅列への項目配置が異なります"
    )
    assert "effect_type" in layouts[0].props_by_column.get("col0", ()), "種類が1列目にありません"
    assert "brush_size_mm" in layouts[0].props, "線設定が共通詳細にありません"
    assert "in_percent" in layouts[0].props, "入り抜きが共通詳細にありません"
    assert "line_color" in layouts[0].props, "色設定が共通詳細にありません"
    assert "line_image_source" in layouts[0].props, "パス線設定が共通詳細にありません"


def _assert_graph_numeric_to_curve(effect_line_op, effect_inout_curve, context, scene, session):
    def numeric_values(p):
        p.effect_type = "focus"
        p.in_percent = 30.0
        p.out_percent = 20.0
        p.in_start_percent = 40.0
        p.out_start_percent = 25.0
        p.in_easing_curve = effect_inout_curve.DEFAULT_CURVE_TEXT
        p.out_easing_curve = effect_inout_curve.DEFAULT_CURVE_TEXT

    _set_params_silently(scene, effect_line_op, numeric_values)
    sync_actual_session(MOD_NAME, context, session)
    layout = _Layout()
    _draw_session(context, session, layout)
    node = effect_inout_curve.get_profile_node()
    assert node is not None, "効果線詳細設定に線幅グラフが作成されていません"
    points = effect_inout_curve.read_node_points(node)
    _assert_point(points, 0.0, 0.20, "抜き(%)が線幅グラフの内端へ反映されていません")
    _assert_point(points, 0.25, 1.0, "内端側の変化位置が線幅グラフへ反映されていません")
    _assert_point(points, 0.60, 1.0, "外端側の変化位置が線幅グラフへ反映されていません")
    _assert_point(points, 1.0, 0.30, "入り(%)が線幅グラフの外端へ反映されていません")
    return node


def _assert_graph_check_does_not_commit(
    effect_inout_curve, effect_line_op, context, session, params, obj, layer, node
) -> None:
    """check() (ダイアログの毎フレーム同期) はグラフのドラッグを確定しない。

    線幅グラフはドラッグのたびに重いメッシュ再生成を伴うため、v0.6系のある
    時点からリアルタイム確定をやめ、「適用」ボタン (または詳細設定のOK確定)
    を押した時だけ確定する契約へ変更した。ここでは、その契約どおり
    check() 相当の同期・常駐タイマーのどちらもパラメータを変えないことを
    確認する。
    """

    before = (
        float(params.in_percent),
        float(params.out_percent),
        float(params.in_start_percent),
        float(params.out_start_percent),
    )
    saved_before = effect_line_op._layer_params_data(obj, layer)
    effect_inout_curve._apply_points_to_node(
        node,
        ((0.0, 0.2), (0.2, 0.65), (0.35, 1.0), (0.65, 1.0), (0.85, 0.7), (1.0, 0.4)),
    )
    sync_actual_session(MOD_NAME, context, session)
    after_check = (
        float(params.in_percent),
        float(params.out_percent),
        float(params.in_start_percent),
        float(params.out_start_percent),
    )
    assert after_check == before, "check() だけで線幅グラフの編集内容が確定されました"
    for _tick in range(3):
        effect_inout_curve._live_profile_sync_tick()
    after_tick = (
        float(params.in_percent),
        float(params.out_percent),
        float(params.in_start_percent),
        float(params.out_start_percent),
    )
    assert after_tick == before, "常駐タイマーが線幅グラフの編集内容を確定しました(重い再生成の原因)"
    saved_after = effect_line_op._layer_params_data(obj, layer)
    assert saved_after["in_percent"] == saved_before["in_percent"], (
        "未確定のグラフ編集で効果線の保存値(入り)が変わりました"
    )
    assert saved_after["out_percent"] == saved_before["out_percent"], (
        "未確定のグラフ編集で効果線の保存値(抜き)が変わりました"
    )


def _assert_graph_apply_commits(effect_inout_curve, context, session, params, node) -> None:
    """「適用」ボタン (bmanga.effect_profile_graph_apply) 相当の確定を検証する。"""

    changed = effect_inout_curve.commit_profile_node_to_params(params)
    assert changed, "「適用」相当の確定でパラメータが変化しませんでした"
    _assert_close(params.in_percent, 40.0, "線幅グラフの外端が入り(%)へ確定されていません")
    _assert_close(params.out_percent, 20.0, "線幅グラフの内端が抜き(%)へ確定されていません")
    _assert_close(params.in_start_percent, 35.0, "線幅グラフの山位置が入り始点(%)へ確定されていません")
    _assert_close(params.out_start_percent, 35.0, "線幅グラフの山位置が抜き始点(%)へ確定されていません")
    # 適用後はノード表示・保存テキストも正規化した点列へ揃う (last_source
    # ガードを経由しない強制確定のため、次のcheck()で巻き戻らないことも兼ねて確認)。
    sync_actual_session(MOD_NAME, context, session)
    settled_points = effect_inout_curve.read_node_points(node)
    expected_points = effect_inout_curve.flip_horizontal(
        effect_inout_curve.profile_points_from_params(params)
    )
    assert len(settled_points) == len(expected_points), "適用後の線幅グラフ点数が正規化されていません"
    for (ax, ay), (ex, ey) in zip(settled_points, expected_points):
        _assert_close(ax, ex, "適用後の線幅グラフ位置(X)が正規化されていません")
        _assert_close(ay, ey, "適用後の線幅グラフ位置(Y)が正規化されていません")


def _assert_graph_live_sync_does_not_commit(
    effect_inout_curve, effect_line_op, params, obj, layer, node
) -> None:
    """常駐タイマーは線幅グラフの表示更新だけを行い、パラメータへ確定しない。"""

    before = (
        float(params.in_percent),
        float(params.out_percent),
        float(params.in_start_percent),
        float(params.out_start_percent),
    )
    effect_inout_curve.request_live_profile_sync(params)
    effect_inout_curve._apply_points_to_node(
        node,
        ((0.0, 0.15), (0.25, 0.7), (0.45, 1.0), (0.75, 1.0), (1.0, 0.35)),
    )
    for _tick in range(3):
        effect_inout_curve._live_profile_sync_tick()
    after = (
        float(params.in_percent),
        float(params.out_percent),
        float(params.in_start_percent),
        float(params.out_start_percent),
    )
    assert after == before, "常駐タイマーがドラッグ内容をパラメータへ確定しました(重い再生成の原因)"
    saved = effect_line_op._layer_params_data(obj, layer)
    _assert_close(saved["in_percent"], before[0], "未確定のグラフ編集で効果線の保存値が変わりました")

    # 「適用」相当の確定を呼んで初めて反映され、効果線の保存値へも伝播する。
    effect_inout_curve.commit_profile_node_to_params(params)
    _assert_close(params.in_percent, 35.0, "「適用」相当の確定で入り(%)が反映されていません")
    _assert_close(params.out_percent, 15.0, "「適用」相当の確定で抜き(%)が反映されていません")
    _assert_close(params.in_start_percent, 25.0, "「適用」相当の確定で外端側変化が反映されていません")
    _assert_close(params.out_start_percent, 45.0, "「適用」相当の確定で内端側変化が反映されていません")
    saved_after = effect_line_op._layer_params_data(obj, layer)
    _assert_close(saved_after["in_percent"], 35.0, "適用後の入り(%)が効果線へ保存されていません")
    _assert_close(saved_after["out_percent"], 15.0, "適用後の抜き(%)が効果線へ保存されていません")


def _assert_drag_reorder_keeps_point_identity(effect_inout_curve, params, node) -> None:
    """ドラッグ中 (パラメータ未変更) の常駐同期はノードの点へ一切触れない。

    以前は ensure_profile_node がドラッグ中の点列を正規化 (ソート・結合) して
    書き戻していたため、点が隣の点を跨いだ瞬間に並べ替えが走り、掴んでいた
    点が別の点 (中央の点など) にすり替わる不具合があった。
    """
    effect_inout_curve._apply_points_to_node(
        node,
        ((0.0, 0.2), (0.3, 0.6), (0.5, 1.0), (0.7, 1.0), (1.0, 0.4)),
    )
    curve = node.mapping.curves[0]
    # ユーザーのドラッグ相当: 2番目の点を右隣 (x=0.5) を跨ぐ位置まで動かす
    curve.points[1].location = (0.55, 0.62)
    dragged = [(float(p.location.x), float(p.location.y)) for p in curve.points]
    for _ in range(3):
        effect_inout_curve.ensure_profile_node(params)
        effect_inout_curve._live_profile_sync_tick()
    after = [(float(p.location.x), float(p.location.y)) for p in curve.points]
    assert after == dragged, (
        f"ドラッグ中の線幅グラフ点列が常駐同期で書き換えられました: {dragged} -> {after}"
    )
    assert math.isclose(after[1][0], 0.55, abs_tol=1.0e-5), (
        "掴んでいた点が別の点にすり替わりました"
    )


def _assert_apply_preserves_unrepresentable_shapes(
    effect_inout_curve, effect_line_gen, params, node
) -> None:
    """入り抜き分解で表せない形を適用しても、Y値が100%へ潰れない。"""

    # 両端100%・中央に谷: 以前は適用で全区間が100%へ潰れた
    effect_inout_curve._apply_points_to_node(
        node, ((0.0, 1.0), (0.5, 0.2), (1.0, 1.0))
    )
    changed = effect_inout_curve.commit_profile_node_to_params(params)
    assert changed, "谷形状の適用でパラメータが変化しませんでした"
    display = effect_inout_curve.flip_horizontal(
        effect_inout_curve.profile_points_from_params(params)
    )
    assert abs(effect_inout_curve.evaluate(display, 0.5) - 0.2) <= 0.02, (
        f"両端100%の谷が適用で失われました: {display}"
    )
    assert abs(effect_inout_curve.evaluate(display, 0.0) - 1.0) <= 0.02, display
    assert abs(effect_inout_curve.evaluate(display, 1.0) - 1.0) <= 0.02, display
    profile, _d_in, _d_out = effect_line_gen._inout_profile(params, 1.0)
    assert abs(profile(0.5) - 0.2) <= 0.02, "生成側プロファイルへ谷が伝わっていません"

    # どこも100%に届かない山も保存される
    effect_inout_curve._apply_points_to_node(
        node, ((0.0, 0.0), (0.5, 0.8), (1.0, 0.0))
    )
    effect_inout_curve.commit_profile_node_to_params(params)
    display = effect_inout_curve.flip_horizontal(
        effect_inout_curve.profile_points_from_params(params)
    )
    assert abs(effect_inout_curve.evaluate(display, 0.5) - 0.8) <= 0.02, (
        f"100%未満の山が適用で失われました: {display}"
    )
    assert abs(effect_inout_curve.evaluate(display, 0.0) - 0.0) <= 0.02, display
    assert abs(effect_inout_curve.evaluate(display, 1.0) - 0.0) <= 0.02, display
    profile, _d_in, _d_out = effect_line_gen._inout_profile(params, 1.0)
    assert abs(profile(0.5) - 0.8) <= 0.02, "生成側プロファイルへ山が伝わっていません"


def _assert_white_black_graphs(effect_inout_curve, context, session, params) -> None:
    params.effect_type = "white_outline"
    params.white_outline_white_in_percent = 30.0
    params.white_outline_white_out_percent = 20.0
    params.white_outline_white_inout_range_mode = "percent"
    params.white_outline_white_in_range_percent = 40.0
    params.white_outline_white_out_range_percent = 25.0
    params.white_outline_black_in_percent = 50.0
    params.white_outline_black_out_percent = 10.0
    params.white_outline_black_inout_range_mode = "percent"
    params.white_outline_black_in_range_percent = 30.0
    params.white_outline_black_out_range_percent = 20.0
    sync_actual_session(MOD_NAME, context, session)
    layout = _Layout()
    _draw_session(context, session, layout)
    assert "in_start_percent" not in layout.props
    assert "out_start_percent" not in layout.props
    white = effect_inout_curve.get_profile_node(effect_inout_curve.WHITE_PROFILE_NODE_NAME)
    black = effect_inout_curve.get_profile_node(effect_inout_curve.BLACK_PROFILE_NODE_NAME)
    assert white is not None and black is not None and white != black
    white_points = effect_inout_curve.read_node_points(white)
    _assert_point(white_points, 0.0, 0.20, "白線グラフの内端")
    _assert_point(white_points, 0.25, 1.0, "白線グラフの内端側変化")
    _assert_point(white_points, 0.60, 1.0, "白線グラフの外端側変化")
    _assert_point(white_points, 1.0, 0.30, "白線グラフの外端")
    black_points = effect_inout_curve.read_node_points(black)
    _assert_point(black_points, 0.0, 0.10, "黒線グラフの内端")
    _assert_point(black_points, 0.20, 1.0, "黒線グラフの内端側変化")
    _assert_point(black_points, 0.70, 1.0, "黒線グラフの外端側変化")
    _assert_point(black_points, 1.0, 0.50, "黒線グラフの外端")
    effect_inout_curve._apply_points_to_node(
        white, ((0.0, 0.15), (0.35, 1.0), (0.80, 1.0), (1.0, 0.45))
    )
    effect_inout_curve._apply_points_to_node(
        black, ((0.0, 0.25), (0.10, 1.0), (0.65, 1.0), (1.0, 0.55))
    )
    # check() だけでは白線・黒線のグラフも確定しない (新契約)。
    sync_actual_session(MOD_NAME, context, session)
    assert math.isclose(
        float(params.white_outline_white_in_percent), 30.0, abs_tol=1.0e-4
    ), "check() だけで白線グラフが確定されました"
    assert math.isclose(
        float(params.white_outline_black_in_percent), 50.0, abs_tol=1.0e-4
    ), "check() だけで黒線グラフが確定されました"
    # 白線・黒線それぞれの「適用」ボタン相当の確定を個別に呼ぶ。
    effect_inout_curve.commit_profile_node_to_params(
        params,
        fields=effect_inout_curve.WHITE_PROFILE_FIELDS,
        node_name=effect_inout_curve.WHITE_PROFILE_NODE_NAME,
        source_prop=effect_inout_curve.WHITE_PROFILE_SOURCE_PROP,
    )
    effect_inout_curve.commit_profile_node_to_params(
        params,
        fields=effect_inout_curve.BLACK_PROFILE_FIELDS,
        node_name=effect_inout_curve.BLACK_PROFILE_NODE_NAME,
        source_prop=effect_inout_curve.BLACK_PROFILE_SOURCE_PROP,
    )
    _assert_close(params.white_outline_white_in_percent, 45.0, "白線グラフ外端")
    _assert_close(params.white_outline_white_out_percent, 15.0, "白線グラフ内端")
    _assert_close(params.white_outline_white_in_range_percent, 20.0, "白線グラフ外端側範囲")
    _assert_close(params.white_outline_white_out_range_percent, 35.0, "白線グラフ内端側範囲")
    _assert_close(params.white_outline_black_in_percent, 55.0, "黒線グラフ外端")
    _assert_close(params.white_outline_black_out_percent, 25.0, "黒線グラフ内端")
    _assert_close(params.white_outline_black_in_range_percent, 35.0, "黒線グラフ外端側範囲")
    _assert_close(params.white_outline_black_out_range_percent, 10.0, "黒線グラフ内端側範囲")


def _assert_ok_commits_pending_graph_edit(
    effect_inout_curve, effect_line_op, context, session, params, obj, layer
) -> None:
    """「適用」ボタンを押し忘れても、詳細設定のOK確定時に線幅グラフを確定する。"""

    node = effect_inout_curve.get_profile_node()
    assert node is not None, "OK確定前提の線幅グラフが見つかりません"
    before = (
        float(params.in_percent),
        float(params.out_percent),
        float(params.in_start_percent),
        float(params.out_start_percent),
    )
    effect_inout_curve._apply_points_to_node(
        node,
        ((0.0, 0.3), (0.4, 1.0), (0.6, 1.0), (1.0, 0.6)),
    )
    runtime = _sub("operators.detail_dialog_runtime")
    runtime.sync_actual_session(context, session)
    after_check = (
        float(params.in_percent),
        float(params.out_percent),
        float(params.in_start_percent),
        float(params.out_start_percent),
    )
    assert after_check == before, "check() だけでOK確定前にグラフが確定されました"
    runtime.commit_actual_session(context, session)
    _assert_close(params.in_percent, 60.0, "OK確定で未適用のグラフ編集(入り)が確定されていません")
    _assert_close(params.out_percent, 30.0, "OK確定で未適用のグラフ編集(抜き)が確定されていません")
    saved = effect_line_op._layer_params_data(obj, layer)
    _assert_close(saved["in_percent"], 60.0, "OK確定後の入り(%)が効果線へ保存されていません")
    _assert_close(saved["out_percent"], 30.0, "OK確定後の抜き(%)が効果線へ保存されていません")


def _assert_graph_saved_and_generated(
    effect_line_op,
    effect_line_gen,
    context,
    session,
    params,
    obj,
    layer,
) -> None:
    sync_actual_session(MOD_NAME, context, session)
    saved = effect_line_op._layer_params_data(obj, layer)
    _assert_close(saved["in_percent"], 40.0, "入り(%)が効果線レイヤーへ保存されていません")
    _assert_close(saved["out_percent"], 20.0, "抜き(%)が効果線レイヤーへ保存されていません")
    _assert_close(saved["in_start_percent"], 35.0, "外端側の変化位置が効果線レイヤーへ保存されていません")
    _assert_close(saved["out_start_percent"], 35.0, "内端側の変化位置が効果線レイヤーへ保存されていません")

    strokes = effect_line_gen.generate_strokes(
        params,
        center_xy_mm=(82.0, 87.0),
        radius_xy_mm=(42.0, 32.0),
        seed=4,
    )
    radii = _collect_radii(strokes)
    assert radii and min(radii) < max(radii) * 0.8, "線幅グラフの太さ変化が生成線へ出ていません"


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_effect_line_detail_graph_"))
    old_config = os.environ.get("BMANGA_USER_CONFIG_DIR")
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(temp_root / "config")
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "EffectLineDetailGraph.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        effect_line_gen = _sub("operators.effect_line_gen")
        effect_line_op = _sub("operators.effect_line_op")
        get_work = _sub("core.work").get_work
        effect_inout_curve = _sub("utils.effect_inout_curve")
        state_adapters = _sub("utils.detail_state_adapters")
        object_naming = _sub("utils.object_naming")
        page_stack_key = _sub("utils.layer_hierarchy").page_stack_key

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        params = scene.bmanga_effect_line_params

        obj, layer = _create_test_effect(context, scene, page, effect_line_op, page_stack_key)
        assert obj is not None and layer is not None
        bmanga_id = object_naming.get_bmanga_id(obj)
        session = open_actual_session(MOD_NAME, context, obj)
        assert session.target.stable_id == bmanga_id
        assert session.layout.max_columns == 3
        fixed_width = session.layout.dialog_width

        _assert_repeated_draw_is_read_only(
            context,
            session,
            params,
            effect_inout_curve,
            state_adapters,
            "効果線詳細設定",
        )

        _assert_detail_layout(effect_line_op, context, scene, session)
        assert session.layout.dialog_width == fixed_width, "種類変更で固定最大幅が変化しました"
        _assert_all_entry_layouts_match(
            effect_line_op,
            context,
            scene,
            session,
        )
        node = _assert_graph_numeric_to_curve(
            effect_line_op,
            effect_inout_curve,
            context,
            scene,
            session,
        )
        _assert_graph_check_does_not_commit(
            effect_inout_curve, effect_line_op, context, session, params, obj, layer, node
        )
        _assert_graph_apply_commits(effect_inout_curve, context, session, params, node)
        _assert_graph_saved_and_generated(
            effect_line_op,
            effect_line_gen,
            context,
            session,
            params,
            obj,
            layer,
        )
        _assert_graph_live_sync_does_not_commit(
            effect_inout_curve, effect_line_op, params, obj, layer, node
        )
        _assert_drag_reorder_keeps_point_identity(effect_inout_curve, params, node)
        _assert_apply_preserves_unrepresentable_shapes(
            effect_inout_curve, effect_line_gen, params, node
        )
        _assert_white_black_graphs(effect_inout_curve, context, session, params)
        assert session.layout.dialog_width == fixed_width, "編集途中で固定最大幅が変化しました"
        _assert_ok_commits_pending_graph_edit(
            effect_inout_curve, effect_line_op, context, session, params, obj, layer
        )
        _assert_balloon_and_image_path_draw_are_read_only(
            context,
            page,
            effect_inout_curve,
            state_adapters,
        )
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        if old_config is None:
            os.environ.pop("BMANGA_USER_CONFIG_DIR", None)
        else:
            os.environ["BMANGA_USER_CONFIG_DIR"] = old_config
        shutil.rmtree(temp_root, ignore_errors=True)
    print("[ok] effect line detail graph works")


if __name__ == "__main__":
    try:
        main()
        os._exit(0)
    except Exception:
        import traceback

        traceback.print_exc()
        os._exit(1)
