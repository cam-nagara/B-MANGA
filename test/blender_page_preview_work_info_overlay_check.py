"""Blender実機チェック: ページ一覧プレビューと作品情報の分離."""

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
MOD_NAME = "bmanga_dev_page_preview_work_info"


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


def _count_magenta_pixels(path: Path) -> int:
    from PIL import Image

    with Image.open(str(path)) as opened:
        image = opened.convert("RGBA")
        return sum(
            1
            for r, g, b, a in image.getdata()
            if a > 200 and r > 180 and g < 80 and b > 180
        )


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_page_preview_work_info_"))
    mod = None
    success = False
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PreviewWorkInfo.bmanga"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")

        from bmanga_dev_page_preview_work_info.core.work import get_work
        from bmanga_dev_page_preview_work_info.utils import page_preview_object, work_info_text_object

        scene = bpy.context.scene
        work = get_work(bpy.context)
        if work is None or not work.loaded:
            raise AssertionError("作品データが読み込まれていません")
        page = work.pages[0]
        info = work.work_info
        info.work_name = "MAGENTA_WORK_INFO_VISIBLE"
        info.display_work_name.enabled = True
        info.display_work_name.position = "top-left"
        info.display_work_name.font_size_q = 60.0
        info.display_work_name.color = (1.0, 0.0, 1.0, 1.0)

        page_preview_object.sync_page_previews(bpy.context, work, force=True)
        work_info_text_object.regenerate_all_work_info_texts(scene, work)
        preview_path = page_preview_object.ensure_preview_png(
            work,
            page,
            0,
            current=False,
            scene=scene,
            force=True,
        )
        if preview_path is None or not Path(preview_path).is_file():
            raise AssertionError("ページ一覧プレビュー画像が作られていません")
        if _count_magenta_pixels(Path(preview_path)) > 0:
            raise AssertionError("ページ一覧プレビュー画像に作品情報が焼き込まれています")

        preview = bpy.data.objects.get(f"{page_preview_object.PREVIEW_OBJECT_PREFIX}{page.id}")
        if preview is None:
            raise AssertionError("ページ一覧プレビューの板が作られていません")
        info_objects = [
            obj
            for obj in bpy.data.objects
            if obj.get(work_info_text_object.PROP_WORK_INFO_KIND) == "work_info_text"
        ]
        if not info_objects:
            raise AssertionError("作品情報のオーバーレイが作られていません")
        if not all(float(preview.location.z) < float(obj.location.z) for obj in info_objects):
            raise AssertionError("作品情報のオーバーレイがページ一覧プレビューより手前にありません")

        from PIL import Image

        with Image.open(str(preview_path)) as opened:
            version = str(opened.info.get(page_preview_object.PREVIEW_RENDER_VERSION_KEY, "") or "")
        if version != page_preview_object.PREVIEW_RENDER_VERSION:
            raise AssertionError("ページ一覧プレビュー画像の生成仕様版が保存されていません")

        print(f"BMANGA_PAGE_PREVIEW_WORK_INFO_OVERLAY_OK image={preview_path}", flush=True)
        success = True
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)
        os._exit(0 if success else 1)


if __name__ == "__main__":
    main()
