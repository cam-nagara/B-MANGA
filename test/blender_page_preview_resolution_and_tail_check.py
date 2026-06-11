"""Blender 実機チェック: ページ一覧プレビューの解像度としっぽ結合.

1. プレビュー画像サイズが「ページ実解像度 (用紙サイズ×DPI) × 画像解像度%」
   (長辺 1536px 上限) になること。
2. 外から内へえぐるしっぽが、出力系 (プレビュー含む) でも本体から
   えぐられた形に結合されること。
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bname_dev_preview_res_tail"


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


def _check_image_size() -> None:
    ppo = _sub("utils.page_preview_object")
    scene = bpy.context.scene
    work = scene.bname_work
    work.paper.canvas_width_mm = 257.0
    work.paper.canvas_height_mm = 364.0
    work.paper.dpi = 600

    # 実解像度 長辺 = 364 / 25.4 * 600 ≈ 8598px
    scene.bname_page_preview_resolution_percentage = 12.5
    w, h = ppo._image_size(work, scene)
    assert h == 1075 and w == 759, (w, h)

    scene.bname_page_preview_resolution_percentage = 25.0
    w, h = ppo._image_size(work, scene)
    assert h == 1536, (w, h)  # 2150 → 上限 1536 へクランプ

    scene.bname_page_preview_resolution_percentage = 5.0
    w, h = ppo._image_size(work, scene)
    assert h == 430, (w, h)
    print("IMAGE_SIZE_OK", flush=True)


def _check_merged_outline() -> None:
    export_balloon = _sub("io.export_balloon")
    from shapely.geometry import Point, Polygon

    body = [(10.0, 10.0), (60.0, 10.0), (60.0, 60.0), (10.0, 60.0)]
    body_area = Polygon(body).area

    # 外から内へえぐるしっぽ (先端が本体内、根本が辺をまたぐ)
    inward_tail = [(30.0, 62.0), (35.0, 35.0), (40.0, 62.0)]
    merged = export_balloon._merged_outline_with_tails(body, [inward_tail])
    assert merged is not None, "えぐりしっぽの結合に失敗"
    poly = Polygon(merged)
    assert poly.area < body_area, (poly.area, body_area)
    assert not poly.contains(Point(35.0, 50.0)), "えぐられるべき位置が本体に残っています"
    assert poly.contains(Point(15.0, 15.0)), "本体が消えています"

    # 外へ伸びる通常しっぽ (結合して面積が増える)
    outward_tail = [(30.0, 11.0), (35.0, -15.0), (40.0, 11.0)]
    merged_out = export_balloon._merged_outline_with_tails(body, [outward_tail])
    assert merged_out is not None, "外しっぽの結合に失敗"
    poly_out = Polygon(merged_out)
    assert poly_out.area > body_area, (poly_out.area, body_area)
    assert poly_out.contains(Point(35.0, -5.0)), "しっぽ先端が結合されていません"
    print("MERGED_OUTLINE_OK", flush=True)


def _check_preview_png_tail_carve(temp_root: Path) -> None:
    """実ページのプレビュー PNG で、えぐりしっぽが白く抜けることを確認."""
    ppo = _sub("utils.page_preview_object")
    balloon_op = _sub("operators.balloon_op")
    from PIL import Image

    scene = bpy.context.scene
    work = scene.bname_work
    page = work.pages[0]

    entry = page.balloons.add()
    entry.id = balloon_op._allocate_balloon_id(page)
    entry.shape = "rect"
    entry.x_mm = 100.0
    entry.y_mm = 150.0
    entry.width_mm = 60.0
    entry.height_mm = 60.0
    entry.parent_kind = "page"
    entry.parent_key = str(page.id)
    entry.fill_color = (0.0, 0.0, 0.0, 1.0)  # 黒塗り (紙は白)

    # 外(下)から内へえぐるしっぽ: 始点は本体の外、終点は本体中心
    tail_index = balloon_op._add_tail_polyline(
        entry,
        [(130.0, 145.0), (130.0, 180.0)],
    )
    assert tail_index >= 0, "しっぽ作成に失敗"

    scene.bname_page_preview_resolution_percentage = 12.5
    path = ppo.ensure_preview_png(work, page, 0, current=False, scene=scene, force=True)
    assert path is not None and Path(path).is_file(), path

    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        width, height = rgba.size
        cw = float(work.paper.canvas_width_mm)
        ch = float(work.paper.canvas_height_mm)

        def px(x_mm: float, y_mm: float) -> tuple[int, int, int, int]:
            x = int(round(x_mm / cw * (width - 1)))
            y = int(round((1.0 - y_mm / ch) * (height - 1)))
            return rgba.getpixel((x, y))

        body_px = px(115.0, 180.0)   # 本体内 (しっぽから離れた位置)
        notch_px = px(130.0, 165.0)  # えぐり中心 (しっぽ経路上)
    assert body_px[0] < 80 and body_px[3] > 200, f"本体の塗りが描かれていません: {body_px}"
    assert notch_px[0] > 180, f"えぐり部分が塗りつぶされたままです: {notch_px}"
    print(f"PREVIEW_PNG_SIZE: {width}x{height}", flush=True)
    assert max(width, height) == 1075, (width, height)
    print("PREVIEW_TAIL_CARVE_OK", flush=True)


def _check_tail_point_plain_press() -> None:
    """Ctrl無しクリックで選択中フキダシのしっぽポイントをつかめること.

    つかめるのはフキダシ実体を編集できるページ編集シーンだけ。
    ページ一覧シーンでは不可視のポイントをつかまないことも確認する。
    """
    from types import SimpleNamespace

    tail_helper = _sub("operators.object_tool_balloon_tail")
    balloon_op = _sub("operators.balloon_op")
    object_selection = _sub("utils.object_selection")
    balloon_tail_geom = _sub("utils.balloon_tail_geom")
    page_file_scene = _sub("utils.page_file_scene")

    context = bpy.context
    work = context.scene.bname_work
    page = work.pages[0]
    entry = page.balloons[0]
    object_selection.select_key(context, object_selection.balloon_key(page, entry), mode="single")

    rect = balloon_op._tail_rect_for_entry(entry)
    tail_points = balloon_tail_geom.tail_world_points(rect, entry.tails[0])
    assert len(tail_points) >= 2, tail_points

    tool = SimpleNamespace()
    original_resolve = balloon_op._resolve_page_from_event
    fake_event = SimpleNamespace(type="LEFTMOUSE", value="PRESS")

    def _resolve_at(x_mm: float, y_mm: float):
        balloon_op._resolve_page_from_event = lambda _ctx, _event: (work, page, x_mm, y_mm)

    try:
        assert page_file_scene.is_page_edit_scene(context.scene), (
            "このチェックはページ編集シーンで実行する前提です"
        )
        # しっぽポイント上 → ドラッグ開始
        px, py = tail_points[0]
        _resolve_at(float(px), float(py))
        assert tail_helper.handle_plain_press(tool, context, fake_event) is True
        assert getattr(tool, "_drag_action", "") == "balloon_tail_point", getattr(tool, "_drag_action", "")
        # フキダシ本体 (ポイント以外) → つかまない (通常の移動操作に任せる)
        tool2 = SimpleNamespace()
        _resolve_at(float(entry.x_mm) + 5.0, float(entry.y_mm) + 5.0)
        assert tail_helper.handle_plain_press(tool2, context, fake_event) is False
        assert not getattr(tool2, "_dragging", False)
        # ページ編集シーン以外 (ページ一覧) では不可視ポイントをつかまない
        tool3 = SimpleNamespace()
        _resolve_at(float(px), float(py))
        original_is_page_edit = page_file_scene.is_page_edit_scene
        page_file_scene.is_page_edit_scene = lambda _scene: False
        try:
            assert tail_helper.handle_plain_press(tool3, context, fake_event) is False
        finally:
            page_file_scene.is_page_edit_scene = original_is_page_edit
    finally:
        balloon_op._resolve_page_from_event = original_resolve
    print("TAIL_POINT_PLAIN_PRESS_OK", flush=True)


def main() -> None:
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bname_preview_res_tail_"))
    result = bpy.ops.bname.work_new(filepath=str(temp_root / "PreviewResTail.bname"))
    assert result == {"FINISHED"}, result
    _check_merged_outline()
    # フキダシ実体としっぽ操作はページ編集シーンで検証する
    result = bpy.ops.bname.open_page_file("EXEC_DEFAULT", index=0)
    assert result == {"FINISHED"}, result
    _check_image_size()
    _check_preview_png_tail_carve(temp_root)
    _check_tail_point_plain_press()
    print("BNAME_PAGE_PREVIEW_RESOLUTION_AND_TAIL_CHECK_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        os._exit(1)
    os._exit(0)
