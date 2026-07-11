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

    def template_curve_mapping(self, *_args, **_kwargs):
        self.labels.append("線幅グラフ")
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


def _assert_detail_layout(layer_detail_op, effect_line_op, context, scene, obj) -> None:
    layout = _Layout()
    layer_detail_op._draw_effect_detail(layout, context, obj, load_from_layer=True)
    assert 5 in layout.grid_columns, f"効果線詳細設定が5列で描画されていません: {layout.grid_columns}"
    assert scene.bmanga_active_layer_kind == "effect", "効果線詳細設定の編集対象が選択されていません"
    assert scene.bmanga_active_effect_layer_name, "効果線詳細設定の対象レイヤー名が設定されていません"

    def white_outline_values(p):
        p.effect_type = "white_outline"

    _set_params_silently(scene, effect_line_op, white_outline_values)
    layout = _Layout()
    layer_detail_op._draw_effect_detail(layout, context, obj, load_from_layer=False)
    assert "white_outline_count" in layout.props_by_column.get("col1", ()), (
        "白抜き線の基本設定が線列に分割されていません"
    )
    for prop_name in (
        "white_outline_white_ratio_percent",
        "white_outline_black_ratio_percent",
        "white_outline_length_percent",
    ):
        assert prop_name in layout.props_by_column.get("col1", ()), (
            f"白線割合・黒線割合・長さが白抜き線セクションにありません: {prop_name}"
        )
    assert "white_outline_white_brush_mm" in layout.props_by_column.get("col3", ()), (
        "白線設定が別列に分割されていません"
    )
    assert "white_outline_black_direction" in layout.props_by_column.get("col2", ()), (
        "黒線設定が別列に分割されていません"
    )
    for prop_name in (
        "white_outline_black_in_percent",
        "white_outline_black_out_percent",
    ):
        assert prop_name in layout.props_by_column.get("col2", ()), (
            f"黒線入り抜き設定が黒線列にありません: {prop_name}"
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
    assert "line_image_source" in layout.props_by_column.get("col4", ()), (
        "パス線設定が独立列に分割されていません"
    )


def _assert_layer_stack_dialog_layout(
    layer_stack_detail_ui,
    layer_stack_op,
    layer_stack_utils,
    effect_line_op,
    context,
    scene,
    layer,
) -> None:
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    uid = layer_stack_utils.target_uid("effect", layer_stack_utils._node_stack_key(layer))
    item = next((candidate for candidate in stack if layer_stack_utils.stack_item_uid(candidate) == uid), None)
    assert item is not None, "レイヤーリスト上の効果線行が見つかりません"
    assert layer_stack_op._detail_dialog_width_for_item(context, item) == 1320
    resolved = layer_stack_utils.resolve_stack_item(context, item)

    def focus_values(p):
        p.effect_type = "focus"

    _set_params_silently(scene, effect_line_op, focus_values)
    layout = _Layout()
    layer_stack_detail_ui.draw_stack_item_detail(layout, context, item, resolved, wide=True)
    assert 5 in layout.grid_columns, f"レイヤーリスト詳細の効果線設定が5列で描画されていません: {layout.grid_columns}"
    assert "effect_type" in layout.props_by_column.get("col0", ()), "種類が1列目にありません"
    assert "brush_size_mm" in layout.props_by_column.get("col1", ()), "線設定が2列目にありません"
    assert "in_percent" in layout.props_by_column.get("col2", ()), "入り抜きが3列目にありません"
    assert "line_color" in layout.props_by_column.get("col3", ()), "色設定が4列目にありません"
    assert "line_image_source" in layout.props_by_column.get("col4", ()), "パス線設定が5列目にありません"

    layout = _Layout()
    layer_stack_detail_ui.draw_stack_item_detail(layout, context, item, resolved, wide=False)
    assert 5 not in layout.grid_columns, "サイドバー内の詳細表示まで5列になっています"


def _assert_graph_numeric_to_curve(layer_detail_op, effect_line_op, effect_inout_curve, context, scene, obj):
    def numeric_values(p):
        p.effect_type = "focus"
        p.in_percent = 30.0
        p.out_percent = 20.0
        p.in_start_percent = 40.0
        p.out_start_percent = 25.0
        p.in_easing_curve = effect_inout_curve.DEFAULT_CURVE_TEXT
        p.out_easing_curve = effect_inout_curve.DEFAULT_CURVE_TEXT

    _set_params_silently(scene, effect_line_op, numeric_values)
    layout = _Layout()
    layer_detail_op._draw_effect_detail(layout, context, obj, load_from_layer=False)
    node = effect_inout_curve.get_profile_node()
    assert node is not None, "効果線詳細設定に線幅グラフが作成されていません"
    points = effect_inout_curve.read_node_points(node)
    _assert_point(points, 0.0, 0.20, "抜き(%)が線幅グラフの内端へ反映されていません")
    _assert_point(points, 0.25, 1.0, "内端側の変化位置が線幅グラフへ反映されていません")
    _assert_point(points, 0.60, 1.0, "外端側の変化位置が線幅グラフへ反映されていません")
    _assert_point(points, 1.0, 0.30, "入り(%)が線幅グラフの外端へ反映されていません")
    return node


def _assert_graph_curve_to_numeric(layer_detail_op, effect_inout_curve, context, obj, params, node) -> None:
    effect_inout_curve._apply_points_to_node(
        node,
        ((0.0, 0.2), (0.2, 0.65), (0.35, 1.0), (0.65, 1.0), (0.85, 0.7), (1.0, 0.4)),
    )
    assert layer_detail_op._sync_detail_profile_curve(context, "effect", obj.get("bmanga_id", ""))
    _assert_close(params.in_percent, 40.0, "線幅グラフの外端が入り(%)へ反映されていません")
    _assert_close(params.out_percent, 20.0, "線幅グラフの内端が抜き(%)へ反映されていません")
    _assert_close(params.in_start_percent, 35.0, "線幅グラフの山位置が入り始点(%)へ反映されていません")
    _assert_close(params.out_start_percent, 35.0, "線幅グラフの山位置が抜き始点(%)へ反映されていません")


def _assert_graph_live_sync(effect_inout_curve, effect_line_op, params, obj, layer, node) -> None:
    effect_inout_curve.request_live_profile_sync(params)
    effect_inout_curve._apply_points_to_node(
        node,
        ((0.0, 0.15), (0.25, 0.7), (0.45, 1.0), (0.75, 1.0), (1.0, 0.35)),
    )
    effect_inout_curve._live_profile_sync_tick()
    _assert_close(params.in_percent, 35.0, "線幅グラフの外端が入り(%)へ即時反映されていません")
    _assert_close(params.out_percent, 15.0, "線幅グラフの内端が抜き(%)へ即時反映されていません")
    _assert_close(params.in_start_percent, 25.0, "線幅グラフの外端側変化が即時反映されていません")
    _assert_close(params.out_start_percent, 45.0, "線幅グラフの内端側変化が即時反映されていません")
    saved = effect_line_op._layer_params_data(obj, layer)
    _assert_close(saved["in_percent"], 35.0, "同期タイマー後の入り(%)が効果線へ保存されていません")
    _assert_close(saved["out_percent"], 15.0, "同期タイマー後の抜き(%)が効果線へ保存されていません")


def _assert_white_black_graphs(layer_detail_op, effect_inout_curve, context, obj, params) -> None:
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
    layout = _Layout()
    layer_detail_op._draw_effect_detail(layout, context, obj, load_from_layer=False)
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
    assert effect_inout_curve.sync_all_profile_nodes_to_params(params)
    _assert_close(params.white_outline_white_in_percent, 45.0, "白線グラフ外端")
    _assert_close(params.white_outline_white_out_percent, 15.0, "白線グラフ内端")
    _assert_close(params.white_outline_white_in_range_percent, 20.0, "白線グラフ外端側範囲")
    _assert_close(params.white_outline_white_out_range_percent, 35.0, "白線グラフ内端側範囲")
    _assert_close(params.white_outline_black_in_percent, 55.0, "黒線グラフ外端")
    _assert_close(params.white_outline_black_out_percent, 25.0, "黒線グラフ内端")
    _assert_close(params.white_outline_black_in_range_percent, 35.0, "黒線グラフ外端側範囲")
    _assert_close(params.white_outline_black_out_range_percent, 10.0, "黒線グラフ内端側範囲")


def _assert_graph_saved_and_generated(
    layer_detail_op,
    effect_line_op,
    effect_line_gen,
    context,
    params,
    obj,
    layer,
    bmanga_id: str,
) -> None:
    layer_detail_op._sync_detail_profile_curve(context, "effect", bmanga_id)
    assert layer_detail_op._apply_effect_detail_params_to_layer(context, obj, layer)
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
        layer_stack_op = _sub("operators.layer_stack_op")
        layer_stack_detail_ui = _sub("panels.layer_stack_detail_ui")
        layer_detail_op = _sub("operators.layer_detail_op")
        get_work = _sub("core.work").get_work
        effect_inout_curve = _sub("utils.effect_inout_curve")
        layer_stack_utils = _sub("utils.layer_stack")
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
        assert layer_detail_op._detail_dialog_width_for_kind(context, "effect", bmanga_id) == 1320

        _assert_detail_layout(layer_detail_op, effect_line_op, context, scene, obj)
        _assert_layer_stack_dialog_layout(
            layer_stack_detail_ui,
            layer_stack_op,
            layer_stack_utils,
            effect_line_op,
            context,
            scene,
            layer,
        )
        node = _assert_graph_numeric_to_curve(
            layer_detail_op,
            effect_line_op,
            effect_inout_curve,
            context,
            scene,
            obj,
        )
        _assert_graph_curve_to_numeric(layer_detail_op, effect_inout_curve, context, obj, params, node)
        _assert_graph_saved_and_generated(
            layer_detail_op,
            effect_line_op,
            effect_line_gen,
            context,
            params,
            obj,
            layer,
            bmanga_id,
        )
        _assert_graph_live_sync(effect_inout_curve, effect_line_op, params, obj, layer, node)
        _assert_white_black_graphs(layer_detail_op, effect_inout_curve, context, obj, params)
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
