"""Blender通常画面で詳細設定の固定最大幅と列切替を確認する。"""

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
MOD_NAME = "bmanga_dev_detail_dialog_width_visual"
OUT_DIR = Path(
    os.environ.get(
        "BMANGA_DETAIL_DIALOG_WIDTH_VISUAL_OUT",
        str(ROOT / "_verify" / "2026-07-14_detail_dialog_width_visual"),
    )
)
TARGET_KIND = str(os.environ.get("BMANGA_DETAIL_DIALOG_VISUAL_TARGET", "effect") or "effect").strip().lower()
if TARGET_KIND not in {"effect", "text"}:
    raise ValueError("BMANGA_DETAIL_DIALOG_VISUAL_TARGET は effect または text を指定してください")
EFFECT_TYPE = str(os.environ.get("BMANGA_DETAIL_DIALOG_VISUAL_TYPE", "focus") or "focus").strip().lower()
if EFFECT_TYPE not in {"focus", "speed"}:
    raise ValueError("BMANGA_DETAIL_DIALOG_VISUAL_TYPE は focus または speed を指定してください")
EXPECTED_COLUMNS = 2 if TARGET_KIND == "text" else (3 if EFFECT_TYPE == "focus" else 2)
EXPECTED_MAX_COLUMNS = 2 if TARGET_KIND == "text" else 3
IMAGE_PATH = OUT_DIR / (
    "text_2columns.png"
    if TARGET_KIND == "text"
    else ("effect_focus_3columns.png" if EFFECT_TYPE == "focus" else "effect_speed_2columns.png")
)
RESULT_PATH = OUT_DIR / (
    "result_text.json" if TARGET_KIND == "text" else f"result_{EFFECT_TYPE}.json"
)

_WORK_ROOT: Path | None = None
_FIXED_WIDTH = 0
_ATTEMPTS = 0
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


def _ptr(value) -> int:
    try:
        return int(value.as_pointer())
    except Exception:
        return 0


def _temporary_regions() -> list[dict[str, int | str]]:
    regions: list[dict[str, int | str]] = []
    for window in tuple(getattr(bpy.context.window_manager, "windows", ()) or ()):
        screen = getattr(window, "screen", None)
        for area in tuple(getattr(screen, "areas", ()) or ()):
            for region in tuple(getattr(area, "regions", ()) or ()):
                if str(getattr(region, "type", "") or "") != "TEMPORARY":
                    continue
                width = int(getattr(region, "width", 0) or 0)
                height = int(getattr(region, "height", 0) or 0)
                if width < 200 or height < 100:
                    continue
                regions.append(
                    {
                        "window": _ptr(window),
                        "area": _ptr(area),
                        "region": _ptr(region),
                        "area_type": str(getattr(area, "type", "") or ""),
                        "width": width,
                        "height": height,
                    }
                )
    return sorted(regions, key=lambda item: int(item["width"]), reverse=True)


def _region_inventory() -> list[tuple[str, str, int, int]]:
    inventory: list[tuple[str, str, int, int]] = []
    for window in tuple(getattr(bpy.context.window_manager, "windows", ()) or ()):
        for area in tuple(getattr(window.screen, "areas", ()) or ()):
            for region in tuple(getattr(area, "regions", ()) or ()):
                width = int(getattr(region, "width", 0) or 0)
                height = int(getattr(region, "height", 0) or 0)
                if width >= 150 and height >= 80:
                    inventory.append((str(area.type), str(region.type), width, height))
    return inventory


def _dialog_region(session) -> dict[str, int | str]:
    regions = _temporary_regions()
    assert len(regions) <= 1, f"詳細設定ダイアログが二重です: {regions}"
    if regions:
        return regions[0]
    window = next(iter(bpy.context.window_manager.windows), None)
    assert window is not None
    return {
        "window": _ptr(window),
        "area": 0,
        "region": 0,
        "area_type": "POPUP_SCREENSHOT",
        "width": int(session.layout.dialog_width),
        "height": 0,
    }


def _running_detail_session():
    runtime = _sub("operators.detail_dialog_runtime")
    sessions = tuple(runtime._OPEN_ACTUAL_SESSIONS.values())  # noqa: SLF001
    return sessions[0] if len(sessions) == 1 else None


def _view3d_override() -> dict[str, object]:
    for window in tuple(bpy.context.window_manager.windows):
        screen = window.screen
        for area in tuple(screen.areas):
            if area.type != "VIEW_3D":
                continue
            region = next((item for item in area.regions if item.type == "WINDOW"), None)
            if region is not None:
                return {"window": window, "screen": screen, "area": area, "region": region}
    raise AssertionError("詳細設定を開ける3Dビューがありません")


def _capture(path: Path) -> None:
    window = bpy.context.window or next(iter(bpy.context.window_manager.windows), None)
    assert window is not None and window.screen is not None
    with bpy.context.temp_override(window=window, screen=window.screen):
        if bpy.ops.wm.redraw_timer.poll():
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=5)
        result = bpy.ops.screen.screenshot(
            "EXEC_DEFAULT",
            filepath=str(path),
            check_existing=False,
        )
    assert "FINISHED" in result and path.is_file(), f"画面を保存できません: {result}"


def _finish_success(region: dict[str, int | str], session) -> None:
    global _DONE
    _DONE = True
    wm = bpy.context.window_manager
    prefix = "bmanga_popup_placement_"
    placement = {
        "target_left": float(wm[f"{prefix}target_left"]),
        "target_right": float(wm[f"{prefix}target_right"]),
        "anchor_x": float(wm[f"{prefix}anchor_x"]),
        "side": str(wm[f"{prefix}side"]),
    }
    scale = float(bpy.context.preferences.system.ui_scale or 1.0)
    half_width = float(_FIXED_WIDTH) * scale * 0.5
    if placement["side"] == "right":
        assert placement["anchor_x"] - half_width > placement["target_right"], placement
    elif placement["side"] == "left":
        assert placement["anchor_x"] + half_width < placement["target_left"], placement
    else:
        raise AssertionError(f"対象を隠さない側へ配置できませんでした: {placement}")
    payload = {
        "target_kind": TARGET_KIND,
        "effect_type": EFFECT_TYPE,
        "requested_width": _FIXED_WIDTH,
        "region": region,
        "columns": int(session.layout.column_count),
        "max_columns": int(session.layout.max_columns),
        "region_detected": int(region["region"]) != 0,
        "image": str(IMAGE_PATH),
        "placement": placement,
    }
    RESULT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("BMANGA_DETAIL_DIALOG_WIDTH_VISUAL_OK", json.dumps(payload, ensure_ascii=False), flush=True)
    if _WORK_ROOT is not None:
        shutil.rmtree(_WORK_ROOT, ignore_errors=True)
    os._exit(0)


def _after_dialog_open():
    global _ATTEMPTS, _FIXED_WIDTH
    try:
        _ATTEMPTS += 1
        session = _running_detail_session()
        if session is None:
            if _ATTEMPTS < 30:
                return 0.1
            raise AssertionError(
                f"詳細設定ダイアログが表示されません: session={session is not None}, "
                f"regions={_region_inventory()}"
            )
        assert int(session.layout.max_columns) == EXPECTED_MAX_COLUMNS
        assert int(session.layout.column_count) == EXPECTED_COLUMNS
        _FIXED_WIDTH = int(session.layout.dialog_width)
        region = _dialog_region(session)
        actual_width = int(region["width"])
        assert _FIXED_WIDTH - 40 <= actual_width <= _FIXED_WIDTH + 100, (
            f"指定した固定最大幅と実画面が一致しません: requested={_FIXED_WIDTH}, actual={actual_width}"
        )
        _capture(IMAGE_PATH)
        _finish_success(region, session)
    except Exception:
        traceback.print_exc()
        os._exit(1)
    return None


def _fail_safe():
    if _DONE:
        return None
    print("詳細設定の画面検証が時間内に完了しませんでした", flush=True)
    os._exit(1)


def _run() -> None:
    global _WORK_ROOT
    if bpy.app.background:
        raise RuntimeError("この検証はBlender通常画面で実行してください")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _WORK_ROOT = Path(tempfile.mkdtemp(prefix="bmanga_detail_width_visual_"))
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(_WORK_ROOT / "config")
    _load_addon()
    result = bpy.ops.bmanga.work_new(
        filepath=str(_WORK_ROOT / "DetailWidthVisual.bmanga")
    )
    assert "FINISHED" in result, result
    result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
    assert "FINISHED" in result, result

    context = bpy.context
    work = _sub("core.work").get_work(context)
    page = work.pages[0]
    if TARGET_KIND == "text":
        entry = page.texts.add()
        entry.id = "detail_visual_text"
        entry.title = "詳細設定レイアウト"
        entry.body = "右上のプリセット一覧\n左上の配置"
        entry.x_mm = 40.0
        entry.y_mm = 55.0
        entry.width_mm = 60.0
        entry.height_mm = 35.0
        text_real_object = _sub("utils.text_real_object")
        obj = text_real_object.ensure_text_real_object(
            scene=context.scene,
            entry=entry,
            page=page,
        )
        assert obj is not None
        _sub("utils.layer_stack").sync_layer_stack_after_data_change(context)
        bmanga_id = text_real_object.text_object_bmanga_id(page, entry)
        detail_kind = "text"
    else:
        effect_op = _sub("operators.effect_line_op")
        params = context.scene.bmanga_effect_line_params
        effect_op._set_scene_params_syncing(context.scene, True)
        try:
            params.effect_type = EFFECT_TYPE
            params.spacing_mode = "angle"
            params.spacing_angle_deg = 30.0
            params.max_line_count = 12
        finally:
            effect_op._set_scene_params_syncing(context.scene, False)
        parent_key = _sub("utils.layer_hierarchy").page_stack_key(page)
        obj, layer = effect_op._create_effect_layer(
            context,
            (40.0, 55.0, 85.0, 65.0),
            parent_key=parent_key,
        )
        assert obj is not None and layer is not None
        bmanga_id = _sub("utils.object_naming").get_bmanga_id(obj)
        detail_kind = "effect"
    for candidate in tuple(context.view_layer.objects):
        if candidate.select_get():
            candidate.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    with context.temp_override(**_view3d_override()):
        result = bpy.ops.bmanga.layer_detail_open(
            "INVOKE_DEFAULT",
            bmanga_id=bmanga_id,
            kind=detail_kind,
        )
    assert "RUNNING_MODAL" in result, result
    bpy.app.timers.register(_after_dialog_open, first_interval=0.5)
    bpy.app.timers.register(_fail_safe, first_interval=30.0)


if __name__ == "__main__":
    try:
        _run()
    except Exception:
        traceback.print_exc()
        os._exit(1)
