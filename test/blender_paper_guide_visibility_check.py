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


def _guide_curve_objects(paper_guide_object, page):
    page_id = str(getattr(page, "id", "") or "")
    return [
        obj
        for obj in bpy.data.objects
        if str(obj.get(paper_guide_object.PROP_GUIDE_OWNER_ID, "") or "") == page_id
        and str(obj.get(paper_guide_object.PROP_GUIDE_KIND, "") or "") in {"dim", "light", "inner", "safe"}
        and obj.type == "CURVE"
    ]


def _assert_guide_materials_are_opaque(guide_objects) -> None:
    for guide_obj in guide_objects:
        materials = list(getattr(guide_obj.data, "materials", []) or [])
        if not materials:
            raise AssertionError(f"用紙ガイド線の素材がありません: {guide_obj.name}")
        for mat in materials:
            alpha = float(mat.diffuse_color[3])
            if abs(alpha - 1.0) > 1.0e-6:
                raise AssertionError(f"用紙ガイド線の素材が不透明ではありません: {mat.name} alpha={alpha}")


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
            radius = float(obj.data.bevel_depth)
            if abs(radius - expected_radius) > 1.0e-9:
                raise AssertionError(f"用紙ガイド線の太さが1px相当ではありません: {radius} != {expected_radius}")
    finally:
        paper_guide_object._active_view3d_region = original_region
        paper_guide_object._meters_per_pixel = original_mpp
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
        from bname_dev_paper_guide_visibility.utils import paper_guide_object

        scene = bpy.context.scene
        work = get_work(bpy.context)
        if work is None or not work.loaded:
            raise AssertionError("作品データが読み込まれていません")
        page = work.pages[0]
        guide_objects = _guide_curve_objects(paper_guide_object, page)
        if not guide_objects:
            raise AssertionError("用紙ガイド線の実体がありません")
        if not any(len(getattr(obj.data, "splines", []) or []) > 0 for obj in guide_objects):
            raise AssertionError("用紙ガイド線が作られていません")

        _assert_guide_materials_are_opaque(guide_objects)
        _assert_constant_thickness(paper_guide_object, guide_objects)

        print("BNAME_PAPER_GUIDE_VISIBILITY_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
