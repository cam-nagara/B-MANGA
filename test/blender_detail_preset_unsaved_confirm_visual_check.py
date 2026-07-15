"""Blender通常画面で、未保存設定を破棄するプリセット切替確認を撮影する。"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_detail_preset_unsaved_confirm_visual"
OUT_DIR = Path(
    os.environ.get(
        "BMANGA_DETAIL_PRESET_CONFIRM_VISUAL_OUT",
        str(ROOT / "_verify" / "2026-07-16_detail_preset_unsaved_confirm"),
    )
)
IMAGE_PATH = OUT_DIR / "unsaved_preset_switch_confirm.png"
RESULT_PATH = OUT_DIR / "result.json"

_WORK_ROOT: Path | None = None
_TARGET_ID = ""
_FROM_PRESET = ""
_TO_PRESET = ""
_ATTEMPTS = 0
_CONFIRM_ATTEMPTS = 0
_DONE = False


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _sub(path: str):
    __import__(f"{MOD_NAME}.{path}")
    return sys.modules[f"{MOD_NAME}.{path}"]


def _view3d_override() -> dict[str, object]:
    for window in tuple(bpy.context.window_manager.windows):
        for area in tuple(window.screen.areas):
            if area.type != "VIEW_3D":
                continue
            region = next((item for item in area.regions if item.type == "WINDOW"), None)
            if region is not None:
                return {
                    "window": window,
                    "screen": window.screen,
                    "area": area,
                    "region": region,
                }
    raise AssertionError("3Dビューがありません")


def _temporary_regions() -> list[tuple[int, int]]:
    regions = []
    for window in tuple(bpy.context.window_manager.windows):
        for area in tuple(window.screen.areas):
            for region in tuple(area.regions):
                if region.type == "TEMPORARY" and region.width >= 180:
                    regions.append((int(region.width), int(region.height)))
    return regions


def _running_session():
    sessions = tuple(
        _sub("operators.detail_dialog_runtime")._OPEN_ACTUAL_SESSIONS.values()  # noqa: SLF001
    )
    return sessions[0] if len(sessions) == 1 else None


def _capture() -> None:
    window = bpy.context.window or next(iter(bpy.context.window_manager.windows), None)
    assert window is not None
    with bpy.context.temp_override(window=window, screen=window.screen):
        if bpy.ops.wm.redraw_timer.poll():
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=5)
        result = bpy.ops.screen.screenshot(
            "EXEC_DEFAULT",
            filepath=str(IMAGE_PATH),
            check_existing=False,
        )
    assert "FINISHED" in result and IMAGE_PATH.is_file()


def _finish() -> None:
    global _DONE
    _DONE = True
    payload = {
        "image": str(IMAGE_PATH),
        "from_preset": _FROM_PRESET,
        "to_preset": _TO_PRESET,
        "temporary_regions": _temporary_regions(),
    }
    RESULT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("BMANGA_DETAIL_PRESET_UNSAVED_CONFIRM_VISUAL_OK", payload, flush=True)
    if _WORK_ROOT is not None:
        shutil.rmtree(_WORK_ROOT, ignore_errors=True)
    os._exit(0)


def _after_confirm_open():
    global _CONFIRM_ATTEMPTS
    try:
        _CONFIRM_ATTEMPTS += 1
        # 親の詳細設定に加えて確認ポップアップが描画されるまで待つ。
        if len(_temporary_regions()) < 2 and _CONFIRM_ATTEMPTS < 30:
            return 0.1
        _capture()
        _finish()
    except Exception:
        traceback.print_exc()
        os._exit(1)
    return None


def _request_preset_switch():
    global _ATTEMPTS, _FROM_PRESET, _TO_PRESET
    try:
        _ATTEMPTS += 1
        session = _running_session()
        if session is None:
            if _ATTEMPTS < 30:
                return 0.1
            raise AssertionError("詳細設定セッションが開始されません")
        params = session.target.params
        params.brush_size_mm = min(float(params.brush_size_mm) + 0.4, 10.0)
        entries = _sub("operators.detail_preset_apply_op")._detail_preset_entries(  # noqa: SLF001
            bpy.context,
            "effect_line",
        )
        _FROM_PRESET = str(session.preset_selection or "").strip()
        assert _FROM_PRESET, "切替元プリセットが選択されていません"
        candidates = [item for item in entries if str(item[0]) != _FROM_PRESET]
        assert candidates, "切替元と異なる効果線プリセットがありません"
        identifier, label, _description = candidates[0]
        _TO_PRESET = str(identifier)
        assert _TO_PRESET != _FROM_PRESET
        with bpy.context.temp_override(**_view3d_override()):
            result = bpy.ops.bmanga.detail_preset_apply(
                "INVOKE_DEFAULT",
                preset_type="effect_line",
                preset_name=identifier,
                preset_label=label,
                target_kind="effect",
                target_id=_TARGET_ID,
                stable_id=_TARGET_ID,
                stack_uid=session.target.stack_uid or "",
                session_token=session.token,
                confirm_unsaved_changes=True,
            )
        assert "RUNNING_MODAL" in result, result
        bpy.app.timers.register(_after_confirm_open, first_interval=0.5)
    except Exception:
        traceback.print_exc()
        os._exit(1)
    return None


def _fail_safe():
    if not _DONE:
        print("プリセット切替確認の画面検証が時間内に完了しませんでした", flush=True)
        os._exit(1)
    return None


def _run() -> None:
    global _WORK_ROOT, _TARGET_ID
    if bpy.app.background:
        raise RuntimeError("この検証はBlender通常画面で実行してください")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _WORK_ROOT = Path(tempfile.mkdtemp(prefix="bmanga_preset_confirm_visual_"))
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(_WORK_ROOT / "config")
    _load_addon()
    assert "FINISHED" in bpy.ops.bmanga.work_new(
        filepath=str(_WORK_ROOT / "PresetConfirmVisual.bmanga")
    )
    assert "FINISHED" in bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)

    context = bpy.context
    work = _sub("core.work").get_work(context)
    page = work.pages[0]
    effect_op = _sub("operators.effect_line_op")
    parent_key = _sub("utils.layer_hierarchy").page_stack_key(page)
    obj, layer = effect_op._create_effect_layer(
        context,
        (40.0, 55.0, 85.0, 65.0),
        parent_key=parent_key,
    )
    assert obj is not None and layer is not None
    for candidate in tuple(context.view_layer.objects):
        candidate.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    _TARGET_ID = _sub("utils.object_naming").get_bmanga_id(obj)
    with context.temp_override(**_view3d_override()):
        result = bpy.ops.bmanga.layer_detail_open(
            "INVOKE_DEFAULT",
            bmanga_id=_TARGET_ID,
            kind="effect",
        )
    assert "RUNNING_MODAL" in result, result
    bpy.app.timers.register(_request_preset_switch, first_interval=0.5)
    bpy.app.timers.register(_fail_safe, first_interval=30.0)


if __name__ == "__main__":
    try:
        _run()
    except Exception:
        traceback.print_exc()
        os._exit(1)
