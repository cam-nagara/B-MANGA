"""Blender 実機用: 枠線プリセットがページ/コマ往復で崩れないことを確認."""

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
        "bmanga_dev_border_roundtrip",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_border_roundtrip"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _current_page(work):
    page_id = str(getattr(bpy.context.scene, "bmanga_current_page_id", "") or "")
    for page in work.pages:
        if str(getattr(page, "id", "") or "") == page_id:
            return page
    return work.pages[int(getattr(work, "active_page_index", 0))]


def _make_stale_standard_border(coma_border_object, page_id: str, coma_id: str) -> str:
    curve_name = f"{coma_border_object.COMA_BORDER_CURVE_PREFIX}{page_id}_{coma_id}"
    obj_name = f"{coma_border_object.COMA_BORDER_NAME_PREFIX}{page_id}_{coma_id}"
    curve = bpy.data.curves.new(curve_name, type="CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new(type="POLY")
    spline.points.add(1)
    spline.points[0].co = (0.0, 0.0, 0.0, 1.0)
    spline.points[1].co = (0.08, 0.0, 0.0, 1.0)
    curve.bevel_depth = 0.001
    obj = bpy.data.objects.new(obj_name, curve)
    obj[coma_border_object.PROP_COMA_BORDER_KIND] = "coma_border"
    obj[coma_border_object.PROP_COMA_BORDER_OWNER_ID] = f"{page_id}:{coma_id}"
    bpy.context.scene.collection.objects.link(obj)
    return obj.name


def _assert_border_runtime(work, page, preset_name: str) -> None:
    from bmanga_dev_border_roundtrip.utils import coma_border_object, coma_plane

    if len(page.comas) == 0:
        raise AssertionError(f"{preset_name}: コマがありません")
    coma = page.comas[0]
    page_id = str(getattr(page, "id", "") or "")
    coma_id = str(getattr(coma, "id", "") or getattr(coma, "coma_id", "") or "")
    owner = f"{page_id}:{coma_id}"
    border = getattr(coma, "border", None)
    style = str(getattr(border, "style", "solid") or "solid")
    border_objects = [
        obj for obj in bpy.data.objects
        if str(obj.get(coma_border_object.PROP_COMA_BORDER_OWNER_ID, "") or "") == owner
    ]
    visible_borders = [obj for obj in border_objects if not bool(getattr(obj, "hide_viewport", False))]
    if style == "brush":
        if border_objects:
            raise AssertionError(f"{preset_name}: 輪郭ぼかしに通常枠線実体が残っています: {border_objects}")
        plane = coma_plane.find_coma_plane_object(page_id, coma_id)
        if plane is None:
            raise AssertionError(f"{preset_name}: 輪郭ぼかしのコマ面がありません")
        if plane.data.attributes.get(coma_plane.COMA_PLANE_SOFT_MASK_ATTR) is None:
            raise AssertionError(f"{preset_name}: 輪郭ぼかし濃度がありません")
        return
    if not bool(getattr(border, "visible", True)):
        if visible_borders:
            raise AssertionError(f"{preset_name}: 非表示プリセットの枠線が表示されています: {visible_borders}")
        return
    if not visible_borders:
        raise AssertionError(f"{preset_name}: 枠線実体が表示されていません")


def _ensure_pages_for_presets(work, preset_count: int) -> None:
    while len(work.pages) < preset_count:
        result = bpy.ops.bmanga.page_add("EXEC_DEFAULT")
        if "FINISHED" not in result:
            raise AssertionError(f"ページ追加に失敗しました: {result}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_border_roundtrip_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        work_dir = temp_root / "BorderRoundtrip.bmanga"
        result = bpy.ops.bmanga.work_new(filepath=str(work_dir))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")

        from bmanga_dev_border_roundtrip.io import border_presets
        from bmanga_dev_border_roundtrip.utils import coma_border_object, page_file_scene

        work = bpy.context.scene.bmanga_work
        presets = border_presets.list_global_presets()
        if not presets:
            raise AssertionError("枠線プリセットが見つかりません")
        _ensure_pages_for_presets(work, len(presets))
        for index, preset in enumerate(presets):
            page = work.pages[index]
            if len(page.comas) == 0:
                raise AssertionError(f"{preset.name}: 初期コマがありません")
            coma = page.comas[0]
            coma.title = preset.name
            coma.rect_x_mm = 30.0
            coma.rect_y_mm = 55.0
            coma.rect_width_mm = 150.0
            coma.rect_height_mm = 185.0
            border_presets.apply_preset_to_coma(preset, coma)
        if "FINISHED" not in bpy.ops.bmanga.work_save("EXEC_DEFAULT"):
            raise AssertionError("作品保存に失敗しました")

        for index, preset in enumerate(presets):
            result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=index)
            if "FINISHED" not in result:
                raise AssertionError(f"{preset.name}: ページを開けません: {result}")
            work = bpy.context.scene.bmanga_work
            page = _current_page(work)
            if page_file_scene.current_page_id(bpy.context.scene) != str(getattr(page, "id", "") or ""):
                raise AssertionError(f"{preset.name}: 現在ページが一致しません")
            _assert_border_runtime(work, page, preset.name)

            coma = page.comas[0]
            if str(getattr(coma.border, "style", "") or "") == "brush":
                stale_name = _make_stale_standard_border(coma_border_object, page.id, coma.id)
                bpy.ops.wm.save_as_mainfile(filepath=str(bpy.data.filepath))
                result = bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT")
                if "FINISHED" not in result:
                    raise AssertionError(f"{preset.name}: ページ一覧へ戻れません: {result}")
                result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=index)
                if "FINISHED" not in result:
                    raise AssertionError(f"{preset.name}: ページを再度開けません: {result}")
                if bpy.data.objects.get(stale_name) is not None:
                    raise AssertionError(f"{preset.name}: 古い標準枠線実体が再読み込み後も残っています")
                work = bpy.context.scene.bmanga_work
                page = _current_page(work)
                _assert_border_runtime(work, page, preset.name)

            work.active_page_index = index
            page.active_coma_index = 0
            result = bpy.ops.bmanga.enter_coma_mode("EXEC_DEFAULT")
            if "FINISHED" not in result:
                raise AssertionError(f"{preset.name}: コマ編集へ入れません: {result}")
            result = bpy.ops.bmanga.exit_coma_mode("EXEC_DEFAULT")
            if "FINISHED" not in result:
                raise AssertionError(f"{preset.name}: ページへ戻れません: {result}")
            work = bpy.context.scene.bmanga_work
            page = _current_page(work)
            _assert_border_runtime(work, page, preset.name)
            result = bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT")
            if "FINISHED" not in result:
                raise AssertionError(f"{preset.name}: ページ一覧へ戻れません: {result}")

        print("BMANGA_BORDER_PRESET_FILE_ROUNDTRIP_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
