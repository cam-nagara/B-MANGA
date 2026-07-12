"""Blender 実機チェック: ツールドロップダウンの全プリセット選択と切替."""

from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_tool_preset_switching"


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
    assert expected <= set(ids), ids
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
    for name in fill_ids:
        if not name or name == "NONE":
            continue
        wm.bmanga_fill_tool_preset_selector = name
        entry = scene.bmanga_fill_layers.add()
        assert preset_op.apply_fill_preset_to_entry(context, entry), name
        preset = fill_presets.load_preset_by_name(name)
        assert preset is not None, name
    grad_ids = _ids(preset_op._gradient_tool_preset_enum_items(None, context))
    assert grad_ids, "グラデーションプリセットが空です"
    for name in grad_ids:
        if not name or name == "NONE":
            continue
        wm.bmanga_gradient_tool_preset_selector = name
        entry = scene.bmanga_fill_layers.add()
        assert preset_op.apply_gradient_preset_to_entry(context, entry), name
        preset = gradient_presets.load_preset_by_name(name)
        assert preset is not None, name
    print("FILL_PRESETS", fill_ids, flush=True)
    print("GRADIENT_PRESETS", grad_ids, flush=True)


def _check_text_presets(context, wm, page, work_dir: Path) -> None:
    preset_op = _sub("operators.preset_op")
    text_presets = _sub("io.text_presets")
    items = preset_op._text_preset_enum_items(None, context)
    names = [name for name in _ids(items) if name and name != "NONE"]
    assert names, items
    for name in names:
        wm.bmanga_text_tool_preset_selector = name
        entry = page.texts.add()
        assert preset_op.apply_text_preset_to_entry(context, entry), name
        preset = next(p for p in text_presets.list_all_presets(work_dir) if p.name == name)
        if "writing_mode" in preset.data:
            assert str(entry.writing_mode) == str(preset.data["writing_mode"])
    print("TEXT_PRESETS", names, flush=True)


def _check_effect_line_presets(context, wm, work_dir: Path) -> None:
    effect_line_preset_op = _sub("operators.effect_line_preset_op")
    effect_line_presets = _sub("io.effect_line_presets")
    params = context.scene.bmanga_effect_line_params
    names = [name for name in _ids(effect_line_preset_op._effect_line_tool_preset_enum_items(None, context)) if name]
    assert names, "効果線プリセットが空です"
    for name in names:
        wm.bmanga_effect_line_tool_preset_selector = name
        assert effect_line_preset_op.apply_selected_effect_line_preset(context, params), name
        preset = effect_line_presets.load_preset_by_name(name, work_dir)
        assert preset is not None
        expected = str(preset.data.get("effect_type", "") or "")
        if expected:
            assert str(params.effect_type) == expected, name
    print("EFFECT_LINE_PRESETS", names, flush=True)


def _check_tail_presets(context, wm, page, work_dir: Path) -> None:
    balloon_tail_detail_op = _sub("operators.balloon_tail_detail_op")
    tail_presets = _sub("io.tail_presets")
    entry = page.balloons.add()
    tail = entry.tails.add()
    names = [name for name in _ids(balloon_tail_detail_op._tail_preset_enum_items(None, context)) if name]
    assert names, "しっぽプリセットが空です"
    for name in names:
        wm.bmanga_tail_preset_selector = name
        preset = tail_presets.load_preset_by_name(name, work_dir)
        assert preset is not None, name
        tail_presets.apply_preset_to_tail(preset, tail)
        expected = str(preset.data.get("tail", {}).get("lineType", "") or "")
        if expected:
            assert str(tail.line_type) == expected, name
    print("TAIL_PRESETS", names, flush=True)


def _check_image_path_presets(context, wm, work_dir: Path) -> None:
    preset_op = _sub("operators.preset_op")
    image_path_presets = _sub("io.image_path_presets")
    scene = context.scene
    names = [name for name in _ids(preset_op._image_path_tool_preset_enum_items(None, context)) if name]
    assert names, "パターンカーブプリセットが空です"
    for name in names:
        wm.bmanga_image_path_tool_preset_selector = name
        entry = scene.bmanga_image_path_layers.add()
        assert preset_op.apply_image_path_preset_to_entry(context, entry), name
        preset = image_path_presets.load_preset_by_name(name, work_dir)
        assert preset is not None, name
        assert str(entry.draw_mode) == str(preset.data.get("drawMode", "stamp") or "stamp")
        assert str(entry.content_source) == str(preset.data.get("contentSource", "image") or "image")
    print("IMAGE_PATH_PRESETS", names, flush=True)


def _check_border_presets(context, wm, page, work_dir: Path) -> None:
    preset_op = _sub("operators.preset_op")
    border_presets = _sub("io.border_presets")
    coma = page.comas[0] if len(page.comas) else page.comas.add()
    names = [name for name in _ids(preset_op._border_preset_enum_items(None, context)) if name]
    assert names, "枠線プリセットが空です"
    for name in names:
        wm.bmanga_border_preset_selector = name
        preset = border_presets.load_preset_by_name(name, work_dir)
        assert preset is not None, name
        border_presets.apply_preset_to_coma(preset, coma)
        assert str(coma.border.preset_name) == name
    print("BORDER_PRESETS", names, flush=True)


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

        _check_tool_panel_rows(context, wm)
        _check_balloon_selector(context, wm)
        _check_cursor_sync()
        _check_fill_and_gradient(context, wm)
        _check_text_presets(context, wm, page, work_dir)
        _check_effect_line_presets(context, wm, work_dir)
        _check_tail_presets(context, wm, page, work_dir)
        _check_image_path_presets(context, wm, work_dir)
        _check_border_presets(context, wm, page, work_dir)
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
