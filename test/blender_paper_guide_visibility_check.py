"""用紙ガイド線が不透明かつ画面上 1px 相当の太さになることを確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_paper_guide_visibility",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_paper_guide_visibility"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _guide_objects(paper_guide_object, page):
    page_id = str(getattr(page, "id", "") or "")
    return [
        obj
        for obj in bpy.data.objects
        if str(obj.get(paper_guide_object.PROP_GUIDE_OWNER_ID, "") or "") == page_id
        and str(obj.get(paper_guide_object.PROP_GUIDE_KIND, "") or "") == paper_guide_object.GUIDE_KIND_LINES
        and obj.type == "CURVE"
    ]


def _curve_spline_count(obj) -> int:
    return len(getattr(getattr(obj, "data", None), "splines", []) or [])


def _assert_guide_materials_are_opaque(guide_objects) -> None:
    for guide_obj in guide_objects:
        materials = list(getattr(guide_obj.data, "materials", []) or [])
        if not materials:
            raise AssertionError(f"用紙ガイド線の素材がありません: {guide_obj.name}")
        for mat in materials:
            alpha = float(mat.diffuse_color[3])
            if abs(alpha - 1.0) > 1.0e-6:
                raise AssertionError(f"用紙ガイド線の素材が不透明ではありません: {mat.name} alpha={alpha}")
            if str(getattr(mat, "blend_method", "")) == "BLEND":
                raise AssertionError(f"用紙ガイド線の素材が半透明表示方式です: {mat.name}")
            if str(getattr(mat, "surface_render_method", "")) == "BLENDED":
                raise AssertionError(f"用紙ガイド線の素材が半透明表示方式です: {mat.name}")


def _assert_stable_viewport_order(guide_objects, safe_fill) -> None:
    for obj in guide_objects:
        if bool(getattr(obj, "show_in_front", False)):
            raise AssertionError(f"用紙ガイド線が最前面ワイヤ表示に依存しています: {obj.name}")
        if bool(getattr(obj, "show_transparent", False)):
            raise AssertionError(f"用紙ガイド線が透明表示になっています: {obj.name}")
        if float(obj.location.z) <= float(safe_fill.location.z):
            raise AssertionError(f"用紙ガイド線がセーフライン外塗りより奥にあります: {obj.name}")


def _assert_guide_below_coma_planes(guide_objects, page, coma_z_order) -> None:
    if len(getattr(page, "comas", []) or []) == 0:
        return
    lowest_plane_z = min(float(coma_z_order.plane_z(coma)) for coma in page.comas)
    for obj in guide_objects:
        if not (float(obj.location.z) < lowest_plane_z):
            raise AssertionError(
                f"用紙ガイド線がコマプレビュー面より手前にあります: guide={obj.location.z}, plane={lowest_plane_z}"
            )


def _assert_constant_thickness(paper_guide_object, guide_objects) -> None:
    if abs(float(paper_guide_object.GUIDE_SCREEN_PX) - 1.0) > 1.0e-6:
        raise AssertionError(f"用紙ガイド線の画面太さが1pxではありません: {paper_guide_object.GUIDE_SCREEN_PX}")

    original_region = paper_guide_object._active_view3d_region
    original_mpp = paper_guide_object._meters_per_pixel
    try:
        mpp = 0.002
        paper_guide_object._last_mpp = -1.0
        paper_guide_object._active_view3d_region = lambda: (object(), object())
        paper_guide_object._meters_per_pixel = lambda _region, _rv3d: mpp
        paper_guide_object.apply_view_constant_thickness()
        expected_radius = (
            paper_guide_object.GUIDE_SCREEN_PX
            * mpp
            * 0.5
            * paper_guide_object._GUIDE_CURVE_RADIUS_SCALE
        )
        for obj in guide_objects:
            radius = float(getattr(obj.data, "bevel_depth", 0.0) or 0.0)
            if abs(radius - expected_radius) > 1.0e-9:
                raise AssertionError(f"用紙ガイド線の太さが1px相当ではありません: {radius} != {expected_radius}")
    finally:
        paper_guide_object._active_view3d_region = original_region
        paper_guide_object._meters_per_pixel = original_mpp
        paper_guide_object._last_mpp = -1.0


def _assert_timer_does_not_touch_closed_panel(paper_guide_object, guide_objects) -> None:
    sample = next((obj for obj in guide_objects if _curve_spline_count(obj) > 0), None)
    if sample is None:
        raise AssertionError("用紙ガイド線の太さ監視確認対象がありません")
    original_allowed = paper_guide_object._live_guide_updates_allowed
    original_depth = float(sample.data.bevel_depth)
    manual_depth = original_depth * 3.0 + 0.001
    try:
        sample.data.bevel_depth = manual_depth
        paper_guide_object._live_guide_updates_allowed = lambda: False
        paper_guide_object._last_mpp = -1.0
        paper_guide_object._thickness_timer()
        if abs(float(sample.data.bevel_depth) - manual_depth) > 1.0e-9:
            raise AssertionError("B-Nameタブ非表示扱いでも用紙ガイド線の太さが戻されています")
    finally:
        sample.data.bevel_depth = original_depth
        paper_guide_object._live_guide_updates_allowed = original_allowed
        paper_guide_object._last_mpp = -1.0


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_paper_guide_visibility_"))
    mod = None
    try:
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "PaperGuideVisibility.bname"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")

        from bname_dev_paper_guide_visibility.core.work import get_work
        from bname_dev_paper_guide_visibility.utils import coma_z_order, paper_guide_object

        scene = bpy.context.scene
        work = get_work(bpy.context)
        if work is None or not work.loaded:
            raise AssertionError("作品データが読み込まれていません")
        page = work.pages[0]
        guide_objects = _guide_objects(paper_guide_object, page)
        if not guide_objects:
            raise AssertionError("用紙ガイド線の実体がありません")
        if len(guide_objects) != 1:
            raise AssertionError(f"用紙ガイド線はページごとに1オブジェクトである必要があります: {guide_objects}")
        if not any(_curve_spline_count(obj) > 0 for obj in guide_objects):
            raise AssertionError("用紙ガイド線が作られていません")
        safe_fill = bpy.data.objects.get(f"{paper_guide_object.PAPER_SAFE_FILL_PREFIX}{page.id}")
        if safe_fill is None:
            raise AssertionError("セーフライン外塗りが作られていません")

        _assert_guide_materials_are_opaque(guide_objects)
        _assert_stable_viewport_order(guide_objects, safe_fill)
        _assert_guide_below_coma_planes(guide_objects, page, coma_z_order)
        _assert_constant_thickness(paper_guide_object, guide_objects)
        _assert_timer_does_not_touch_closed_panel(paper_guide_object, guide_objects)
        if paper_guide_object.repair_loaded_work_paper_guides(scene, work):
            raise AssertionError("用紙ガイド線の修復が不要な状態で再実行されています")

        print("BNAME_PAPER_GUIDE_VISIBILITY_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
