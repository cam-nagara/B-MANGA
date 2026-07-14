"""Blender実機用: もやもやフキダシの小山・乱れが表示用曲線へ反映されることを確認。"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
ADDON_NAME = "bmanga_dev_fluffy_shape_params"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        ADDON_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ADDON_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _import(name: str):
    return importlib.import_module(f"{ADDON_NAME}.{name}")


def _signature(anchors) -> tuple[tuple[float, float, float, float, float, float], ...]:
    if not anchors:
        raise AssertionError("もやもやフキダシの表示用曲線が生成されていません")
    return tuple(
        (
            round(float(anchor.co[0]), 4),
            round(float(anchor.co[1]), 4),
            round(float(anchor.handle_left[0]), 4),
            round(float(anchor.handle_left[1]), 4),
            round(float(anchor.handle_right[0]), 4),
            round(float(anchor.handle_right[1]), 4),
        )
        for anchor in anchors
    )


def main() -> None:
    _load_addon()
    balloon_shapes = _import("utils.balloon_shapes")
    geom = _import("utils.geom")

    scene = bpy.context.scene
    page = scene.bmanga_work.pages.add()
    page.id = "p0001"
    entry = page.balloons.add()
    entry.id = "fluffy_shape_param_test"
    entry.shape = "fluffy"
    entry.width_mm = 90.0
    entry.height_mm = 52.0
    params = entry.shape_params
    params.cloud_bump_width_mm = 12.0
    params.cloud_bump_height_mm = 7.0
    params.cloud_offset_percent = 35.0
    params.shape_seed = 17

    rect = geom.Rect(0.0, 0.0, entry.width_mm, entry.height_mm)
    base_sig = _signature(balloon_shapes.bezier_loop_for_entry(entry, rect))

    params.cloud_sub_width_ratio = 45.0
    params.cloud_sub_height_ratio = 90.0
    sub_sig = _signature(balloon_shapes.bezier_loop_for_entry(entry, rect))
    # De Casteljau 分割では小山設定を変えてもアンカー数は一定なので、
    # アンカー数ではなく輪郭自体が変わることを確認する。
    if sub_sig == base_sig:
        raise AssertionError("小山の設定が、もやもやフキダシの表示用曲線へ反映されていません")

    params.cloud_bump_width_jitter = 0.8
    params.cloud_bump_height_jitter = 0.7
    params.cloud_sub_width_jitter = 0.9
    params.cloud_sub_height_jitter = 0.9
    params.shape_seed = 91
    jitter_sig = _signature(balloon_shapes.bezier_loop_for_entry(entry, rect))
    if jitter_sig == sub_sig:
        raise AssertionError("乱れの設定が、もやもやフキダシの表示用曲線へ反映されていません")

    print("BMANGA_BALLOON_FLUFFY_SHAPE_PARAMS_CHECK_OK")


if __name__ == "__main__":
    try:
        main()
    finally:
        mod = sys.modules.get(ADDON_NAME)
        if mod is not None and hasattr(mod, "unregister"):
            mod.unregister()
