"""Blender実機用: テキスト編集中の入力・選択・装飾表示の目視確認."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(os.environ.get("BMANGA_TEXT_EDIT_MATRIX_OUT", "") or tempfile.mkdtemp(prefix="bmanga_text_edit_matrix_"))
_MOD = None
_TEMP_ROOT: Path | None = None
_PROBE = None
_STATE: dict[str, object] = {"captures": []}


def _record_step(name: str) -> None:
    _STATE["last_step"] = str(name)
    _write_state()


def _abort_with_error(exc: BaseException) -> None:
    import traceback

    _STATE["error"] = repr(exc)
    _STATE["traceback"] = traceback.format_exc()
    _write_state()
    print("BMANGA_TEXT_EDIT_INPUT_MATRIX_VISUAL_ERROR", repr(exc), flush=True)
    print(json.dumps(_STATE, ensure_ascii=False, sort_keys=True), flush=True)
    os._exit(1)


def _timer_guard(fn):
    def _wrapped():
        try:
            _record_step(fn.__name__)
            return fn()
        except Exception as exc:  # noqa: BLE001
            _abort_with_error(exc)
            return None

    return _wrapped


class _InlineTextProbe:
    _editing = True
    _page_id = ""
    _text_id = ""
    _cursor_index = 0
    _selection_anchor = -1

    def finish_from_external(self, context, *, keep_selection: bool) -> None:
        _ = context
        _ = keep_selection

    def _touch_current_text(self, context, page, entry, idx) -> None:
        _ = page
        _ = idx
        from bmanga_dev.operators import text_edit_runtime
        from bmanga_dev.utils import layer_stack as layer_stack_utils, text_real_object

        with text_real_object.suspend_auto_sync():
            text_edit_runtime.fit_text_rect_to_body(
                entry,
                min_width=2.0,
                min_height=2.0,
                allow_shrink=True,
            )
        text_real_object.set_text_object_preview_hidden(entry, page, hidden=True)
        layer_stack_utils.tag_view3d_redraw(context)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _write_state() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "state.json").write_text(json.dumps(_STATE, ensure_ascii=False, indent=2), encoding="utf-8")


def _view3d_override() -> dict[str, object]:
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return {}
    for window in wm.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((item for item in area.regions if item.type == "WINDOW"), None)
            if region is not None:
                return {"window": window, "screen": screen, "area": area, "region": region}
    return {}


def _tag_redraw() -> None:
    for window in getattr(bpy.context.window_manager, "windows", []):
        for area in getattr(window.screen, "areas", []):
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _screenshot(name: str) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    result = bpy.ops.screen.screenshot("EXEC_DEFAULT", filepath=str(path), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"screenshot failed: {result}")
    _STATE["captures"].append(str(path))
    _write_state()
    return str(path)


def _setup() -> None:
    global _MOD, _TEMP_ROOT, _PROBE
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _MOD = _load_addon()
    _TEMP_ROOT = OUT_DIR / "Text_Edit_Matrix_work"
    if _TEMP_ROOT.exists():
        import shutil

        shutil.rmtree(_TEMP_ROOT, ignore_errors=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    result = bpy.ops.bmanga.work_new(filepath=str(_TEMP_ROOT / "Text_Edit_Matrix.bmanga"))
    if "FINISHED" not in result:
        raise RuntimeError(f"work_new failed: {result}")
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    _ensure_text_cases(work, page)
    page.active_text_index = 0
    work.active_page_index = 0
    _PROBE = _InlineTextProbe()
    from bmanga_dev.operators import coma_modal_state

    coma_modal_state.set_active("text_tool", _PROBE, bpy.context)
    override = _view3d_override()
    if override:
        with bpy.context.temp_override(**override):
            bpy.ops.bmanga.view_fit_page()
    _STATE["blend"] = str(_TEMP_ROOT / "Text_Edit_Matrix.bmanga" / "work.blend")
    _write_state()


def _text_case_specs():
    return [
        ("横書き 選択", "ABCDE", "horizontal", 26.0, 190.0, 130.0, 28.0),
        ("縦書き 選択", "日本語テスト", "vertical", 118.0, 128.0, 42.0, 70.0),
        ("IME 変換中", "入力", "horizontal", 38.0, 92.0, 120.0, 26.0),
        ("装飾変更", "太字色サイズ", "horizontal", 34.0, 54.0, 150.0, 28.0),
    ]


def _ensure_text_cases(work, page) -> None:
    from bmanga_dev.io import page_io
    from bmanga_dev.utils import text_real_object

    expected_ids = [f"text_matrix_{index + 1}" for index in range(len(_text_case_specs()))]
    current_ids = [str(getattr(entry, "id", "") or "") for entry in getattr(page, "texts", [])]
    if current_ids != expected_ids:
        page.texts.clear()
        for index, (title, body, mode, x, y, w, h) in enumerate(_text_case_specs()):
            entry = page.texts.add()
            entry.id = f"text_matrix_{index + 1}"
            entry.title = title
            entry.body = body
            entry.writing_mode = mode
            entry.x_mm = x
            entry.y_mm = y
            entry.width_mm = w
            entry.height_mm = h
            entry.font_size_q = 32.0 if index != 1 else 26.0
            text_real_object.ensure_text_real_object(scene=bpy.context.scene, entry=entry, page=page)
            text_real_object.set_text_object_preview_hidden(entry, page=page, hidden=True)
        page_io.save_page_json(Path(work.work_dir), page)
        page_io.save_pages_json(Path(work.work_dir), work)


def _activate_case(index: int, *, composition: str = "") -> None:
    from bmanga_dev.operators import text_edit_runtime
    from bmanga_dev.operators import coma_modal_state

    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    _ensure_text_cases(work, page)
    entry = page.texts[index]
    cursors = [2, 3, 2, 5]
    anchors = [0, 0, -1, 0]
    page.active_text_index = index
    _PROBE._page_id = page.id
    _PROBE._text_id = entry.id
    _PROBE._cursor_index = cursors[index]
    _PROBE._selection_anchor = anchors[index]
    coma_modal_state.set_active("text_tool", _PROBE, bpy.context)
    text_edit_runtime.set_view_edit_state(
        bpy.context,
        getattr(page, "id", ""),
        getattr(entry, "id", ""),
        _PROBE._cursor_index,
        _PROBE._selection_anchor,
    )
    text_edit_runtime._clear_ime_text_queue()
    if composition:
        text_edit_runtime._set_ime_composition_text(composition, active=True)
    _tag_redraw()


def _make_montage() -> None:
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return
    paths = [Path(item) for item in _STATE.get("captures", [])]
    images = [Image.open(path).convert("RGB") for path in paths if path.exists()]
    if not images:
        return
    width = min(960, max(image.width for image in images))
    thumbs = []
    for image in images:
        ratio = width / image.width
        thumbs.append(image.resize((width, max(1, int(image.height * ratio)))))
    label_h = 28
    montage = Image.new("RGB", (width, sum(img.height + label_h for img in thumbs)), "white")
    draw = ImageDraw.Draw(montage)
    y = 0
    for path, image in zip(paths, thumbs, strict=False):
        draw.text((10, y + 6), path.name, fill=(0, 0, 0))
        y += label_h
        montage.paste(image, (0, y))
        y += image.height
    out = OUT_DIR / "text_edit_input_matrix_montage.png"
    montage.save(out)
    _STATE["montage"] = str(out)
    _write_state()


def _finish() -> None:
    try:
        _make_montage()
        print("BMANGA_TEXT_EDIT_INPUT_MATRIX_VISUAL_OK", flush=True)
        print(json.dumps(_STATE, ensure_ascii=False, sort_keys=True), flush=True)
    finally:
        os._exit(0)


def _run_sequence():
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    _ensure_text_cases(work, page)
    _activate_case(0)
    bpy.app.timers.register(_timer_guard(_capture1), first_interval=0.4)


def _capture1():
    _screenshot("01_horizontal_selection.png")
    _step2()
    return None


def _step2():
    _activate_case(1)
    bpy.app.timers.register(_timer_guard(_capture2), first_interval=0.35)


def _capture2():
    _screenshot("02_vertical_selection.png")
    _step3()
    return None


def _step3():
    _activate_case(2, composition="日本")
    bpy.app.timers.register(_timer_guard(_capture3), first_interval=0.35)


def _capture3():
    _screenshot("03_ime_composition.png")
    _step4()
    return None


def _step4():
    _activate_case(3)
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    entry = page.texts[3]
    result = bpy.ops.bmanga.text_selection_style_popup(
        "EXEC_DEFAULT",
        page_id=getattr(page, "id", ""),
        text_id=getattr(entry, "id", ""),
        start=0,
        end=2,
        font_choice="__DEFAULT__",
        font_size_q=48.0,
        color=(0.0, 0.0, 1.0, 1.0),
        font_bold=True,
        font_italic=False,
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"text style change failed: {result}")
    from bmanga_dev.utils import text_real_object

    assert not text_real_object.has_visible_text_object(entry, page=page)
    bpy.app.timers.register(_timer_guard(_capture4), first_interval=0.35)


def _capture4():
    _screenshot("04_style_change_no_duplicate.png")
    _finish()
    return None


def main() -> None:
    try:
        _setup()
        bpy.app.timers.register(_timer_guard(_run_sequence), first_interval=0.5)
    except Exception as exc:  # noqa: BLE001
        _STATE["error"] = repr(exc)
        _write_state()
        raise


if __name__ == "__main__":
    main()
