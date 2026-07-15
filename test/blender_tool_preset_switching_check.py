"""Blender 実機チェック: ツールドロップダウンの全プリセット選択と切替."""

from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import MethodType, SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_tool_preset_switching"

EXPECTED_PRESETS = {
    "paper": {"商業誌B4マンガ原稿用紙"},
    "border": {"標準", "輪郭ぼかし", "極太", "線無し"},
    "balloon": {
        "DEFAULT", "mode:nurbs", "shape:rect", "shape:ellipse",
        "shape:cloud", "shape:fluffy", "shape:thorn", "shape:thorn-curve",
    },
    "tail": {"標準 (三角)", "曲線", "ペン線 (抜き)", "心の声 (楕円)"},
    "text": {"セリフ（標準）", "ナレーション"},
    "effect": {"集中線", "ウニフラ", "ベタフラ", "流線", "白抜き線"},
    "fill": {"ベタ塗り (黒)", "ベタ塗り (白)", "ベタ塗り (50%)", "ベタ塗り (黒 半透明)"},
    "gradient": {"黒→白", "白→黒", "黒→白 (円形)", "黒→白 (半透明)"},
    "image_path": {"標準スタンプ", "標準リボン", "一枚リボン", "円形スタンプ"},
}


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
    return importlib.import_module(f"{MOD_NAME}.{path}")


class _Op:
    def __init__(self, op_id: str):
        self.op_id = op_id

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)


class _Layout:
    def __init__(self, props=None, labels=None, ops=None):
        self.props = [] if props is None else props
        self.labels = [] if labels is None else labels
        self.ops = [] if ops is None else ops
        self.enabled = True

    def row(self, align: bool = False):
        return _Layout(self.props, self.labels, self.ops)

    def column(self, align: bool = False):
        return _Layout(self.props, self.labels, self.ops)

    def box(self):
        return _Layout(self.props, self.labels, self.ops)

    def separator(self, **_kwargs):
        return None

    def label(self, text: str = "", **_kwargs):
        self.labels.append(str(text))
        return None

    def prop(self, _owner, attr: str, **_kwargs):
        self.props.append(str(attr))
        return None

    def operator(self, op_id: str, **_kwargs):
        self.ops.append(str(op_id))
        return _Op(str(op_id))

    def template_list(self, listtype, list_id, data, propname, active_data, active_propname, **_kwargs):
        self.props.append(str(propname))
        return None


class _DummyTool:
    def __init__(self):
        self.finished = False

    def finish_from_external(self, _context, *, keep_selection: bool = True) -> None:
        del keep_selection
        self.finished = True


class _CursorWindow:
    def __init__(self):
        self.set_calls: list[str] = []
        self.restore_count = 0

    def cursor_modal_set(self, cursor: str) -> None:
        self.set_calls.append(str(cursor))

    def cursor_modal_restore(self) -> None:
        self.restore_count += 1


class _CursorContext:
    def __init__(self, window):
        self.window = window


class _CursorOp:
    _cursor_modal_set = True
    _cursor_temporarily_restored = False


def _ids(items) -> list[str]:
    return [str(item[0]) for item in items]


def _assert_close_tuple(actual, expected, label: str, eps: float = 1.0e-5) -> None:
    actual_vals = [float(v) for v in actual]
    expected_vals = [float(v) for v in expected]
    assert len(actual_vals) == len(expected_vals), label
    for a, e in zip(actual_vals, expected_vals, strict=False):
        assert abs(a - e) <= eps, (label, actual_vals, expected_vals)


def _assert_exact(actual, category: str) -> None:
    assert set(actual) == EXPECTED_PRESETS[category], (
        category,
        sorted(actual),
        sorted(EXPECTED_PRESETS[category]),
    )


def _assert_declared_mapping(actual: dict, expected: dict, label: str) -> None:
    for key, value in expected.items():
        assert key in actual, (label, key, sorted(actual))
        current = actual[key]
        if isinstance(value, dict):
            assert isinstance(current, dict), (label, key, current)
            _assert_declared_mapping(current, value, f"{label}:{key}")
        elif isinstance(value, (list, tuple)):
            if value and isinstance(value[0], (list, tuple)):
                assert len(current) == len(value), (label, key, current, value)
                for index, (left, right) in enumerate(zip(current, value, strict=True)):
                    _assert_close_tuple(left, right, f"{label}:{key}:{index}")
            else:
                _assert_close_tuple(current, value, f"{label}:{key}")
        elif isinstance(value, float):
            assert abs(float(current) - value) <= 1.0e-5, (label, key, current, value)
        else:
            assert current == value, (label, key, current, value)


def _check_paper_preset(context, work_dir: Path) -> None:
    presets = _sub("io.presets")
    names = [preset.name for preset in presets.list_global_presets()]
    _assert_exact(names, "paper")
    work = context.scene.bmanga_work
    work.coma_gap.vertical_mm = 91.0
    work.coma_gap.horizontal_mm = 92.0
    result = bpy.ops.bmanga.paper_preset_apply(
        "EXEC_DEFAULT", preset_name="商業誌B4マンガ原稿用紙"
    )
    assert "FINISHED" in result, result
    assert abs(float(work.coma_gap.vertical_mm) - 7.3) <= 1.0e-6
    assert abs(float(work.coma_gap.horizontal_mm) - 2.1) <= 1.0e-6
    preset = presets.load_preset_by_name("商業誌B4マンガ原稿用紙", work_dir)
    assert preset is not None and "comaGap" in preset.data
    print("PAPER_PRESETS", names, flush=True)


def _check_tool_panel_rows(context, wm) -> None:
    del wm
    coma_modal_state = _sub("operators.coma_modal_state")
    tool_panel = _sub("panels.tool_panel")
    expected = {
        "coma_create": ("コマ作成の枠線", "bmanga_border_preset_list"),
        "balloon_tool": ("フキダシプリセット", "bmanga_balloon_preset_list"),
        "balloon_nurbs_tool": ("フキダシプリセット", "bmanga_balloon_preset_list"),
        "balloon_tail_tool": ("しっぽプリセット", "bmanga_tail_preset_list"),
        "text_tool": ("テキストプリセット", "bmanga_text_preset_list"),
        "effect_line_tool": ("効果線プリセット", "bmanga_effect_line_preset_list"),
        "fill_tool": ("囲い塗りプリセット", "bmanga_fill_preset_list"),
        "gradient_tool": ("グラデーション", "bmanga_gradient_preset_list"),
        "image_path_tool": ("パターンカーブ", "bmanga_image_path_preset_list"),
    }
    dummies: list[_DummyTool] = []
    for tool_name, (_label, prop_name) in expected.items():
        dummy = _DummyTool()
        dummies.append(dummy)
        coma_modal_state.set_active(tool_name, dummy, context)
        try:
            layout = _Layout()
            tool_panel._draw_tool_preset_list(layout, context)
            assert prop_name in layout.props, (tool_name, layout.props)
        finally:
            coma_modal_state.clear_active(tool_name, dummy, context)
    print("TOOL_PANEL_ROWS", sorted(expected), flush=True)


def _check_balloon_selector(context, wm) -> None:
    preset_op = _sub("operators.preset_op")
    coma_modal_state = _sub("operators.coma_modal_state")
    items = preset_op._balloon_tool_preset_enum_items(None, context)
    ids = _ids(items)
    expected = {
        "DEFAULT",
        "mode:nurbs",
        "shape:rect",
        "shape:ellipse",
        "shape:cloud",
        "shape:fluffy",
        "shape:thorn",
        "shape:thorn-curve",
    }
    assert expected == set(ids), ids
    _assert_exact(ids, "balloon")
    for preset_id in ids:
        wm.bmanga_balloon_tool_preset_selector = preset_id
        mode = preset_op.selected_balloon_tool_creation_mode(context)
        shape, custom_name = preset_op.selected_balloon_tool_shape(context)
        if preset_id == "mode:nurbs":
            assert mode == "nurbs" and shape == "" and custom_name == ""
        elif preset_id.startswith("shape:"):
            assert mode == "drag" and shape == preset_id.split(":", 1)[1]
        elif preset_id.startswith("custom:"):
            assert mode == "drag" and shape == "custom"
            assert custom_name == preset_id.split(":", 1)[1]
        else:
            assert mode == "drag" and shape == "" and custom_name == ""

    dummy_nurbs = _DummyTool()
    coma_modal_state.set_active("balloon_nurbs_tool", dummy_nurbs, context)
    wm.bmanga_balloon_tool_preset_selector = "DEFAULT"
    assert dummy_nurbs.finished
    assert coma_modal_state.get_active("balloon_nurbs_tool") is None
    coma_modal_state.finish_all(context)

    dummy_drag = _DummyTool()
    coma_modal_state.set_active("balloon_tool", dummy_drag, context)
    wm.bmanga_balloon_tool_preset_selector = "mode:nurbs"
    assert dummy_drag.finished
    assert coma_modal_state.get_active("balloon_tool") is None
    coma_modal_state.finish_all(context)
    print("BALLOON_PRESETS", ids, flush=True)


def _check_cursor_sync() -> None:
    coma_modal_state = _sub("operators.coma_modal_state")
    view_event_region = _sub("operators.view_event_region")
    original = view_event_region.is_view3d_window_event
    window = _CursorWindow()
    context = _CursorContext(window)
    op = _CursorOp()
    try:
        view_event_region.is_view3d_window_event = lambda _context, _event: False
        coma_modal_state.sync_modal_cursor_for_event_region(context, object(), op, "CROSSHAIR")
        assert window.restore_count == 1
        assert op._cursor_modal_set is False
        assert op._cursor_temporarily_restored is True

        view_event_region.is_view3d_window_event = lambda _context, _event: True
        coma_modal_state.sync_modal_cursor_for_event_region(context, object(), op, "CROSSHAIR")
        assert window.set_calls[-1] == "CROSSHAIR"
        assert op._cursor_modal_set is True
        assert op._cursor_temporarily_restored is False
    finally:
        view_event_region.is_view3d_window_event = original
    print("CURSOR_SYNC_OK", flush=True)


def _check_fill_and_gradient(context, wm) -> None:
    preset_op = _sub("operators.preset_op")
    fill_presets = _sub("io.fill_presets")
    gradient_presets = _sub("io.gradient_presets")
    scene = context.scene
    fill_ids = _ids(preset_op._fill_tool_preset_enum_items(None, context))
    assert fill_ids, "囲い塗りプリセットが空です"
    _assert_exact(fill_ids, "fill")
    for name in fill_ids:
        if not name or name == "NONE":
            continue
        wm.bmanga_fill_tool_preset_selector = name
        entry = scene.bmanga_fill_layers.add()
        assert preset_op.apply_fill_preset_to_entry(context, entry), name
        preset = fill_presets.load_preset_by_name(name)
        assert preset is not None, name
        snapshot = fill_presets.snapshot_from_entry(entry)
        _assert_close_tuple(snapshot["color"], preset.data["color"], name)
        assert int(snapshot["opacity"]) == int(preset.data["opacity"]), name
    grad_ids = _ids(preset_op._gradient_tool_preset_enum_items(None, context))
    assert grad_ids, "グラデーションプリセットが空です"
    _assert_exact(grad_ids, "gradient")
    for name in grad_ids:
        if not name or name == "NONE":
            continue
        wm.bmanga_gradient_tool_preset_selector = name
        entry = scene.bmanga_fill_layers.add()
        assert preset_op.apply_gradient_preset_to_entry(context, entry), name
        preset = gradient_presets.load_preset_by_name(name)
        assert preset is not None, name
        snapshot = gradient_presets.snapshot_from_entry(entry)
        _assert_close_tuple(snapshot["color"], preset.data["color"], name)
        _assert_close_tuple(snapshot["color2"], preset.data["color2"], name)
        assert snapshot["gradient_type"] == preset.data["gradient_type"], name
        assert int(snapshot["opacity"]) == int(preset.data["opacity"]), name
    print("FILL_PRESETS", fill_ids, flush=True)
    print("GRADIENT_PRESETS", grad_ids, flush=True)


def _check_text_presets(context, wm, page, work_dir: Path) -> None:
    preset_op = _sub("operators.preset_op")
    text_presets = _sub("io.text_presets")
    items = preset_op._text_preset_enum_items(None, context)
    names = [name for name in _ids(items) if name and name != "NONE"]
    assert names, items
    _assert_exact(names, "text")
    for name in names:
        wm.bmanga_text_tool_preset_selector = name
        entry = page.texts.add()
        assert preset_op.apply_text_preset_to_entry(context, entry), name
        preset = next(p for p in text_presets.list_all_presets(work_dir) if p.name == name)
        snapshot = text_presets.snapshot_from_entry(entry)
        expected = {
            key: value for key, value in preset.data.items()
            if key in text_presets._TEXT_KEYS
        }
        assert set(expected) == set(text_presets._TEXT_KEYS), (name, sorted(expected))
        for key, value in expected.items():
            actual = snapshot[key]
            if isinstance(value, (list, tuple)):
                _assert_close_tuple(actual, value, f"{name}:{key}")
            elif isinstance(value, float):
                assert abs(float(actual) - value) <= 1.0e-4, (name, key, actual, value)
            else:
                assert actual == value, (name, key, actual, value)
    print("TEXT_PRESETS", names, flush=True)


def _check_effect_line_presets(context, wm, work_dir: Path) -> None:
    effect_line_preset_op = _sub("operators.effect_line_preset_op")
    effect_line_presets = _sub("io.effect_line_presets")
    params = context.scene.bmanga_effect_line_params
    names = [name for name in _ids(effect_line_preset_op._effect_line_tool_preset_enum_items(None, context)) if name]
    assert names, "効果線プリセットが空です"
    _assert_exact(names, "effect")
    effect_line = _sub("core.effect_line")
    effect_line_op = _sub("operators.effect_line_op")
    effect_line_op._reset_scene_effect_params(params)
    default_data = effect_line.effect_params_to_dict(params)
    for name in names:
        params.rotation_deg = 73.0
        params.brush_size_mm = 1.75
        params.start_shape = "cloud"
        params.spacing_mode = "distance"
        params.white_underlay_enabled = True
        wm.bmanga_effect_line_tool_preset_selector = name
        assert effect_line_preset_op.apply_selected_effect_line_preset(context, params), name
        preset = effect_line_presets.load_preset_by_name(name, work_dir)
        assert preset is not None
        actual = effect_line.effect_params_to_dict(params)
        expected = dict(default_data)
        expected.update({
            key: value for key, value in preset.data.items()
            if key in effect_line.EFFECT_PARAM_FIELDS or key == "schema_version"
        })
        for key in effect_line.EFFECT_PARAM_FIELDS:
            left, right = actual[key], expected[key]
            if isinstance(right, (list, tuple)):
                _assert_close_tuple(left, right, f"{name}:{key}")
            elif isinstance(right, float):
                assert abs(float(left) - right) <= 1.0e-5, (name, key, left, right)
            else:
                assert left == right, (name, key, left, right)
    print("EFFECT_LINE_PRESETS", names, flush=True)


def _check_tail_presets(context, wm, page, work_dir: Path) -> None:
    balloon_tail_detail_op = _sub("operators.balloon_tail_detail_op")
    tail_presets = _sub("io.tail_presets")
    entry = page.balloons.add()
    tail = entry.tails.add()
    names = [name for name in _ids(balloon_tail_detail_op._tail_preset_enum_items(None, context)) if name]
    assert names, "しっぽプリセットが空です"
    _assert_exact(names, "tail")
    for name in names:
        wm.bmanga_tail_preset_selector = name
        preset = tail_presets.load_preset_by_name(name, work_dir)
        assert preset is not None, name
        tail_presets.apply_preset_to_tail(preset, tail)
        snapshot = tail_presets.preset_dict_from_tail(tail, name)["tail"]
        assert snapshot == preset.data["tail"], (name, snapshot, preset.data["tail"])
    print("TAIL_PRESETS", names, flush=True)


def _check_image_path_presets(context, wm, work_dir: Path) -> None:
    preset_op = _sub("operators.preset_op")
    image_path_presets = _sub("io.image_path_presets")
    scene = context.scene
    names = [name for name in _ids(preset_op._image_path_tool_preset_enum_items(None, context)) if name]
    assert names, "パターンカーブプリセットが空です"
    _assert_exact(names, "image_path")
    for name in names:
        wm.bmanga_image_path_tool_preset_selector = name
        entry = scene.bmanga_image_path_layers.add()
        assert preset_op.apply_image_path_preset_to_entry(context, entry), name
        preset = image_path_presets.load_preset_by_name(name, work_dir)
        assert preset is not None, name
        snapshot = image_path_presets.preset_dict_from_entry(entry, name)
        for key, expected in preset.data.items():
            if key in {"schemaVersion", "presetType", "presetName", "description"}:
                continue
            actual = snapshot[key]
            if isinstance(expected, (list, tuple)):
                _assert_close_tuple(actual, expected, f"{name}:{key}")
            elif isinstance(expected, float):
                assert abs(float(actual) - expected) <= 1.0e-4, (name, key, actual, expected)
            else:
                assert actual == expected, (name, key, actual, expected)
    print("IMAGE_PATH_PRESETS", names, flush=True)


def _check_border_presets(context, wm, page, work_dir: Path) -> None:
    preset_op = _sub("operators.preset_op")
    border_presets = _sub("io.border_presets")
    coma = page.comas[0] if len(page.comas) else page.comas.add()
    names = [name for name in _ids(preset_op._border_preset_enum_items(None, context)) if name]
    assert names, "枠線プリセットが空です"
    _assert_exact(names, "border")
    for name in names:
        wm.bmanga_border_preset_selector = name
        preset = border_presets.load_preset_by_name(name, work_dir)
        assert preset is not None, name
        border_presets.apply_preset_to_coma(preset, coma)
        assert str(coma.border.preset_name) == name
        snapshot = border_presets.preset_dict_from_coma(coma, name)
        _assert_declared_mapping(snapshot["border"], preset.data["border"], f"{name}:border")
        _assert_declared_mapping(
            snapshot["whiteMargin"], preset.data["whiteMargin"], f"{name}:whiteMargin"
        )
    print("BORDER_PRESETS", names, flush=True)


def _check_border_creation_sequence(context, wm, page) -> None:
    """作成用の選択変更が直前のコマを上書きしないことを確認する。"""
    coma_modal_state = _sub("operators.coma_modal_state")
    coma_create_op = _sub("operators.coma_create_op")
    start_count = len(page.comas)
    expected = {
        "標準": ("solid", True, 0.5),
        "輪郭ぼかし": ("brush", True, 35.0),
        "極太": ("solid", True, 1.2),
        "線無し": ("solid", False, 0.5),
    }
    dummy = _DummyTool()
    coma_modal_state.set_active("coma_create", dummy, context)
    try:
        for index, name in enumerate(expected):
            wm.bmanga_border_preset_selector = name
            operator = SimpleNamespace(_page_id=str(page.id))
            operator.report = lambda *_args, **_kwargs: None
            for method_name in (
                "_locked_page", "_apply_border_preset", "_refresh_coma_objects"
            ):
                setattr(
                    operator,
                    method_name,
                    MethodType(getattr(coma_create_op.BMANGA_OT_coma_create_tool, method_name), operator),
                )
            MethodType(coma_create_op.BMANGA_OT_coma_create_tool._create_coma, operator)(
                context,
                "rect",
                x=10.0 + index * 25.0,
                y=10.0,
                w=20.0,
                h=20.0,
                poly=None,
            )
    finally:
        coma_modal_state.clear_active("coma_create", dummy, context)
    created = list(page.comas)[start_count:]
    assert len(created) == len(expected), len(created)
    for entry, (name, (style, visible, width)) in zip(created, expected.items(), strict=True):
        assert str(entry.border.preset_name) == name, (name, entry.border.preset_name)
        assert str(entry.border.style) == style, (name, entry.border.style)
        assert bool(entry.border.visible) is visible, (name, entry.border.visible)
        assert abs(float(entry.border.width_mm) - width) <= 1.0e-6, (name, entry.border.width_mm)
    print("BORDER_CREATION_SEQUENCE", list(expected), flush=True)


def _check_raster_dpi_presets() -> None:
    raster_layer_op = _sub("operators.raster_layer_op")
    cls = raster_layer_op.BMANGA_OT_raster_layer_add
    prop = bpy.ops.bmanga.raster_layer_add.get_rna_type().properties["dpi_preset"]
    identifiers = {item.identifier for item in prop.enum_items}
    assert identifiers == {"150", "300", "600", "custom"}, identifiers
    expected = {"150": 150, "300": 300, "600": 600, "custom": 72}
    for preset_id, dpi in expected.items():
        probe = SimpleNamespace(dpi_preset=preset_id, dpi=72)
        assert cls._resolved_dpi(probe) == dpi, (preset_id, cls._resolved_dpi(probe))
    print("RASTER_DPI_PRESETS", expected, flush=True)


def _check_balloon_custom_shape_save(page) -> None:
    balloon_presets = _sub("io.balloon_presets")
    balloon_multiline_curve = _sub("utils.balloon_multiline_curve")
    source = page.balloons.add()
    source.id = "preset_shape_source"
    source.shape = "ellipse"
    source.x_mm = 33.0
    source.y_mm = 44.0
    source.width_mm = 48.0
    source.height_mm = 26.0
    page.active_balloon_index = len(page.balloons) - 1
    result = bpy.ops.bmanga.balloon_save_preset(
        "EXEC_DEFAULT",
        preset_name="楕円輪郭保存確認",
        description="テスト用",
        absolute_coords=False,
    )
    assert "FINISHED" in result, result
    preset = balloon_presets.load_preset_by_name("楕円輪郭保存確認")
    assert preset is not None
    vertices = preset.data.get("vertices", ())
    assert len(vertices) > 4, "カスタム形状保存が外接矩形4点へ劣化しています"
    target = page.balloons.add()
    target.id = "preset_shape_target"
    target.shape = "custom"
    target.custom_preset_name = preset.name
    target.width_mm = 96.0
    target.height_mm = 52.0
    outline, _corners = balloon_multiline_curve.body_outline_for_entry(target)
    assert len(outline) == len(vertices) and len(outline) > 4
    print("BALLOON_CUSTOM_SHAPE_SAVE", preset.name, len(vertices), flush=True)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_tool_preset_switching_"))
    old_config = os.environ.get("BMANGA_USER_CONFIG_DIR")
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(temp_root / "config")
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "ToolPresetSwitching.bmanga"))
        assert "FINISHED" in result, result
        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]
        work_dir = Path(str(work.work_dir))
        wm = context.window_manager

        _check_paper_preset(context, work_dir)
        _check_tool_panel_rows(context, wm)
        _check_balloon_selector(context, wm)
        _check_cursor_sync()
        _check_fill_and_gradient(context, wm)
        _check_text_presets(context, wm, page, work_dir)
        _check_effect_line_presets(context, wm, work_dir)
        _check_tail_presets(context, wm, page, work_dir)
        _check_image_path_presets(context, wm, work_dir)
        _check_border_presets(context, wm, page, work_dir)
        _check_border_creation_sequence(context, wm, page)
        _check_raster_dpi_presets()
        _check_balloon_custom_shape_save(page)
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
    print("[ok] tool preset switching works", flush=True)


if __name__ == "__main__":
    try:
        main()
        os._exit(0)
    except Exception:
        import traceback

        traceback.print_exc()
        os._exit(1)
