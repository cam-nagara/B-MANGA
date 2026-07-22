"""Blender UI実機: 作品ファイル(ページ一覧)でレイヤーがクリック選択されないこと.

2026-07-22 ユーザー報告「シナリオ取込後、1ページ目のテキストだけ一覧で
クリック選択できハンドルが出る」の再発防止。

- 作品ファイル (work.blend) では、ページ詳細 (texts) がメモリへ残っていても
  hit_object_at_event がレイヤー (text等) を返さないこと
- ページ用blend (page.blend) では従来どおりテキストがヒットすること
  (ガードがページ編集のピッキングを壊していないこと)
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_work_list_layer_pick_guard"


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


def _submodule(path: str):
    return importlib.import_module(f"{MOD_NAME}.{path}")


def _view3d_context():
    windows = list(bpy.context.window_manager.windows)
    current = getattr(bpy.context, "window", None)
    if current is not None:
        windows = [current, *[w for w in windows if w != current]]
    for window in windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            space = area.spaces.active
            rv3d = getattr(space, "region_3d", None)
            if region is not None and rv3d is not None:
                return window, screen, area, region, space, rv3d
    return None


def _event_at_world_mm(x_mm: float, y_mm: float):
    from bpy_extras.view3d_utils import location_3d_to_region_2d

    geom = _submodule("utils.geom")
    view = _view3d_context()
    if view is None:
        raise RuntimeError("VIEW_3D が見つかりません")
    window, screen, area, region, space, rv3d = view
    with bpy.context.temp_override(
        window=window, screen=screen, area=area, region=region,
        space_data=space, region_data=rv3d,
    ):
        try:
            bpy.ops.view3d.view_axis(type="TOP", align_active=False)
        except Exception:
            pass
        space.show_region_ui = False
        space.show_region_toolbar = False
        rv3d.view_perspective = "ORTHO"
        rv3d.view_location = Vector((geom.mm_to_m(x_mm), geom.mm_to_m(y_mm), 0.0))
        rv3d.view_distance = 0.6
        try:
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
        except Exception:
            pass
    point = location_3d_to_region_2d(
        region, rv3d, (geom.mm_to_m(x_mm), geom.mm_to_m(y_mm), 0.0)
    )
    if point is None:
        raise AssertionError("対象座標を画面座標へ変換できません")
    return view, SimpleNamespace(
        type="LEFTMOUSE",
        value="PRESS",
        mouse_x=int(region.x + point.x),
        mouse_y=int(region.y + point.y),
        mouse_region_x=int(point.x),
        mouse_region_y=int(point.y),
        ctrl=False,
        shift=False,
        alt=False,
        oskey=False,
    )


def _text_center_mm(work, page_index: int) -> tuple[float, float]:
    page_grid = _submodule("utils.page_grid")

    page = work.pages[page_index]
    entry = page.texts[0]
    ox, oy = page_grid.page_total_offset_mm(work, bpy.context.scene, page_index)
    return (
        ox + float(entry.x_mm) + float(entry.width_mm) * 0.5,
        oy + float(entry.y_mm) + float(entry.height_mm) * 0.5,
    )


def _hit_kind_at(view, event) -> str:
    object_tool_op = _submodule("operators.object_tool_op")

    window, screen, area, region, space, rv3d = view
    with bpy.context.temp_override(
        window=window, screen=screen, area=area, region=region,
        space_data=space, region_data=rv3d,
    ):
        hit = object_tool_op.hit_object_at_event(bpy.context, event)
    return str((hit or {}).get("kind", "") or "")


def _start_check(temp_root: Path) -> None:
    page_detail = _submodule("utils.page_detail")
    page_io = _submodule("io.page_io")
    page_file_scene = _submodule("utils.page_file_scene")

    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PickGuard.bmanga"))
    assert result == {"FINISHED"}, result
    scene = bpy.context.scene
    work = scene.bmanga_work
    scene.bmanga_overview_mode = True
    assert page_file_scene.is_work_list_scene(scene)

    # ページ詳細がメモリに残った状態 (シナリオ取込直後にユーザーが踏んだ
    # 状態の等価物) を作る: p0001 にテキストを追加し JSON にも保存する。
    page = work.pages[0]
    page_detail.ensure_page_detail(work, page)
    entry = page.texts.add()
    entry.id = "t0001"
    entry.body = "選択ガード確認"
    entry.x_mm = 40.0
    entry.y_mm = 60.0
    entry.width_mm = 60.0
    entry.height_mm = 40.0
    page_io.save_page_json(Path(work.work_dir), page)
    assert page.detail_loaded and len(page.texts) == 1

    cx, cy = _text_center_mm(work, 0)
    view, event = _event_at_world_mm(cx, cy)
    kind = _hit_kind_at(view, event)
    assert kind != "text", (
        "作品ファイル(ページ一覧)でテキストがクリックヒットしています: "
        f"kind={kind!r}"
    )
    print("WORK_LIST_TEXT_PICK_BLOCKED_OK", f"kind={kind!r}", flush=True)

    # ページ用blendでは従来どおりテキストがヒットすること
    result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
    assert result == {"FINISHED"}, result
    scene = bpy.context.scene
    work = scene.bmanga_work
    assert not page_file_scene.is_work_list_scene(scene)
    page = work.pages[0]
    assert len(page.texts) >= 1, "ページ用blendでテキスト詳細が読み込まれていません"
    cx, cy = _text_center_mm(work, 0)
    view, event = _event_at_world_mm(cx, cy)
    kind = _hit_kind_at(view, event)
    assert kind == "text", (
        f"ページ用blendでテキストがヒットしません (ガードの巻き込み): kind={kind!r}"
    )
    print("PAGE_FILE_TEXT_PICK_ALLOWED_OK", flush=True)


def main() -> None:
    if bpy.app.background:
        raise RuntimeError("このチェックは --background なしで実行してください")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        bpy.context.preferences.view.show_splash = False
    except Exception:
        pass
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_pick_guard_"))
    attempts = {"count": 0}

    def _timer():
        attempts["count"] += 1
        if bpy.context.window is None and attempts["count"] < 30:
            return 0.1
        try:
            _start_check(temp_root)
        except Exception:
            traceback.print_exc()
            os._exit(1)
        print("BMANGA_WORK_LIST_LAYER_PICK_GUARD_OK", flush=True)
        os._exit(0)
        return None

    bpy.app.timers.register(_timer, first_interval=0.2)


if __name__ == "__main__":
    main()
