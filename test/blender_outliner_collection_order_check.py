"""Blender 実機(背景)用: アウトライナー主要コレクション順とコマ名表示の確認."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_outliner_order",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_outliner_order"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = _load_addon()
    try:
        from bname_dev_outliner_order.core.work import get_work
        from bname_dev_outliner_order.io import schema
        from bname_dev_outliner_order.utils import layer_object_sync, layer_stack, outliner_model

        context = bpy.context
        work = get_work(context)
        work.loaded = True

        for page_index in range(2):
            page = work.pages.add()
            page.id = f"p{page_index + 1:04d}"
            page.title = f"{page_index + 1}ページ"
            coma = page.comas.add()
            coma.id = f"c{page_index + 1:02d}"
            coma.coma_id = coma.id
            coma.title = "基本枠 1-1" if page_index == 0 else "通常コマ"
            coma.shape_type = "rect"
            coma.rect_x_mm = 20.0
            coma.rect_y_mm = 20.0
            coma.rect_width_mm = 60.0
            coma.rect_height_mm = 90.0
            coma.z_order = page_index

        layer_object_sync.mirror_work_to_outliner(context.scene, work)
        root = outliner_model.ensure_root_collection(context.scene)
        names = [coll.name for coll in root.children]
        assert names[:3] == ["text", "outside", "p0001"], f"主要コレクション順が不正です: {names}"
        assert "workinfo" in names, f"workinfo コレクションがありません: {names}"

        coma_labels = [
            str(target.label)
            for target in layer_stack.collect_targets(context)
            if target.kind == layer_stack.COMA_KIND
        ]
        if not coma_labels:
            raise AssertionError("コマ行がレイヤーリストに作られていません")
        if any("基本枠" in label for label in coma_labels):
            raise AssertionError(f"レイヤーリストのコマ名に基本枠が残っています: {coma_labels}")

        loaded_page = work.pages.add()
        schema.page_from_dict(
            loaded_page,
            {
                "id": "p9999",
                "title": "p9999",
                "comas": [
                    {
                        "id": "c01",
                        "comaId": "c01",
                        "title": "c01 (分割)",
                    }
                ],
            },
        )
        if loaded_page.title != "":
            raise AssertionError(f"自動ページ名が空になっていません: {loaded_page.title!r}")
        if loaded_page.comas[0].title != "":
            raise AssertionError(f"自動コマ名が空になっていません: {loaded_page.comas[0].title!r}")
        print("BNAME_OUTLINER_COLLECTION_ORDER_OK")
    finally:
        mod.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
