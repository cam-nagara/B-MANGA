"""Blender実機用: コマ用blendのページ画像/ページ一覧プレビューのカメラ追従確認."""

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
        "bmanga_dev_coma_preview_follow",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_coma_preview_follow"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _page_preview_objects() -> list[bpy.types.Object]:
    return [
        obj
        for obj in bpy.data.objects
        if str(obj.get("bmanga_kind", "") or "") == "page_preview"
        and not bool(getattr(obj, "hide_viewport", False))
    ]


def _translation(obj: bpy.types.Object):
    return obj.matrix_world.to_translation().copy()


def _assert_delta(value: float, expected: float, label: str) -> None:
    if abs(value - expected) > 1.0e-5:
        raise AssertionError(f"{label}: {value} != {expected}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_preview_follow_"))
    mod = None
    try:
        mod = _load_addon()
        work_dir = temp_root / "ComaPreviewFollow.bmanga"
        result = bpy.ops.bmanga.work_new(filepath=str(work_dir))
        assert result == {"FINISHED"}, result
        for _ in range(2):
            result = bpy.ops.bmanga.page_add()
            assert result == {"FINISHED"}, result

        result = bpy.ops.bmanga.open_page_file(index=1)
        assert result == {"FINISHED"}, result
        work = bpy.context.scene.bmanga_work
        work.active_page_index = 1
        work.pages[1].active_coma_index = 0
        result = bpy.ops.bmanga.enter_coma_mode()
        assert result == {"FINISHED"}, result

        from bmanga_dev_coma_preview_follow.utils import coma_camera, page_file_scene, page_preview_object

        role, page_id, coma_id = page_file_scene.current_role(bpy.context)
        assert role == page_file_scene.ROLE_COMA, (role, page_id, coma_id)
        scene = bpy.context.scene
        scene.bmanga_page_preview_enabled = True
        scene.bmanga_page_preview_range_mode = "ALL"
        scene.bmanga_coma_camera_settings.name_visible = True
        scene.bmanga_coma_camera_settings.name_show_all_pages = True
        coma_camera.ensure_coma_camera_scene(
            bpy.context,
            work=scene.bmanga_work,
            page_id=page_id,
            coma_id=coma_id,
            generate_references=True,
        )
        camera = scene.camera
        if camera is None or getattr(camera, "type", "") != "CAMERA":
            raise AssertionError("コマ用blendのカメラが作られていません")

        backgrounds = list(getattr(camera.data, "background_images", []) or [])
        page_backgrounds = [
            bg
            for bg in backgrounds
            if getattr(bg, "image", None) is not None
            and (
                bool(bg.image.get("bmanga_full_page_mask", False))
                or str(bg.image.get("bmanga_kind", "") or "") == "name"
            )
        ]
        if not page_backgrounds:
            raise AssertionError("ページ画像がカメラ背景として作られていません")

        updated = page_preview_object.sync_page_previews(bpy.context, scene.bmanga_work, force=True)
        if updated <= 0:
            raise AssertionError("ページ一覧プレビューが作られていません")
        previews = _page_preview_objects()
        if not previews:
            raise AssertionError("表示中のページ一覧プレビューがありません")
        for obj in previews:
            if obj.parent is not camera:
                raise AssertionError(f"{obj.name} がカメラに追従していません")
            if not bool(obj.get(page_preview_object.PREVIEW_CAMERA_FOLLOW_PROP, False)):
                raise AssertionError(f"{obj.name} にカメラ追従状態が保存されていません")

        before = [(obj, _translation(obj)) for obj in previews]
        camera.location.x += 0.25
        camera.location.y -= 0.125
        bpy.context.view_layer.update()
        for obj, start in before:
            moved = _translation(obj) - start
            _assert_delta(float(moved.x), 0.25, f"{obj.name} X移動")
            _assert_delta(float(moved.y), -0.125, f"{obj.name} Y移動")

        page_preview_object.sync_page_previews(bpy.context, scene.bmanga_work, force=True)
        bpy.context.view_layer.update()
        for obj, start in before:
            moved = _translation(obj) - start
            _assert_delta(float(moved.x), 0.25, f"{obj.name} 再同期後X移動")
            _assert_delta(float(moved.y), -0.125, f"{obj.name} 再同期後Y移動")

        print("BMANGA_COMA_PAGE_PREVIEW_CAMERA_FOLLOW_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
