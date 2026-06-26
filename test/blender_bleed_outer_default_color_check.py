"""Blender background check: bleed-outer fill defaults to Blender viewport gray."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_bleed_outer_default"


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


def _rgb255_from_linear(color) -> tuple[int, int, int]:
    from bmanga_dev_bleed_outer_default.utils import color_space

    srgb = color_space.linear_to_srgb_rgb(color[:3])
    return tuple(int(round(max(0.0, min(1.0, float(c))) * 255.0)) for c in srgb)


def _rgb255_from_display(color) -> tuple[int, int, int]:
    return tuple(int(round(max(0.0, min(1.0, float(c))) * 255.0)) for c in color[:3])


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_bleed_outer_default_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "BleedOuterDefault.bmanga"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")

        from bmanga_dev_bleed_outer_default.io import schema
        from bmanga_dev_bleed_outer_default.ui import overlay_paper_guide
        from bmanga_dev_bleed_outer_default.utils import page_preview_decor
        from bmanga_dev_bleed_outer_default.utils import paper_guide_object

        work = bpy.context.scene.bmanga_work
        page = work.pages[0]
        overlay = work.safe_area_overlay

        if _rgb255_from_linear(overlay.bleed_outer_color) != (64, 64, 64):
            raise AssertionError(f"新規作品の裁ち落とし枠外が標準背景色ではありません: {overlay.bleed_outer_color[:3]}")
        saved = schema.safe_area_to_dict(overlay)
        if saved.get("bleedOuterFill", {}).get("color") != "#404040":
            raise AssertionError(f"保存データの裁ち落とし枠外色が標準背景色ではありません: {saved}")

        schema.safe_area_from_dict(
            overlay,
            {"bleedOuterFill": {"enabled": True, "color": "#000000FF", "opacity": 100.0, "opacityUnit": "percent"}},
        )
        if _rgb255_from_linear(overlay.bleed_outer_color) != (64, 64, 64):
            raise AssertionError("旧黒初期値の読込補正が効いていません")

        preview_rgba = page_preview_decor._bleed_outer_fill_color(work)
        if preview_rgba[:3] != (64, 64, 64) or preview_rgba[3] != 255:
            raise AssertionError(f"ページ一覧プレビュー用の裁ち落とし枠外色が不正です: {preview_rgba}")

        display_rgba = overlay_paper_guide._bleed_outer_fill_color(work)
        if _rgb255_from_display(display_rgba) != (64, 64, 64):
            raise AssertionError(f"画面描画用の裁ち落とし枠外色が不正です: {display_rgba}")

        paper_guide_object.regenerate_all_paper_guides(bpy.context.scene, work)
        bleed_obj = bpy.data.objects.get(f"{paper_guide_object.PAPER_BLEED_OUTER_FILL_PREFIX}{page.id}")
        if bleed_obj is None:
            raise AssertionError("裁ち落とし枠外塗りの実体が作られていません")
        if _rgb255_from_linear(bleed_obj.color) != (64, 64, 64):
            raise AssertionError(f"裁ち落とし枠外塗り実体の色が標準背景色ではありません: {bleed_obj.color[:]}")

        print("BMANGA_BLEED_OUTER_DEFAULT_COLOR_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:  # noqa: BLE001
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
